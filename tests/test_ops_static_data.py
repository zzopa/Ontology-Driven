"""Static showcase contracts: complete synthetic data, no operational reads/writes."""
from hashlib import sha256
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException

from app.ops import api, static_data
from app.ops.domain import KINDS, check_candidate, dt
from tests.test_ops_workbench import Client


class StaticFixtureTests(unittest.TestCase):
    def test_perception_layout_and_event_scenarios_share_the_fixture_objects(self):
        data = static_data.load_fixture()
        config = data['perception']
        self.assertFalse(config['calibrated'])
        stands = {s['code']: s for s in data['stands']}
        self.assertEqual(sum(not s.get('kind') for s in stands.values()), 75)
        self.assertEqual({t: sum(s['terminal'] == t and not s.get('kind') for s in stands.values()) for t in ('T2', 'T3')}, {'T2': 20, 'T3': 55})
        for key in ('flights', 'tasks', 'work_orders'):
            for row in data[key]:
                self.assertEqual(stands[row['stand']]['terminal'], row['terminal'])
        for resource in data['resources']:
            self.assertEqual(stands[resource['position']]['terminal'], resource['terminal'])
        for transfer in data['transfers']:
            for field in ('from_stand', 'to_stand'):
                self.assertEqual(stands[transfer[field]]['terminal'], transfer['terminal'])
        positions = {(s['scene']['x'], s['scene']['z']) for s in stands.values()}
        self.assertEqual(len(positions), len(stands))
        for scene in config['scenarios']:
            self.assertIn(scene['alert_id'], {a['id'] for a in data['alerts']})
            self.assertTrue(set(scene['stand_codes']) <= set(stands))
            self.assertEqual([s['phase'] for s in scene['steps']], ['scan', 'detect', 'associate', 'check', 'proposal'])
            self.assertEqual([s['at_ms'] for s in scene['steps']], sorted({s['at_ms'] for s in scene['steps']}))
            self.assertTrue(all(not s.get('focus_stand') or s['focus_stand'] in stands for s in scene['steps']))
            if scene.get('proposal_id'):
                self.assertIn(scene['proposal_id'], {p['id'] for p in data['proposals']})
                self.assertTrue(set(scene['resource_ids']) <= {r['id'] for r in data['resources']})
                for index, code in ((0, scene['route_stands'][0]), (3, scene['route_stands'][1]), (-1, scene['route_stands'][2])):
                    self.assertEqual(scene['route_points'][index], [stands[code]['scene']['x'], stands[code]['scene']['z']])
    def test_fixture_has_complete_cross_references_and_five_operation_states(self):
        data = static_data.load_fixture()
        flights = {f['id']: f for f in data['flights']}
        resources = {r['id']: r for r in data['resources']}
        self.assertEqual(len(flights), 27)
        self.assertEqual({t: sum(f['terminal'] == t for f in flights.values()) for t in ('T2', 'T3')}, {'T2': 12, 'T3': 15})
        for flight in flights.values():
            self.assertEqual({o['kind'] for o in flight['operations']}, set(KINDS))
            for op in flight['operations']:
                self.assertLess(dt(op['plan_start']), dt(op['plan_end']))
                self.assertEqual(op['source'], '静态 JSON 测试实绩')
                for key in ('actual_start', 'actual_end'):
                    if op[key]:
                        self.assertLessEqual(dt(op[key]), dt(data['meta']['captured_at']))
        self.assertEqual({o['status'] for f in flights.values() for o in f['operations']}, {'planned', 'in_progress', 'completed', 'risk'})
        for task in data['tasks']:
            self.assertIn(task['flight_id'], flights)
            self.assertEqual(task['kind'], resources[task['resource_id']]['kind'])
            self.assertEqual(task['terminal'], resources[task['resource_id']]['terminal'])
            self.assertEqual(task['terminal'], flights[task['flight_id']]['terminal'])
        self.assertEqual({o['kind'] for o in data['work_orders']}, set(KINDS))
        self.assertEqual({o['state'] for o in data['work_orders']}, {'pending', 'accepted', 'active', 'review', 'complete', 'exception'})
        orders = {o['id'] for o in data['work_orders']}
        self.assertTrue(all(e['target'] in orders for e in data['events']))

    def test_dynamic_aircraft_have_complete_ground_records_and_valid_monitor_positions(self):
        data = static_data.load_fixture()
        stands = {s['code']: s for s in data['stands']}
        self.assertEqual({f['aircraft_state']['code'] for f in data['flights']} & {'cleaning', 'fueling', 'waiting_hangar', 'taxiing', 'taking_off'}, {'cleaning', 'fueling', 'waiting_hangar', 'taxiing', 'taking_off'})
        for flight in data['flights']:
            self.assertEqual(stands[flight['stand']]['terminal'], flight['terminal'])
            if flight.get('motion'):
                self.assertTrue(all(o['status'] == 'completed' for o in flight['operations']))
                self.assertEqual(flight['motion']['path'][0], [stands[flight['stand']]['scene']['x'], 0, stands[flight['stand']]['scene']['z']])
                self.assertIn(stands[flight['stand']]['kind'], ('taxiway', 'runway'))
        for bad in ({'duration_ms': 0, 'path': [[0, 0, 0], [1, 0, 1]]}, {'duration_ms': 1000, 'path': [[0, 0, 0], [0, 0, 0]]}):
            broken = copy.deepcopy(data); broken['flights'][0]['motion'] = bad
            with patch.object(static_data, 'FIXTURE_PATH', SimpleNamespace(read_text=lambda **_: json.dumps(broken))):
                with self.assertRaises(ValueError): static_data.load_fixture()

    def test_cross_flight_proposal_matches_the_displayed_schedule_and_transfer_evidence(self):
        data = static_data.load_fixture()
        proposal = data['proposals'][0]
        check = check_candidate(proposal['resource'], proposal['target'], data['tasks'], data['rosters'], data['transfers'],
                                dt(proposal['start_at']), dt(proposal['end_at']), dt(data['meta']['captured_at']))
        self.assertTrue(check['eligible'], check['errors'])
        self.assertEqual((check['transfer_before'], check['transfer_after'], check['slack_minutes']), (2, 4, 2))
        self.assertEqual({x['flight_no'] for x in proposal['impacts']}, {'HU7134', 'CA1258'})
        self.assertEqual(check, proposal['check'])
        for proposal in data['proposals']:
            if proposal['state'] == 'pending' and proposal['terminal'] == 'T3':
                self.assertEqual(check_candidate(proposal['resource'], proposal['target'], data['tasks'], data['rosters'], data['transfers'], dt(proposal['start_at']), dt(proposal['end_at']), dt(data['meta']['captured_at'])), proposal['check'])


class StaticApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = {'id': 'admin', 'display_name': '测试查看者', 'roles': ['admin']}
        def identify(_request):
            if self.actor is None:
                raise HTTPException(401, '请先登录')
            return {'user': self.actor}
        for target, options in (
            ('STATIC_TEST_DATA', {'new': True}),
            ('identity', {'side_effect': identify}),
            ('get_service', {'side_effect': AssertionError('静态管理员展示不得读取运行服务')}),
            ('OpsSource', {'side_effect': AssertionError('静态展示不得连接业务源')}),
            ('OpsStore', {'side_effect': AssertionError('静态展示不得创建运行台账')}),
        ):
            obj = patch.object(api, target, **options)
            obj.start(); self.addCleanup(obj.stop)
        app = FastAPI(); app.include_router(api.router)
        self.client = Client(app, default_mode=None)

    async def test_all_read_routes_use_the_same_fixture_even_with_old_live_and_time_parameters(self):
        me = (await self.client.get('/api/ops/me')).json()
        self.assertEqual((me['data_mode'], me['read_only'], me['fixture_time']), ('static-test', True, '2026-11-10T10:32:00+08:00'))
        first = (await self.client.get('/api/ops/snapshot')).json()
        other = (await self.client.get('/api/ops/snapshot?mode=live&at=bad')).json()
        self.assertEqual(first, other)
        self.assertEqual(first['metrics']['flights'], 12)
        self.assertGreater(first['metrics']['in_progress'], 0)
        self.assertGreater(first['metrics']['attention'], 0)
        self.assertEqual(len(first['perception']['scenarios']), 3)
        self.assertEqual(first['proposals'][0]['id'], 'test-proposal-cleaning')
        for path, key in (('/resources', 'resources'), ('/rosters', 'rosters'), ('/alerts', 'alerts'), ('/proposals', 'proposals'), ('/work-orders', 'orders'), ('/flight-options?date=2026-11-10', 'flights')):
            response = await self.client.get('/api/ops' + path)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()[key], path)
        for flight in first['flights']:
            response = await self.client.get('/api/ops/flights/' + flight['id'])
            self.assertEqual(response.status_code, 200, response.text)
            detail = response.json()['turnaround']
            self.assertEqual(len(detail['operations']), 5)
            self.assertEqual(detail['source_label'], '静态 JSON 测试数据')
        self.assertEqual((await self.client.get('/api/ops/flights/old-real-id')).status_code, 404)
        self.assertEqual((await self.client.get('/api/ops/snapshot?terminal=T4')).status_code, 400)

    async def test_t3_and_whole_airport_views_keep_data_and_scenarios_in_scope(self):
        t2 = (await self.client.get('/api/ops/snapshot?terminal=T2')).json()
        t3 = (await self.client.get('/api/ops/snapshot?terminal=T3')).json()
        whole = (await self.client.get('/api/ops/snapshot?terminal=ALL')).json()
        self.assertEqual((t2['metrics']['flights'], t3['metrics']['flights'], whole['metrics']['flights']), (12, 15, 27))
        for view, terminal in ((t2, 'T2'), (t3, 'T3')):
            for key in ('flights', 'stands', 'resources', 'tasks', 'alerts', 'proposals'):
                self.assertTrue(view[key]); self.assertTrue(all(r['terminal'] == terminal for r in view[key]))
            self.assertEqual(len(view['perception']['scenarios']), 3)
            self.assertTrue(all(s['terminal'] == terminal for s in view['perception']['scenarios']))
        self.assertEqual(len(whole['perception']['scenarios']), 6)
        self.assertEqual({f['id'] for f in whole['flights']}, {f['id'] for f in t2['flights'] + t3['flights']})
        for i, row in enumerate(whole['workload']):
            for field in ('demand', 'available', 'gap'):
                self.assertEqual(row[field], t2['workload'][i][field] + t3['workload'][i][field])
        for flight in t3['flights']:
            self.assertEqual((await self.client.get('/api/ops/flights/' + flight['id'] + '?terminal=T3')).status_code, 200)
            self.assertEqual((await self.client.get('/api/ops/flights/' + flight['id'] + '?terminal=T2')).status_code, 404)
        for path, key in (('/resources', 'resources'), ('/proposals', 'proposals'), ('/work-orders', 'orders')):
            response = (await self.client.get('/api/ops' + path + '?terminal=T3')).json()
            self.assertTrue(response[key]); self.assertTrue(all(r['terminal'] == 'T3' for r in response[key]))

    async def test_mutations_are_rejected_and_cannot_change_the_json_or_live_ledgers(self):
        before = sha256(static_data.FIXTURE_PATH.read_bytes()).hexdigest()
        paths = ['/resources', '/rosters', '/tasks', '/transfers', '/policies', '/grants', '/settings', '/proposals',
                 '/proposals/test-proposal-cleaning/approve', '/proposals/test-proposal-cleaning/reject',
                 '/replay/prepare', '/work-orders/test-order/accept', '/tasks/test-task/report',
                 '/alerts/test-alert/acknowledge', '/assist']
        for path in paths:
            response = await self.client.post('/api/ops' + path + '?mode=live', json={})
            self.assertEqual(response.status_code, 405, (path, response.text))
        response = await self.client.request('PATCH', '/api/ops/resources/test-cleaning-a', json={})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(sha256(static_data.FIXTURE_PATH.read_bytes()).hexdigest(), before)

    async def test_original_authentication_terminal_and_crew_scope_are_preserved(self):
        self.actor = None
        self.assertEqual((await self.client.get('/api/ops/snapshot')).status_code, 401)
        self.actor = {'id': 'viewer', 'display_name': '值守查看者', 'roles': []}
        grant = {'user_id': 'viewer', 'role': 'viewer', 'terminal': 'T2', 'resource_id': ''}
        service = SimpleNamespace(store=SimpleNamespace(grants=lambda *_: [grant]))
        with patch.object(api, 'get_service', return_value=service), patch.object(api, 'service_for_mode', return_value=service):
            self.assertEqual((await self.client.get('/api/ops/snapshot')).status_code, 200)
            self.assertEqual((await self.client.get('/api/ops/snapshot?terminal=T3')).status_code, 403)
            self.assertEqual((await self.client.get('/api/ops/snapshot?terminal=ALL')).status_code, 403)
            self.assertEqual((await self.client.get('/api/ops/users?q=test')).status_code, 403)
            grant['terminal'] = 'ALL'
            self.assertEqual((await self.client.get('/api/ops/snapshot?terminal=ALL')).status_code, 403)
            grant['terminal'] = 'T2'
        self.actor = {'id': 'crew', 'display_name': '班组查看者', 'roles': []}
        grant.update(user_id='crew', role='crew', resource_id='test-cleaning-a')
        with patch.object(api, 'get_service', return_value=service):
            response = (await self.client.get('/api/ops/work-orders')).json()
            self.assertTrue(response['orders'])
            self.assertTrue(all(o['resource_id'] == 'test-cleaning-a' for o in response['orders']))
            self.assertEqual((await self.client.get('/api/ops/snapshot')).status_code, 403)

    async def test_missing_fixture_does_not_silently_fall_back_to_real_data(self):
        for error in (FileNotFoundError, ValueError('invalid json')):
            with self.assertLogs('app.ops.api', level='ERROR'), patch.object(static_data, 'load_fixture', side_effect=error):
                self.assertEqual((await self.client.get('/api/ops/snapshot')).status_code, 503)


if __name__ == '__main__':
    unittest.main()
