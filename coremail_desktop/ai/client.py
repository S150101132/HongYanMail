"""OpenAI-compatible chat completions client (DeepSeek by default).

Only fixed AppError codes leave this module: response bodies and server error text
are never echoed. HTTPS only, no redirects (a redirect could carry the key elsewhere).
"""
import json
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from coremail_desktop.errors import AppError

DEFAULT_BASE_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-chat'
MAX_RESPONSE = 2 * 1024 * 1024
RETRY_CODES = ('AI_RATE_LIMITED', 'AI_UNAVAILABLE')  # 429 and 5xx; 401/402 never retry.
MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}$')
KEY_RE = re.compile(r'^[\x21-\x7e]{8,512}$')


def check_base_url(value):
    value = (value or '').strip().rstrip('/')
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.query or parts.fragment or '@' in parts.netloc:
        raise AppError('INVALID_AI_SETTINGS')
    return value


def check_model(value):
    value = (value or '').strip()
    if not MODEL_RE.match(value):
        raise AppError('INVALID_AI_SETTINGS')
    return value


def check_key(value):
    value = (value or '').strip()
    if not KEY_RE.match(value):
        raise AppError('INVALID_AI_SETTINGS')
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # The 3xx surfaces as an HTTPError instead of being followed.


def _http_code(error):
    status = error.code
    if status in (401, 403):
        return 'AI_AUTH_FAILED'
    if status == 402:
        return 'AI_QUOTA'
    if status == 429:
        return 'AI_RATE_LIMITED'
    if status >= 500:
        return 'AI_UNAVAILABLE'
    if status in (400, 413, 422):
        try:
            text = error.read(65536).decode('utf-8', 'replace').casefold()
        except Exception:
            text = ''
        # Looked at locally to pick the code; never shown.
        return 'AI_CONTEXT_TOO_LONG' if status == 413 or 'context' in text or 'too long' in text else 'AI_BAD_RESPONSE'
    return 'AI_NOT_FOUND'  # 3xx, 404, 405: the address or model is not an API endpoint.


class ChatClient:
    def __init__(self, base_url, model, key, opener=None, timeout=60, retries=2, sleep=time.sleep):
        self.base_url = check_base_url(base_url)
        self.model = check_model(model)
        self._key = check_key(key)
        self.opener = opener or build_opener(_NoRedirect)
        self.timeout, self.retries, self.sleep = timeout, retries, sleep

    def chat(self, messages, max_tokens=800, json_mode=False):
        """-> (reply text, total tokens). Retries 429 and 5xx with backoff."""
        body = {'model': self.model, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0.3, 'stream': False}
        if json_mode:
            body['response_format'] = {'type': 'json_object'}
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        for attempt in range(self.retries + 1):
            try:
                return self._post(data)
            except AppError as error:
                if error.code not in RETRY_CODES or attempt == self.retries:
                    raise
            self.sleep(2 ** attempt)

    def _post(self, data):
        request = Request(self.base_url + '/chat/completions', data=data, method='POST',
                          headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                   'Authorization': 'Bearer ' + self._key})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as error:
            code = _http_code(error)
            error.close()
            raise AppError(code) from None
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise AppError('AI_TIMEOUT') from None
            raise AppError('TLS_ERROR' if isinstance(error.reason, ssl.SSLError) else 'NETWORK_ERROR') from None
        except TimeoutError:
            raise AppError('AI_TIMEOUT') from None
        except ssl.SSLError:
            raise AppError('TLS_ERROR') from None
        except OSError:
            raise AppError('NETWORK_ERROR') from None
        if len(raw) > MAX_RESPONSE:
            raise AppError('AI_BAD_RESPONSE')
        try:
            value = json.loads(raw.decode('utf-8'))
            content = value['choices'][0]['message']['content']
            tokens = int((value.get('usage') or {}).get('total_tokens') or 0)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AppError('AI_BAD_RESPONSE') from None
        if not isinstance(content, str) or not content.strip():
            raise AppError('AI_BAD_RESPONSE')
        return content, tokens
