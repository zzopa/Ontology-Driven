"""Explicit localhost preview of synthetic JSON only, independent of business databases."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.ops import api


def preview_app():
    api.STATIC_TEST_DATA = True
    user = {'id': 'ops-preview', 'display_name': '本地测试预览', 'roles': ['admin'], 'enabled': True}
    api.identity = lambda _request: {'user': user}
    app = FastAPI(docs_url=None, redoc_url=None)
    app.include_router(api.router)
    out = Path(__file__).resolve().parents[2] / 'frontend' / 'out'
    app.mount('/_next', StaticFiles(directory=out / '_next'))
    app.mount('/brand', StaticFiles(directory=out / 'brand'))

    @app.get('/')
    def original_service():
        return RedirectResponse('http://127.0.0.1:8088/')

    @app.get('/api/auth/status')
    def auth():
        return {'initialized': True, 'can_setup': False, 'allow_guest': False, 'user': user}

    @app.get('/api/status')
    def status():
        return {'ready': True, 'data_mode': 'static-test', 'demo_only': True}

    return app


if __name__ == '__main__':
    uvicorn.run(preview_app(), host='127.0.0.1', port=8098)
