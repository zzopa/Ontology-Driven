"""Tests for planned-vs-operated reconciliation and cross-day flight details."""
from datetime import date, datetime
import json
import unittest
from unittest.mock import patch

from app.agent.analysis import run
from app.agent.coordinator import BEIJING, Coordinator
from app.agent.cross_day import build_statements, is_cross_day_request
from app.config import load_settings
from app.execution import ExecutionTrace
from app.ontology import Catalog


def _catalog():
    with patch.dict('os.environ', {'HBASK_DATA_SOURCE': 'ops_ask'}):
        s = load_settings()
    return Catalog(
        json.loads(s.schema_path.read_text(encoding='utf-8')),
        json.loads(s.ontology_path.read_text(encoding='utf-8')),
        s,
    )


class _StubCursor:
    def __init__(self, responses):
        self._responses = responses
        self.description = []
        self._rows = []

    def execute(self, sql, params=()):
        cols, rows = self._responses.pop(0)
        self.description = [(c,) for c in cols]
        self._rows = rows

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _StubConn:
    def __init__(self, responses):
        self._responses = list(responses)

    def cursor(self):
        return _StubCursor(self._responses)


class CrossDayReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = _catalog()
        self.coordinator = Coordinator(self.catalog)

    def test_detection_positive_and_negative_cases(self):
        self.assertTrue(is_cross_day_request(
            '2026-11-02至2026-11-15计划7042架次、取消40架次，为什么实际执行是7003而不是7002？请用跨日航班明细解释。'
        ))
        self.assertTrue(is_cross_day_request('2026-11-02至2026-11-15为什么计划减取消不等于实际执行？'))
        self.assertTrue(is_cross_day_request('2026-11-02至2026-11-15跨日航班明细'))
        self.assertFalse(is_cross_day_request('2026-11-10出港计划航班量和执行航班量分别多少'))
        self.assertFalse(is_cross_day_request('2026-11-02至2026-11-15 计划航班架次和实际执行航班架次分别是多少'))
        self.assertFalse(is_cross_day_request('2026-11-02至2026-11-15 ACDM通用报告'))
        self.assertFalse(is_cross_day_request('预测明天实际执行航班架次'))
        self.assertFalse(is_cross_day_request('2026-11-09 CA8203 跨日过站保障情况'))

    def test_plan_and_sql_compilation_for_reconciliation_question(self):
        q = '2026-11-02至2026-11-15计划7042架次、取消40架次，为什么实际执行是7003而不是7002？请用跨日航班明细解释。'
        plan = self.coordinator.plan(q)
        self.assertEqual(plan['intent'], 'analysis')
        self.assertEqual(plan['analysis_kind'], 'cross_day_reconciliation')
        self.assertEqual(plan['reconcile_mode'], 'reconciliation')
        self.assertEqual(plan['period'], ['2026-11-02', '2026-11-16'])
        self.assertEqual(plan['subject'], 'mv_acdm_dyn_flight_detail')

        summary_stmt, detail_stmt = build_statements(self.catalog, plan)
        self.assertEqual(summary_stmt.params, ('2026-11-02', '2026-11-16'))
        self.assertEqual(detail_stmt.params, ('2026-11-02', '2026-11-16'))
        self.assertIn('plan_day', summary_stmt.sql)
        self.assertIn('real_exec_day', summary_stmt.sql)
        self.assertIn('cross_out', detail_stmt.sql)
        self.assertIn('cross_in', detail_stmt.sql)

    def test_run_reconciliation_produces_exact_equation_and_detail_table(self):
        q = '2026-11-02至2026-11-15计划7042架次、取消40架次，为什么实际执行是7003而不是7002？请用跨日航班明细解释。'
        plan = self.coordinator.plan(q)
        summary_cols = [
            'planned_count', 'cancel_count', 'operated_count', 'planned_and_operated',
            'cross_out_count', 'cross_in_count', 'unoperated_non_cancel_count',
            'cancel_yet_operated_count', 'internal_cross_day_count',
        ]
        detail_cols = [
            'flight_id', 'flight_no', 'direction', 'is_off_in', 'airline_two_char_cd',
            'craft_no', 'aircraft_type', 'origin_airport', 'dest_airport',
            'plan_day', 'real_exec_day', 'exec_date', 'plan_time_bj', 'real_time_bj',
            'flight_status', 'abnormal_flight_stat_cd', 'is_operated_flight', 'discrepancy_type',
        ]
        conn = _StubConn([
            (summary_cols, [(7042, 40, 7003, 7002, 3, 4, 0, 0, 50)]),
            (detail_cols, [
                (1, 'CZ5836', 'ARR', 'A', 'CZ', 'B5741', 'B738', 'URC', 'WUH', date(2026, 11, 2), date(2026, 11, 1), date(2026, 11, 2), '2026-11-02 00:25', '2026-11-01 23:36', 'ARR', None, 'Y', 'cross_out'),
                (2, 'CZ3367', 'ARR', 'A', 'CZ', 'B1402', 'B738', 'CAN', 'WUH', date(2026, 11, 2), date(2026, 11, 1), date(2026, 11, 2), '2026-11-02 00:10', '2026-11-01 23:47', 'ARR', None, 'Y', 'cross_out'),
                (3, 'CA8234', 'ARR', 'A', 'CA', 'B1853', 'A320', 'SZX', 'WUH', date(2026, 11, 2), date(2026, 11, 1), date(2026, 11, 2), '2026-11-02 00:05', '2026-11-01 23:54', 'ARR', None, 'Y', 'cross_out'),
                (4, 'CA8228', 'ARR', 'A', 'CA', 'B1873', 'A320', 'XMN', 'WUH', date(2026, 11, 16), date(2026, 11, 15), date(2026, 11, 16), '2026-11-16 00:05', '2026-11-15 23:19', 'ARR', None, 'Y', 'cross_in'),
                (5, 'CA8234', 'ARR', 'A', 'CA', 'B304E', 'A320', 'SZX', 'WUH', date(2026, 11, 16), date(2026, 11, 15), date(2026, 11, 16), '2026-11-16 00:05', '2026-11-15 23:41', 'ARR', None, 'Y', 'cross_in'),
                (6, 'CZ3367', 'ARR', 'A', 'CZ', 'B1411', 'B738', 'CAN', 'WUH', date(2026, 11, 16), date(2026, 11, 15), date(2026, 11, 16), '2026-11-16 00:10', '2026-11-15 23:46', 'ARR', None, 'Y', 'cross_in'),
                (7, 'MU2544', 'ARR', 'A', 'MU', 'B5689', 'B738', 'PVG', 'WUH', date(2026, 11, 16), date(2026, 11, 15), date(2026, 11, 16), '2026-11-16 00:10', '2026-11-15 23:51', 'ARR', None, 'Y', 'cross_in'),
                (8, 'HO1001', 'ARR', 'A', 'HO', 'B9999', 'A320', 'SHA', 'WUH', date(2026, 11, 5), date(2026, 11, 4), date(2026, 11, 5), '2026-11-05 00:05', '2026-11-04 23:50', 'ARR', None, 'Y', 'internal_cross_day'),
            ]),
        ])
        with patch('app.agent.cross_day.get_airport_name', side_effect=lambda c: {'URC': '乌鲁木齐', 'CAN': '广州', 'SZX': '深圳', 'XMN': '厦门', 'PVG': '上海浦东', 'WUH': '武汉'}.get(c, c)):
            res = run(conn, q, plan, self.catalog, ExecutionTrace())

        self.assertEqual(res['outcome'], 'answered')
        self.assertEqual(res['main_total'], 7)
        self.assertEqual(len(res['main_rows']), 7)
        self.assertEqual(res['visual_meta']['kind'], 'detail_table')
        self.assertFalse(res['visual_meta']['is_flight'])
        self.assertEqual(res['answer_document']['version'], 'evidence-answer-v1')
        self.assertEqual(res['answer_document']['claim_count'], len(res['claims']))
        self.assertIn('计划 7,042 − 取消 40 − 跨出本期 3 + 跨入本期 4 = 实际执行 7,003 架次', res['answer'])
        self.assertIn('CZ5836', res['answer'])
        self.assertIn('MU2544', res['answer'])
        self.assertEqual(res['reconciliation']['net_diff'], 1)
        self.assertEqual(len(res['related']), 2)
        self.assertEqual(res['related'][0]['table'], 'reconciliation_summary')
        self.assertEqual(res['related'][1]['table'], 'internal_cross_day')

    def test_missing_period_asks_clarification_and_resumes(self):
        first = self.coordinator.plan('为什么计划减取消不等于实际执行？请用跨日航班明细解释。')
        self.assertEqual(first['outcome'], 'clarification_needed')
        self.assertEqual(first['clarification']['slot'], 'period')

        resumed = self.coordinator.plan('2026-11-02至2026-11-15', clarification=first['clarification'])
        self.assertEqual(resumed['intent'], 'analysis')
        self.assertEqual(resumed['analysis_kind'], 'cross_day_reconciliation')
        self.assertEqual(resumed['period'], ['2026-11-02', '2026-11-16'])

    def test_inherits_period_from_previous_turn(self):
        prev = self.coordinator.plan('2026-11-02至2026-11-15 计划航班架次和实际执行航班架次分别是多少')
        follow = self.coordinator.plan('为什么实际执行是7003而不是7002？请用跨日航班明细解释。', previous_plan=prev)
        self.assertEqual(follow['intent'], 'analysis')
        self.assertEqual(follow['analysis_kind'], 'cross_day_reconciliation')
        self.assertEqual(follow['period'], ['2026-11-02', '2026-11-16'])

    def test_followup_date_switch_and_non_hijack_new_query(self):
        prev = self.coordinator.plan('2026-11-02至2026-11-15计划7042架次、取消40架次，为什么实际执行是7003而不是7002？请用跨日航班明细解释。')
        follow = self.coordinator.plan('那2026-11-09呢', previous_plan=prev)
        self.assertEqual(follow['analysis_kind'], 'cross_day_reconciliation')
        self.assertEqual(follow['reconcile_mode'], 'reconciliation')
        self.assertEqual(follow['period'], ['2026-11-09', '2026-11-10'])

        new_q = self.coordinator.plan('2026-11-09实际执行航班架次是多少', previous_plan=prev)
        self.assertNotEqual(new_q.get('analysis_kind'), 'cross_day_reconciliation')
        self.assertEqual(new_q.get('metric'), 'operated_flight_count')

    def test_operational_day_mode_rejected_explicitly(self):
        res = self.coordinator.plan('2026-11-02至2026-11-15跨日航班明细', day_mode='OPERATIONAL')
        self.assertEqual(res['outcome'], 'unsupported')
        self.assertIn('自然日', res['answer'])

    def test_out_of_coverage_returns_notice(self):
        plan = self.coordinator.plan('2025-01-01至2025-01-05 计划与实际执行差异 跨日航班')
        summary_cols = [
            'planned_count', 'cancel_count', 'operated_count', 'planned_and_operated',
            'cross_out_count', 'cross_in_count', 'unoperated_non_cancel_count',
            'cancel_yet_operated_count', 'internal_cross_day_count',
        ]
        conn = _StubConn([
            (summary_cols, [(0, 0, 0, 0, 0, 0, 0, 0, 0)]),
            (['min_plan', 'max_plan', 'min_real', 'max_real'], [('2026-11-01', '2026-11-16', '2026-11-01', '2026-11-16')]),
        ])
        res = run(conn, '2025-01-01至2025-01-05 计划与实际执行差异 跨日航班', plan, self.catalog, ExecutionTrace())
        self.assertEqual(res['outcome'], 'out_of_coverage')
        self.assertIn('2026-11-01', res['answer'])


if __name__ == '__main__':
    unittest.main()
