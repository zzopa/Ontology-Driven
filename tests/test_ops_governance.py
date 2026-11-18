"""Tests for ops-ask governance menus, enhanced ontology resolution, dry-run planner, and feedback loop."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import business_access, core, ops_governance
from app.agent.coordinator import ACTUAL, CORE, Coordinator
from app.agent.governance_runtime import (
    apply_rule_guardrails,
    check_capability_gate,
    match_plan_template,
    render_template_summary,
)
from app.admin import service
from app.admin.security import digest
from app.admin.store import AdminStore
from app.config import load_settings
from app.ontology import Catalog
from app.planner import Planner
from app.query import Compiler
from test_admin import INITIAL, PASSWORD, http


class OpsGovernanceUnitTests(unittest.TestCase):
    def setUp(self):
        with patch.dict('os.environ', {'HBASK_DATA_SOURCE': 'ops_ask'}):
            self.settings = load_settings()
        self.schema = json.loads(self.settings.schema_path.read_text(encoding='utf-8'))
        doc = json.loads(self.settings.ontology_path.read_text(encoding='utf-8'))
        # Inject rich governance concepts, filters, synonyms, metrics, scenarios & home_airport matching PG
        doc['home_airport'] = {
            'iataCode': 'WUH',
            'cityName': '武汉',
            'displayName': '武汉天河',
            'aliases': ['天河', '武汉机场', '武汉天河机场'],
        }
        doc['operational_day'] = {'enabled': True, 'startHour': 5, 'timezone': 'Asia/Shanghai'}
        doc['concepts'] = {
            'FlightDelay': {'name': '航班延误', 'domain': 'FIMS', 'concept_type': 'METRIC', 'status': 'PUBLISHED'},
            'FlightVolume': {'name': '航班量', 'domain': 'FIMS', 'concept_type': 'METRIC', 'status': 'PUBLISHED'},
            'FlightProduction': {'name': '航班生产量', 'domain': 'PRODUCTION', 'concept_type': 'METRIC', 'status': 'PUBLISHED'},
        }
        doc['concept_metrics'] = [
            {'concept_code': 'FlightDelay', 'metric_code': 'dep_delay_count', 'condition_expr': {'direction': 'DEP'}, 'priority': 100},
            {'concept_code': 'FlightDelay', 'metric_code': 'arr_delay_count', 'condition_expr': {'direction': 'ARR'}, 'priority': 90},
            {'concept_code': 'FlightProduction', 'metric_code': 'pax_total_sum', 'condition_expr': {}, 'priority': 100},
        ]
        doc['synonyms'] = [
            {'concept_code': 'FlightDelay', 'term': '晚点', 'term_type': 'alias', 'weight': 1.0},
            {'concept_code': 'FlightDelay', 'term': '进港延误', 'term_type': 'alias', 'weight': 1.2},
            {'concept_code': 'FlightProduction', 'term': '旅客人次', 'term_type': 'alias', 'weight': 1.2},
        ]
        doc['filters'] = {
            'direction': {'name': '进出港', 'column_expr': 'c.direction', 'operator': 'eq', 'join_indicator': False, 'value_type': 'string', 'status': 'PUBLISHED'},
            'is_delayed': {'name': '是否延误', 'column_expr': 'i.is_delayed', 'operator': 'eq', 'join_indicator': True, 'value_type': 'boolean', 'status': 'PUBLISHED'},
            'is_local_leg': {'name': '本场航段', 'column_expr': 'c.is_local_leg', 'operator': 'eq', 'join_indicator': False, 'value_type': 'boolean', 'status': 'PUBLISHED'},
            'originCity': {'name': '起站城市', 'column_expr': 'SELECT 1 FROM dim_airport a WHERE a.iata_code = c.origin_airport AND a.city_name ILIKE ?', 'operator': 'exists', 'join_indicator': False, 'value_type': 'string', 'status': 'PUBLISHED'},
            'shareFlightNo': {'name': '共享航班号', 'column_expr': "SELECT 1 FROM std_flight_link l WHERE l.flight_id = c.id AND l.link_type = 'CODESHARE' AND UPPER(l.linked_flight_identity) = UPPER(?)", 'operator': 'exists', 'join_indicator': False, 'value_type': 'string', 'status': 'PUBLISHED'},
        }
        doc.setdefault('scenarios', {})['RankByAirline'] = {
            'name': '按航司排名', 'query_type': 'RANK', 'metric_code': 'flight_count',
            'dimensions': ['airline'], 'default_filters': {}, 'select_fields': [],
            'trigger_keywords': ['航司排名', '哪个航司'], 'priority': 80, 'status': 'PUBLISHED',
        }
        self.catalog = Catalog(self.schema, doc, self.settings)
        self.planner = Planner(self.catalog)
        self.compiler = Compiler(self.catalog)

    def test_sql_expression_safety_rejects_injection_and_allows_subquery(self):
        self.assertIn('SELECT 1 FROM dim_airport', ops_governance._validate_sql_expr('SELECT 1 FROM dim_airport a WHERE a.city_name ILIKE ?'))
        for bad in ('COUNT(*); DROP TABLE std_flight_core', '1=1 -- comment', 'pg_sleep(5)', 'DELETE FROM std_flight_core'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ops_governance._validate_sql_expr(bad)

    def test_synonym_concept_and_home_airport_enhance_ontology_planning(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        # 1. Synonym + direction -> arr_delay_count
        arr_plan = self.planner.rules('昨天进港晚点多少架次？')
        self.assertEqual(arr_plan['metric'], 'arr_delay_count')
        self.assertIn({'table': 'mv_acdm_dyn_flight_detail', 'col': 'direction', 'op': '=', 'value': 'ARR'}, arr_plan['conditions'])
        arr_stmt = self.compiler.aggregate(arr_plan)
        self.assertIn('arr_delay_time', arr_stmt.sql)
        self.assertIn('real_exec_day', arr_stmt.sql)

        # 2. Home airport disambiguation + originCity EXISTS + indicator EXISTS in detail mode
        list_plan = self.planner.rules('昨天从上海来武汉天河的进港延误航班有哪些？')
        self.assertEqual(list_plan['intent'], 'detail')
        self.assertIsNone(list_plan['metric'])
        self.assertEqual(list_plan['subject'], 'std_flight_core')
        cols = {c['col']: c['value'] for c in list_plan['conditions']}
        self.assertEqual(cols.get('direction'), 'ARR')
        self.assertEqual(cols.get('originCity'), '上海')
        self.assertEqual(cols.get('is_delayed'), True)
        self.assertEqual(cols.get('flight_date'), yesterday)
        list_stmt = self.compiler.detail(list_plan, [], 50, 200)
        self.assertIn('EXISTS (SELECT 1 FROM dim_airport', list_stmt.sql)
        self.assertIn('EXISTS (SELECT 1 FROM "public"."std_flight_indicator"', list_stmt.sql)
        self.assertIn('%上海%', list_stmt.params)

        # 3. Airline ranking uses the canonical FIMS-scoped flight population.
        rank_plan = self.planner.rules('今天各个航司出港航班排名')
        self.assertEqual(rank_plan['subject'], 'mv_acdm_dyn_flight_detail')
        self.assertEqual(rank_plan['intent'], 'metric')
        self.assertEqual(rank_plan['metric'], 'flight_count')
        self.assertEqual(rank_plan['group_by'], ['airline'])

        # 4. Codeshare query compiles EXISTS on std_flight_link with bound flight number
        share_plan = self.planner.rules('查一下共享航班 MU5101 的动态')
        self.assertEqual(share_plan['subject'], 'std_flight_core')
        share_stmt = self.compiler.detail(share_plan, [], 50, 200)
        self.assertIn('EXISTS (SELECT 1 FROM std_flight_link', share_stmt.sql)
        self.assertIn('MU5101', share_stmt.params)

    def test_dry_run_plan_returns_sql_and_operational_window(self):
        with patch.object(core, 'current_catalog', return_value=self.catalog):
            res = ops_governance.dry_run_plan('11月8日出港延误有多少架次？', day_mode='OPERATIONAL')
        self.assertTrue(res['resolved'])
        self.assertEqual(res['plan']['metric'], 'dep_delay_count')
        self.assertIn('COUNT(*)', res['sql'])
        self.assertIsNotNone(res['operationalWindow'])
        self.assertEqual(res['operationalWindow']['startHour'], 5)
        self.assertEqual(res['operationalWindow']['start'], f'{date.today().year}-11-08 05:00:00')
        self.assertTrue(any('05:00:00+08:00' in str(p) for p in res['params']))

    def test_coordinator_consumes_promoted_synonym_home_airport_rules_and_templates(self):
        cat = deepcopy(self.catalog)
        cat.synonyms = [*getattr(cat, 'synonyms', []), {
            'concept_code': 'FlightDelay', 'term': '航班不正常班次', 'term_type': 'feedback_promoted', 'weight': 1.5,
        }]
        delay_table = (self.catalog.metrics.get('dep_delay_count') or {}).get('base_table') or CORE
        cat.business_rules = [{
            'code': 'rule_delay_scope',
            'name': '延误规则绑定',
            'ruleType': 'validation',
            'domain': 'FIMS',
            'versionNo': 2,
            'status': 'PUBLISHED',
            'priority': 120,
            'description': '自动校验出港统计主表与航段',
            'outputMapping': {'applies_to': [delay_table]},
        }]
        cat.plan_templates = [{
            'code': 'tpl_delay_summary',
            'name': '延误指标简报模板',
            'planType': 'single_metric',
            'triggerConcepts': ['FlightDelay'],
            'requiredCaps': [],
            'responseTemplate': '{period} {metricName}为 {value}{unit}（共 {count} 条）。',
            'status': 'PUBLISHED',
        }]
        coord = Coordinator(cat)

        # 1. Promoted synonym resolves deterministically in Coordinator + attaches rule & template
        plan = coord.plan('2026-11-08出港航班不正常班次有多少', day_mode='OPERATIONAL')
        self.assertEqual(plan['metric'], 'dep_delay_count')
        self.assertEqual(plan['applied_rules'][0]['code'], 'rule_delay_scope')
        self.assertEqual(plan['plan_template']['code'], 'tpl_delay_summary')
        self.assertIn({'table': delay_table, 'col': 'airport_code', 'op': '=', 'value': 'WUH'}, plan['conditions'])

        # 2. Operational day transforms condition when finalized
        finalized = core._finalize_governance_plan('2026-11-08出港航班不正常班次有多少', deepcopy(plan), cat, day_mode='OPERATIONAL')
        self.assertEqual(finalized['day_mode'], 'OPERATIONAL')
        self.assertEqual(finalized['operational_window']['start'], '2026-11-08 05:00:00')
        self.assertEqual(finalized['operational_window']['end'], '2026-11-09 05:00:00')

        # 3. Deterministic responseTemplate rendering matches verified metric facts
        rendered = render_template_summary(
            cat, finalized,
            {'facts': [{'id': 'F1', 'name': '出港延误', 'metric': {'name': '出港延误架次', 'unit': '架次'}, 'values': {'total': 14, 'n': 14}}]},
            14,
        )
        self.assertEqual(rendered, '2026-11-08 出港延误架次为 14架次（共 14 条）。')

        # 4. Home airport disambiguates arrival + originCity + airport_code in Coordinator
        home_plan = coord.plan('2026-11-08从成都飞往武汉天河的计划航班有哪些')
        self.assertEqual(home_plan['subject'], CORE)
        home_cols = {c['col']: c['value'] for c in home_plan['conditions']}
        self.assertEqual(home_cols.get('direction'), 'ARR')
        self.assertEqual(home_cols.get('originCity'), '成都')
        self.assertEqual(home_cols.get('airport_code'), 'WUH')

        # 5. Capability gate blocks disabled/planned capabilities and surfaces alternatives
        cat_blocked = deepcopy(cat)
        cat_blocked.capabilities = [{
            'code': 'flight.delay_metric',
            'name': '延误专题指标',
            'domain': 'FIMS',
            'phase': 2,
            'status': 'PLANNED',
            'coverageJson': {'tables': [delay_table]},
            'fallbackMessage': '延误专题视图尚未开放生产接入',
            'alternatives': ['昨天靠桥率是多少？'],
        }]
        blocked = Coordinator(cat_blocked).plan('2026-11-08出港航班不正常班次有多少')
        self.assertEqual(blocked['outcome'], 'unsupported')
        self.assertIn('尚未开放', blocked['answer'])
        self.assertEqual(blocked['capability_gate']['alternatives'], ['昨天靠桥率是多少？'])
        with patch.object(core, 'current_catalog', return_value=cat_blocked):
            dry_blocked = ops_governance.dry_run_plan('2026-11-08出港航班不正常班次有多少')
        self.assertFalse(dry_blocked['resolved'])
        self.assertEqual(dry_blocked['outcome'], 'unsupported')
        self.assertEqual(dry_blocked['capabilityGate']['alternatives'], ['昨天靠桥率是多少？'])

    def test_rule_guardrail_injects_required_filters_safely(self):
        cat = deepcopy(self.catalog)
        cat.business_rules = [{
            'code': 'rule_正班约束',
            'name': '正班过滤护栏',
            'ruleType': 'scope',
            'domain': 'FLIGHT',
            'versionNo': 1,
            'status': 'PUBLISHED',
            'priority': 110,
            'outputMapping': {'applies_to': [CORE], 'required_filters': {'flight_task_cd': 'W/Z'}},
        }]
        plan = {'subject': CORE, 'intent': 'detail', 'conditions': []}
        matched = apply_rule_guardrails(cat, plan)
        self.assertEqual(len(matched), 1)
        self.assertIn({'table': CORE, 'col': 'flight_task_cd', 'op': '=', 'value': 'W/Z'}, plan['conditions'])
        self.assertEqual(plan['applied_rules'][0]['code'], 'rule_正班约束')


class OpsGovernanceApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from app import business_access as business
        self.business = business
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AdminStore(self.temp.name, INITIAL)
        self.accounts = {}

        def lookup(conn, user_id):
            acc = self.accounts.get(str(user_id))
            if acc is None:
                raise HTTPException(401, '账号不存在')
            return acc

        def login(username, password, ip):
            if password != PASSWORD:
                raise HTTPException(401, '密码错误')
            acc = next(a for a in self.accounts.values() if a.username == username)
            tok = 'tok-' + username
            with self.store.db() as db:
                db.execute('INSERT INTO business_credentials VALUES (?,?,?)', (digest(tok), acc.user_id, 9e9))
            return tok, acc.profile()

        for m in (
            patch.object(service, 'get_store', return_value=self.store),
            patch.object(core, 'connect_db', return_value=SimpleNamespace(close=lambda: None)),
            patch.object(business, 'load_access', side_effect=lookup),
            patch.object(business, 'login', side_effect=login),
        ):
            m.start()
            self.addCleanup(m.stop)

    async def login(self, role='admin'):
        acc = self.business.Access('u-' + role, role, '用户-' + role, 'ALL', (role,), None, None, frozenset(), 'rev-1', role_names=(role,))
        self.accounts[acc.user_id] = acc
        _, headers, _ = await http('/api/auth/login', 'POST', {'username': role, 'password': PASSWORD})
        return headers['set-cookie'].split(';', 1)[0]

    async def test_governance_routes_require_admin_and_same_origin(self):
        self.assertEqual((await http('/api/admin/metrics'))[0], 401)
        self.assertEqual((await http('/api/admin/feedback'))[0], 401)
        self.assertEqual((await http('/api/ask/feedback', 'POST', {}))[0], 401)

        user_cookie = await self.login('jtgly')
        for path, method in (
            ('/api/admin/ontology-catalog', 'GET'),
            ('/api/admin/model/overview', 'GET'),
            ('/api/admin/metrics', 'GET'),
            ('/api/admin/rules', 'GET'),
            ('/api/admin/capabilities', 'GET'),
            ('/api/admin/planner/dry-run', 'POST'),
            ('/api/admin/ask-settings/home-airport', 'PUT'),
            ('/api/admin/feedback', 'GET'),
        ):
            self.assertEqual((await http(path, method, {}, cookie=user_cookie))[0], 403)

        admin_cookie = await self.login('admin')
        self.assertEqual((await http('/api/admin/metrics', 'POST', {}, cookie=admin_cookie, origin='https://evil.invalid'))[0], 403)

    async def test_feedback_submission_triage_and_synonym_promotion(self):
        user_cookie = await self.login('jtgly')
        # Non-admin users are blocked under ops_ask mode and allowed under hbairport mode
        with patch.object(business_access, 'settings', replace(business_access.settings, data_source='ops_ask')):
            st_ops, _, _ = await http('/api/ask/feedback', 'POST', {'question_text': 'test'}, cookie=user_cookie)
        self.assertEqual(st_ops, 403)

        # Submit inaccurate feedback as logged-in business user under hbairport mode (same-origin)
        with patch.object(business_access, 'settings', replace(business_access.settings, data_source='hbairport')):
            st, _, created = await http(
                '/api/ask/feedback', 'POST',
                {
                    'conversation_id': 'c1',
                    'message_id': 'm1',
                    'question_text': '昨天天河走廊桥率怎样',
                    'answer_text': '未命中',
                    'reason_codes': ['WRONG_INTENT', 'WRONG_DATA'],
                    'comment': '应该识别为靠桥率',
                    'response_summary': {'intent': 'detail', 'sql': 'SELECT 1'},
                },
                cookie=user_cookie,
            )
        self.assertEqual(st, 200)
        fb_id = created['item']['id']
        self.assertEqual(created['item']['severity'], 'HIGH')

        admin_cookie = await self.login('admin')
        st_list, _, listed = await http('/api/admin/feedback?status=OPEN&severity=HIGH', cookie=admin_cookie)
        self.assertEqual(st_list, 200)
        self.assertEqual(listed['counts']['OPEN'], 1)
        self.assertEqual(len(listed['items']), 1)

        fake_doc = {'entities': {}, 'logical_links': [], 'relationship_hints': [], 'concepts': {}, 'synonyms': [], 'plan_templates': []}
        fake_state = {'revision': 'rev-a', 'document': deepcopy(fake_doc)}
        with patch.object(ops_governance, '_has_pg_governance', return_value=False), \
             patch.object(core, 'ontology_document', return_value=fake_state), \
             patch.object(core, 'update_ontology', return_value={'revision': 'rev-b'}) as mock_upd:
            st_prom, _, prom = await http(
                f'/api/admin/feedback/{fb_id}/promote-synonym',
                'POST',
                {'conceptCode': 'BridgeStandRate', 'term': '廊桥率', 'adminNote': '已纳入同义词'},
                cookie=admin_cookie,
            )
            self.assertEqual(st_prom, 200)
            self.assertEqual(prom['feedback']['status'], 'FIXED')
            self.assertTrue(mock_upd.called)
            updated_doc = mock_upd.call_args[0][0]
            self.assertEqual(updated_doc['synonyms'][-1]['term'], '廊桥率')
            self.assertEqual(self.store.audit_list()['items'][0]['action'], 'feedback.promote')

            st_tpl, _, saved_tpl = await http(
                '/api/admin/planner/templates',
                'POST',
                {'code': 'tpl_demo', 'name': '示例模板', 'planType': 'single_metric', 'triggerConcepts': ['BridgeStandRate']},
                cookie=admin_cookie,
            )
            self.assertEqual(st_tpl, 200)
            self.assertEqual(saved_tpl['item']['code'], 'tpl_demo')


if __name__ == '__main__':
    unittest.main()
