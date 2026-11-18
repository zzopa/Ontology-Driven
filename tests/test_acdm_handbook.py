"""Regression and calibration tests for A-CDM Handbook (metrics, 11 dims, Planner, Compiler, Reports, API)."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app import acdm_reports, core
from app.agent.visual import build_visual_meta
from app.config import load_settings
from app.ontology import Catalog
from app.planner import Planner
from app.query import Compiler
from test_admin import http


def _load_ops_catalog():
    with patch.dict(os.environ, {'HBASK_DATA_SOURCE': 'ops_ask'}):
        settings = load_settings()
    schema = json.loads(settings.schema_path.read_text(encoding='utf-8'))
    doc = json.loads(settings.ontology_path.read_text(encoding='utf-8'))
    catalog = Catalog(schema, doc, settings)
    return settings, schema, doc, catalog


class AcdmHandbookOntologyAndSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings, cls.schema, cls.doc, cls.catalog = _load_ops_catalog()
        cls.planner = Planner(cls.catalog)
        cls.compiler = Compiler(cls.catalog)

    def test_handbook_entities_metrics_and_dimensions_registered_without_warnings(self):
        self.assertEqual(self.catalog.warnings, [])
        for entity in (
            'mv_acdm_dyn_flight_detail',
            'mv_acdm_turnaround_pair',
            'ops_ask_views.flight_hourly_movements',
            'std_flight_calc_total',
            'std_flight_air_bridge',
            'std_flight_safeguard_node',
            'dim_air_bridge',
            'dim_safeguard_node',
            'dim_runway',
        ):
            self.assertIn(entity, self.catalog.entities)

        self.assertEqual(self.doc['entities']['std_flight_production']['metrics'], {})

        avail = [k for k, v in self.catalog.metrics.items() if v.get('availability') == 'AVAILABLE']
        planned = [k for k, v in self.catalog.metrics.items() if v.get('availability') == 'PLANNED']
        self.assertGreaterEqual(len(avail), 58)
        self.assertGreaterEqual(len(planned), 8)

        cargo_expr = self.catalog.metrics['cargo_mail_sum']['aggregate_expression'].lower()
        self.assertIn('fpa_goods', cargo_expr)
        self.assertIn('fpa_mail', cargo_expr)
        self.assertNotIn('fpa_luggage', cargo_expr)

        peak_expr = self.catalog.dimensions['peak_hour']['expression']
        self.assertIn('c.peak_hour', peak_expr)
        self.assertIn('YYYY-MM-DD', peak_expr)
        self.assertNotIn('%', peak_expr)

    def test_planner_slots_topn_and_sql_compilation(self):
        p_ground, _ = self.planner.parse('2026-11-10 地服起飞正常率是多少？')
        self.assertEqual(p_ground['metric'], 'ground_takeoff_on_time_rate')
        stmt_g = self.compiler.aggregate(p_ground)
        self.assertIn("agency = '210'", stmt_g.sql)
        self.assertIn("attribute = '2403'", stmt_g.sql)
        self.assertIn('mv_acdm_dyn_flight_detail', stmt_g.sql)

        p_ac, _ = self.planner.parse('2026-11-10 B738 国际出港起飞正常率是多少？')
        self.assertEqual(p_ac['metric'], 'takeoff_on_time_rate')
        cols_vals = {c['col']: c['value'] for c in p_ac['conditions']}
        self.assertEqual(cols_vals.get('craft_type_icao_cd'), 'B738')
        self.assertNotIn('flight_no', cols_vals)
        self.assertEqual(cols_vals.get('attribute'), '2401')
        self.assertEqual(cols_vals.get('direction'), 'DEP')

        p_turn, _ = self.planner.parse('2026-11-10 重点机型过站不足45分钟有多少班？')
        self.assertEqual(p_turn['metric'], 'key_short_turnaround_count')
        self.assertEqual(p_turn['group_by'], [])
        stmt_t = self.compiler.aggregate(p_turn)
        self.assertIn('mv_acdm_turnaround_pair', stmt_t.sql)
        self.assertIn('sched_turnaround_minutes < 45', stmt_t.sql)

        p_top, _ = self.planner.parse('2026-11-10 各航司放行正常率前5名')
        self.assertEqual(p_top['metric'], 'release_on_time_rate')
        self.assertEqual(p_top['group_by'], ['airline'])
        self.assertEqual(p_top.get('limit'), 5)
        stmt_top = self.compiler.aggregate(p_top)
        self.assertTrue(stmt_top.sql.endswith('LIMIT %s'))
        self.assertEqual(stmt_top.params[-1], 5)

        p_peak_dist, _ = self.planner.parse('2026-11-10 高峰小时起降架次分布')
        self.assertEqual(p_peak_dist['metric'], 'peak_hour_movements')
        self.assertEqual(p_peak_dist['group_by'], ['peak_hour'])
        stmt_pk = self.compiler.aggregate(p_peak_dist)
        self.assertIn('flight_hourly_movements', stmt_pk.sql)
        self.assertIn("TO_CHAR(c.peak_hour, 'YYYY-MM-DD HH24:MI')", stmt_pk.sql)

        p_peak_max, _ = self.planner.parse('2026-11-10 哪个小时起降架次最多？')
        self.assertEqual(p_peak_max['metric'], 'peak_hour_movements')
        self.assertEqual(p_peak_max['group_by'], ['peak_hour'])
        self.assertEqual(p_peak_max.get('limit'), 1)
        self.assertEqual(p_peak_max.get('sort_dir'), 'desc')
        stmt_pkmax = self.compiler.aggregate(p_peak_max)
        self.assertIn('DESC NULLS LAST', stmt_pkmax.sql)
        self.assertTrue(stmt_pkmax.sql.endswith('LIMIT %s'))

        p_reason, _ = self.planner.parse('2026-11-10 延误原因分布')
        self.assertIn('delay_reason', p_reason['group_by'])
        stmt_rsn = self.compiler.aggregate(p_reason)
        self.assertIn('std_flight_indicator', stmt_rsn.sql)

        vis = build_visual_meta(
            p_ground,
            [{'ground_takeoff_on_time_rate': 95.52, 'n': 237}],
            main_total=1,
            metric=self.catalog.metrics['ground_takeoff_on_time_rate'],
            catalog=self.catalog,
        )
        self.assertEqual(vis['kind'], 'stats')
        self.assertEqual(vis['stats'][0]['value'], '95.52')
        self.assertEqual(vis['stats'][0]['unit'], '%')

    def test_temporal_groups_follow_each_metric_date_including_operational_days(self):
        from app.agent.operational_day import apply_operational_day_to_plan
        for code, field in [('operated_flight_count', 'real_exec_day'), ('planned_flight_count', 'plan_day'),
                            ('flight_execution_rate', 'calendar_day')]:
            metric = self.catalog.metrics[code]
            plan = {'subject': metric['base_table'], 'intent': 'metric', 'metric': code, 'group_by': ['date'],
                    'conditions': [{'col': field, 'op': '>=', 'value': '2026-11-09'},
                                   {'col': field, 'op': '<', 'value': '2026-11-10'}], 'focus_tables': []}
            with self.subTest(metric=code):
                stmt = self.compiler.aggregate(plan)
                self.assertIn('c."' + field + '"::date', stmt.sql)
                op = apply_operational_day_to_plan(plan, self.catalog, day_mode='OPERATIONAL')
                stmt = self.compiler.aggregate(op)
                self.assertIn("AT TIME ZONE 'Asia/Shanghai'", stmt.sql)
                self.assertIn("hours'", stmt.sql)

    def test_actual_flight_counts_use_actual_beijing_date_and_keep_plan_date_separate(self):
        from app.agent.coordinator import Coordinator
        coordinator = Coordinator(self.catalog)
        for question, metric in [('2026-11-09实际执行航班架次', 'operated_flight_count'),
                                 ('2026-11-09出港执飞架次', 'dep_flight_count'),
                                 ('2026-11-09进港执飞架次', 'arr_flight_count')]:
            self.assertEqual(self.catalog.metrics[metric]['date_field'], 'real_exec_day')
            for plan in [coordinator.plan(question), self.planner.parse(question)[0]]:
                self.assertEqual(plan['metric'], metric)
                dates = [c for c in plan['conditions'] if c['op'] in ('>=', '<', 'prefix')]
                self.assertTrue(dates)
                self.assertEqual({c['col'] for c in dates}, {'real_exec_day'})
                self.assertIn('real_exec_day', self.compiler.aggregate(plan).sql)
                self.assertFalse(any(c['col'] in ('destCity', 'originCity') for c in plan['conditions']))
        self.assertEqual(self.catalog.metrics['planned_flight_count']['date_field'], 'plan_day')

    def test_acdm_report_intent_and_planned_capability_boundary(self):
        from app.agent.coordinator import Coordinator
        coord = Coordinator(self.catalog)
        for q, expected_kind in [
            ('生成 2026-11-10 A-CDM 通用报告', 'general'),
            ('生成 2026-11-10 地服公司运行简报', 'ground_briefing'),
            ('近7天保障节点录入排行与登机桥周转', 'safeguard_board'),
        ]:
            plan, _ = self.planner.parse(q)
            self.assertEqual(plan['intent'], 'acdm_report')
            self.assertEqual(plan['report_kind'], expected_kind)
            c_plan = coord.plan(q)
            self.assertEqual(c_plan['intent'], 'acdm_report')
            self.assertEqual(c_plan['report_kind'], expected_kind)

        # Coordinator handles metric group_by + TopN + peak hour + short delay offline cleanly
        c_top = coord.plan('昨天各航司放行正常率前5名')
        self.assertEqual(c_top['metric'], 'release_on_time_rate')
        self.assertEqual(c_top['group_by'], ['airline'])
        self.assertEqual(c_top.get('limit'), 5)
        c_peak = coord.plan('昨天哪个小时起降架次最多？')
        self.assertEqual(c_peak['metric'], 'peak_hour_movements')
        self.assertEqual(c_peak['group_by'], ['peak_hour'])
        self.assertEqual(c_peak.get('limit'), 1)

        with patch.object(core, 'current_catalog', return_value=self.catalog), \
             patch.object(core, 'CATALOG', self.catalog):
            with patch.object(core, 'settings', replace(core.settings, coordinator_enabled=False)):
                with self.assertRaises(ValueError) as ctx:
                    core.do_ask(MagicMock(), '2026-11-10 临界干预放行成功架次是多少？')
                self.assertIn('PLANNED', str(ctx.exception))
                self.assertIn('临界', str(ctx.exception))
            with patch.object(core, 'settings', replace(core.settings, coordinator_enabled=True, data_source='ops_ask')):
                res = core.do_ask(MagicMock(), '2026-11-10 临界干预放行成功架次是多少？')
                self.assertEqual(res['intent'], 'notice')
                self.assertEqual(res['outcome'], 'unsupported')
                self.assertIn('PLANNED', res['answer'])
                self.assertIn('临界', res['answer'])


class _FakeReportCursor:
    """Regex-backed cursor generating exact alias columns for acdm_reports SQL templates."""

    def __init__(self):
        self.description = []
        self._one = None
        self._all = []

    def execute(self, sql, params=()):
        import re
        s = ' '.join(str(sql).split())
        counts_select = re.search(r'\bcounts AS \(\s*SELECT\b', s, re.I)
        m_start = counts_select or re.search(r'\bSELECT\b', s, re.I)
        aliases = []
        if m_start:
            # Split top-level (depth==0) SELECT columns until top-level FROM
            pos = m_start.end()
            parts, cur, depth = [], [], 0
            i = pos
            while i < len(s):
                ch = s[i]
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth = max(0, depth - 1)
                if depth == 0 and s[i:i + 5].upper() == ' FROM':
                    break
                if ch == ',' and depth == 0:
                    parts.append(''.join(cur).strip())
                    cur = []
                else:
                    cur.append(ch)
                i += 1
            if cur:
                parts.append(''.join(cur).strip())
            for p in parts:
                m_as = re.search(r'\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$', p, re.I)
                if m_as:
                    aliases.append(m_as.group(1))
                else:
                    m_col = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*$', p)
                    if m_col:
                        aliases.append(m_col.group(1))
        if counts_select:
            aliases.extend(('eligible_flights', 'entry_rate_pct'))
        if 'AS start_date' in s and 'start_date' in aliases:
            self.description = [(c,) for c in aliases]
            row = ('2026-10-12', '2026-11-12', 15599)
            self._all = [row]
            self._one = row
            return
        if not aliases:
            aliases = ['val']
        self.description = [(c,) for c in aliases]
        vals = []
        for col in aliases:
            cl = col.lower()
            if 'bridge_duration_min' in cl or 'avg_' in cl or 'minutes' in cl:
                vals.append(58.2)
            elif cl.endswith('_rate') or '_rate_' in cl or cl.endswith('_pct') or 'factor' in cl or 'share' in cl:
                vals.append(95.4)
            elif 'hour' in cl:
                vals.append(8)
            elif 'operated_all' in cl or 'planned_today' in cl:
                vals.append(476)
            elif 'info' in cl or 'name' in cl or 'code' in cl or 'flight_no' in cl or 'period' in cl or 'reason' in cl or 'craft_type' in cl or 'site_cd' in cl or 'direction' in cl or 'agency' in cl or 'attribute' in cl or 'is_off_in' in cl or 'label' in cl or 'iata' in cl or 'craft_no' in cl:
                vals.append('B-1234')
            elif cl.endswith('_day') or cl.endswith('_date') or cl in ('exec_date', 'flight_date', 'movement_date'):
                vals.append('2026-11-10')
            elif ('time' in cl and 'ontime' not in cl and 'on_time' not in cl) or cl in ('arr_t', 'dep_t', 'plan_t', 'takeoff_t'):
                vals.append('09:30')
            elif 'remote' in cl or 'ori_act' in cl:
                vals.append('Y')
            else:
                vals.append(12)
        row = tuple(vals)
        self._all = [row]
        self._one = row

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all

    def close(self):
        return None


class _FakeConn:
    def cursor(self):
        return _FakeReportCursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class AcdmReportsAndApiTests(unittest.IsolatedAsyncioTestCase):
    def test_ground_briefing_shows_both_rate_numerators_and_denominators(self):
        q_one = acdm_reports._q_one
        counts = {'all_dep_22': 238, 'all_release_norm_22': 233, 'all_takeoff_norm_22': 229,
                  'gh_dep_22': 66, 'gh_release_norm_22': 64, 'gh_takeoff_norm_22': 62, 'gh_release_delay_22': 2}

        def read_one(cur, sql, params=()):
            return dict(counts) if 'AS all_dep_22' in sql else q_one(cur, sql, params)

        with patch.object(acdm_reports, '_q_one', side_effect=read_one):
            report = acdm_reports.build_ground_briefing(_FakeConn(), target_date=date(2026, 11, 9))
        for text in ('97.90%（分子 233，分母 238）', '96.22%（分子 229，分母 238）',
                     '96.97%（分子 64，分母 66）', '93.94%（分子 62，分母 66）'):
            self.assertIn(text, report['text'])

    def test_three_special_reports_render_complete_sections_and_unit_conversions(self):
        conn = _FakeConn()
        gen = acdm_reports.build_general_report(conn, period_type='day', start_date=date(2026, 11, 10), end_date=date(2026, 11, 10))
        self.assertEqual(gen['kind'], 'general')
        self.assertGreaterEqual(len(gen['document']['sections']), 5)
        self.assertEqual(gen['visual_meta']['stats'][0]['value'], '476')

        gb = acdm_reports.build_ground_briefing(conn, target_date=date(2026, 11, 10))
        self.assertEqual(gb['kind'], 'ground_briefing')
        self.assertEqual(len(gb['document']['sections']), 4)
        self.assertIn('外场', gb['text'])

        sb = acdm_reports.build_safeguard_and_resource_board(conn, start_date=date(2026, 11, 4), end_date=date(2026, 11, 10))
        self.assertEqual(sb['kind'], 'safeguard_board')
        self.assertEqual(len(sb['related']), 3)
        self.assertEqual(sb['related'][1]['rows'][0]['avg_bridge_duration_min'], 58.2)

        # End-to-end core.do_ask must build evidence & field_meta cleanly for acdm_report synthetic related tables
        _, _, _, cat = _load_ops_catalog()
        with patch.object(core, 'current_catalog', return_value=cat), \
             patch.object(core, 'CATALOG', cat):
            out = core.do_ask(conn, '生成 2026-11-10 A-CDM 通用报告')
            self.assertEqual(out['intent'], 'acdm_report')
            self.assertEqual(out['visual_meta']['stats'][0]['value'], '476')
            self.assertEqual(len(out['related']), 3)

    async def test_acdm_report_http_endpoints_enforce_admin_auth_and_return_data(self):
        from app import main as app_main

        with patch.object(app_main, 'with_connection', side_effect=lambda fn, *a, **kw: fn(_FakeConn())):
            st_anon, _, _ = await http('/api/acdm/reports/general?start_date=2026-11-10')
            self.assertIn(st_anon, (401, 403))

            with patch.object(app_main, 'require_role', return_value=SimpleNamespace(username='admin', role='admin')):
                st1, _, r1 = await http('/api/acdm/reports/general?period_type=day&start_date=2026-11-10')
                st2, _, r2 = await http('/api/acdm/reports/ground-briefing?target_date=2026-11-10')
                st3, _, r3 = await http('/api/acdm/reports/safeguard-board?start_date=2026-11-04&end_date=2026-11-10')
            self.assertEqual(st1, 200)
            self.assertEqual(r1['report']['kind'], 'general')
            self.assertEqual(st2, 200)
            self.assertEqual(r2['report']['kind'], 'ground_briefing')
            self.assertEqual(st3, 200)
            self.assertEqual(r3['report']['kind'], 'safeguard_board')
