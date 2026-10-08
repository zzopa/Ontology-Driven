"""Isolated, loopback-only UI verification. Never seeds production credentials.

Run python -m scripts.demo.admin_preview, then open http://127.0.0.1:9089/admin.
Login uses the existing hbairport01 account and roles; no demo account is created.
All management state, ontology edits and snapshots live in a TemporaryDirectory.
Business metadata reads remain read-only; no external model calls are needed.
"""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from app import core
from app import business_access
from app.admin import service
from app.admin import auth
from app.admin import api
from app.admin.store import AdminStore
from app.config import settings
from app.management import OntologyStore
from app.metadata import save_snapshot


def main():
    # Cookies are scoped by host, not port: never overwrite the 8088 credential.
    business_access.COOKIE = auth.COOKIE = api.COOKIE = 'hbask_preview_session'
    with TemporaryDirectory(prefix='hbask-admin-ui-') as directory:
        root = Path(directory)
        test_settings = replace(settings, admin_directory=root / 'admin',
                                ontology_path=root / 'ontology.json',
                                schema_path=root / 'runtime/schema.json',
                                graph_path=root / 'runtime/graph.json', memory_path=root / 'memory.json')
        save_snapshot(test_settings.ontology_path, json.loads(settings.ontology_path.read_text(encoding='utf-8')))
        core.settings = test_settings
        core.STORE = OntologyStore(test_settings.ontology_path, test_settings)
        store = AdminStore(root / 'admin', {'url': 'https://example.invalid/v1/chat/completions',
                           'model': 'ui-verification-only', 'key': '', 'retention_days': 90})
        service.get_store = lambda: store
        uvicorn.run('app.main:app', host='127.0.0.1', port=9089)


if __name__ == '__main__':
    main()
