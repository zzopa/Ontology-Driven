import json
from dataclasses import replace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock

from app import report_api, reporting
from app.admin import service
from app.admin.store import AdminStore
from test_admin import http


class ReportApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AdminStore(self.tmp.name)
        self.user = {'owner': 'business:u1', 'user': {'id': 'u1'}}
        self.original_account = report_api.account
        a = patch.object(report_api, 'account', side_effect=lambda *a, **k: self.user)
        b = patch.object(service, 'get_store', return_value=self.store)
        c = patch.object(report_api, 'settings', replace(report_api.settings, data_source='ops_ask'))
        a.start(); b.start(); c.start()
        self.addCleanup(a.stop); self.addCleanup(b.stop); self.addCleanup(c.stop)

    async def test_queue_view_and_other_owner_isolation(self):
        code, _, data = await http('/api/reports', 'POST', {'target_date': '2026-11-10'})
        self.assertEqual(code, 202)
        rid = data['report']['id']
        self.assertEqual((await http('/api/reports/' + rid))[0], 200)
        self.assertEqual((await http('/api/reports/' + rid + '/download'))[0], 409)
        self.user = {'owner': 'business:u2', 'user': {'id': 'u2'}}
        self.assertEqual((await http('/api/reports'))[2]['items'], [])
        self.assertEqual((await http('/api/reports/' + rid))[0], 400)
        self.assertEqual((await http('/api/reports/' + rid + '/download'))[0], 400)

    async def test_malformed_dates_and_identifiers_are_rejected(self):
        for body in [{'kind': []}, {'period': {}}, {'target_date': 123}, {'target_date': '2026-02-30'}, {'message_id': []}]:
            self.assertEqual((await http('/api/reports', 'POST', body))[0], 400, body)
        self.assertEqual((await http('/api/reports/invalid'))[0], 400)

    async def test_pause_preserves_subscription_settings_and_other_owner_cannot_edit(self):
        _, _, created = await http('/api/report-subscriptions', 'POST', {'kind': 'safeguard_board', 'period': 'week', 'send_time': '09:30', 'status': 'active', 'include_benchmarks': True})
        sid = created['id']
        self.assertEqual((await http('/api/report-subscriptions/' + sid, 'PATCH', {'status': 'paused'}))[0], 200)
        row = (await http('/api/report-subscriptions'))[2]['items'][0]
        self.assertEqual((row['kind'], row['period'], row['send_time'], row['include_benchmarks']), ('safeguard_board', 'week', '09:30', 1))
        self.user = {'owner': 'business:u2', 'user': {'id': 'u2'}}
        self.assertEqual((await http('/api/report-subscriptions/' + sid, 'PATCH', {'status': 'active'}))[0], 400)
        self.assertEqual((await http('/api/report-subscriptions/' + sid, 'DELETE'))[0], 404)

    async def test_snapshot_export_requires_owned_completed_message(self):
        cid, mid = self.store.begin_question('business:u1', '对比', 'test')
        self.store.complete_question(mid, {'ok': True, 'outcome': 'answered', 'answer': '模拟', 'is_simulated': True})
        status, _, value = await http('/api/reports', 'POST', {'conversation_id': cid, 'message_id': mid})
        self.assertEqual(status, 202)
        row = reporting.get_report(self.store, 'business:u1', value['report']['id'])
        payload = json.loads(self.store.vault.open(row['payload']))
        self.assertTrue(payload['snapshot']['is_simulated'])
        self.user = {'owner': 'business:u2', 'user': {'id': 'u2'}}
        self.assertGreaterEqual((await http('/api/reports', 'POST', {'conversation_id': cid, 'message_id': mid}))[0], 400)

    async def test_authentication_and_same_origin_are_required(self):
        with patch.object(report_api, 'account', self.original_account):
            self.assertEqual((await http('/api/reports'))[0], 401)
            self.assertEqual((await http('/api/reports', 'POST', {}, origin='https://foreign.invalid'))[0], 403)
