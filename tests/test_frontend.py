"""Frontend contract checks; no browser or model calls required."""
import unittest
import asyncio
import json

from app.main import app
from app.config import settings, ROOT


async def get(path):
    """Minimal ASGI GET; no lifespan, DB connection, or optional HTTP test dependency."""
    events = []
    supplied = False

    async def receive():
        nonlocal supplied
        if not supplied:
            supplied = True
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        await asyncio.Event().wait()

    async def send(event):
        events.append(event)

    await asyncio.wait_for(app({
        'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
        'http_version': '1.1', 'method': 'GET', 'scheme': 'http', 'path': path,
        'raw_path': path.encode(), 'query_string': b'', 'root_path': '',
        'headers': [(b'host', b'127.0.0.1:8088')],
        'client': ('127.0.0.1', 50001), 'server': ('127.0.0.1', 8088),
    }, receive, send), timeout=5)
    start = next(event for event in events if event['type'] == 'http.response.start')
    body = b''.join(event.get('body', b'') for event in events if event['type'] == 'http.response.body')
    return start['status'], {k.decode(): v.decode() for k, v in start['headers']}, body


class FrontendContractTests(unittest.IsolatedAsyncioTestCase):

    def test_frontend_export_is_the_configured_entry(self):
        self.assertEqual(settings.page_path, ROOT / 'frontend/out/index.html')
        for page in ('/', '/ontology', '/embed'):
            self.assertIn(page, {getattr(route, 'path', None) for route in app.routes})

    async def test_ui_config_exposes_only_embedding_allowlist(self):
        status, _, body = await get('/api/ui-config')
        self.assertEqual(status, 200)
        self.assertEqual(set(json.loads(body)), {'embed_parent_origins'})
        self.assertNotIn(b'password', body)
        self.assertNotIn(b'api_key', body)

    async def test_iframe_origins_are_not_wildcarded(self):
        _, headers, _ = await get('/api/ui-config')
        policy = headers['content-security-policy']
        self.assertTrue(policy.startswith("frame-ancestors 'self'"))
        self.assertNotIn('*', policy)
        self.assertEqual(headers['x-content-type-options'], 'nosniff')

    def test_no_unescaped_model_html(self):
        for path in (ROOT / 'frontend/components').rglob('*.tsx'):
            self.assertNotIn('dangerouslySetInnerHTML', path.read_text(encoding='utf-8'), path)


if __name__ == '__main__':
    unittest.main()
