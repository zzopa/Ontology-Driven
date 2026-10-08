"""HTTP model client; database planning remains independent of the provider."""
import json
import urllib.request
from urllib.parse import urlparse
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from app.config import settings

_provider = None
_request_model = ContextVar('request_model', default=None)


def set_provider(provider):
    global _provider
    _provider = provider


def current_model():
    return _request_model.get() or (_provider() if _provider else {
        'url': settings.llm_url, 'key': settings.llm_key, 'model': settings.llm_model})


def model_fingerprint():
    value = current_model()
    return sha256(json.dumps([value['url'], value['model'], value['key']]).encode()).hexdigest()[:20]


def model_available():
    value = current_model()
    return bool(value.get('key') or value['url'].startswith(('http://localhost', 'http://127.0.0.1', 'http://[::1]')))


@contextmanager
def model_context(value):
    token = _request_model.set(value)
    try:
        yield
    finally:
        _request_model.reset(token)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward API credentials to a redirect destination.
        return None


def open_request(request, timeout):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)

def chat(system, user, max_tokens=800, temperature=0.1, timeout=45, enable_thinking=None):
    """通用 LLM 对话，返回模型输出文本；失败抛异常由调用方处理。"""
    model = current_model()
    payload = {
        'model': model['model'],
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'temperature': temperature,
        'max_tokens': max_tokens,
    }
    # Verified SiliconFlow capability, applied only when a caller requests it.
    # Other providers and planning calls retain their existing request shape.
    model_name = model['model'].lower()
    switchable = ('deepseek-v4' in model_name or 'qwen3' in model_name) and 'thinking' not in model_name
    if (enable_thinking is not None and switchable and
            urlparse(model['url']).hostname in ('api.siliconflow.cn','api.siliconflow.com') and
            isinstance(enable_thinking,bool)):
        payload['enable_thinking'] = bool(enable_thinking)
    req = urllib.request.Request(
        model['url'], json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + model['key'], 'Content-Type': 'application/json'})
    with open_request(req, timeout) as r:
        data = json.loads(r.read().decode('utf-8'))
    return data['choices'][0]['message']['content']


def chat_stream(system, user, on_delta, max_tokens=800, temperature=0.1, timeout=45):
    """以 OpenAI 兼容 SSE 协议读取模型增量文本。"""
    model = current_model()
    payload = {
        'model': model['model'],
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': True,
    }
    req = urllib.request.Request(
        model['url'], json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + model['key'], 'Content-Type': 'application/json'})
    parts = []
    with open_request(req, timeout) as r:
        for raw in r:
            line = raw.decode('utf-8', errors='replace').strip()
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if not data or data == '[DONE]':
                continue
            event = json.loads(data)
            choices = event.get('choices') or []
            if not choices:
                continue
            delta = choices[0].get('delta') or {}
            content = delta.get('content') or ''
            if content:
                parts.append(content)
                on_delta(content)
    return ''.join(parts)
