import unittest
from app.agent.visual import build_visual_meta
from fixtures import fixture_catalog


class VisualBuilderTests(unittest.TestCase):
    def setUp(self):
        self.catalog = fixture_catalog()

    def test_single_row_stats(self):
        plan = {'subject': 'contract_main', 'intent': 'sum', 'conditions': []}
        metric = {'name': '合同总金额', 'unit': '万元'}
        rows = [{'total': 4800.5, 'n': 12, 'mx': 1500.0}]
        meta = build_visual_meta(plan, rows, 12, metric, self.catalog)
        self.assertEqual(meta['kind'], 'stats')
        self.assertEqual(len(meta['stats']), 3)
        self.assertEqual(meta['stats'][0]['label'], '合同总金额')
        self.assertEqual(meta['stats'][0]['value'], '4800.5')
        self.assertEqual(meta['stats'][0]['unit'], '万元')

    def test_multi_category_donut(self):
        plan = {'subject': 'contract_main', 'intent': 'sum', 'conditions': []}
        metric = {'name': '合同金额', 'unit': '万元'}
        rows = [
            {'enterprise_name': '信科公司', 'total': 3000, 'n': 6},
            {'enterprise_name': '中天智航', 'total': 2000, 'n': 4},
            {'enterprise_name': '机场实业', 'total': 1000, 'n': 2},
        ]
        meta = build_visual_meta(plan, rows, 12, metric, self.catalog)
        self.assertEqual(meta['kind'], 'donut')
        self.assertEqual(len(meta['slices']), 3)
        self.assertEqual(meta['slices'][0]['label'], '信科公司')
        self.assertEqual(meta['slices'][0]['percent'], 50.0)
        self.assertEqual(meta['total'], 6000.0)

    def test_temporal_trend(self):
        plan = {'subject': 'contract_main', 'intent': 'count', 'conditions': []}
        rows = [
            {'__period': '2026-01', 'n': 5},
            {'__period': '2026-02', 'n': 8},
            {'__period': '2026-03', 'n': 12},
        ]
        meta = build_visual_meta(plan, rows, 25, None, self.catalog)
        self.assertEqual(meta['kind'], 'trend')
        self.assertEqual(len(meta['series']), 3)
        self.assertEqual(meta['series'][0]['label'], '2026-01')
        self.assertEqual(meta['series'][0]['value'], 5.0)

    def test_empty_rows_return_none(self):
        plan = {'subject': 'contract_main', 'intent': 'detail', 'conditions': []}
        meta = build_visual_meta(plan, [], 0, None, self.catalog)
        self.assertEqual(meta['kind'], 'none')

    def test_multi_flight_detail_returns_detail_table(self):
        plan = {'subject': 'flight_execution', 'intent': 'detail', 'conditions': []}
        rows = [
            {
                'flight_no': 'CZ3101',
                'airline_code': 'CZ',
                'direction': 'DEP',
                'flight_date': '2026-11-11',
                'origin_city': '武汉',
                'dest_city': '北京大兴',
                'scheduled_departure': '2026-11-11 08:00:00',
                'actual_departure': '2026-11-11 08:05:00',
                'stand_code': '211',
                'gate_code': '211',
                'aircraft_type': 'B738',
                'aircraft_reg': 'B1234',
                'flight_status': '正常',
            },
            {
                'flight_no': 'CZ3102',
                'airline_code': 'CZ',
                'direction': 'DEP',
                'flight_date': '2026-11-11',
                'origin_city': '武汉',
                'dest_city': '广州',
                'scheduled_departure': '2026-11-11 09:00:00',
                'actual_departure': '2026-11-11 09:30:00',
                'stand_code': '212',
                'gate_code': '212',
                'aircraft_type': 'A320',
                'aircraft_reg': 'B5678',
                'flight_status': '延误',
            },
        ]
        meta = build_visual_meta(plan, rows, 23, None, self.catalog)
        self.assertEqual(meta['kind'], 'detail_table')
        self.assertTrue(meta['is_flight'])
        self.assertEqual(meta['total'], 23)
        self.assertEqual(meta['shown'], 2)
        self.assertEqual(meta['summary']['dep_count'], 2)
        self.assertEqual(meta['summary']['ontime_count'], 1)
        self.assertEqual(meta['summary']['delayed_count'], 1)
        self.assertEqual(meta['rows'][0]['flight_no'], 'CZ3101')
        self.assertEqual(meta['rows'][0]['drill_question'], '2026-11-11 CZ3101 时序图')
        self.assertTrue(len(meta['summary'].get('hourly_distribution', [])) == 24)

    def test_planned_flights_without_actual_time_populate_hourly_distribution_and_planned_count(self):
        plan = {'subject': 'flight_execution', 'intent': 'detail', 'conditions': []}
        rows = [
            {
                'flight_no': 'CZ3579',
                'airline_code': 'CZ',
                'direction': 'DEP',
                'flight_date': '2026-11-16',
                'origin_city': '武汉',
                'dest_city': '上海浦东',
                'scheduled_departure': '2026-11-16 15:40:00',
                'actual_departure': None,
                'flight_status': '计划',
            },
            {
                'flight_no': 'CZ3823',
                'airline_code': 'CZ',
                'direction': 'DEP',
                'flight_date': '2026-11-16',
                'origin_city': '武汉',
                'dest_city': '上海浦东',
                'scheduled_departure': '2026-11-16 07:50:00',
                'actual_departure': '2026-11-16 07:52:00',
                'flight_status': '正常',
            },
        ]
        meta = build_visual_meta(plan, rows, 2, None, self.catalog)
        self.assertEqual(meta['summary']['dep_count'], 2)
        self.assertEqual(meta['summary']['planned_count'], 1)
        self.assertEqual(meta['summary']['ontime_count'], 1)
        h_dist = {item['hour']: item['count'] for item in meta['summary']['hourly_distribution']}
        self.assertEqual(h_dist[15], 1)
        self.assertEqual(h_dist[7], 1)
        self.assertEqual(sum(h_dist.values()), 2)

    def test_generic_entity_detail_returns_detail_table(self):
        plan = {'subject': 'contract_main', 'intent': 'detail', 'conditions': []}
        rows = [
            {'contract_no': 'HT-001', 'contract_name': '站坪保障协议', 'amount': 120.5},
            {'contract_no': 'HT-002', 'contract_name': '廊桥维保项目', 'amount': 88.0},
        ]
        meta = build_visual_meta(plan, rows, 2, None, self.catalog)
        self.assertEqual(meta['kind'], 'detail_table')
        self.assertFalse(meta['is_flight'])
        self.assertEqual(meta['shown'], 2)
        self.assertLessEqual(len(meta['columns']), 7)
        self.assertTrue(len(meta['columns']) >= 3)

    def test_rate_and_ranking_breakdown_render_as_bar_not_donut(self):
        rate_plan = {'subject': 'flight_execution', 'intent': 'metric', 'group_by': ['airline_code'], 'conditions': []}
        rate_metric = {'name': '放行正常率', 'unit': '%', 'value_alias': 'rate'}
        rate_rows = [
            {'airline_code': 'CZ', 'rate': 94.5},
            {'airline_code': 'MU', 'rate': 91.2},
            {'airline_code': 'CA', 'rate': 89.8},
        ]
        rate_meta = build_visual_meta(rate_plan, rate_rows, 3, rate_metric, self.catalog)
        self.assertEqual(rate_meta['kind'], 'bar')
        self.assertTrue(rate_meta.get('is_rate'))
        self.assertEqual(rate_meta['bars'][0]['percent'], 0.0)
        self.assertEqual(rate_meta['bars'][0]['label'], '南方航空（CZ）')
        self.assertEqual(rate_meta['bars'][1]['label'], '东方航空（MU）')
        self.assertEqual(rate_meta['bars'][2]['label'], '中国国航（CA）')
        self.assertEqual(rate_meta['group_label'], '航司')
        self.assertEqual(rate_meta['title'], '按航司排名统计')

        rank_plan = {'subject': 'flight_execution', 'intent': 'count', 'group_by': ['dest_city'], 'limit': 5, 'conditions': []}
        rank_metric = {'name': 'Top5 航线架次', 'unit': '班'}
        rank_rows = [
            {'dest_city': '北京', 'n': 18},
            {'dest_city': '上海', 'n': 15},
            {'dest_city': '广州', 'n': 12},
        ]
        rank_meta = build_visual_meta(rank_plan, rank_rows, 3, rank_metric, self.catalog)
        self.assertEqual(rank_meta['kind'], 'bar')
        self.assertEqual(rank_meta['unit'], '班')

    def test_airline_group_fallback_claims_translates_to_chinese_with_code(self):
        from app.evidence import fallback_claims
        from tests.test_coordinator import ops_catalog
        cat = ops_catalog()
        plan = {
            'subject': 'mv_acdm_dyn_flight_detail',
            'intent': 'metric',
            'group_by': ['airline_code'],
            'conditions': []
        }
        evidence = {
            'facts': [
                {
                    'id': 'F1',
                    'name': '放行正常率',
                    'table': 'mv_acdm_dyn_flight_detail',
                    'metric': {'name': '放行正常率', 'unit': '%', 'value_alias': 'rate'},
                    'values': {'airline_code': '3U', 'rate': '100.00', 'n': 10}
                },
                {
                    'id': 'F2',
                    'name': '放行正常率',
                    'table': 'mv_acdm_dyn_flight_detail',
                    'metric': {'name': '放行正常率', 'unit': '%', 'value_alias': 'rate'},
                    'values': {'airline_code': 'CA', 'rate': '100.00', 'n': 59}
                }
            ]
        }
        claims, text = fallback_claims(cat, plan, evidence, 69)
        self.assertIn('航司=四川航空（3U）：放行正常率为 100.00 % （匹配 10 条记录）。', text)
        self.assertIn('航司=中国国航（CA）：放行正常率为 100.00 % （匹配 59 条记录）。', text)

    def test_flight_count_stats_uses_flight_unit(self):
        plan = {'subject': 'flight_execution', 'intent': 'count', 'conditions': []}
        meta = build_visual_meta(plan, [{'n': 23}], 23, None, self.catalog)
        self.assertEqual(meta['kind'], 'stats')
        self.assertEqual(meta['stats'][0]['value'], '23')
        self.assertEqual(meta['stats'][0]['unit'], '班')

    def test_detail_template_summary_skips_count_only_template(self):
        from app.agent.governance_runtime import render_template_summary

        plan = {
            'subject': 'flight_execution',
            'intent': 'detail',
            'conditions': [],
            'plan_template': {'responseTemplate': '{{count}} 条航班记录'},
        }
        res = render_template_summary(self.catalog, plan, {'facts': []}, 23)
        self.assertIsNone(res)

    def test_temporal_trend_sorts_chronologically_for_peak_hour_and_dates(self):
        # 1. Unsorted single-day peak_hour rows (as returned when SQL sorts by value DESC)
        plan = {'subject': 'flight_hourly_movements', 'intent': 'metric', 'group_by': ['peak_hour', 'hour'], 'conditions': []}
        metric = {'name': '起降架次(高峰小时)', 'unit': '架次', 'value_alias': 'peak_hour_movements'}
        rows = [
            {'peak_hour': '2026-11-09 12:00', 'hour': 12, 'peak_hour_movements': 37, 'n': 37},
            {'peak_hour': '2026-11-09 08:00', 'hour': 8, 'peak_hour_movements': 36, 'n': 36},
            {'peak_hour': '2026-11-09 23:00', 'hour': 23, 'peak_hour_movements': 14, 'n': 14},
            {'peak_hour': '2026-11-09 01:00', 'hour': 1, 'peak_hour_movements': 3, 'n': 3},
            {'peak_hour': '2026-11-09 05:00', 'hour': 5, 'peak_hour_movements': 1, 'n': 1},
        ]
        meta = build_visual_meta(plan, rows, 91, metric, self.catalog)
        self.assertEqual(meta['kind'], 'trend')
        self.assertEqual([pt['label'] for pt in meta['series']], ['01:00', '05:00', '08:00', '12:00', '23:00'])
        self.assertEqual([pt['value'] for pt in meta['series']], [3.0, 1.0, 36.0, 37.0, 14.0])

        # 2. Operational-day cross-midnight peak_hour keeps true chronological order via raw timestamp
        op_rows = [
            {'peak_hour': '2026-11-10 01:00', 'peak_hour_movements': 5, 'n': 5},
            {'peak_hour': '2026-11-09 23:00', 'peak_hour_movements': 18, 'n': 18},
            {'peak_hour': '2026-11-09 06:00', 'peak_hour_movements': 22, 'n': 22},
        ]
        op_meta = build_visual_meta(plan, op_rows, 45, metric, self.catalog)
        self.assertEqual([pt['label'] for pt in op_meta['series']], ['2026-11-09 06:00', '2026-11-09 23:00', '2026-11-10 01:00'])

        # 3. Unsorted monthly / weekly / daily __period rows sort chronologically
        month_rows = [
            {'__period': '2026-11', 'n': 10},
            {'__period': '2026-02', 'n': 30},
            {'__period': '2026-09', 'n': 20},
        ]
        month_meta = build_visual_meta({'subject': 'contract_main', 'intent': 'count', 'conditions': []}, month_rows, 60, None, self.catalog)
        self.assertEqual([pt['label'] for pt in month_meta['series']], ['2026-02', '2026-09', '2026-11'])

    def test_temporal_bar_chart_sorts_by_time_not_value(self):
        plan = {'subject': 'contract_main', 'intent': 'count', 'conditions': [], 'limit': 5}
        rows = [
            {'stat_month': '2026-10', 'n': 50},
            {'stat_month': '2026-01', 'n': 10},
            {'stat_month': '2026-05', 'n': 30},
        ]
        meta = build_visual_meta(plan, rows, 90, None, self.catalog)
        self.assertEqual(meta['kind'], 'bar')
        self.assertEqual([b['label'] for b in meta['bars']], ['2026-01', '2026-05', '2026-10'])

    def test_null_metric_stats_never_falls_back_to_row_count(self):
        plan = {'subject': 'mv_acdm_turnaround_pair', 'intent': 'metric', 'metric': 'avg_turnaround_deice_minutes', 'conditions': []}
        metric = {'code': 'avg_turnaround_deice_minutes', 'name': '平均除冰时长', 'unit': '分钟', 'value_alias': 'avg_turnaround_deice_minutes'}
        rows = [{'avg_turnaround_deice_minutes': None, 'n': 190}]
        meta = build_visual_meta(plan, rows, 190, metric, self.catalog)
        self.assertEqual(meta['kind'], 'stats')
        self.assertEqual(meta['stats'][0]['label'], '平均除冰时长')
        self.assertEqual(meta['stats'][0]['value'], '无有效样本')
        self.assertEqual(meta['stats'][0]['unit'], '')
        self.assertEqual(meta['stats'][1]['label'], '扫描记录数')
        self.assertEqual(meta['stats'][1]['value'], '190')


if __name__ == '__main__':
    unittest.main()
