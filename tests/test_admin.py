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

    def test_sync_runs_lifecycle_concurrency_and_reconciliation(self):
        run = self.store.begin_sync_run('full_pipeline', '一键组合同步', 'ui', 'INCREMENTAL', 'admin-1', '管理员')
        self.assertEqual(run['status'], 'running')
        with self.assertRaisesRegex(ValueError, '已有同步任务正在执行'):
            self.store.begin_sync_run('flights_fims', 'FIMS 航班动态', 'ui', 'INCREMENTAL', 'admin-1', '管理员')

        done = self.store.complete_sync_run(
            run['id'], status='completed', elapsed_ms=4200, fetched=3459, inserted=3453, updated=6,
            skipped=0, failed=0, watermark_before='2026-11-08T00:00:00+00:00',
            watermark_after='2026-11-09T01:30:26+00:00', detail={'steps': [{'step': 'flights_fims', 'fetched': 3459}]},
            message='ok:oracle-fims', backup_file='ops_ask-before-test.dump',
        )
        self.assertEqual(done['fetched'], 3459)
        self.assertEqual(done['backup_file'], 'ops_ask-before-test.dump')

        # Overlapping external run within parent window should NOT duplicate UI run.
        added = self.store.reconcile_external_sync_runs([
            {
                'external_key': 'flight_dyn_sync_fims:' + done['finished_at'],
                'job_key': 'flights_fims',
                'title': 'FIMS 航班动态',
                'mode': 'INCREMENTAL',
                'started_at': done['started_at'],
                'finished_at': done['finished_at'],
                'elapsed_ms': 1000,
                'fetched': 3459,
                'inserted': 3453,
                'updated': 6,
                'skipped': 0,
                'failed': 0,
            },
            {
                'external_key': 'flight_dyn_sync_acdm:2026-11-09T01:34:24+00:00',
                'job_key': 'flights_acdm',
                'title': 'ACDM 航班动态',
                'mode': 'INCREMENTAL',
                'started_at': '2026-11-09T01:32:43+00:00',
                'finished_at': '2026-11-09T01:34:24+00:00',
                'elapsed_ms': 101307,
                'fetched': 3591,
                'inserted': 132,
                'updated': 3459,
                'skipped': 0,
                'failed': 0,
                'message': 'ok:oracle-acdm',
            },
        ])
        self.assertEqual(added, 1)
        # Re-reconciling same key is idempotent.
        self.assertEqual(self.store.reconcile_external_sync_runs([{
            'external_key': 'flight_dyn_sync_acdm:2026-11-09T01:34:24+00:00',
            'job_key': 'flights_acdm', 'title': 'ACDM', 'finished_at': '2026-11-09T01:34:24+00:00',
        }]), 0)

        listed = self.store.sync_runs()
        self.assertEqual(listed['total'], 2)
        self.assertEqual(listed['summary']['total_fetched'], 3459 + 3591)

        # Simulated restart marks any unfinished running job as interrupted.
        orphan = self.store.begin_sync_run('master_data', '基础资料全量', 'ui', 'FULL', 'admin-1', '管理员')
        reopened = AdminStore(self.temp.name, INITIAL)
        self.assertEqual(reopened.sync_run(orphan['id'])['status'], 'interrupted')
        self.assertIsNone(reopened.active_sync_run())


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
        for path, method in (('/api/auth/setup','POST'),('/api/auth/password','POST')):
            self.assertEqual((await http(path, method, {}))[0], 404)
        for path, method in (('/api/admin/users','GET'),('/api/admin/users','POST'),
                             ('/api/admin/users/123','PUT'),('/api/admin/users/123','DELETE')):
            self.assertEqual((await http(path, method, {}))[0], 401)

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
        self.assertIn((await http('/api/conversations/'+cid,cookie=ordinary_cookie))[0],(400, 403))
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

    async def test_sync_routes_enforce_admin_role_and_same_origin(self):
        self.assertEqual((await http('/api/admin/sync/overview'))[0], 401)
        self.assertEqual((await http('/api/admin/sync/runs'))[0], 401)
        self.assertEqual((await http('/api/admin/sync/run', 'POST', {}))[0], 401)
        self.assertEqual((await http('/api/admin/sync/cancel', 'POST', {}))[0], 401)
        self.assertEqual((await http('/api/admin/sync/probe-source', 'POST', {}))[0], 401)

        ordinary_cookie = await self.login('jtgly')
        for path, method in (
            ('/api/admin/sync/overview', 'GET'),
            ('/api/admin/sync/runs', 'GET'),
            ('/api/admin/sync/run', 'POST'),
            ('/api/admin/sync/backup', 'POST'),
            ('/api/admin/sync/cancel', 'POST'),
            ('/api/admin/sync/probe-source', 'POST'),
        ):
            self.assertEqual((await http(path, method, {}, cookie=ordinary_cookie))[0], 403)

        admin_cookie = await self.login()
        self.assertEqual((await http('/api/admin/sync/run', 'POST', {}, cookie=admin_cookie, origin='https://evil.invalid'))[0], 403)
        self.assertEqual((await http('/api/admin/sync/run', 'POST', {}, cookie=admin_cookie, origin='', marker=False))[0], 403)

    async def test_sync_overview_reconciles_pg_state_and_run_pipeline_records_counts(self):
        from datetime import date, datetime
        from unittest.mock import MagicMock
        import json
        from app.admin import data_sync
        from app.sync import oracle_source, pg_sink, runner

        parsed = data_sync.parse_sync_result(
            '{airline={upserted=248}, airport={positionLoaded=3685, upserted=3822}, elapsedMs=89894}',
            'master_data_sync',
        )
        self.assertEqual(parsed['fetched'], 248 + 3822)
        self.assertEqual(parsed['elapsed_ms'], 89894)

        secret_pw = 'SuperSecretOraPwd!'
        fake_ora_cfg = {
            'host': '172.26.78.133', 'port': 1521, 'service_name': 'datacache',
            'user': 'sync_reader', 'password': secret_pw, 'airport_code': 'WUH',
            'acdm_schema': 'ACDM', 'acdm_table': 'TB_DYN_FLIGHT',
            'fims_schema': 'FIMS', 'fims_table': 'TB_DYN_FLIGHT',
            'fims_share_table': 'TB_DYN_SHARE_FLIGHTS',
        }
        admin_cookie = await self.login()
        fake_pg = {
            'available': True,
            'database': 'ops_ask',
            'job_states': [{
                'job_name': 'flight_dyn_sync_fims',
                'job_key': 'flights_fims',
                'title': 'FIMS 航班动态',
                'watermark': '2026-11-09T01:30:26.698000+00:00',
                'last_success_at': '2026-11-09T01:32:43.171155+00:00',
                'started_at': '2026-11-09T01:30:45.849155+00:00',
                'parsed_result': {'mode': 'INCREMENTAL', 'fetched': 3459, 'inserted': 3453, 'updated': 6, 'skipped': 0, 'failed': 0, 'elapsed_ms': 117322, 'message': 'ok:oracle-fims'},
            }],
            'tables': {'std_flight_core': {'count': 3864, 'min_date': '2026-09-21 10:25', 'max_date': '2026-11-10 08:00'}},
            'dimensions': {'dim_airline': 247},
        }
        with patch.object(data_sync, 'oracle_config', return_value=fake_ora_cfg), \
             patch.object(data_sync, '_tcp_probe', return_value={'reachable': True, 'host': '172.26.78.133', 'port': 1521, 'latency_ms': 2.1}), \
             patch.object(data_sync, 'read_pg_snapshot', return_value=fake_pg), \
             patch.object(data_sync, 'list_backups', return_value=[]):
            # Even if /runs finishes before /overview on first load, ensure_reconciled populates it immediately.
            runs_first_status, _, runs_first = await http('/api/admin/sync/runs', cookie=admin_cookie)
            self.assertEqual(runs_first_status, 200)
            self.assertEqual(runs_first['total'], 1)
            self.assertEqual(runs_first['summary']['total_fetched'], 3459)

            status_code, _, overview = await http('/api/admin/sync/overview', cookie=admin_cookie)
            settings_status, _, admin_settings = await http('/api/admin/settings', cookie=admin_cookie)
        self.assertEqual(status_code, 200)
        self.assertEqual(settings_status, 200)
        self.assertEqual(overview['engine'], {'mode': 'python-native', 'ready': True, 'airport_code': 'WUH'})
        self.assertEqual(overview['summary']['total_runs'], 1)
        self.assertEqual(overview['summary']['total_fetched'], 3459)
        self.assertEqual(overview['reconciled_added'], 0)
        self.assertNotIn('password', overview['oracle'])
        self.assertNotIn('user', overview['oracle'])
        self.assertNotIn(secret_pw, json.dumps(overview, ensure_ascii=False))
        self.assertNotIn(secret_pw, json.dumps(admin_settings, ensure_ascii=False))

        # Validate explicit Oracle source probe caching without password exposure.
        fake_probe = {
            'latency_ms': 42.3,
            'tables': {'fims_flight': {'count': 1526685, 'min_date': '2018-08-03', 'max_date': '2026-11-11'}},
        }
        with patch.object(data_sync, 'oracle_config', return_value=fake_ora_cfg), \
             patch.object(oracle_source, 'probe_oracle_source', return_value=fake_probe):
            probe_status, _, probe_body = await http('/api/admin/sync/probe-source', 'POST', {}, cookie=admin_cookie)
        self.assertEqual(probe_status, 200)
        self.assertEqual(probe_body['source_stats']['tables']['fims_flight']['count'], 1526685)
        self.assertNotIn(secret_pw, json.dumps(probe_body, ensure_ascii=False))

        # Validate invalid date range rejection.
        status_bad, _, err_bad = await http(
            '/api/admin/sync/run', 'POST',
            {'job_type': 'production', 'mode': 'BY_EXEC_DATE', 'exec_date_from': '2026-11-10', 'exec_date_to': '2026-11-08'},
            cookie=admin_cookie,
        )
        self.assertEqual(status_bad, 400)
        self.assertIn('不能晚于', err_bad['error'])

        # Execute default full_sync against mocked Python runners and verify all 7 steps run in recent-1-month FULL mode + prune.
        seen_calls = []
        pruned_dates = []
        fake_ora_conn = MagicMock()
        fake_pg_conn = MagicMock()
        expected_from, _ = data_sync._full_date_bounds()
        self.assertEqual((date_now := (datetime.now(data_sync.SHANGHAI_TZ).date() - expected_from)).days, 30)

        def fake_prune(pg_conn, keep_from):
            pruned_dates.append(keep_from)
            return {'std_flight_core': 12}

        def fake_master(pg_conn, oracle_conn, ora_cfg, *, on_progress=None, cancel_event=None):
            seen_calls.append(('master_data', 'FULL', None))
            return {
                'mode': 'FULL', 'fetched': 248 + 3822, 'inserted': 0, 'updated': 248 + 3822,
                'skipped': 0, 'failed': 0, 'elapsed_ms': 400,
                'watermark_before': '', 'watermark_after': '2026-11-10T08:00:00+00:00',
                'dimensions': {'airline': {'upserted': 248}, 'airport': {'upserted': 3822}},
                'message': 'ok:master',
            }

        def fake_flights(pg_conn, oracle_conn, ora_cfg, sync_cfg, source_type, *,
                         mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                         updated_since=None, on_progress=None, cancel_event=None):
            seen_calls.append((source_type, mode, exec_date_from))
            self.assertEqual(mode, 'FULL')
            self.assertEqual(exec_date_from, expected_from)
            if 'fims' in source_type:
                return {'mode': mode, 'fetched': 120, 'inserted': 100, 'updated': 20, 'skipped': 0, 'failed': 0, 'elapsed_ms': 500, 'message': 'ok:oracle-fims'}
            return {'mode': mode, 'fetched': 130, 'inserted': 10, 'updated': 120, 'skipped': 0, 'failed': 0, 'elapsed_ms': 600, 'message': 'ok:oracle-acdm'}

        def fake_codeshares(pg_conn, oracle_conn, ora_cfg, sync_cfg, *,
                            mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                            on_progress=None, cancel_event=None):
            seen_calls.append(('codeshares', mode, exec_date_from))
            self.assertEqual(mode, 'FULL')
            self.assertEqual(exec_date_from, expected_from)
            return {'mode': mode, 'fetched': 60, 'inserted': 60, 'updated': 0, 'skipped': 0, 'failed': 0, 'elapsed_ms': 200, 'message': 'ok:codeshare'}

        def fake_production(pg_conn, oracle_conn, ora_cfg, sync_cfg, *,
                            mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                            updated_since=None, on_progress=None, cancel_event=None):
            seen_calls.append(('production', mode, exec_date_from))
            self.assertEqual(mode, 'FULL')
            self.assertEqual(exec_date_from, expected_from)
            return {'mode': mode, 'fetched': 80, 'inserted': 80, 'updated': 0, 'skipped': 0, 'failed': 0, 'elapsed_ms': 300, 'message': 'production ok'}

        def fake_calc_total(pg_conn, oracle_conn, ora_cfg, sync_cfg, *,
                            mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                            updated_since=None, on_progress=None, cancel_event=None):
            seen_calls.append(('calc_total', mode, exec_date_from))
            self.assertEqual(mode, 'FULL')
            self.assertEqual(exec_date_from, expected_from)
            return {'mode': mode, 'fetched': 50, 'inserted': 50, 'updated': 0, 'skipped': 0, 'failed': 0, 'elapsed_ms': 150, 'message': 'calc_total ok'}

        def fake_safeguards(pg_conn, oracle_conn, ora_cfg, sync_cfg, *,
                            mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                            updated_since=None, on_progress=None, cancel_event=None):
            seen_calls.append(('safeguards', mode, exec_date_from))
            self.assertEqual(mode, 'FULL')
            self.assertEqual(exec_date_from, expected_from)
            return {'mode': mode, 'fetched': 70, 'inserted': 70, 'updated': 0, 'skipped': 0, 'failed': 0, 'elapsed_ms': 250, 'message': 'safeguard ok'}

        with patch.object(data_sync, 'oracle_config', return_value=fake_ora_cfg), \
             patch.object(oracle_source, 'connect_oracle', return_value=fake_ora_conn), \
             patch.object(pg_sink, 'connect_pg_write', return_value=fake_pg_conn), \
             patch.object(pg_sink, 'ensure_acdm_schema_extensions'), \
             patch.object(pg_sink, 'prune_stale_window', side_effect=fake_prune), \
             patch.object(runner, 'run_master_data_sync', side_effect=fake_master), \
             patch.object(runner, 'run_flight_sync', side_effect=fake_flights), \
             patch.object(runner, 'run_codeshare_sync', side_effect=fake_codeshares), \
             patch.object(runner, 'run_production_sync', side_effect=fake_production), \
             patch.object(runner, 'run_calc_total_sync', side_effect=fake_calc_total), \
             patch.object(runner, 'run_safeguard_sync', side_effect=fake_safeguards):
            status_run, _, created = await http(
                '/api/admin/sync/run', 'POST',
                {'with_backup': False},
                cookie=admin_cookie,
            )
            self.assertEqual(status_run, 200)
            self.assertEqual(created['run']['job_key'], 'full_sync')
            self.assertEqual(created['run']['mode'], 'FULL')
            run_id = created['run']['id']
            for _ in range(40):
                item = self.store.sync_run(run_id)
                if item['status'] != 'running':
                    break
                await asyncio.sleep(0.05)

        final_run = self.store.sync_run(run_id)
        self.assertEqual(final_run['status'], 'completed')
        self.assertEqual(final_run['fetched'], 248 + 3822 + 120 + 130 + 60 + 80 + 50 + 70)
        self.assertEqual(final_run['inserted'], 100 + 10 + 60 + 80 + 50 + 70)
        self.assertEqual(final_run['updated'], 248 + 3822 + 20 + 120)
        self.assertEqual(len(final_run['detail']['steps']), 7)
        self.assertEqual(pruned_dates, [expected_from])
        self.assertEqual([c[0] for c in seen_calls], ['master_data', 'oracle-fims', 'oracle-acdm', 'codeshares', 'production', 'calc_total', 'safeguards'])
        self.assertTrue(fake_ora_conn.close.called)
        self.assertTrue(fake_pg_conn.close.called)
        status_runs, _, runs_data = await http('/api/admin/sync/runs', cookie=admin_cookie)
        self.assertEqual(status_runs, 200)
        self.assertEqual(runs_data['total'], 2)
        self.assertEqual(self.store.audit_list()['items'][0]['action'], 'data_sync.run')

        # Verify online cancellation transitions a running job cleanly to interrupted.
        def blocking_master(pg_conn, oracle_conn, ora_cfg, *, on_progress=None, cancel_event=None):
            cancel_event.wait(2.0)
            raise oracle_source.SyncCancelledError('同步任务已被管理员中止')

        with patch.object(data_sync, 'oracle_config', return_value=fake_ora_cfg), \
             patch.object(oracle_source, 'connect_oracle', return_value=MagicMock()), \
             patch.object(pg_sink, 'connect_pg_write', return_value=MagicMock()), \
             patch.object(runner, 'run_master_data_sync', side_effect=blocking_master):
            c_status, _, c_created = await http(
                '/api/admin/sync/run', 'POST',
                {'job_type': 'master_data', 'with_backup': False},
                cookie=admin_cookie,
            )
            self.assertEqual(c_status, 200)
            cancel_run_id = c_created['run']['id']
            cancel_status, _, cancel_body = await http('/api/admin/sync/cancel', 'POST', {}, cookie=admin_cookie)
            self.assertEqual(cancel_status, 200)
            self.assertTrue(cancel_body['cancelled'])
            self.assertEqual(cancel_body['run_id'], cancel_run_id)
            for _ in range(40):
                item = self.store.sync_run(cancel_run_id)
                if item['status'] != 'running' and cancel_run_id not in data_sync._CANCEL_EVENTS:
                    break
                await asyncio.sleep(0.05)

        self.assertEqual(self.store.sync_run(cancel_run_id)['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
