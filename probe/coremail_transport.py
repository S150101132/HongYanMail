"""Small HTTPS-only Coremail transport using an independent in-memory session."""
import json
import re
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import (Request, HTTPRedirectHandler, HTTPCookieProcessor,
                            build_opener)


class CoremailError(Exception):
    """A fixed local failure code, without remote response details."""
    def __init__(self, code='TRANSPORT_ERROR'):
        self.code = code if code in {
            'TRANSPORT_ERROR', 'UNSAFE_ORIGIN', 'UNSUPPORTED_LOGIN',
            'AUTH_INTERACTION_REQUIRED', 'NOT_AUTHENTICATED', 'RPC_NOT_ALLOWED',
            'RPC_REJECTED', 'INVALID_RESPONSE', 'MESSAGE_UNAVAILABLE',
        } else 'TRANSPORT_ERROR'
        super().__init__(self.code)


class LoginChallenge(CoremailError):
    def __init__(self):
        super().__init__('AUTH_INTERACTION_REQUIRED')


def _origin(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise CoremailError('UNSAFE_ORIGIN')
    return (parts.scheme, parts.hostname.lower(), parts.port or 443)


class SameOriginRedirectHandler(HTTPRedirectHandler):
    """Check before urllib constructs or sends any redirected request."""
    def __init__(self, base_url):
        super().__init__()
        self.origin = _origin(base_url)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != self.origin:
            raise CoremailError('UNSAFE_ORIGIN')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def decode_extended_json(source):
    """Decode JSON plus Coremail !!date tokens, never interpreting code."""
    output = []
    index = 0
    in_string = False
    escaped = False
    while index < len(source):
        char = source[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
        elif char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif source.startswith('!!date', index):
            match = re.match(r"!!date\s+'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'", source[index:])
            if not match:
                raise CoremailError('INVALID_RESPONSE')
            output.append(json.dumps(match.group(1)))
            index += match.end()
        else:
            output.append(char)
            index += 1
    try:
        return json.loads(''.join(output))
    except (ValueError, TypeError):
        raise CoremailError('INVALID_RESPONSE') from None


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}
        self.active = False
        self.has_password = False
        self.fake_password = False
        self.scripts = []
        self.in_script = False
        self.meta_refresh = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'refresh':
            self.meta_refresh.append(attrs.get('content', ''))
        if tag == 'script':
            self.in_script = True
        if tag == 'form' and 'j-login-form' in attrs.get('class', '').split():
            self.action = attrs.get('action', '')
            self.active = True
        if tag == 'input' and self.active:
            if attrs.get('type', '').lower() == 'hidden' and attrs.get('name'):
                self.fields[attrs['name']] = attrs.get('value', '')
            if attrs.get('name') == 'password':
                self.has_password = True
            if attrs.get('id') == 'fakePassword':
                self.fake_password = True

    def handle_endtag(self, tag):
        if tag == 'form':
            self.active = False
        if tag == 'script':
            self.in_script = False

    def handle_data(self, data):
        if self.in_script:
            self.scripts.append(data)


def _login_form(page):
    parser = _FormParser()
    parser.feed(page)
    if parser.action is not None:
        return parser
    # The public page holds its template in a JavaScript string. Decode only
    # string escapes, not JavaScript expressions or executable code.
    for script in parser.scripts:
        for match in re.finditer(r"'((?:\\.|[^'\\])*)'|\"((?:\\.|[^\"\\])*)\"", script):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            if 'j-login-form' not in value:
                continue
            def unescape(escape):
                token = escape.group(1)
                if token.startswith('u'):
                    return chr(int(token[1:], 16))
                return {'n': '\n', 'r': '\r', 't': '\t'}.get(token, token)
            value = re.sub(r'\\(u[0-9a-fA-F]{4}|.)', unescape, value)
            candidate = _FormParser()
            candidate.feed(value)
            if candidate.action is not None:
                return candidate
    raise CoremailError('UNSUPPORTED_LOGIN')


def _application_redirect(page, final_url, base_url, origin):
    """Read the observed literal login redirect, without executing JavaScript."""
    parsed = _FormParser()
    parsed.feed(page)
    if parsed.action is not None or re.search(r'\bFA_[A-Z0-9_]{3,80}\b', page):
        return None
    candidates = []
    for script in parsed.scripts:
        for match in re.finditer(
                r'(?:window\.)?location\.(?:assign|replace)\s*\(\s*([\'"])([^\'"\\\s<>]+)\1\s*\)', script):
            target = urljoin(final_url, match.group(2))
            if _origin(target) != origin:
                raise CoremailError('UNSAFE_ORIGIN')
            parts = urlsplit(target)
            if (parts.path == urlsplit(urljoin(base_url, 'XT/index.jsp')).path
                    and len(parse_qs(parts.query).get('sid', [])) == 1):
                candidates.append(target)
    return candidates[0] if len(candidates) == 1 else None


def _login_diagnostics(page, final_url, origin, sensitive):
    """Retain response structure only; never retain HTTP body or query values."""
    parsed = _FormParser()
    parsed.feed(page)
    final = urlsplit(final_url)
    def url_metadata(url):
        parts = urlsplit(url)
        return {'path': parts.path,
                'query_keys': sorted(set(parse_qs(parts.query, keep_blank_values=True)))}
    redirects = []
    candidates = []
    for script in parsed.scripts:
        for match in re.finditer(
                r'(?:window\.)?location\.(assign|replace)\s*\(\s*([\'"])(.*?)\2\s*\)', script):
            candidates.append(('location.' + match.group(1), match.group(3)))
    for content in parsed.meta_refresh:
        match = re.search(r'\burl\s*=\s*(.+)', content, re.I)
        if match:
            candidates.append(('meta-refresh', match.group(1).strip(' \t\'"')))
    for kind, target in candidates:
        url = urljoin(final_url, target)
        try:
            same_origin = _origin(url) == origin
        except (CoremailError, ValueError):
            same_origin = False
        entry = {'kind': kind, 'same_origin': same_origin}
        if same_origin:
            entry.update(url_metadata(url))
        redirects.append(entry)
    diagnostics = {
        'final_path': final.path,
        'final_query_keys': sorted(set(parse_qs(final.query, keep_blank_values=True))),
        'has_login_form': parsed.action is not None,
        'has_password_form': bool(re.search(r'<input\b[^>]*\btype\s*=\s*([\'"]?)password\1', page, re.I)),
        'codes': sorted(set(re.findall(r'\bFA_[A-Z0-9_]{3,80}\b', page))),
        'has_s_ok': bool(re.search(r'\bS_OK\b', page)),
        'redirects': redirects,
    }
    # Defense in depth for servers that echo a submitted value in a path,
    # query key or apparent code. No secrets are stored on the session.
    def scrub(value):
        if isinstance(value, str):
            for secret in sensitive:
                if secret:
                    value = value.replace(secret, '[redacted]')
            return value
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        return value
    return scrub(diagnostics)


class CoremailSession:
    ALLOWED = frozenset({'mbox:getAllFolders', 'mbox:listMessages',
                         'mbox:readMessage', 'mbox:compose', 'user:getAttrs',
                         '%mbox/compose'})

    def __init__(self, base_url='https://newmail.inspur.com/coremail/'):
        self._origin = _origin(base_url)
        self.base_url = base_url.rstrip('/') + '/'
        self.sid = None
        self.last_login_diagnostics = {}
        self._cookies = CookieJar()
        self._opener = build_opener(SameOriginRedirectHandler(base_url),
                                    HTTPCookieProcessor(self._cookies))

    def _fetch(self, request):
        if _origin(request.full_url) != self._origin:
            raise CoremailError('UNSAFE_ORIGIN')
        failure = None
        try:
            with self._opener.open(request, timeout=30) as response:
                url = response.geturl()
                if _origin(url) != self._origin:
                    raise CoremailError('UNSAFE_ORIGIN')
                return response.read(), url
        except CoremailError as error:
            failure = error.code
        except Exception:
            failure = 'TRANSPORT_ERROR'
        # Raise outside the except suite to avoid retaining a sensitive
        # urllib exception (which can contain URL query values) as context.
        raise CoremailError(failure)

    def login(self, uid: str, password: str) -> None:
        self.close()
        self.last_login_diagnostics = {}
        raw, url = self._fetch(Request(self.base_url))
        form = _login_form(raw.decode('utf-8', errors='replace'))
        target = urljoin(url, form.action)
        if _origin(target) != self._origin:
            raise CoremailError('UNSAFE_ORIGIN')
        if not form.has_password or form.fake_password:
            raise CoremailError('UNSUPPORTED_LOGIN')
        fields = form.fields.copy()
        fields.update(uid=uid.strip(), password=password, accessToken='',
                      timestamp='', signature='', nonce='', authorizeDevice='')
        fields['device'] = json.dumps({
            'uuid': 'CoremailCompatibilityProbe/Windows',
            'imie': 'CoremailCompatibilityProbe/Windows',
            'friendlyName': 'CoremailCompatibilityProbe',
            'model': 'CoremailCompatibilityProbe', 'os': 'Windows',
            'osLanguage': 'zh-CN', 'deviceType': 'Webmail',
        }, separators=(',', ':'))
        request = Request(target, data=urlencode(fields).encode('utf-8'),
                          headers={'Content-Type': 'application/x-www-form-urlencoded'})
        raw, final_url = self._fetch(request)
        page = raw.decode('utf-8', errors='replace')
        self.last_login_diagnostics = _login_diagnostics(
            page, final_url, self._origin, (uid, password))
        redirect = _application_redirect(page, final_url, self.base_url, self._origin)
        if redirect:
            raw, final_url = self._fetch(Request(redirect))
            page = raw.decode('utf-8', errors='replace')
            self.last_login_diagnostics['followed_application_redirect'] = True
        sid = parse_qs(urlsplit(final_url).query).get('sid', [None])[0]
        if not sid:
            match = re.search(r'\bX\.s\s*=\s*([\'"])([^\'"\s<>]+)\1', page)
            if not match:
                match = re.search(
                    r'\bX\s*=\s*\{[^{}]*?\bs\s*:\s*([\'"])([^\'"\s<>]+)\1', page)
            if match:
                sid = match.group(2)
        if not sid:
            for cookie in self._cookies:
                if cookie.name == 'Coremail.sid' and cookie.value:
                    sid = cookie.value
                    break
        if not sid or len(sid) > 512 or re.search(r'[\s<>"\']', sid):
            self.close()
            raise LoginChallenge()
        self.sid = sid

    def _rpc_url(self, func, **parameters):
        if not self.sid:
            raise CoremailError('NOT_AUTHENTICATED')
        if func == '%mbox/compose':
            return urljoin(self.base_url, 'common/mbox/compose.jsp') + '?' + urlencode(
                {'sid': self.sid, 'isUserConfirmed': 'true'})
        return urljoin(self.base_url, 's/json') + '?' + urlencode(
            {'sid': self.sid, 'func': func, **parameters})

    def call(self, func: str, payload: dict) -> dict:
        if func not in self.ALLOWED:
            raise CoremailError('RPC_NOT_ALLOWED')
        request = Request(self._rpc_url(func),
                          data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                          headers={'Accept': 'text/x-json',
                                   'Content-Type': 'text/x-json; tz="Asia/Riyadh"'})
        raw, unused_url = self._fetch(request)
        try:
            result = decode_extended_json(raw.decode('utf-8-sig'))
        except UnicodeError:
            raise CoremailError('INVALID_RESPONSE') from None
        if not isinstance(result, dict) or result.get('code') != 'S_OK':
            raise CoremailError('RPC_REJECTED')
        return result

    def get_message(self, mid: str) -> bytes:
        raw, unused_url = self._fetch(Request(self._rpc_url(
            'mbox:getMessageData', mid=mid, mode='text')))
        stripped = raw.lstrip()
        if not stripped or stripped.startswith((b'{', b'[', b'<')):
            raise CoremailError('MESSAGE_UNAVAILABLE')
        # A raw RFC 5322 message must at least contain a header section.
        first_line = stripped.splitlines()[0]
        if b':' not in first_line:
            raise CoremailError('MESSAGE_UNAVAILABLE')
        return raw

    def close(self) -> None:
        self.sid = None
        self._cookies.clear()
