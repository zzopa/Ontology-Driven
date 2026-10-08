"""Management regression: temporary SQLite only, no real keys or business writes."""
import asyncio
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
from time import time
from fastapi import HTTPException
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.admin import glossary, service
from app.admin.security import digest
from app.admin.store import AdminStore, model_url
from app import core, llm
from app.evidence import sanitize
from app.main import app
from fixtures import fixture_catalog

PASSWORD = 'test-only-long-password-123!'
INITIAL = {'url': 'https://example.invalid/v1/chat/completions', 'model': 'test-model',
           'key': 'test-secret-never-exposed', 'retention_days': 90}


async def http(path, method='GET', data=None, cookie='', client='127.0.0.1', origin='http://127.0.0.1:8088', marker=True):
    events, supplied = [], False
    path, _, query = path.partition('?')
    payload = json.dumps(data).encode() if data is not None else b''
    headers = [(b'host', b'127.0.0.1:8088'), (b'content-type', b'application/json')]
    if cookie:
        headers.append((b'cookie', cookie.encode()))
    if marker:
        headers.append((b'x-hbask-request', b'1'))
    if origin:
        headers.append((b'origin', origin.encode()))

    async def receive():
        nonlocal supplied
        if not supplied:
            supplied = True
            return {'type': 'http.request', 'body': payload, 'more_body': False}
        await asyncio.Event().wait()

    async def send(event):
        events.append(event)

    await asyncio.wait_for(app({
        'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
        'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
        'query_string': query.encode(), 'root_path': '', 'headers': headers,
        'client': (client, 50000), 'server': ('127.0.0.1', 8088),
    }, receive, send), timeout=20)
    start = next(event for event in events if event['type'] == 'http.response.start')
    body = b''.join(event.get('body', b'') for event in events if event['type'] == 'http.response.body')
    values = {key.decode(): value.decode() for key, value in start['headers']}
    value = [json.loads(line) for line in body.splitlines()] if 'application/x-ndjson' in values.get('content-type', '') else json.loads(body)
    return start['status'], values, value


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AdminStore(self.temp.name, INITIAL)

    def test_fresh_store_has_no_local_accounts_or_passwords(self):
        with self.store.db() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'users', 'credentials'}.isdisjoint(tables))
            self.assertEqual([row['name'] for row in db.execute('PRAGMA table_info(business_credentials)')],
                             ['token_hash', 'user_id', 'expires'])
        self.assertNotIn('allow_guest', self.store.preferences())

    def test_unified_user_migration_backs_up_and_preserves_encrypted_history(self):
        cid, mid = self.store.begin_question('user:legacy-id', '旧问题', 'test')
        self.store.complete_question(mid, {'answer': '保留的旧答案'})
        with self.store.db() as db:
            db.execute('CREATE TABLE users (id TEXT PRIMARY KEY, password_hash TEXT)')
            db.execute('INSERT INTO users VALUES (?,?)', ('legacy-id', 'legacy-hash'))
            db.execute('CREATE TABLE credentials (token_hash TEXT, user_id TEXT REFERENCES users(id))')
            db.execute("INSERT INTO credentials VALUES ('old-token','legacy-id')")
            db.execute("INSERT INTO preferences VALUES ('allow_guest','true')")
        migrated = AdminStore(self.temp.name, INITIAL)
        self.assertEqual(migrated.conversation(cid, 'user:legacy-id')['messages'][0]['result']['answer'], '保留的旧答案')
        self.assertEqual(migrated.model()['key'], INITIAL['key'])
        with migrated.db() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone())
        backups = list(Path(self.temp.name).glob('management.before-unified-users-*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
        AdminStore(self.temp.name, INITIAL)
        self.assertEqual(len(list(Path(self.temp.name).glob('management.before-unified-users-*.sqlite3'))), 1)

    def test_expired_business_sessions_are_cleaned_on_restart(self):
        with self.store.db() as db:
            db.execute('INSERT INTO business_credentials VALUES (?,?,?)', ('old-hash','original-user-id',0))
        reopened = AdminStore(self.temp.name, INITIAL)
        with reopened.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM business_credentials').fetchone()[0], 0)

    def test_secrets_encrypted_and_masked_after_restart(self):
        self.assertNotIn('secret', self.store.models()[0])
        self.assertNotIn('key', self.store.models()[0])
        with self.store.db() as db:
            secret = db.execute('SELECT secret FROM models').fetchone()[0]
        self.assertNotIn(INITIAL['key'], secret)
        self.assertEqual(AdminStore(self.temp.name, INITIAL).model()['key'], INITIAL['key'])

    def test_missing_vault_key_never_silently_generates_replacement(self):
        key = Path(self.temp.name) / 'secret.key'
        key.unlink()
        with self.assertRaisesRegex(ValueError, '加密密钥缺失'):
            AdminStore(self.temp.name, INITIAL)
        self.assertFalse(key.exists())

    def test_model_blank_key_preserves_and_explicit_clear_removes(self):
        model = self.store.models()[0]
        self.store.save_model({**model, 'enabled': True, 'api_key': ''}, 'admin', model['id'])
        self.assertEqual(self.store.model()['key'], INITIAL['key'])
        self.store.save_model({**model, 'enabled': True, 'clear_key': True}, 'admin', model['id'])
        self.assertEqual(self.store.model()['key'], '')

    def test_active_model_cannot_be_deleted_or_disabled(self):
        with self.assertRaises(ValueError):
            self.store.delete_model('default', 'admin')
        with self.assertRaises(ValueError):
            self.store.save_model({**self.store.models()[0], 'enabled': False}, 'admin', 'default')
        model = self.store.save_model({**INITIAL, 'name': 'Second', 'model': 'second', 'api_key': 'other'}, 'admin')
        self.store.activate_model(model['id'], 'admin')
        self.assertEqual(self.store.model()['model'], 'second')
        self.store.delete_model('default', 'admin')

    def test_model_url_rejects_secret_or_invalid_transport(self):
        for url in ('file:///private', 'https://u:p@example.com/v1', 'https://example.com/v1?key=secret'):
            with self.assertRaises(ValueError):
                model_url(url)
        self.assertEqual(model_url('http://127.0.0.1:11434/v1/chat/completions'), 'http://127.0.0.1:11434/v1/chat/completions')
        self.assertEqual(model_url('http://192.168.1.10:11434/v1/chat/completions'), 'http://192.168.1.10:11434/v1/chat/completions')

    def test_model_context_is_stable_and_changes_plan_cache_version(self):
        catalog = fixture_catalog()
        first = {'url': INITIAL['url'], 'model': 'first', 'key': 'a'}
        second = {**first, 'model': 'second'}
        with llm.model_context(first):
            original = core.cache_key(catalog)
            with llm.model_context(second):
                self.assertNotEqual(original, core.cache_key(catalog))
            self.assertEqual(llm.current_model()['model'], 'first')

    def test_conversations_are_owned_and_snapshots_do_not_store_tokens(self):
        cid, mid = self.store.begin_question('user:first', '测试问题', 'test')
        self.store.complete_question(mid, {'ok': True, 'answer': '数据库事实', 'drill_token': 'opaque-page-token'})
        with self.assertRaises(ValueError):
            self.store.conversation(cid, 'user:second')
        self.assertEqual(self.store.conversations('user:second')['total'], 0)
        saved = self.store.conversation(cid, 'user:first')['messages'][0]
        self.assertEqual(saved['question'], '测试问题')
        self.assertNotIn('drill_token', saved['result'])
        self.assertTrue(self.store.check_token('opaque-page-token', 'user:first'))
        self.assertFalse(self.store.check_token('opaque-page-token', 'user:second'))
        with self.store.db() as db:
            row = db.execute('SELECT question,result FROM messages').fetchone()
            self.assertNotIn('测试问题', row['question'])
            self.assertNotIn('数据库事实', row['result'])
        self.assertEqual(self.store.conversation(cid, 'user:second', True)['messages'][0]['question'], '测试问题')

    def test_archive_restore_rename_delete(self):
        cid, _ = self.store.begin_question('guest:a', 'question', 'test')
        self.store.edit_conversation(cid, 'guest:a', {'title': 'New title', 'archived': True})
        self.assertEqual(self.store.conversations('guest:a')['total'], 0)
        self.assertEqual(self.store.conversations('guest:a', archived=True)['total'], 1)
        with self.assertRaises(ValueError):
            self.store.begin_question('guest:a', 'next', 'test', cid)
        self.store.edit_conversation(cid, 'guest:a', {'archived': False})
        self.store.delete_conversation(cid, 'guest:a')
        self.assertEqual(self.store.overview()['messages'], 0)
        self.assertEqual(self.store.audit_list()['items'][0]['action'], 'conversation.delete')

    def test_failed_execution_trace_is_encrypted_and_recoverable(self):
        cid, mid = self.store.begin_question('guest:a', 'question', 'test')
        trace = [{'id': 'model', 'state': 'failed', 'message': '主体校验失败', 'details': []}]
        self.store.complete_question(mid, error='主体校验失败', execution_trace=trace)
        saved = self.store.conversation(cid, 'guest:a')['messages'][0]
        self.assertEqual(saved['status'], 'failed')
        self.assertIsNone(saved['result'])
        self.assertEqual(saved['execution_trace'], trace)
        with self.store.db() as db:
            self.assertNotIn('主体校验失败', db.execute('SELECT execution_trace FROM messages').fetchone()[0])

    def test_trace_migration_preserves_legacy_history(self):
        cid, mid = self.store.begin_question('guest:a', 'legacy question', 'test')
        self.store.complete_question(mid, {'ok': True, 'answer': 'legacy answer'})
        with self.store.db() as db:
            db.execute('ALTER TABLE messages DROP COLUMN execution_trace')
        reopened = AdminStore(self.temp.name, INITIAL)
        saved = reopened.conversation(cid, 'guest:a')['messages'][0]
        self.assertEqual(saved['result']['answer'], 'legacy answer')
        self.assertEqual(saved['execution_trace'], [])
        reopened.begin_question('guest:a', 'new question', 'test')

    def test_retention_does_not_delete_running_conversations(self):
        cid, mid = self.store.begin_question('guest:a', 'test', 'test')
        with self.store.db() as db:
            db.execute("UPDATE conversations SET updated_at='2000-01-01'")
        self.assertEqual(self.store.purge('admin'), 0)
        self.store.complete_question(mid, error='test failure')
        with self.store.db() as db:
            db.execute("UPDATE conversations SET updated_at='2000-01-01'")
        self.assertEqual(self.store.purge('admin'), 1)

    def test_glossary_edits_are_authoritative_and_validated(self):
        catalog = fixture_catalog()
        doc = deepcopy(catalog.overrides)
        alias = {'kind': 'company_alias', 'key': '测试公司简称', 'value': 'CODE1', 'table': ''}
        with self.assertRaises(ValueError):
            glossary.apply(doc, catalog, alias, enterprise_codes={'OTHER'})
        edited = glossary.apply(doc, catalog, alias, enterprise_codes={'CODE1'})
        self.assertEqual(edited['enterprise_aliases']['测试公司简称'], 'CODE1')
        term = next(item for item in glossary.entries(edited, catalog) if item['key'] == '测试公司简称')
        reverted = glossary.apply(edited, catalog, {}, term['id'], delete=True)
        self.assertNotIn('测试公司简称', reverted['enterprise_aliases'])
        with self.assertRaises(ValueError):
            glossary.apply(doc, catalog, {'kind': 'field', 'table': 'comp_employee', 'key': 'missing', 'value': '字段'})

    def test_json_text_sensitive_fields_are_also_filtered(self):
        result = sanitize({'payload': '{"联系电话":"secret-number","普通字段":1}'}, set())
        self.assertNotIn('secret-number', result['payload'])
        self.assertEqual(json.loads(result['payload']), {'普通字段': 1})


class AdminApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from app import business_access as business
        self.business = business
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AdminStore(self.temp.name, INITIAL)
        self.accounts = {}
        def lookup(conn, user_id):
            account = self.accounts.get(str(user_id))
            if account is None:
                raise HTTPException(401, '业务账号已停用')
            return account
        def login(username, password, ip):
            if password != PASSWORD:
                raise HTTPException(401, '账号或密码错误')
            account = next(a for a in self.accounts.values() if a.username == username)
            token = 'session-for-' + username
            with self.store.db() as db:
                db.execute('INSERT INTO business_credentials VALUES (?,?,?)', (digest(token), account.user_id, time()+3600))
            return token, account.profile()
        for mock in (patch.object(service, 'get_store', return_value=self.store),
                     patch.object(core, 'connect_db', return_value=SimpleNamespace(close=lambda: None)),
                     patch.object(business, 'load_access', side_effect=lookup),
                     patch.object(business, 'login', side_effect=login)):
            mock.start(); self.addCleanup(mock.stop)

    async def login(self, role='admin'):
        account = self.business.Access('id-'+role, role, '原系统用户', 'DEPT_ONLY', (role,), None, None,
                                       frozenset({'scc-a'}), 'current-revision', role_names=('原系统角色',))
        self.accounts[account.user_id] = account
        status, headers, _ = await http('/api/auth/login', 'POST', {'username': role, 'password': PASSWORD})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['set-cookie'])
        self.assertIn('SameSite=lax', headers['set-cookie'])
        return headers['set-cookie'].split(';')[0]

    async def test_one_auth_source_and_no_local_user_management_routes(self):
        for path in ('/api/auth/status','/api/business-auth/status'):
            status, _, value = await http(path)
            self.assertEqual(status, 200)
            self.assertEqual(value['source'], 'hbairport01')
            self.assertTrue(value['initialized'])
            self.assertFalse(value['can_setup'])
            self.assertFalse(value['allow_guest'])
        for path, method in (('/api/auth/setup','POST'),('/api/auth/password','POST'),
                             ('/api/admin/users','GET'),('/api/admin/users','POST'),
                             ('/api/admin/users/123','PUT'),('/api/admin/users/123','DELETE')):
            self.assertEqual((await http(path, method, {}))[0], 404)

    async def test_shared_identity_in_admin_query_history_and_alias_status(self):
        cookie = await self.login()
        for path in ('/api/auth/status','/api/business-auth/status'):
            _, _, value = await http(path, cookie=cookie)
            self.assertEqual(value['user']['id'], 'id-admin')
            self.assertTrue(value['user']['can_manage'])
            self.assertEqual(value['user']['roles'], ['admin'])
            self.assertEqual(value['user']['data_scope'], 'ALL')
            self.assertEqual(value['user']['configured_data_scope'], 'DEPT_ONLY')
            self.assertNotIn('role', value['user'])
        self.assertEqual((await http('/api/admin/models', cookie=cookie))[0], 200)
        account = self.accounts['id-admin']
        cid, mid = self.store.begin_question(account.owner, 'query', 'test')
        self.store.complete_question(mid, {'access_revision':account.revision,'answer':'same owner'})
        for path in ('/api/conversations/','/api/business-conversations/'):
            status, _, value = await http(path+cid, cookie=cookie)
            self.assertEqual(status,200)
            self.assertEqual(value['messages'][0]['result']['answer'], 'same owner')

    async def test_cross_site_and_missing_marker_writes_are_rejected(self):
        data = {'username':'admin','password':PASSWORD}
        self.assertEqual((await http('/api/auth/login','POST',data,origin='https://evil.invalid'))[0],403)
        self.assertEqual((await http('/api/auth/login','POST',data,origin='',marker=False))[0],403)

    async def test_admin_auth_required_even_on_loopback(self):
        for path, method in (('/api/admin/models','GET'),('/api/metadata/refresh','POST'),('/api/ontology','GET')):
            self.assertEqual((await http(path,method,{}))[0],401)

    async def test_original_admin_role_and_owner_only_history(self):
        admin_cookie = await self.login()
        ordinary_cookie = await self.login('jtgly')
        self.assertEqual((await http('/api/admin/models', cookie=ordinary_cookie))[0],403)
        self.assertEqual((await http('/api/admin/glossary','POST',{},cookie=ordinary_cookie))[0],403)
        for cookie in (ordinary_cookie,admin_cookie):
            self.assertEqual((await http('/api/conversations?scope=all',cookie=cookie))[0],403)
        cid, _ = self.store.begin_question(self.accounts['id-admin'].owner,'private query','test')
        self.assertEqual((await http('/api/conversations/'+cid,cookie=ordinary_cookie))[0],400)
        self.assertEqual((await http('/api/conversations/'+cid,cookie=admin_cookie))[0],200)

    async def test_live_role_change_and_disabled_user_revoke_management(self):
        from dataclasses import replace
        cookie = await self.login()
        self.accounts['id-admin'] = replace(self.accounts['id-admin'], roles=('jtgly',), revision='changed')
        self.assertEqual((await http('/api/admin/models',cookie=cookie))[0],403)
        profile = (await http('/api/auth/status', cookie=cookie))[2]['user']
        self.assertEqual(profile['data_scope'], 'DEPT_ONLY')
        self.assertFalse(profile['can_manage'])
        self.accounts.pop('id-admin')
        self.assertEqual((await http('/api/conversations',cookie=cookie))[0],401)

    async def test_ontology_edit_permission_uses_original_admin_role(self):
        for role in ('admin','jtgly'):
            cookie = await self.login(role)
            with patch.object(core,'ontology_document',return_value={'document':{},'revision':'current'}):
                _,_,value = await http('/api/ontology',cookie=cookie)
            self.assertEqual(value['can_edit'], role=='admin')

    async def test_models_and_probe_never_expose_secrets(self):
        cookie = await self.login()
        _, _, value = await http('/api/admin/models',cookie=cookie)
        self.assertNotIn(INITIAL['key'], json.dumps(value))
        self.assertNotIn('secret', value['items'][0])
        with patch.object(llm,'chat',return_value=INITIAL['key']):
            _, _, value = await http('/api/admin/models/default/test','POST',{},cookie=cookie)
        self.assertTrue(value['connected'])
        self.assertNotIn(INITIAL['key'],json.dumps(value))

    async def test_settings_cannot_enable_guest_and_query_requires_login(self):
        cookie = await self.login()
        await http('/api/admin/settings','PUT',{'allow_guest':True,'retention_days':30},cookie=cookie)
        self.assertNotIn('allow_guest',self.store.preferences())
        self.assertEqual((await http('/api/ask','POST',{'question':'test'}))[0],401)
        self.assertEqual((await http('/api/records','POST',{'token':'t','page':1}))[0],401)

    async def test_query_is_saved_and_paging_is_owner_bound(self):
        cookie = await self.login()
        result = {'ok':True,'answer':'test','drill_token':'token-for-owner','access_revision':'current-revision'}
        with patch.object(core,'do_ask',return_value=result):
            status,_,value = await http('/api/ask','POST',{'question':'query'},cookie=cookie)
        self.assertEqual(status,200)
        self.assertEqual((await http('/api/conversations/'+value['conversation_id'],cookie=cookie))[0],200)
        other = await self.login('jtgly')
        self.assertEqual((await http('/api/records','POST',{'token':'token-for-owner','page':1},cookie=other))[0],403)
        with patch.object(core,'fetch_drill_page',return_value={'ok':True,'rows':[]}):
            self.assertEqual((await http('/api/records','POST',{'token':'token-for-owner','page':1},cookie=cookie))[0],200)

    async def test_stream_delivers_and_saves_authorized_result(self):
        cookie = await self.login()
        def query(conn, question, on_event=None, trace=None, access=None):
            on_event({'type':'progress','stage':'sql','percent':55})
            on_event({'type':'answer_delta','delta':'测试回答'})
            return {'ok':True,'answer':'测试回答','access_revision':access.revision}
        with patch.object(core,'do_ask',side_effect=query):
            status,_,events = await http('/api/ask/stream','POST',{'question':'stream'},cookie=cookie)
        self.assertEqual(status,200)
        self.assertEqual(events[-1]['type'],'result')
        self.assertIn('answer_delta',[e['type'] for e in events])
        _,_,saved = await http('/api/conversations/'+events[-1]['data']['conversation_id'],cookie=cookie)
        self.assertEqual(saved['messages'][0]['status'],'completed')
        self.assertEqual(saved['messages'][0]['result']['answer'],'测试回答')

    async def test_composed_document_and_original_summary_survive_history_storage(self):
        cookie=await self.login()
        doc={'version':'evidence-answer-v1','mode':'deterministic','claim_count':1,
             'query_claim_count':1,'sections':[{'kind':'query_facts','title':'查询事实','claim_ids':['C1']}]}
        result={'ok':True,'answer':'保留准确回答','query_summary':'保留准确回答',
                'claims':[{'text':'保留准确回答','fact_ids':['F1']}],
                'answer_document':doc,'access_revision':'current-revision'}
        with patch.object(core,'do_ask',return_value=result):
            _,_,value=await http('/api/ask','POST',{'question':'通用分析'},cookie=cookie)
        _,_,saved=await http('/api/conversations/'+value['conversation_id'],cookie=cookie)
        snapshot=saved['messages'][0]['result']
        self.assertEqual(snapshot['answer_document'],doc)
        self.assertEqual(snapshot['query_summary'],'保留准确回答')

    async def test_nonadmin_can_read_glossary_but_cannot_modify(self):
        cookie = await self.login('jtgly')
        catalog = fixture_catalog()
        with patch.object(core,'ontology_document',return_value={'document':catalog.overrides,'revision':'current'}), \
             patch.object(core,'current_catalog',return_value=catalog):
            self.assertEqual((await http('/api/admin/glossary',cookie=cookie))[0],200)
        self.assertEqual((await http('/api/admin/glossary','POST',{},cookie=cookie))[0],403)

    async def test_failed_stream_records_failure_without_unversioned_trace(self):
        cookie = await self.login()
        with patch.object(core,'do_ask',side_effect=ValueError('筛选条件不完整')):
            _,_,events = await http('/api/ask/stream','POST',{'question':'failure'},cookie=cookie)
        self.assertEqual(events[-1]['type'],'error')
        _,_,saved = await http('/api/conversations/'+events[0]['conversation_id'],cookie=cookie)
        self.assertEqual(saved['messages'][0]['status'],'failed')
        self.assertIsNone(saved['messages'][0]['execution_trace'])

    async def test_logout_in_either_endpoint_revokes_shared_credential(self):
        for logout in ('/api/auth/logout','/api/business-auth/logout'):
            cookie = await self.login()
            self.assertEqual((await http(logout,'POST',{},cookie=cookie))[0],200)
            self.assertEqual((await http('/api/admin/models',cookie=cookie))[0],401)
            self.assertIsNone((await http('/api/business-auth/status',cookie=cookie))[2]['user'])


if __name__ == '__main__':
    unittest.main()
