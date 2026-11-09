"""Reuse the user's existing V3 model in the isolated ACDM management store."""
import json
import sqlite3
import urllib.error
import urllib.request

from app.config import ROOT, settings
from app.admin.security import Vault
from app.admin.service import get_store
from app.llm import open_request


def configure():
    if settings.data_source != 'acdm':
        raise ValueError('Switch to ACDM before configuring its isolated model store')
    original = ROOT / 'data/admin'
    conn = sqlite3.connect((original/'management.sqlite3').as_uri()+'?mode=ro',uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute('SELECT * FROM models WHERE model=? AND enabled=1',('DeepSeek-V3.1',)).fetchone()
        if row is None:
            raise ValueError('Existing V3 model not found')
        key = Vault(original).open(row['secret'])
        model = row['model']
    finally:
        conn.close()
    base = 'http://172.26.197.154:3000/v1'
    request = urllib.request.Request(base+'/models',headers={'Authorization':'Bearer '+key})
    try:
        with open_request(request,15) as response:
            models = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise ValueError('Model service authentication failed, HTTP '+str(exc.code)) from None
    names = [entry['id'] for entry in models.get('data',[])]
    if model not in names:
        raise ValueError('Configured V3 not offered by service. Available: '+', '.join(names))
    store = get_store()
    current = next((item for item in store.models() if item['model']==model and item['url']==base+'/chat/completions'),None)
    saved = store.save_model({'name':'本地 V3','url':base+'/chat/completions','model':model,'api_key':key},
                             'local-setup',current['id'] if current else None)
    store.activate_model(saved['id'],'local-setup')
    print('Active model: '+model+' at '+base)


if __name__ == '__main__':
    configure()
