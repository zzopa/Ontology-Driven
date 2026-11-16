"""Isolated operational-ledger and cross-flight dispatch acceptance cases."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from urllib.parse import urlsplit

from app.ops import api
from app.ops.domain import Conflict, KINDS, check_candidate, dt, evaluate_alerts, iso, now, operation
from app.ops.service import OpsService
from app.ops.store import OpsStore


def flight(ident, number, stand, terminal='T2'):
    return {'id': str(ident), 'flight_no': number, 'flight_date': '2026-11-11', 'direction': 'DEP', 'terminal': terminal,
            'stand': stand, 'gate': '', 'aircraft_reg': 'B-TEST', 'cancelled': False, 'updated_at': '2026-11-11T10:31:00+08:00',
            'aibt': None, 'sobt': '2026-11-11T11:20:00+08:00', 'tobt': '2026-11-11T11:20:00+08:00', 'tsat': None,
            'target_at': '2026-11-11T11:20:00+08:00', 'operations': [operation(k, {}, dt('2026-11-11T10:32:00+08:00')) for k in KINDS]}


class Source:
    def __init__(self):
        self.flights = [flight(1, 'CZ3103', '201'), flight(2, 'HU7134', '209'), flight(3, 'CA1258', '205')]
        self.fresh = True

    def read(self, terminal, observed=None, minutes=90, ids=None, all_day=False, details=False, replay=False):
        return {'flights': deepcopy([f for f in self.flights if f['terminal'] == terminal and (ids is None or f['id'] in ids)]),
                'stands': [{'code': f['stand'], 'airport': 'WUH', 'name': f['stand']} for f in self.flights],
                'source': {'fresh': self.fresh, 'jobs': {}, 'checked_at': iso(observed), 'message': '测试快照' if self.fresh else '源端采集延迟'}}

    def flight(self, terminal, ident, observed=None, replay=False):
        result = self.read(terminal, observed, ids=[str(ident)])
        if not result['flights']:
            raise ValueError('当前授权范围没有该航班')
        return result['flights'][0], result['source']

    def linked(self, terminal, ident, observed=None, replay=False):
        return []


class Client:
    """Same minimal ASGI transport as existing frontend tests; no new dependency."""
    def __init__(self, app, default_mode='live'):
        self.app, self.default_mode = app, default_mode

    async def request(self, method, path, **kwargs):
        uri = urlsplit(path)
        query = uri.query
        if self.default_mode and uri.path.startswith('/api/ops') and 'mode=' not in query:
            query += ('&' if query else '') + 'mode=' + self.default_mode
        payload = json.dumps(kwargs['json']).encode() if 'json' in kwargs else str(kwargs.get('content', '')).encode()
        headers = {'host': 'test', 'x-hbask-request': '1', 'content-type': 'application/json', **{k.lower(): v for k, v in kwargs.get('headers', {}).items()}}
        events, supplied = [], False
        async def receive():
            nonlocal supplied
            if not supplied:
                supplied = True
                return {'type': 'http.request', 'body': payload, 'more_body': False}
            await asyncio.Event().wait()
        async def send(event):
            events.append(event)
        await asyncio.wait_for(self.app({'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                                        'method': method, 'scheme': 'http', 'path': uri.path, 'raw_path': uri.path.encode(),
                                        'query_string': query.encode(), 'root_path': '', 'http_version': '1.1',
                                        'headers': [(k.encode(), v.encode()) for k, v in headers.items()],
                                        'client': ('127.0.0.1', 1234), 'server': ('test', 80)}, receive, send), 10)
        status = next(e['status'] for e in events if e['type'] == 'http.response.start')
        raw = b''.join(e.get('body', b'') for e in events if e['type'] == 'http.response.body')
        return SimpleNamespace(status_code=status, text=raw.decode(), json=lambda: json.loads(raw))

    async def get(self, path, **kwargs):
        return await self.request('GET', path, **kwargs)

    async def post(self, path, **kwargs):
        return await self.request('POST', path, **kwargs)


class OpsFixture:
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = dt('2026-11-11T10:32:00+08:00')
        for module in ('app.ops.domain.now', 'app.ops.store.now', 'app.ops.service.now'):
            obj = patch(module, side_effect=lambda: self.clock)
            obj.start(); self.addCleanup(obj.stop)
        self.store = OpsStore(Path(self.tmp.name) / 'ops.sqlite3')
        self.source = Source()
        self.service = OpsService(self.store, self.source)
        self.a = self.resource('A班组', '209')
        self.b = self.resource('B班组', '203')
        for r in (self.a, self.b):
            self.store.roster({'resource_id': r['id'], 'start_at': '2026-11-11T09:00:00+08:00', 'end_at': '2026-11-12T09:00:00+08:00'}, 'T2', 'admin')
        for origin, destination, minutes in [('203', '201', 2), ('201', '205', 4)]:
            self.store.transfer({'from_stand': origin, 'to_stand': destination, 'minutes': minutes}, 'T2', 'admin')
        self.store.policy({'kind': 'cleaning', 'duration_minutes': 20, 'grace_minutes': 0, 'confirmed': True}, 'T2', 'admin')
        self.target = self.task(self.a, self.source.flights[0], '10:30', '10:50')
        self.hu = self.task(self.a, self.source.flights[1], '10:30', '10:50')
        self.ca = self.task(self.b, self.source.flights[2], '11:00', '11:20')
        self.store.set_state('settings:T2', {'shadow_mode': True, 'dispatch_enabled': True})

    def resource(self, name, position, kind='cleaning'):
        return self.store.resource({'name': name, 'kind': kind, 'position': position, 'availability': 'idle', 'qualified_until': '2026-12-01T00:00:00+08:00',
                                    'enabled': True, 'qualifications_confirmed': True, 'equipment_confirmed': True, 'confirm_position': True}, 'T2', 'admin')

    def task(self, r, f, start, end):
        return self.store.task({'resource_id': r['id'], 'start_at': f'2026-11-11T{start}:00+08:00', 'end_at': f'2026-11-11T{end}:00+08:00', 'latest_end_at': iso(dt(f'2026-11-11T{end}:00+08:00') + timedelta(minutes=10))}, f, 'T2', 'admin')

    def preview(self):
        return self.service.preview({'task_id': self.target, 'start_at': '2026-11-11T10:34:00+08:00'}, 'T2', 'dispatcher')

    def approve(self, proposal=None, key='test-approval-key'):
        p = proposal or self.preview()['proposal']
        return self.service.approve(p['id'], p['version'], key, 'T2', 'dispatcher')


class OpsTests(OpsFixture, unittest.TestCase):

    def test_cross_flight_preview_checks_the_next_task_and_preserves_source(self):
        original = deepcopy(self.source.flights)
        p = self.preview()['proposal']
        self.assertEqual(p['resource']['id'], self.b['id'])
        self.assertEqual((p['check']['transfer_before'], p['check']['transfer_after'], p['check']['slack_minutes']), (2, 4, 2))
        self.assertEqual({i['flight_no'] for i in p['impacts']}, {'HU7134', 'CA1258'})
        self.assertEqual(p['tobt'], '2026-11-11T11:20:00+08:00')
        self.assertEqual(self.source.flights, original)
        self.assertEqual(self.store.view('T2')['work_orders'], [])

    def test_next_task_is_checked_even_outside_visible_window(self):
        with self.store.db() as db:
            db.execute('UPDATE tasks SET start_at=?,end_at=? WHERE id=?', ('2026-11-12T00:00:00+08:00', '2026-11-12T00:20:00+08:00', self.ca))
        p = self.preview()['proposal']
        self.assertIn('CA1258', [i['flight_no'] for i in p['impacts']])

    def test_missing_transfer_and_insufficient_slack_block_recommendation(self):
        with self.store.db() as db:
            db.execute("DELETE FROM transfers WHERE from_stand='201'")
        result = self.preview()
        self.assertFalse(result['eligible'])
        self.assertTrue(any('后续' in error for c in result['candidates'] for error in c['errors']))
        self.store.transfer({'from_stand': '201', 'to_stand': '205', 'minutes': 7}, 'T2', 'admin')
        self.assertFalse(self.preview()['eligible'])

    def test_stale_resource_or_no_shift_or_qualification_blocks(self):
        self.clock += timedelta(minutes=11)
        result = self.service.preview({'task_id': self.target, 'start_at': iso(self.clock + timedelta(minutes=3))}, 'T2', 'dispatcher')
        self.assertFalse(result['eligible'])
        self.clock -= timedelta(minutes=11)
        with self.store.db() as db:
            db.execute('DELETE FROM rosters WHERE resource_id=?', (self.b['id'],))
        self.assertFalse(self.preview()['eligible'])

    def test_partial_data_is_not_zero_capacity(self):
        self.assertIsNone(self.service.snapshot('T2')['metrics']['resource_gaps'])

    def test_persisted_alerts_deduplicate_acknowledge_and_resolve(self):
        one = self.service.snapshot('T2', persist=True)
        self.service.snapshot('T2', persist=True)
        stored = self.store.alerts('T2')
        self.assertEqual(len(stored), len(one['alerts']))
        overlap = next(a for a in stored if a['severity'] == 'high')
        self.store.acknowledge(overlap['id'], 'T2', 'viewer')
        self.assertEqual(next(a for a in self.service.snapshot('T2', persist=True)['alerts'] if a['id'] == overlap['id'])['status'], 'acknowledged')
        self.approve()
        self.service.snapshot('T2', persist=True)
        self.assertEqual(next(a for a in self.store.alerts('T2') if a['id'] == overlap['id'])['status'], 'resolved')

    def test_stale_source_does_not_resolve_existing_incidents(self):
        self.service.snapshot('T2', persist=True)
        original = {a['id'] for a in self.store.alerts('T2')}
        self.source.fresh = False
        self.source.flights.clear()
        self.service.snapshot('T2', persist=True)
        self.assertTrue(original <= {a['id'] for a in self.store.alerts('T2') if a['status'] != 'resolved'})

    def test_approval_is_idempotent_and_stale_approval_is_rejected(self):
        p = self.preview()['proposal']
        first = self.approve(p)
        self.source.fresh = False
        self.assertEqual(first['id'], self.approve(p)['id'])
        with self.assertRaises(Conflict):
            self.approve(p, 'different-key')
        self.assertEqual(len(self.store.view('T2')['work_orders']), 1)

    def test_resource_or_affected_flight_change_invalidates_proposal(self):
        p = self.preview()['proposal']
        self.source.flights[2]['stand'] = '207'
        with self.assertRaises(Conflict):
            self.approve(p)
        self.source.flights[2]['stand'] = '205'
        self.store.resource({'revision': self.b['revision'], 'confirm_position': True}, 'T2', 'crew', self.b['id'])
        with self.assertRaises(Conflict):
            self.approve(p)

    def test_source_cancel_or_started_operation_rejects_preview(self):
        self.source.flights[0]['cancelled'] = True
        with self.assertRaises(Conflict):
            self.preview()
        self.source.flights[0]['cancelled'] = False
        self.source.flights[0]['operations'][0]['actual_start'] = '2026-11-11T10:31:00+08:00'
        with self.assertRaises(Conflict):
            self.preview()

    def test_expired_proposal_and_disabled_dispatch_are_rejected(self):
        p = self.preview()['proposal']
        self.clock += timedelta(minutes=6)
        with self.assertRaises(Conflict):
            self.approve(p)
        self.clock -= timedelta(minutes=6)
        self.store.set_state('settings:T2', {'shadow_mode': True, 'dispatch_enabled': False})
        with self.assertRaises(Conflict):
            self.approve(p)

    def test_two_simultaneous_approvals_cannot_double_book(self):
        proposals = [self.preview()['proposal'], self.preview()['proposal']]
        def attempt(p):
            try:
                return self.approve(p)['id']
            except Conflict:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt, proposals))
        self.assertEqual(sum(x is not None for x in result), 1)
        self.assertEqual(len(self.store.view('T2')['work_orders']), 1)

    def test_full_work_order_lifecycle_scope_revisions_and_manual_verification(self):
        order = self.approve()
        with self.assertRaises(PermissionError):
            self.service.transition(order['id'], 'accept', 1, 'T2', 'wrong-crew', {self.a['id']})
        order = self.service.transition(order['id'], 'accept', 1, 'T2', 'crew', {self.b['id']})
        with self.assertRaises(Conflict):
            self.service.transition(order['id'], 'start', 1, 'T2', 'crew', {self.b['id']})
        self.clock = dt('2026-11-11T10:34:00+08:00')
        order = self.service.transition(order['id'], 'start', order['revision'], 'T2', 'crew', {self.b['id']})
        self.clock = dt('2026-11-11T10:54:00+08:00')
        order = self.service.transition(order['id'], 'finish', order['revision'], 'T2', 'crew', {self.b['id']})
        self.assertEqual(order['state'], 'review')
        with self.assertRaises(ValueError):
            self.service.transition(order['id'], 'verify', order['revision'], 'T2', 'dispatcher')
        order = self.service.transition(order['id'], 'verify', order['revision'], 'T2', 'dispatcher', note='现场核验，源实绩尚未回传')
        self.assertEqual((order['state'], order['verification_source']), ('complete', '人工核验'))
        restored = OpsStore(self.store.path)
        self.assertEqual(restored.view('T2')['work_orders'][0]['state'], 'complete')
        self.assertIsNone(self.source.flights[0]['operations'][0]['actual_end'])

    def test_future_and_reversed_manual_times_are_rejected(self):
        order = self.approve()
        order = self.service.transition(order['id'], 'accept', 1, 'T2', 'crew')
        with self.assertRaises(ValueError):
            self.service.transition(order['id'], 'start', order['revision'], 'T2', 'crew', actual_at='2026-11-11T11:00:00+08:00')
        order = self.service.transition(order['id'], 'start', order['revision'], 'T2', 'crew')
        with self.assertRaises(ValueError):
            self.service.transition(order['id'], 'finish', order['revision'], 'T2', 'crew', actual_at='2026-11-11T10:00:00+08:00')

    def test_existing_task_manual_reports_do_not_mutate_source(self):
        self.service.report_task(self.hu, 'start', 1, 'T2', 'crew', {self.a['id']})
        self.service.report_task(self.hu, 'finish', 2, 'T2', 'crew', {self.a['id']})
        self.service.report_task(self.hu, 'verify', 3, 'T2', 'dispatcher', note='现场确认')
        task = next(t for t in self.store.view('T2')['tasks'] if t['id'] == self.hu)
        self.assertEqual(task['status'], 'complete')
        self.assertIsNone(self.source.flights[1]['operations'][0]['actual_end'])

    def test_typed_fields_and_future_actuals_are_preserved_without_fake_completion(self):
        op = operation('cleaning', {'real_start_clean_time': '2026-11-11T10:00:00+08:00', 'real_end_clean_time': '2026-11-11T10:20:00+08:00'}, self.clock)
        self.assertEqual(op['status'], 'completed')
        op = operation('fueling', {'real_end_supply_time': '2026-11-11T23:00:00+08:00'}, self.clock)
        self.assertEqual(op['status'], 'risk')
        self.assertTrue(op['invalid_time'])
        self.assertEqual(operation('catering', {}, self.clock)['status'], 'missing')

    def test_assist_only_uses_authorized_snapshot_facts(self):
        value = self.service.snapshot('T2')
        result = self.service.assist(value, '当前有哪些风险？')
        self.assertEqual(result['snapshot_id'], value['snapshot_id'])
        self.assertEqual(result['mode'], 'deterministic')
        self.assertIn('班组任务重叠', result['answer'])

    def test_missing_extension_object_is_flagged_instead_of_crashing(self):
        for value in ('broken-json', ['wrong-shape']):
            op = operation('cleaning', {'ext_json': value}, self.clock)
            self.assertEqual(op['status'], 'risk')
            self.assertTrue(op['invalid_extension'])

    def test_unknown_busy_state_and_overdue_order_block_reassignment(self):
        current = self.store.resource({'revision': self.b['revision'], 'availability': 'unknown', 'confirm_position': True}, 'T2', 'crew', self.b['id'])
        self.assertFalse(self.preview()['eligible'])
        self.store.resource({'revision': current['revision'], 'availability': 'idle', 'confirm_position': True}, 'T2', 'crew', self.b['id'])
        self.assertTrue(self.preview()['eligible'])

    def test_unconfirmed_flight_window_blocks_dispatch_even_with_idle_team(self):
        with self.store.db() as db:
            row = db.execute('SELECT payload FROM tasks WHERE id=?', (self.target,)).fetchone()
            payload = json.loads(row['payload'])
            payload['latest_end_at'] = None
            db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(payload), self.target))
        result = self.preview()
        self.assertFalse(result['eligible'])
        self.assertTrue(any('窗口' in e for c in result['candidates'] for e in c['errors']))

    def test_a_single_shared_resource_incident_groups_three_flights(self):
        tasks = deepcopy(self.store.view('T2')['tasks'])
        tasks[2].update(resource_id=self.a['id'], start_at='2026-11-11T10:35:00+08:00', end_at='2026-11-11T10:55:00+08:00')
        alerts = evaluate_alerts([], tasks, {}, 'T2', self.clock)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(set(alerts[0]['flight_ids']), {'1', '2', '3'})

    def test_window_exit_does_not_prove_source_incident_recovery(self):
        self.service.snapshot('T2', persist=True)
        original = [a['id'] for a in self.store.alerts('T2') if a['title'] == '开始记录待核查']
        read = self.source.read
        def restricted(terminal, observed=None, minutes=90, ids=None, **kwargs):
            result = read(terminal, observed, minutes, ids=ids, **kwargs)
            if ids is None:
                result['flights'] = []
            return result
        self.source.read = restricted
        self.clock += timedelta(hours=3)
        self.service.snapshot('T2', persist=True)
        active = {a['id'] for a in self.store.alerts('T2') if a['status'] != 'resolved'}
        self.assertTrue(set(original) <= active)


class OpsApiTests(OpsFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        mode_patch = patch.object(api, 'STATIC_TEST_DATA', False)
        mode_patch.start(); self.addCleanup(mode_patch.stop)
        self.actor = {'id': 'admin', 'display_name': '管理员', 'roles': ['admin']}
        self.service_patch = patch.object(api, 'get_service', return_value=self.service)
        self.service_patch.start(); self.addCleanup(self.service_patch.stop)
        def identify(_request):
            if self.actor is None:
                raise HTTPException(401, '请先登录')
            return {'user': self.actor}
        self.identity_patch = patch.object(api, 'identity', side_effect=identify)
        self.identity_patch.start(); self.addCleanup(self.identity_patch.stop)
        self.app = FastAPI(); self.app.include_router(api.router)
        self.client = Client(self.app)

    async def test_auth_terminal_and_crew_visibility(self):
        self.actor = None
        self.assertEqual((await self.client.get('/api/ops/snapshot')).status_code, 401)
        self.store.grant({'user_id': 'viewer', 'role': 'viewer', 'terminal': 'T2'}, 'admin')
        self.actor = {'id': 'viewer', 'display_name': '值守员', 'roles': []}
        self.assertEqual((await self.client.get('/api/ops/snapshot?terminal=T2')).status_code, 200)
        self.assertEqual((await self.client.get('/api/ops/snapshot?terminal=T3')).status_code, 403)
        self.assertEqual((await self.client.post('/api/ops/proposals', json={})).status_code, 403)
        self.actor = {'id': 'admin', 'display_name': '管理员', 'roles': ['admin']}
        self.approve()
        self.store.grant({'user_id': 'crew', 'role': 'crew', 'terminal': 'T2', 'resource_id': self.a['id']}, 'admin')
        self.actor = {'id': 'crew', 'display_name': 'A班组长', 'roles': []}
        response = await self.client.get('/api/ops/work-orders')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['orders'], [])
        self.assertEqual({r['id'] for r in response.json()['resources']}, {self.a['id']})
        self.assertEqual((await self.client.get('/api/ops/snapshot')).status_code, 403)

    async def test_crew_confirmation_cannot_escalate_qualifications(self):
        self.store.grant({'user_id': 'crew', 'role': 'crew', 'terminal': 'T2', 'resource_id': self.a['id']}, 'admin')
        self.actor = {'id': 'crew', 'display_name': '班组长', 'roles': []}
        response = await self.client.post(f"/api/ops/resources/{self.a['id']}/confirm", json={'position': '201', 'revision': 1, 'qualifications_confirmed': False})
        self.assertEqual(response.status_code, 400)
        response = await self.client.post(f"/api/ops/resources/{self.b['id']}/confirm", json={'position': '201', 'revision': 1})
        self.assertEqual(response.status_code, 403)

    async def test_cross_origin_write_and_oversized_body_are_rejected(self):
        response = await self.client.post('/api/ops/resources', json={}, headers={'Origin': 'https://attacker.example'})
        self.assertEqual(response.status_code, 403)
        response = await self.client.post('/api/ops/resources', content='x' * 65537)
        self.assertEqual(response.status_code, 413)

    async def test_all_static_routes_and_unknown_route(self):
        folder = Path(self.tmp.name) / 'out'
        (folder / 'ops').mkdir(parents=True)
        (folder / 'ops.html').write_text('overview', encoding='utf-8')
        for name in ('flights', 'dispatch', 'work-orders', 'resources'):
            (folder / 'ops' / (name + '.html')).write_text(name, encoding='utf-8')
        with patch.object(api, 'settings', SimpleNamespace(page_path=folder / 'index.html')):
            for route in ('/ops', '/ops/flights', '/ops/dispatch', '/ops/work-orders', '/ops/resources'):
                self.assertEqual((await self.client.get(route)).status_code, 200, route)
            self.assertEqual((await self.client.get('/ops/unknown')).status_code, 404)

    async def test_single_flight_is_scoped_and_returns_real_available_nodes(self):
        response = await self.client.get('/api/ops/flights/1?terminal=T2')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['flight']['flight_no'], 'CZ3103')
        self.assertEqual(len(response.json()['turnaround']['operations']), 5)
        self.assertEqual((await self.client.get('/api/ops/flights/1?terminal=T3')).status_code, 400)

    async def test_role_grants_require_existing_accounts_and_admin(self):
        with patch.object(api, 'existing_users', return_value=[]):
            response = await self.client.post('/api/ops/grants', json={'user_id': 'missing', 'terminal': 'T2', 'role': 'viewer'})
            self.assertEqual(response.status_code, 400)
        with patch.object(api, 'existing_users', return_value=[{'id': 'valid', 'username': 'test', 'display_name': '测试值守员'}]):
            response = await self.client.post('/api/ops/grants', json={'user_id': 'valid', 'terminal': 'T2', 'role': 'viewer'})
            self.assertEqual(response.status_code, 200)
        self.actor = {'id': 'valid', 'display_name': '值守员', 'roles': []}
        self.assertEqual((await self.client.get('/api/ops/users?q=test')).status_code, 403)


class OpsReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = dt('2026-11-11T10:32:00+08:00')
        self.real_time = self.clock + timedelta(days=1)
        for module in ('app.ops.domain.now', 'app.ops.store.now', 'app.ops.service.now', 'app.ops.api.now'):
            obj = patch(module, side_effect=lambda: self.real_time)
            obj.start(); self.addCleanup(obj.stop)
        self.store = OpsStore(Path(self.tmp.name) / 'replay.sqlite3', clock=lambda: self.clock, simulation=True)
        self.source = Source()
        self.source.fresh = False
        for f in self.source.flights:
            f['target_at'] = f['tobt'] = f['sobt'] = iso(self.clock + timedelta(minutes=90))
        self.service = OpsService(self.store, self.source, mode='replay', clock=lambda: self.clock)

    def test_default_factory_uses_the_confirmed_data_date_and_an_isolated_ledger(self):
        api.service_for_mode.cache_clear()
        self.addCleanup(api.service_for_mode.cache_clear)
        with patch.dict('os.environ', HBASK_OPS_LEDGER=str(Path(self.tmp.name) / 'ops.sqlite3')):
            live, replay = api.service_for_mode('live'), api.service_for_mode('replay')
        self.assertEqual(replay.clock(), dt('2026-11-10T10:32:00+08:00'))
        self.assertEqual(live.clock() - replay.clock(), timedelta(days=2))
        self.assertNotEqual(live.store.path, replay.store.path)
        self.assertEqual(replay.store.path.name, 'ops-replay.sqlite3')
        self.assertTrue(replay.store.view('T2')['settings']['dispatch_enabled'])
        self.assertFalse(live.store.view('T2')['settings']['dispatch_enabled'])
        replay.store.grant({'user_id': 'crew', 'role': 'viewer', 'terminal': 'T2'}, 'admin')
        self.assertEqual(live.store.grants(), [])

    def test_future_historical_actuals_are_hidden_without_false_corruption(self):
        raw = {'real_start_clean_time': iso(self.clock + timedelta(minutes=1)), 'real_end_clean_time': iso(self.clock + timedelta(minutes=21))}
        op = operation('cleaning', raw, self.clock, replay=True)
        self.assertEqual((op['actual_start'], op['actual_end'], op['status']), (None, None, 'missing'))
        self.assertTrue(op['replay_censored'])
        self.assertEqual(op['status_label'], '此时无实绩')
        self.assertEqual(operation('catering', {}, self.clock, replay=True)['status_label'], '作业未记录')
        self.assertFalse(op['invalid_time'])
        op = operation('cleaning', raw, self.clock + timedelta(minutes=10), replay=True)
        self.assertEqual((op['status'], op['actual_end']), ('in_progress', None))
        self.assertEqual(op['status_label'], '进行中')
        self.assertEqual(operation('cleaning', raw, self.clock + timedelta(minutes=30), replay=True)['status'], 'completed')
        self.assertTrue(operation('cleaning', {**raw, 'real_end_clean_time': iso(self.clock)}, self.clock, replay=True)['invalid_time'])

    def test_simulated_end_to_end_flow_keeps_business_clock_and_actual_audit_time(self):
        original = deepcopy(self.source.flights)
        proposal = self.service.prepare_replay('T2', 'admin')['proposal']
        self.assertEqual(proposal['check']['slack_minutes'], 2)
        self.assertEqual(len(proposal['impacts']), 2)
        order = self.service.approve(proposal['id'], proposal['version'], 'replay-approval-key', 'T2', 'admin')
        self.assertEqual(self.service.approve(proposal['id'], proposal['version'], 'replay-approval-key', 'T2', 'admin')['id'], order['id'])
        for action in ('accept', 'start', 'finish', 'verify'):
            order = self.service.transition(order['id'], action, order['revision'], 'T2', 'admin', note='模拟核验')
        self.assertEqual((order['state'], order['mode'], order['verification_source']), ('complete', 'replay', '模拟核验'))
        self.assertIn('模拟', order['source'])
        self.assertEqual(dt(order['actual_start']), self.clock)
        self.assertTrue(all(dt(e['at']) == self.real_time for e in self.store.events('T2')))
        value = self.service.snapshot('T2', persist=True)
        self.assertEqual((value['mode'], value['clock_offset_days']), ('replay', -1))
        self.assertEqual(dt(value['captured_at']), self.clock)
        self.assertEqual(dt(value['real_time']), self.real_time)
        self.assertFalse(any(a['title'] == '数据更新待核查' for a in value['alerts']))
        self.assertEqual(self.source.flights, original)
        self.assertEqual(len(OpsStore(self.store.path, simulation=True).view('T2')['work_orders']), 1)

    def test_scenario_never_overwrites_configuration_and_requires_real_history(self):
        self.store.policy({'kind': 'cleaning', 'duration_minutes': 15, 'grace_minutes': 5, 'confirmed': True}, 'T2', 'admin')
        with self.assertRaises(Conflict):
            self.service.prepare_replay('T2', 'admin')
        self.assertEqual(self.store.view('T2')['resources'], [])
        self.assertEqual(self.store.view('T2')['policies']['cleaning']['duration_minutes'], 15)
        self.source.flights = []
        with self.assertRaisesRegex(ValueError, '手动登记'):
            self.service.prepare_replay('T2', 'admin')
        with self.assertRaises(ValueError):
            OpsService(self.store, self.source).prepare_replay('T2', 'admin')

    def test_replay_worker_never_starts_source_sync(self):
        from app.ops.worker import run_once
        with patch('app.ops.worker.sync_sources') as sync:
            run_once(self.service, 'T2', sync=True)
            sync.assert_not_called()

    def test_api_defaults_to_replay_and_never_exposes_simulation_to_live_requests(self):
        live = OpsService(OpsStore(Path(self.tmp.name) / 'live.sqlite3'), self.source)
        app = FastAPI(); app.include_router(api.router)
        client = Client(app, default_mode=None)
        async def check():
            first = (await client.get('/api/ops/me')).json()
            self.real_time += timedelta(seconds=15)
            self.clock += timedelta(seconds=15)
            self.assertEqual((await client.get('/api/ops/me')).json(), first)
            self.assertEqual(first['mode'], 'replay')
            prepared = await client.post('/api/ops/replay/prepare', json={})
            self.assertEqual(prepared.status_code, 200, prepared.text)
            self.assertTrue(prepared.json()['eligible'])
            self.assertEqual((await client.get('/api/ops/resources?mode=live')).json()['resources'], [])
            self.assertEqual((await client.post('/api/ops/replay/prepare?mode=live', json={})).status_code, 400)
            self.assertEqual((await client.post('/api/ops/replay/prepare', json={})).status_code, 409)
            self.assertEqual((await client.get('/api/ops/me?mode=invalid')).status_code, 400)
        with patch.object(api, 'STATIC_TEST_DATA', False), patch.object(api, 'service_for_mode', side_effect=lambda mode: self.service if mode == 'replay' else live), patch.object(api, 'identity', return_value={'user': {'id': 'admin', 'display_name': '管理员', 'roles': ['admin']}}):
            asyncio.run(check())
        self.assertEqual(live.store.view('T2')['work_orders'], [])


class OpsClockSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mode_patch = patch.object(api, 'STATIC_TEST_DATA', False)
        mode_patch.start(); self.addCleanup(mode_patch.stop)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        api.service_for_mode.cache_clear()
        self.addCleanup(api.service_for_mode.cache_clear)
        for target, options in (
            ('app.ops.api.now', {'return_value': dt('2026-11-12T10:32:00+08:00')}),
            ('app.ops.api.identity', {'return_value': {'user': {'id': 'admin', 'display_name': '管理员', 'roles': ['admin']}}}),
        ):
            obj = patch(target, **options); obj.start(); self.addCleanup(obj.stop)
        env = patch.dict('os.environ', HBASK_OPS_LEDGER=str(Path(self.tmp.name) / 'ops.sqlite3'))
        env.start(); self.addCleanup(env.stop)
        self.source = Source()
        self.source.flights = json.loads(json.dumps(self.source.flights).replace('2026-11-11', '2026-11-10'))
        for f in self.source.flights:
            f['target_at'] = f['tobt'] = f['sobt'] = '2026-11-10T13:00:00+08:00'
        self.source.fresh = False
        source_patch = patch.object(api, 'OpsSource', return_value=self.source)
        source_patch.start(); self.addCleanup(source_patch.stop)
        app = FastAPI(); app.include_router(api.router)
        self.client = Client(app, default_mode=None)

    async def test_concurrent_selected_clocks_are_request_local_and_default_does_not_drift(self):
        results = await asyncio.gather(*[self.client.get('/api/ops/snapshot?at=' + value) for value in ('2026-11-10T01:00:00Z', '2026-11-10T08:00:00Z')])
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual([r.json()['captured_at'] for r in results], ['2026-11-10T09:00:00+08:00', '2026-11-10T16:00:00+08:00'])
        result = (await self.client.get('/api/ops/snapshot')).json()
        expected_offset = (dt('2026-11-10T10:32:00+08:00').date() - now().date()).days
        self.assertEqual((result['captured_at'], result['clock_offset_days']), ('2026-11-10T10:32:00+08:00', expected_offset))
        self.assertIsNone(api.OPS_AT.get())
        self.assertEqual(api.service_for_mode('replay').clock(), dt('2026-11-10T10:32:00+08:00'))

    async def test_invalid_out_of_range_and_live_clock_overrides_are_rejected(self):
        for query in ('at=', 'at=bad', 'at=2026-11-10', 'at=2026-11-11T01:00:00Z', 'at=2026-11-10T01:00:00Z&mode=live'):
            response = await self.client.get('/api/ops/me?' + query)
            self.assertEqual(response.status_code, 400, query)
        response = await self.client.get('/api/ops/snapshot?at=2026-11-10T15:59:00Z')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['captured_at'], '2026-11-10T23:59:00+08:00')

    async def test_selected_clock_applies_to_simulated_dispatch_and_cannot_reverse_order_history(self):
        at = 'at=2026-11-10T02:00:00Z'
        result = await self.client.post('/api/ops/replay/prepare?' + at, json={})
        self.assertEqual(result.status_code, 200, result.text)
        proposal = result.json()['proposal']
        self.assertEqual(proposal['created_at'], '2026-11-10T10:00:00+08:00')
        payload = {'version': proposal['version'], 'idempotency_key': 'selected-clock-approval'}
        url = '/api/ops/proposals/' + proposal['id'] + '/approve?'
        self.assertEqual((await self.client.post(url + 'at=2026-11-10T01:59:00Z', json=payload)).status_code, 409)
        result = await self.client.post(url + at, json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        order = result.json()['order']
        self.assertEqual(order['approved_at'], '2026-11-10T10:00:00+08:00')
        result = await self.client.post(f"/api/ops/work-orders/{order['id']}/accept?at=2026-11-10T01:59:00Z", json={'revision': order['revision']})
        self.assertEqual(result.status_code, 409, result.text)
        for action in ('accept', 'start', 'finish', 'verify'):
            result = await self.client.post(f"/api/ops/work-orders/{order['id']}/{action}?" + at, json={'revision': order['revision'], 'note': '模拟核验'})
            self.assertEqual(result.status_code, 200, result.text)
            order = result.json()['order']
        self.assertEqual(order['actual_start'], '2026-11-10T10:00:00+08:00')
        self.assertEqual(order['state'], 'complete')
        resources = (await self.client.get('/api/ops/resources?' + at)).json()['resources']
        self.assertTrue(all(r['confirmed_at'] == '2026-11-10T10:00:00+08:00' for r in resources))
        self.assertEqual(api.service_for_mode('live').store.view('T2')['work_orders'], [])


if __name__ == '__main__':
    unittest.main()
