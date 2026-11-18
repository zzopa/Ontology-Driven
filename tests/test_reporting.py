from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from app.admin.store import AdminStore
from app import reporting as r


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AdminStore(self.tmp.name)
        self.payload = r.validate_request({'kind': 'general', 'target_date': '2026-11-10'}, 'u1')

    def test_request_dates_and_periods_are_validated(self):
        self.assertEqual(r.period_bounds('quarter', date(2026, 11, 16), True), (date(2026, 7, 1), date(2026, 9, 30)))
        self.assertEqual(r.period_bounds('year', date(2025, 1, 1), True), (date(2024, 1, 1), date(2024, 12, 31)))
        with self.assertRaises(ValueError):
            r.validate_request({'kind': 'ground_briefing', 'period': 'month'}, 'u1')

    def test_jobs_are_idempotent_and_owner_scoped(self):
        rid = r.enqueue(self.store, 'owner1', self.payload, 'key')
        self.assertEqual(rid, r.enqueue(self.store, 'owner1', self.payload, 'key'))
        self.assertEqual(r.list_reports(self.store, 'owner2')['items'], [])
        with self.assertRaises(ValueError):
            r.get_report(self.store, 'owner2', rid)
        with self.store.db() as db:
            self.assertNotIn('business_user_id', db.execute('SELECT payload FROM reports').fetchone()[0])

    def test_report_is_encrypted_and_worker_rechecks_authorization(self):
        rid = r.enqueue(self.store, 'owner1', self.payload)
        auth = Mock()
        r.process_one(self.store, auth, lambda p: {'answer': '安全文本', 'is_simulated': True}, lambda html: b'%PDF-fixture')
        self.assertEqual(auth.call_count, 2)
        row = r.get_report(self.store, 'owner1', rid)
        self.assertEqual(row['status'], 'completed')
        raw = (Path(self.tmp.name) / 'reports' / row['pdf_path']).read_bytes()
        self.assertFalse(raw.startswith(b'%PDF'))
        self.assertEqual(self.store.vault.cipher.decrypt(raw), b'%PDF-fixture')

    def test_subscriptions_schedule_once_and_pause_cancels_pending(self):
        sid = r.subscribe(self.store, 'owner1', 'u1', {'status': 'active', 'send_time': '08:00'})
        instant = datetime(2026, 11, 16, 9, tzinfo=r.TZ)
        self.assertEqual(r.schedule_due(self.store, instant), 1)
        self.assertEqual(r.schedule_due(self.store, instant), 0)
        r.subscribe(self.store, 'owner1', 'u1', {'status': 'paused'}, sid)
        render = Mock()
        r.process_one(self.store, Mock(), Mock(), render)
        render.assert_not_called()
        self.assertEqual(r.list_reports(self.store, 'owner1')['items'][0]['status'], 'cancelled')

    def test_only_successful_subscription_sends_one_in_app_notification(self):
        r.subscribe(self.store, 'owner1', 'u1', {'status': 'active'})
        r.schedule_due(self.store, datetime(2026, 11, 16, 9, tzinfo=r.TZ))
        r.process_one(self.store, Mock(), lambda p: {'answer': '通知报告'}, lambda h: b'%PDF-test')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], 1)
        self.assertFalse(r.process_one(self.store))

    def test_permission_change_pauses_subscription_without_notification(self):
        r.subscribe(self.store, 'owner1', 'u1', {'status': 'active'})
        r.schedule_due(self.store, datetime(2026, 11, 16, 9, tzinfo=r.TZ))
        with self.assertLogs('app.reporting', level='ERROR'):
            r.process_one(self.store, Mock(side_effect=PermissionError('权限已变化')), Mock(), Mock())
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM report_subscriptions').fetchone()[0], 'paused')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0], 0)

    def test_pdf_html_escapes_data_and_marks_simulation(self):
        html = r.html_report({'answer': '<script>alert(1)</script>', 'is_simulated': True}, '<img>')
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('模拟测试数据', html)
        self.assertIn('&lt;img&gt;', html)
        wide = r.html_report({'main_rows': [{f'field{i}': f'value{i}' for i in range(15)}]}, '宽表')
        self.assertIn('value14', wide)

    def test_expired_claim_recovers_and_worker_start_does_not_interrupt_chat(self):
        cid, mid = self.store.begin_question('owner1', '查询', 'test')
        AdminStore(self.tmp.name, recover=False)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM messages WHERE id=?', (mid,)).fetchone()[0], 'running')
        rid = r.enqueue(self.store, 'owner1', self.payload)
        self.assertEqual(r.claim(self.store)['id'], rid)
        self.assertIsNone(r.claim(self.store))
        with self.store.db() as db:
            db.execute('UPDATE reports SET lease_until=0 WHERE id=?', (rid,))
        self.assertEqual(r.claim(self.store)['id'], rid)

    def test_expired_worker_cannot_publish_over_a_new_claim(self):
        rid = r.enqueue(self.store, 'owner1', self.payload)
        def reclaim(html):
            with self.store.db() as db:
                db.execute('UPDATE reports SET lease_until=0 WHERE id=?', (rid,))
            self.assertEqual(r.claim(self.store)['id'], rid)
            return b'%PDF-stale'
        r.process_one(self.store, Mock(), lambda p: {'answer': '旧任务'}, reclaim)
        row = r.get_report(self.store, 'owner1', rid)
        self.assertEqual(row['status'], 'running')
        self.assertIsNone(row['pdf_path'])
        self.assertFalse(list((Path(self.tmp.name) / 'reports').glob('*.enc')))
