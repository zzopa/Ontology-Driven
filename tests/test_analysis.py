from datetime import date, datetime, timedelta
import json
import unittest
from unittest.mock import Mock, patch

from app.agent.analysis import calculate_comparison, comparison_window, numeric_value, run, windows
from app.agent.coordinator import Coordinator, BEIJING
from app.agent.forecast import make_plan, predict_series, run_forecast
from app.config import load_settings
from app.execution import ExecutionTrace
from app.ontology import Catalog
from app.query import Compiler


def catalog():
    with patch.dict('os.environ', {'HBASK_DATA_SOURCE': 'ops_ask'}):
        s = load_settings()
    return Catalog(json.loads(s.schema_path.read_text(encoding='utf-8')),
                   json.loads(s.ontology_path.read_text(encoding='utf-8')), s)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.catalog = catalog()
        self.coordinator = Coordinator(self.catalog)

    def test_each_metric_keeps_all_common_filters(self):
        p = self.coordinator.plan('2026-11-10出港计划航班量和执行航班量分别多少')
        self.assertEqual(p['intent'], 'analysis')
        self.assertEqual(len(p['queries']), 2)
        for q in p['queries']:
            self.assertTrue(any(c.get('value') in ('D', 'DEP') for c in q['plan']['conditions']))
            Compiler(self.catalog).validate(q['plan'])

    def test_route_grouping_keeps_the_requested_metrics(self):
        question = ('2026-11-02至2026-11-15，按航线统计旅客吞吐量、货邮吞吐量、'
                    '实际执行航班架次和过站平均保障时长，分别是多少？')
        plan = self.coordinator.plan(question)
        self.assertEqual(plan.get('intent'), 'analysis', plan.get('answer'))
        children = [query['plan'] for query in plan['queries']]
        self.assertEqual([child['metric'] for child in children], [
            'pax_total_sum', 'cargo_mail_sum', 'operated_flight_count', 'avg_turnaround_service_minutes',
        ])
        compiler = Compiler(self.catalog)
        for child in children:
            self.assertEqual(child['group_by'], ['origin_airport', 'dest_airport'])
            group_sql = compiler.aggregate(child).sql.split(' GROUP BY ', 1)[1]
            self.assertIn('c.origin_airport', group_sql)
            self.assertIn('c.dest_airport', group_sql)
            self.assertEqual({c['col'] for c in child['conditions']}, {
                'airport_code', self.catalog.metrics[child['metric']]['date_field'],
            })
        fpa = self.coordinator.plan('2026-11-02至2026-11-15航线统计旅客')
        self.assertEqual(fpa.get('metric'), 'pax_fpa_sum')
        self.assertEqual(fpa.get('group_by'), [])

    def test_month_and_year_windows_in_analysis_and_followup(self):
        with patch('app.agent.coordinator.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 18, 16, tzinfo=BEIJING)
            for phrase, start in (('本月', '2026-11-01'), ('一个月', '2026-10-20'),
                                  ('本年', '2026-01-01'), ('一年', '2025-11-19')):
                with self.subTest(phrase=phrase):
                    plan = self.coordinator.plan(phrase + '计划航班量和实际执行航班量各是多少')
                    self.assertEqual(plan.get('intent'), 'analysis', plan.get('answer'))
                    self.assertEqual([q['period'] for q in plan['queries']], [[start, '2026-11-19']] * 2)
            followup = self.coordinator.plan('那一个月呢？', previous_plan=plan)
            self.assertEqual(followup.get('intent'), 'analysis', followup.get('answer'))
            self.assertEqual([q['period'] for q in followup['queries']], [['2026-10-20', '2026-11-19']] * 2)
            rolling = self.coordinator.plan('过去一年实际执行航班量环比')
            self.assertEqual(rolling.get('comparison_mode'), 'mom', rolling.get('answer'))
            self.assertEqual([q['period'] for q in rolling['queries']],
                             [['2025-11-18', '2026-11-18'], ['2024-11-18', '2025-11-18']])
            board = self.coordinator.plan('生成本年A-CDM通用报告')
            self.assertEqual(board.get('intent'), 'acdm_report', board.get('answer'))
            self.assertEqual((board['report_start'], board['report_end']), ('2026-01-01', '2026-11-18'))

    def test_month_and_year_windows_across_leap_day(self):
        anchor = datetime(2024, 2, 29, tzinfo=BEIJING)
        self.assertEqual(windows('本月截至今天', anchor), ([('2024-02-01', '2024-03-01')], 'month'))
        self.assertEqual(windows('一个月', anchor), ([('2024-01-31', '2024-03-01')], 'range'))
        self.assertEqual(windows('本年', anchor), ([('2024-01-01', '2024-03-01')], 'year'))
        self.assertEqual(windows('一年', anchor), ([('2023-03-02', '2024-03-01')], 'range'))
        for text in ('一年后', '超过一年', '一年以上', '每年', '上一年'):
            with self.subTest(text=text):
                self.assertEqual(windows(text, anchor)[0], [])

    def test_unknown_filter_is_not_dropped(self):
        p = self.coordinator.plan('2026-11-10计划量和执行量排除神秘航班状态XYZ')
        self.assertEqual(p['outcome'], 'clarification_needed')

    def test_bridge_usage_and_average_share_the_inclusive_start_date_range(self):
        for text in ('登机桥使用多少次、平均使用时长多少分钟？',
                     '廊桥的使用次数和平均使用时长是几分钟？'):
            with self.subTest(text=text):
                plan = self.coordinator.plan(f'2026-11-02至2026-11-15，{text}')
                self.assertEqual(plan.get('intent'), 'analysis', plan.get('answer'))
                children = [q['plan'] for q in plan['queries']]
                self.assertEqual([p['metric'] for p in children], ['air_bridge_usage_count', 'avg_air_bridge_minutes'])
                for p in children:
                    self.assertEqual(p['subject'], 'std_flight_air_bridge')
                    self.assertEqual([(c['col'], c['op'], c['value'][:10]) for c in p['conditions']],
                                     [('bridge_start_time', '>=', '2026-11-02'), ('bridge_start_time', '<', '2026-11-16')])
                    self.assertIn('bridge_start_time', Compiler(self.catalog).aggregate(p).sql)

    def test_bridge_usage_reasked_after_unsuccessful_turn(self):
        question = '2026-11-02至2026-11-15，登机桥使用多少次、平均使用时长多少分钟？'
        anchor = datetime(2026, 11, 18, tzinfo=BEIJING)
        pending = self.coordinator.notice('clarification_needed', '请明确业务对象', question, anchor, {})
        plan = self.coordinator.plan(question, clarification=pending['clarification'])
        self.assertEqual(plan.get('intent'), 'analysis', plan.get('answer'))

    def test_turnaround_nodes_share_average_duration_and_arrival_date_range(self):
        question = '2026-11-02至2026-11-15，过站航班平均登机、清洁、配餐、加油时长分别是多少？'
        plan = self.coordinator.plan(question)
        self.assertEqual(plan.get('intent'), 'analysis', plan.get('answer'))
        children = [q['plan'] for q in plan['queries']]
        self.assertEqual([p['metric'] for p in children], [
            'avg_turnaround_board_minutes', 'avg_turnaround_clean_minutes',
            'avg_turnaround_meal_minutes', 'avg_turnaround_supply_minutes',
        ])
        for child in children:
            self.assertEqual(child['subject'], 'mv_acdm_turnaround_pair')
            self.assertEqual([(c['op'], c['value']) for c in child['conditions'] if c['col'] == 'arr_landin_day'],
                             [('>=', '2026-11-02'), ('<', '2026-11-16')])
            Compiler(self.catalog).aggregate(child)

    def test_bridge_usage_keeps_unresolved_scope(self):
        for text in ('T2登机桥使用多少次、平均使用时长多少分钟',
                     '登机桥使用多少次、机位平均使用时长多少分钟'):
            with self.subTest(text=text):
                plan = self.coordinator.plan(f'2026-11-02至2026-11-15，{text}')
                self.assertEqual(plan.get('outcome'), 'clarification_needed')

    def test_peer_filters_cannot_be_silently_ignored(self):
        p = self.coordinator.plan('2026-11-10长沙和南京T2航班量对比')
        self.assertEqual(p['outcome'], 'clarification_needed')

    def test_airport_benchmark_understands_execution_volume_and_source_request(self):
        question = '对比2026-11-02至2026-11-15武汉、长沙、南京、郑州的执行量和客座率，并说明各机场数据来源。'
        for phrasing in (question, question.replace('说明各机场数据来源', '注明每个机场的数据出处')):
            with self.subTest(phrasing=phrasing):
                plan = self.coordinator.plan(phrasing)
                self.assertEqual(plan.get('intent'), 'analysis', plan.get('answer'))
                self.assertTrue(plan['airport_benchmark'])
                self.assertEqual(len(plan['queries']), 8)
                self.assertEqual({q['metric_key'] for q in plan['queries']}, {'benchmark_flight_count', 'benchmark_load_factor'})
                self.assertEqual({q['object'] for q in plan['queries']}, {'WUH', 'CSX', 'NKG', 'CGO'})
        result = self.coordinator.plan(question.rstrip('。') + '，只用真实数据')
        self.assertEqual(result['outcome'], 'clarification_needed')

    def test_route_is_not_misread_as_airport_benchmark(self):
        p = self.coordinator.plan('2026-11-10武汉到长沙实际执行航班量')
        self.assertNotEqual(p.get('intent'), 'analysis')

    def test_rolling_comparison_keeps_two_full_weeks(self):
        p = self.coordinator.plan('最近两周执行量环比')
        for child in p['queries']:
            self.assertEqual((date.fromisoformat(child['period'][1]) - date.fromisoformat(child['period'][0])).days, 14)

    def test_peer_queries_preserve_simulation_fields(self):
        p = self.coordinator.plan('11月1日至15日武汉长沙南京郑州航班量和客座率对比')
        self.assertEqual(p['intent'], 'analysis')
        self.assertEqual(len(p['queries']), 8)
        for q in p['queries']:
            self.assertEqual(q['period'], ['2026-11-01', '2026-11-16'])
            if q['object'] != 'WUH':
                self.assertIn('is_simulated', q['plan']['group_by'])
                sql = Compiler(self.catalog).aggregate(q['plan'])
                self.assertNotIn(q['object'], sql.sql)
                self.assertIn(q['object'], sql.params)

    def test_query_expansion_is_bounded(self):
        p = self.coordinator.plan('2026-11-10武汉长沙南京郑州航班量、旅客吞吐量、客座率、国内执行率对比')
        self.assertEqual(p['outcome'], 'clarification_needed')
        self.assertIn('12', p['answer'])

    def test_comparison_math_zero_and_percentage_points(self):
        self.assertEqual(calculate_comparison(93, 90, '%')['difference_unit'], '百分点')
        self.assertEqual(calculate_comparison(10, 0, '架次')['growth_pct'], None)
        self.assertEqual(calculate_comparison(None, 9, '架次')['difference'], None)
        self.assertEqual(calculate_comparison(80, 100, '架次')['growth_pct'], -20)
        self.assertEqual(numeric_value({'outcome': 'answered', 'metric': {'field': 'x'}, 'main_rows': [{'x': '82.54'}]}), 82.54)

    def test_explicit_date_comparison_uses_later_period_unless_direction_is_stated(self):
        for phrasing, expected in (
            ('对比2026-11-09与2026-11-08实际执行航班量，列出两天数值、差值和增长率。', ('2026-11-09', '2026-11-08')),
            ('2026-11-08与2026-11-09实际执行航班量对比', ('2026-11-09', '2026-11-08')),
            ('2026-11-08比2026-11-09实际执行航班量多多少架次，增幅是多少？', ('2026-11-08', '2026-11-09')),
        ):
            with self.subTest(phrasing=phrasing):
                plan = self.coordinator.plan(phrasing)
                self.assertEqual(plan.get('comparison_mode'), 'explicit', plan.get('answer'))
                self.assertEqual(tuple(q['period'][0] for q in plan['queries']), expected)
                self.assertTrue(all(q['plan']['metric'] == 'operated_flight_count' for q in plan['queries']))

    def test_comparison_keeps_direction_and_refuses_ambiguous_base(self):
        plan = self.coordinator.plan('对比2026-11-09与2026-11-08出港实际执行航班量，算差值和增长率')
        self.assertEqual(plan.get('comparison_mode'), 'explicit', plan.get('answer'))
        for child in plan['queries']:
            self.assertTrue(any(c['value'] in ('D', 'DEP') for c in child['plan']['conditions']))
        for prefix in ('2026-11-09', '2026-11-08、2026-11-09和2026-11-10'):
            result = self.coordinator.plan(f'{prefix}实际执行航班量的差值和增长率')
            self.assertEqual(result.get('outcome'), 'clarification_needed')
            self.assertIn('两个业务日期', result['answer'])

    def test_comparison_answer_keeps_values_and_does_not_calculate_from_partial_data(self):
        question = '2026-11-09与2026-11-08实际执行航班量对比'
        plan = self.coordinator.plan(question)
        def child(value, status='COMPLETE'):
            return {'outcome': 'answered', 'answer': str(value), 'main_rows': [{'total': value, 'n': value}],
                    'metric': {'field': 'total', 'unit': '架次'}, 'coverage_assessment': {'status': status}}
        for status, expected in [('COMPLETE', -8), ('PARTIAL', None)]:
            with self.subTest(status=status), patch('app.core.do_ask', side_effect=[child(506), child(514, status)]):
                result = run(Mock(), question, plan, self.catalog, ExecutionTrace())
                self.assertEqual([s['value'] for s in result['sections']], [506, 514])
                self.assertEqual(result['comparisons'][0]['difference'], expected)
                self.assertIn('差值 -8 架次，增长率 -1.56%' if expected is not None else '数据不足，无法计算', result['answer'])

    def test_leap_year_and_month_comparisons(self):
        self.assertEqual(comparison_window(('2024-02-29', '2024-03-01'), 'day', 'yoy'), ('2023-02-28', '2023-03-01'))
        self.assertEqual(comparison_window(('2026-11-01', '2026-12-01'), 'month', 'mom'), ('2026-10-01', '2026-11-01'))
        ws, kind = windows('上周', datetime(2026, 11, 16, tzinfo=BEIJING))
        self.assertEqual((ws, kind), ([('2026-11-09', '2026-11-16')], 'week'))

    def test_followup_targets_one_child_or_asks(self):
        p = self.coordinator.plan('2026-11-10计划量和执行量分别多少')
        result = self.coordinator.plan('明细', previous_plan=p)
        self.assertEqual(result['outcome'], 'clarification_needed')
        result = self.coordinator.plan('出港呢', previous_plan=p)
        self.assertEqual(result['intent'], 'analysis')
        self.assertEqual(len(result['queries']), 2)

    def test_registered_metric_daily_bucket_is_compiled(self):
        p = self.coordinator.plan('2026-11-10实际执行航班量')
        p['time_bucket'] = {'field': 'exec_date', 'grain': 'day'}
        sql = Compiler(self.catalog).aggregate(p)
        self.assertIn('AS __period', sql.sql)
        self.assertIn('ORDER BY __period ASC', sql.sql)

    def test_coordinated_directional_load_factor_expands_both_metrics(self):
        p = self.coordinator.plan('2026-11-02至2026-11-15，出港和进港客座率分别是多少？')
        self.assertEqual(p['intent'], 'analysis')
        self.assertEqual(len(p['queries']), 2)
        keys = [q['metric_key'] for q in p['queries']]
        self.assertEqual(keys, ['dep_load_factor', 'arr_load_factor'])
        self.assertEqual(p['queries'][0]['period'], ['2026-11-02', '2026-11-16'])
        self.assertEqual(p['queries'][1]['period'], ['2026-11-02', '2026-11-16'])

    def test_three_metric_punct_list_expands_cleanly(self):
        p = self.coordinator.plan('2026-11-02至2026-11-15，进港正常率、始发正常率、全量进出港靠桥率分别是多少？')
        self.assertEqual(p['intent'], 'analysis')
        self.assertEqual(len(p['queries']), 3)
        keys = [q['metric_key'] for q in p['queries']]
        self.assertEqual(keys, ['arr_on_time_rate', 'origin_dep_on_time_rate', 'bridge_stand_rate_all'])
        for child in p['queries']:
            self.assertEqual(child['period'], ['2026-11-02', '2026-11-16'])

    def test_delay_metrics_multi_query_without_fake_flight_count(self):
        p = self.coordinator.plan('2026-11-02至2026-11-15，出港延误、起飞延误、放行延误分别多少架次？')
        self.assertEqual(p['intent'], 'analysis')
        self.assertEqual(len(p['queries']), 3)
        keys = [q['metric_key'] for q in p['queries']]
        self.assertEqual(keys, ['dep_delay_count', 'takeoff_delay_count', 'release_delay_count'])
        for child in p['queries']:
            self.assertEqual(child['period'], ['2026-11-02', '2026-11-16'])

    def test_coordinated_delay_query(self):
        p = self.coordinator.plan('2026-11-02至2026-11-15，出港和进港延误分别是多少架次？')
        self.assertEqual(p['intent'], 'analysis')
        self.assertEqual(len(p['queries']), 2)
        keys = [q['metric_key'] for q in p['queries']]
        self.assertEqual(keys, ['dep_delay_count', 'arr_delay_count'])

    def test_both_directions_not_falsely_forced_to_departure(self):
        from app.planner import extract_home_airport_city_and_dir
        explicit_dir, _, _, _, _ = extract_home_airport_city_and_dir('出港和进港客座率分别是多少', self.catalog)
        self.assertIsNone(explicit_dir)

    def test_unhashable_metric_rejected_with_clean_value_error(self):
        plan = {
            'subject': 'ops_ask_views.flight_execution',
            'intent': 'metric',
            'metric': ['dep_load_factor', 'arr_load_factor'],
            'conditions': []
        }
        with self.assertRaises(ValueError) as ctx:
            self.coordinator.validate(plan, '出港和进港客座率', {}, datetime.now(BEIJING))
        self.assertIn('单次查询只能指定一个已发布的指标', str(ctx.exception))

    def test_followup_dep_detail_from_analysis(self):
        p = self.coordinator.plan('2026-11-02至2026-11-15，出港和进港客座率分别是多少？')
        result = self.coordinator.plan('出港明细', previous_plan=p)
        self.assertEqual(result.get('intent'), 'detail')
        dir_cond = next((c for c in result.get('conditions', []) if c.get('col') == 'direction'), None)
        self.assertIsNotNone(dir_cond)
        self.assertEqual(dir_cond['value'], 'DEP')

    def test_followup_arr_detail_from_analysis(self):
        p = self.coordinator.plan('2026-11-02至2026-11-15，出港和进港客座率分别是多少？')
        result = self.coordinator.plan('进港明细', previous_plan=p)
        self.assertEqual(result.get('intent'), 'detail')
        dir_cond = next((c for c in result.get('conditions', []) if c.get('col') == 'direction'), None)
        self.assertIsNotNone(dir_cond)
        self.assertEqual(dir_cond['value'], 'ARR')

    def test_followup_numbered_item_detail(self):
        p = self.coordinator.plan('2026-11-02至2026-11-15，出港和进港客座率分别是多少？')
        result = self.coordinator.plan('第二项明细', previous_plan=p)
        self.assertEqual(result.get('intent'), 'detail')
        dir_cond = next((c for c in result.get('conditions', []) if c.get('col') == 'direction'), None)
        self.assertIsNotNone(dir_cond)
        self.assertEqual(dir_cond['value'], 'ARR')

    def test_multi_metric_delay_detail_follow_up(self):
        p1 = self.coordinator.plan('2026-11-02至2026-11-15，出港延误、起飞延误、放行延误分别多少架次？')
        self.assertEqual(p1.get('intent'), 'analysis')
        self.assertEqual(len(p1.get('queries', [])), 3)

        # 1. Follow up on clearance delay detail
        p2_release = self.coordinator.plan('其中放行延误架次明细', previous_plan=p1)
        self.assertEqual(p2_release.get('intent'), 'detail')
        self.assertEqual(p2_release.get('subject'), 'mv_acdm_dyn_flight_detail')
        release_conds = {c['col']: c for c in p2_release.get('conditions', [])}
        self.assertIn('clearance_delay_time', release_conds)
        self.assertEqual(release_conds['clearance_delay_time']['op'], '>')
        self.assertEqual(release_conds['clearance_delay_time']['value'], 0)
        self.assertEqual(release_conds.get('is_off_in', {}).get('value'), 'D')

        # 2. Follow up on takeoff delay detail
        p2_takeoff = self.coordinator.plan('起飞延误明细', previous_plan=p1)
        self.assertEqual(p2_takeoff.get('intent'), 'detail')
        takeoff_conds = {c['col']: c for c in p2_takeoff.get('conditions', [])}
        self.assertIn('takeoff_delay_time', takeoff_conds)
        self.assertEqual(takeoff_conds['takeoff_delay_time']['op'], '>')

        # 3. Follow up on departure delay detail
        p2_dep = self.coordinator.plan('出港延误明细', previous_plan=p1)
        self.assertEqual(p2_dep.get('intent'), 'detail')
        dep_conds = {c['col']: c for c in p2_dep.get('conditions', [])}
        self.assertIn('dep_delay_time', dep_conds)
        self.assertEqual(dep_conds['dep_delay_time']['op'], '>')

    def test_detail_html_report_rendering(self):
        from app.reporting import html_report
        fake_result = {
            'intent': 'detail',
            'main_total': 102,
            'main_rows': [
                {'flight_no': 'WC2502', 'clearance_delay_time': 12, 'direction': 'DEP', 'flight_status': 'DEP'},
                {'flight_no': 'ZZMJC285', 'clearance_delay_time': 15, 'direction': 'DEP', 'flight_status': 'DEP'},
            ],
            'claims': [
                {'id': 'C1', 'text': 'A-CDM校准航班宽表视图命中 102 条记录。本次读取 20 / 102 条主记录。'},
                {'id': 'C2', 'text': '查询范围：执行业务日；仅反映本地已同步记录。'},
            ],
            'answer_document': {
                'title': '放行延误架次明细记录',
                'sections': [
                    {'title': '查询事实', 'kind': 'query_facts', 'claim_ids': ['C1']},
                    {'title': '证据边界', 'kind': 'limitations', 'claim_ids': ['C2']},
                ],
            },
        }
        html = html_report(fake_result, '放行延误明细')
        self.assertIn('WC2502', html)
        self.assertIn('ZZMJC285', html)
        self.assertIn('命中 102 条记录', html)
        self.assertIn('查询范围：执行业务日', html)
        self.assertIn('<table class="data-table">', html)

    def test_new_standalone_question_is_not_hijacked_by_prior_analysis_and_item_choice_does_not_loop(self):
        prior = self.coordinator.plan('2026-11-02至2026-11-15，登机桥使用多少次、平均使用时长多少分钟？')
        self.assertEqual(prior.get('intent'), 'analysis')
        self.assertEqual(len(prior['queries']), 2)

        fresh = self.coordinator.plan(
            '2026-11-02至2026-11-15，保障节点实测录入多少条？按节点列出记录数、去重航班数和适用航班覆盖率。',
            previous_plan=prior,
        )
        self.assertEqual(fresh.get('intent'), 'metric', fresh.get('answer'))
        self.assertEqual(fresh['metric'], 'safeguard_node_recorded_count')
        self.assertEqual(fresh['group_by'], ['safeguard_code'])
        statement = Compiler(self.catalog).aggregate(fresh)
        self.assertEqual(statement.params[-2:], ('2026-11-02T00:00:00+08:00', '2026-11-16T00:00:00+08:00'))
        self.assertIn('COUNT(DISTINCT s.flight_id) AS distinct_flights', statement.sql)
        self.assertIn('NULLIF(eligible_flights, 0)', statement.sql)
        self.assertNotIn('LIMIT', statement.sql)

        # 2. Legitimate detail follow-up asks item clarification, and clicking option or typing label resolves cleanly
        ask_detail = self.coordinator.plan('明细', previous_plan=prior)
        self.assertEqual(ask_detail.get('outcome'), 'clarification_needed')
        self.assertEqual(ask_detail['clarification']['slot'], 'item')

        clicked = self.coordinator.plan(
            '登机桥保障总次',
            previous_plan=prior,
            clarification=ask_detail['clarification'],
            choice_id='1',
        )
        self.assertIsNone(clicked.get('outcome'))
        self.assertEqual(clicked.get('intent'), 'detail')
        self.assertEqual(clicked.get('subject'), 'std_flight_air_bridge')

        typed = self.coordinator.plan(
            '平均接桥保障时长',
            previous_plan=prior,
            clarification=ask_detail['clarification'],
        )
        self.assertIsNone(typed.get('outcome'))
        self.assertEqual(typed.get('intent'), 'detail')
        self.assertEqual(typed.get('subject'), 'std_flight_air_bridge')

    def test_safeguard_entry_keeps_report_and_metric_scope_distinct(self):
        period = '2026-11-02至2026-11-15'
        total = self.coordinator.plan(period + '保障节点实测录入多少条')
        self.assertEqual(total.get('metric'), 'safeguard_node_recorded_count', total.get('answer'))
        self.assertEqual(total['group_by'], [])
        board = self.coordinator.plan(period + '保障节点质量与资源周转看板')
        self.assertEqual(board.get('report_kind'), 'safeguard_board', board.get('answer'))
        self.assertEqual(board['report_end'], '2026-11-15')
        filtered = self.coordinator.plan(period + '保障节点实测录入，按节点列出覆盖率，只看T2')
        self.assertEqual(filtered.get('outcome'), 'unsupported')


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2026, 10, 16)
        self.series = {self.start + timedelta(days=i): float(400 + (self.start + timedelta(days=i)).weekday() * 10) for i in range(31)}

    def test_explicit_cutoff_and_mixed_date_range_are_separate(self):
        question = '只使用截至2026-11-15的历史数据，预测2026-11-16至11月22日的执行量，说明回测误差和预测区间。'
        current_catalog = catalog()
        plan = make_plan(Coordinator(current_catalog), question, datetime(2026, 11, 18, tzinfo=BEIJING), 'CALENDAR')
        self.assertEqual((plan['history_end'], plan['target_start'], plan['horizon']), ('2026-11-16', '2026-11-16', 7))
        self.assertEqual(plan['base_plan']['metric'], 'operated_flight_count')
        self.assertTrue(plan['history_cutoff_explicit'])
        self.assertTrue(any(c['op'] == '<' and c['value'] == '2026-11-16' for c in plan['base_plan']['conditions']))
        for replacement in ('2026-11-15至11月21日', '2026-11-16至11月23日'):
            with self.subTest(period=replacement), self.assertRaises(ValueError):
                make_plan(Coordinator(current_catalog), question.replace('2026-11-16至11月22日', replacement),
                          datetime(2026, 11, 18, tzinfo=BEIJING), 'CALENDAR')

    def test_explicit_cutoff_plan_forecast_does_not_fetch_target_day_plans(self):
        question = '截至2026-11-15，预测2026-11-16至11月22日的计划航班量。'
        current_catalog = catalog()
        plan = make_plan(Coordinator(current_catalog), question, datetime(2026, 11, 18, tzinfo=BEIJING), 'CALENDAR')
        rows = [{'__period': str(day), 'planned_flight_count': value, 'n': 1} for day, value in self.series.items()]
        with patch('app.agent.forecast.execute', return_value=rows) as execute:
            result = run_forecast(Mock(), question, plan, current_catalog, ExecutionTrace())
        self.assertEqual(result['forecast']['training_end'], '2026-11-15')
        self.assertEqual(len(result['forecast']['points']), 7)
        self.assertNotIn('planned_value', result['forecast']['points'][0])
        execute.assert_called_once()

    def test_weekly_fixture_is_predicted_and_backtested_exactly(self):
        last = max(self.series)
        points = predict_series(self.series, [last + timedelta(days=i) for i in range(1, 8)], '架次')
        for p in points:
            expected = 400 + date.fromisoformat(p['date']).weekday() * 10
            self.assertEqual(p['value'], expected)
            self.assertEqual(p['mae'], 0)
            self.assertEqual(p['lower'], p['upper'])
            self.assertEqual(p['backtest_origins'], 7)

    def test_missing_or_short_history_is_not_zero_filled(self):
        bad = dict(self.series)
        del bad[max(bad) - timedelta(days=3)]
        for values in [bad, dict(list(self.series.items())[:20])]:
            with self.assertRaises(ValueError):
                predict_series(values, [max(self.series) + timedelta(days=1)], '%')

    def test_ratios_are_bounded(self):
        values = {day: value / 5 for day, value in self.series.items()}
        p = predict_series(values, [max(values) + timedelta(days=1)], '%')[0]
        self.assertTrue(0 <= p['lower'] <= p['value'] <= p['upper'] <= 100)

    def test_each_backtest_origin_excludes_future_values(self):
        from app.agent import forecast
        seen = []
        original = forecast.estimate
        def verify(history, target, method):
            seen.append((max(history), target))
            self.assertLess(max(history), target)
            return original(history, target, method)
        with patch.object(forecast, 'estimate', verify):
            predict_series(self.series, [max(self.series) + timedelta(days=7)], '架次')
        self.assertGreaterEqual(len(seen), 14)


class ChainedMultiStepTests(unittest.TestCase):
    def setUp(self):
        self.catalog = catalog()
        self.coordinator = Coordinator(self.catalog)

    def test_chained_count_dep_arr_and_detail_without_direction_clarification(self):
        for prompt in (
            '2026-11-09实际执行多少架次？然后问出港呢？再问进港呢？最后问列出进港明细。',
            '先查2026-11-09实际执行多少架次，然后查出港呢，再查进港呢，最后列出进港明细',
            '2026-11-09实际执行多少架次？出港呢？进港呢？列出进港明细。',
        ):
            with self.subTest(prompt=prompt):
                plan = self.coordinator.plan(prompt)
                self.assertEqual(plan.get('intent'), 'analysis', plan.get('answer'))
                self.assertTrue(plan.get('chained'))
                self.assertEqual(len(plan['queries']), 4)
                intents = [q['plan']['intent'] for q in plan['queries']]
                self.assertTrue(all(it in ('count', 'metric') for it in intents[:3]))
                self.assertEqual(intents[3], 'detail')
                dirs = [
                    next((c['value'] for c in q['plan']['conditions'] if c.get('col') == 'direction'), None)
                    for q in plan['queries']
                ]
                self.assertEqual(dirs, [None, 'DEP', 'ARR', 'ARR'])
                for q in plan['queries']:
                    Compiler(self.catalog).validate(q['plan'])

    def test_discourse_zuihou_does_not_trigger_flight_sequence(self):
        from app.agent.coordinator_lexicon import detect_sequence
        self.assertIsNone(detect_sequence('2026-11-09实际执行多少架次？最后问列出进港明细。'))
        self.assertIsNone(detect_sequence('最后列出进港明细'))
        self.assertEqual(detect_sequence('2026-11-09最后落地的是哪一班？'), 'desc')
        self.assertEqual(detect_sequence('2026-11-09最后一班出港航班'), 'desc')

    def test_dep_and_arr_actual_flight_count_expansion(self):
        plan = self.coordinator.plan('2026-11-09 出港和进港实际执行架次分别是多少')
        self.assertEqual(plan.get('intent'), 'analysis', plan.get('answer'))
        self.assertEqual([q['plan'].get('metric') for q in plan['queries']], ['dep_flight_count', 'arr_flight_count'])


