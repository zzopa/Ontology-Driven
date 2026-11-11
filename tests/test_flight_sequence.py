"""First/last flight questions use actual event dates and deterministic ordering."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import core
from app.acdm_ontology import parse_question
from app.config import load_settings
from app.evidence import build_evidence, fallback_claims
from app.ontology import Catalog
from app.planner import Planner
from app.query import Compiler


class FlightSequenceTests(unittest.TestCase):
    def setUp(self):
        with patch.dict('os.environ', {'HBASK_DATA_SOURCE': 'ops_ask'}):
            settings = load_settings()
        self.catalog = Catalog(json.loads(settings.schema_path.read_text(encoding='utf-8')),
                               json.loads(settings.ontology_path.read_text(encoding='utf-8')), settings)
        self.compiler = Compiler(self.catalog)

    def plan(self, question='昨天最后一个航班是哪个'):
        with patch('app.acdm_ontology.datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 11, tzinfo=timezone(timedelta(hours=8)))
            return parse_question(question, self.catalog)

    def test_plain_last_flight_is_one_actual_event_on_yesterday_in_beijing(self):
        plan = self.plan()
        self.assertEqual(plan['subject'], 'ops_ask_views.flight_execution')
        self.assertEqual(plan['limit'], 1)
        self.assertEqual(plan['order_by'], [{'field': 'execution_time', 'direction': 'desc'}])
        self.assertEqual(plan['conditions'][:2], [
            {'col': 'execution_time', 'op': '>=', 'value': '2026-11-10T00:00:00+08:00'},
            {'col': 'execution_time', 'op': '<', 'value': '2026-11-11T00:00:00+08:00'}])
        self.assertFalse(any(c['col'] == 'flight_date' for c in plan['conditions']))

    def test_first_last_and_direction_variants(self):
        for question, ordering, direction in [
            ('昨天最后一班出港航班是哪个', 'desc', 'DEP'),
            ('昨天最晚起飞的航班是哪班', 'desc', 'DEP'),
            ('昨天最后一个进港航班是哪个', 'desc', 'ARR'),
            ('2026年11月9日最早落地的航班是哪个', 'asc', 'ARR'),
            ('昨天第一班航班是哪一个', 'asc', None),
            ('昨天首班航班是哪个', 'asc', None),
        ]:
            with self.subTest(question=question):
                plan = self.plan(question)
                self.assertEqual(plan['order_by'][0]['direction'], ordering)
                actual = next((c['value'] for c in plan['conditions'] if c['col'] == 'direction'), None)
                self.assertEqual(actual, direction)

    def test_unknown_filters_and_dates_are_never_dropped(self):
        for question in ['最后一个航班是哪个', '昨天南航最后一个航班是哪个',
                         '昨天最后一个取消的航班是哪个', '昨天最晚的计划航班是哪个',
                         '昨天最后一班进港和出港航班是哪个',
                         '2026-11-09到2026-11-10最后一个航班是哪个']:
            with self.subTest(question=question), self.assertRaises(ValueError):
                self.plan(question)

    def test_conflicting_model_is_not_invoked_for_a_complete_sequence_query(self):
        chat = Mock(return_value=json.dumps({'subject': 'std_flight_core', 'intent': 'detail'}))
        plan, source = Planner(self.catalog, chat=chat).parse('昨天最后一个航班是哪个')
        self.assertEqual(plan['flight_sequence'], 'last')
        self.assertIn('本体规则', source)
        chat.assert_not_called()

    def test_live_inclusive_coverage_and_legacy_exclusive_coverage(self):
        for coverage in [{'start': '2026-11-09', 'end': '2026-11-10'},
                         {'start': '2026-11-09', 'end_exclusive': '2026-11-11'}]:
            with self.subTest(coverage=coverage):
                self.catalog.overrides['coverage'] = coverage
                self.assertEqual(self.plan()['limit'], 1)
                with self.assertRaisesRegex(ValueError, '超出'):
                    self.plan('2026-11-11最后一个航班是哪个')

    def test_selected_subset_is_counted_ordered_and_paginated_consistently(self):
        plan = self.plan()
        statement = self.compiler.detail(plan, [], 50, 200)
        self.assertIn('FROM (SELECT q0.*', statement.sql)
        self.assertIn('q0."execution_time" DESC NULLS LAST', statement.sql)
        self.assertIn('LIMIT 1) t0', statement.sql)
        self.assertEqual(statement.params, ('2026-11-10T00:00:00+08:00', '2026-11-11T00:00:00+08:00'))
        page = self.compiler.page(plan, plan['subject'], 2, 20)
        self.assertIn('LIMIT 1) t0', page.sql)
        self.assertEqual(page.params[-2:], (20, 20))
        matched = self.compiler.matched(plan, plan['subject'])
        self.assertIn('LIMIT 1) t0', matched.sql)

    def test_sort_and_limit_are_validated_before_sql(self):
        for changes in [
            {'order_by': [{'field': 'unknown', 'direction': 'desc'}]},
            {'order_by': [{'field': 'execution_time', 'direction': 'desc; DROP TABLE x'}]},
            {'order_by': 'execution_time'}, {'limit': True}, {'limit': 0}, {'limit': 1001},
            {'limit': '1'}, {'order_by': []}, {'intent': 'count'},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.compiler.detail({**self.plan(), **changes}, [], 50, 200)

    def test_top_record_is_selected_inside_the_authorized_source(self):
        access = SimpleNamespace(source=lambda table, catalog:
                                 ('(SELECT * FROM authorized_flights WHERE owner_id = %s)', ('allowed',)))
        compiler = Compiler(self.catalog, access)
        statement = compiler.detail(self.plan(), [], 50, 200)
        self.assertIn('FROM (SELECT * FROM authorized_flights WHERE owner_id = %s) q0', statement.sql)
        self.assertEqual(statement.params, ('allowed', '2026-11-10T00:00:00+08:00', '2026-11-11T00:00:00+08:00'))

    def test_answer_names_the_actual_flight_and_converts_timezone(self):
        plan = self.plan('2026-11-09最后一个航班是哪个')
        evidence = build_evidence(self.catalog, plan, [
            {'id': 1, 'flight_no': 'MU1234', 'direction': 'DEP',
             'execution_time': '2026-11-09T15:50:00+00:00'}], 1, [], [])
        claims, text = fallback_claims(self.catalog, plan, evidence, 1)
        self.assertIn('最后一班航班是 MU1234', text)
        self.assertIn('实际起飞时间为 2026-11-09 23:50:00', text)
        self.assertEqual(claims[0]['fact_ids'], ['F1'])
        _, empty = fallback_claims(self.catalog, plan, {'facts': []}, 0)
        self.assertIn('不表示当天没有航班', empty)

    def test_full_question_ignores_stale_cache_and_previous_flight(self):
        question = '昨天最后一个出港实际执飞航班是哪个'
        with patch.object(core, 'settings', self.catalog.settings), \
             patch.object(core, 'cache_key', return_value='old'), \
             patch.object(core, 'MEM', {'entries': [
                 {'q': question, 'version': 'old', 'parsed': {'subject': 'std_flight_core', 'intent': 'detail'}}]}):
            plan, _ = core.parse_question(question, self.catalog,
                                         previous_plan={'subject': 'std_flight_core', 'intent': 'detail'})
        self.assertEqual(plan['subject'], 'ops_ask_views.flight_execution')
        self.assertEqual(plan['limit'], 1)


if __name__ == '__main__':
    unittest.main()
