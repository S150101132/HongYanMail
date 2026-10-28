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


def _failure(error):
    """The fixed code for a failure while connecting or reading."""
    if isinstance(error, HTTPError):
        code = _http_code(error)
        error.close()
        return code
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, TimeoutError):
        return 'AI_TIMEOUT'
    if isinstance(reason, ssl.SSLError):
        return 'TLS_ERROR'
    if isinstance(error, (URLError, OSError)):
        return 'NETWORK_ERROR'
    raise error  # A programming error, not the network: let it surface as INTERNAL_ERROR.


class ChatClient:
    def __init__(self, base_url, model, key, opener=None, timeout=60, retries=2, sleep=time.sleep):
        self.base_url = check_base_url(base_url)
        self.model = check_model(model)
        self._key = check_key(key)
        self.opener = opener or build_opener(_NoRedirect)
        self.timeout, self.retries, self.sleep = timeout, retries, sleep

    def chat(self, messages, max_tokens=800, json_mode=False):
        """-> (reply text, total tokens). Retries 429 and 5xx with backoff."""
        message, tokens = self.complete(messages, max_tokens=max_tokens, json_mode=json_mode)
        if not message['content'].strip():
            raise AppError('AI_BAD_RESPONSE')
        return message['content'], tokens

    def complete(self, messages, tools=None, max_tokens=800, json_mode=False):
        """-> ({content, tool_calls: [{id, name, arguments}]}, total tokens). tools: function
        definitions the model may call instead of answering."""
        body = {'model': self.model, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0.3, 'stream': False}
        if json_mode:
            body['response_format'] = {'type': 'json_object'}
        if tools:
            body['tools'] = tools
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        for attempt in range(self.retries + 1):
            try:
                return self._post(data)
            except AppError as error:
                if error.code not in RETRY_CODES or attempt == self.retries:
                    raise
            self.sleep(2 ** attempt)

    def stream(self, messages, on_delta, cancelled=lambda: False, max_tokens=800):
        """Streamed reply: each piece of text goes to on_delta as it arrives. -> (text, total
        tokens). Retries 429 and 5xx only before any text arrived; cancelled() is checked
        between pieces and ends the call with AI_CANCELLED."""
        body = {'model': self.model, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0.3,
                'stream': True, 'stream_options': {'include_usage': True}}
        data, parts = json.dumps(body, ensure_ascii=False).encode('utf-8'), []
        for attempt in range(self.retries + 1):
            try:
                return self._stream(data, on_delta, cancelled, parts)
            except AppError as error:
                if parts or error.code not in RETRY_CODES or attempt == self.retries:
                    raise
            self.sleep(2 ** attempt)

    def _stream(self, data, on_delta, cancelled, parts):
        if cancelled():
            raise AppError('AI_CANCELLED')
        tokens, size = 0, 0
        try:
            with self._open(data, 'text/event-stream') as response:
                while True:
                    line = response.readline(MAX_RESPONSE + 1)
                    if not line:
                        raise AppError('AI_UNAVAILABLE' if parts else 'AI_BAD_RESPONSE')  # Ended without [DONE].
                    size += len(line)
                    if size > MAX_RESPONSE:
                        raise AppError('AI_BAD_RESPONSE')
                    line = line.strip()
                    if not line.startswith(b'data:'):
                        continue  # Blank lines, ': keep-alive' comments, other fields.
                    payload = line[5:].strip()
                    if payload == b'[DONE]':
                        break
                    try:
                        value = json.loads(payload.decode('utf-8'))
                        choices = value.get('choices') or []
                        delta = (choices[0].get('delta') or {}).get('content') or '' if choices else ''
                        tokens = int((value.get('usage') or {}).get('total_tokens') or tokens)
                    except (ValueError, AttributeError, TypeError):
                        raise AppError('AI_BAD_RESPONSE') from None
                    if not isinstance(delta, str):
                        raise AppError('AI_BAD_RESPONSE')
                    if delta:
                        parts.append(delta)
                        on_delta(delta)
                    if cancelled():
                        raise AppError('AI_CANCELLED')
        except AppError:
            raise
        except Exception as error:
            raise AppError(_failure(error)) from None
        text = ''.join(parts)
        if not text.strip():
            raise AppError('AI_BAD_RESPONSE')
        return text, tokens

    def models(self):
        """The provider's model ids (GET /models, OpenAI-compatible), sorted; names that
        would not pass check_model are dropped."""
        request = Request(self.base_url + '/models', method='GET',
                          headers={'Accept': 'application/json', 'Authorization': 'Bearer ' + self._key})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except Exception as error:
            raise AppError(_failure(error)) from None
        try:
            items = json.loads(raw.decode('utf-8'))['data'] if len(raw) <= MAX_RESPONSE else None
            ids = {item['id'] for item in items if isinstance(item.get('id'), str) and MODEL_RE.match(item['id'])}
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AppError('AI_BAD_RESPONSE') from None
        return sorted(ids)[:300]

    def _open(self, data, accept):
        request = Request(self.base_url + '/chat/completions', data=data, method='POST',
                          headers={'Content-Type': 'application/json', 'Accept': accept,
                                   'Authorization': 'Bearer ' + self._key})
        try:
            return self.opener.open(request, timeout=self.timeout)
        except Exception as error:
            raise AppError(_failure(error)) from None

    def _post(self, data):
        try:
            with self._open(data, 'application/json') as response:
                raw = response.read(MAX_RESPONSE + 1)
        except AppError:
            raise
        except Exception as error:
            raise AppError(_failure(error)) from None
        if len(raw) > MAX_RESPONSE:
            raise AppError('AI_BAD_RESPONSE')
        try:
            value = json.loads(raw.decode('utf-8'))
            message = value['choices'][0]['message']
            content = message.get('content') or ''
            calls = [{'id': c['id'], 'name': c['function']['name'], 'arguments': c['function']['arguments'] or '{}'}
                     for c in message.get('tool_calls') or ()]
            tokens = int((value.get('usage') or {}).get('total_tokens') or 0)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AppError('AI_BAD_RESPONSE') from None
        if not isinstance(content, str) or not all(isinstance(c[k], str) and c[k] for c in calls for k in c):
            raise AppError('AI_BAD_RESPONSE')
        if not content.strip() and not calls:
            raise AppError('AI_BAD_RESPONSE')
        return {'content': content, 'tool_calls': calls}, tokens
