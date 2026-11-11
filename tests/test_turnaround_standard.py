import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import core
from app.planner import Planner
from app.turnaround_standard import ACDM_FIELDS, build_standard_turnaround, get_recorded_acdm_turnaround, get_standard_turnaround_data, has_operation_times, turnaround_identity, turnaround_target


class TurnaroundStandardTests(unittest.TestCase):
    def test_response_starts_with_the_subject_the_user_asked_for(self):
        self.assertEqual(turnaround_identity('MU2613', 'B20AE', 'MU2613'), '航班 MU2613，执飞机号 B20AE')
        self.assertEqual(turnaround_identity('MU2613', 'B20AE', 'B-20AE'), '飞机 B20AE，对应航班 MU2613')

    def setUp(self):
        self.flight = {'id': 3584, 'flight_no': 'MU2554', 'aircraft_reg': 'B20AE',
                       'flight_date': '2026-11-09T08:00:00+08:00', 'direction': 'ARR'}

    def test_aircraft_identity_and_explicit_business_date_are_preserved(self):
        self.assertEqual(turnaround_target('查询 B-20AE 飞机 2026-11-09 的过站作业甘特图'), ('B-20AE', '2026-11-09'))
        self.assertEqual(turnaround_target('查询 B1856 飞机的过站作业'), ('B1856', None))
        self.assertEqual(turnaround_target('查询 MU2554 过站作业'), ('MU2554', None))
        self.assertEqual(turnaround_target('查询过站保障'), (None, None))
        self.assertEqual(turnaround_target('最近7天航班保障量'), (None, None))
        self.assertEqual(turnaround_target('11月9日航班保障情况'), (None, None))
        self.assertEqual(turnaround_target('查询保障编号 12345'), (None, None))

    def test_date_ranges_and_unparsed_dates_are_not_silently_ignored(self):
        for question in ['B20AE 2026-11-08 至 2026-11-09 过站', 'B20AE 11月8日过站', 'B20AE 最近7天过站']:
            with self.subTest(question=question), self.assertRaises(ValueError):
                turnaround_target(question)

    def test_tail_lookup_uses_standard_source_and_latest_business_day(self):
        cursor = Mock()
        cursor.fetchall.return_value = [(self.flight, {}), ({**self.flight, 'id': 1, 'flight_date': '2026-10-10'}, {})]
        conn = Mock()
        conn.cursor.return_value = cursor
        with patch('app.turnaround_standard.connect_db', return_value=conn), \
             patch('app.turnaround_standard.get_recorded_acdm_turnaround', return_value=None):
            result = get_standard_turnaround_data('B-20AE')
        sql, params = cursor.execute.call_args.args
        self.assertIn('std_flight_core', sql)
        self.assertIn('std_flight_process', sql)
        self.assertNotIn('acdm.', sql)
        self.assertIn('ORDER BY f.flight_date DESC', sql)
        self.assertEqual(params, ('B20AE',))
        self.assertEqual((result['craft_no'], result['flight_pair'], result['flight_date']), ('B20AE', 'MU2554', '2026-11-09'))
        self.assertIn('最新业务日', result['selection_note'])
        conn.close.assert_called_once()

    def test_multiple_flights_on_same_day_require_disambiguation(self):
        cursor = Mock()
        cursor.fetchall.return_value = [(self.flight, {}), ({**self.flight, 'id': 2, 'flight_no': 'MU2506'}, {})]
        conn = Mock()
        conn.cursor.return_value = cursor
        with patch('app.turnaround_standard.connect_db', return_value=conn), \
             patch('app.turnaround_standard.get_recorded_acdm_turnaround', return_value=None):
            with self.assertRaisesRegex(ValueError, '多个航班'):
                get_standard_turnaround_data('B20AE', '2026-11-09')
        self.assertEqual(cursor.execute.call_args.args[1], ('B20AE', '2026-11-09'))

    def test_missing_target_does_not_fall_back_to_a_demo_flight(self):
        cursor = Mock()
        cursor.fetchall.return_value = []
        conn = Mock()
        conn.cursor.return_value = cursor
        with patch('app.turnaround_standard.connect_db', return_value=conn), \
             patch('app.turnaround_standard.get_recorded_acdm_turnaround', return_value=None):
            with self.assertRaisesRegex(ValueError, '未找到 B20AE'):
                get_standard_turnaround_data('B20AE')

    def test_existing_acdm_record_is_not_rejected_when_standard_table_has_no_match(self):
        cursor = Mock()
        cursor.fetchall.return_value = []
        conn = Mock()
        conn.cursor.return_value = cursor
        available = {'flight_pair': 'MU2472', 'source_table': 'acdm.tb_dyn_flight'}
        with patch('app.turnaround_standard.connect_db', return_value=conn), \
             patch('app.turnaround_standard.get_recorded_acdm_turnaround', return_value=available) as fetch:
            self.assertIs(get_standard_turnaround_data('MU2472', '2026-10-03'), available)
        fetch.assert_called_once_with('MU2472', '2026-10-03')

    def test_acdm_read_preserves_identity_date_and_actual_times_without_defaults(self):
        raw = {'FLIGHT_ID': '4797239', 'FLIGHT_NO': 'MU2613', 'CRAFT_NO': 'B20AE',
               'EXEC_DATE': '2026-10-10T00:00:00', 'NORMAL_FLIGHT_STAT_CD': 'DEP',
               'REAL_START_CLEAN_TIME': '2026-10-10T13:00:00', 'REAL_END_CLEAN_TIME': '2026-10-10T13:12:00'}
        cursor = Mock()
        cursor.fetchall.return_value = [tuple(raw.get(key) for key in ACDM_FIELDS)]
        conn = Mock()
        conn.cursor.return_value = cursor
        with patch('app.turnaround_standard.connect_db', return_value=conn):
            result = get_recorded_acdm_turnaround('B-20AE', '2026-10-10')
        sql, params = cursor.execute.call_args.args
        self.assertIn('acdm.tb_dyn_flight', sql)
        self.assertIn('"EXEC_DATE"::date = %s::date', sql)
        self.assertEqual(params, ('B20AE', '2026-10-10'))
        self.assertEqual((result['craft_no'], result['flight_pair'], result['flight_date']), ('B20AE', 'MU2613', '2026-10-10'))
        self.assertEqual(result['source_table'], 'acdm.tb_dyn_flight')
        self.assertEqual(next(item for item in result['operations'] if item['id'] == 'cleaning')['actual_end'], '13:12')
        self.assertIsNone(result['milestones']['aibt'])
        self.assertIsNone(result['milestones']['tobt'])
        self.assertEqual(result['stand'], '')

    def test_acdm_missing_business_date_is_reported_without_assigning_today(self):
        raw = {'FLIGHT_ID': 'record-without-date', 'FLIGHT_NO': 'MU2472', 'CRAFT_NO': 'B-8XYZ'}
        cursor = Mock()
        cursor.fetchall.return_value = [tuple(raw.get(key) for key in ACDM_FIELDS)]
        conn = Mock()
        conn.cursor.return_value = cursor
        with patch('app.turnaround_standard.connect_db', return_value=conn):
            result = get_recorded_acdm_turnaround('MU2472')
        self.assertEqual(result['flight_date'], '')
        self.assertIn('未填写业务日期', result['selection_note'])
        self.assertIn('NULLS LAST', cursor.execute.call_args.args[0])
        self.assertTrue(all(value is None for value in result['milestones'].values()))

    def test_history_is_labelled_separately_from_latest_empty_standard_record(self):
        cursor = Mock()
        cursor.fetchall.return_value = [(self.flight, {})]
        conn = Mock()
        conn.cursor.return_value = cursor
        history = {'flight_pair': 'MU2613', 'craft_no': 'B20AE', 'flight_date': '2026-10-10',
                   'dynamic_narrative': '历史保障记录', 'operations': [{'id': 'cleaning'}]}
        with patch('app.turnaround_standard.connect_db', return_value=conn), \
             patch('app.turnaround_standard.get_recorded_acdm_turnaround', return_value=history):
            result = get_standard_turnaround_data('B20AE')
        self.assertEqual(result['flight_pair'], 'MU2613')
        self.assertIn('MU2554（2026-11-09）', result['selection_note'])
        self.assertIn('2026-10-10', result['selection_note'])
        self.assertIn('不是该最新航班的作业', result['selection_note'])

    def test_explicit_date_does_not_mix_acdm_from_a_different_flight(self):
        cursor = Mock()
        cursor.fetchall.return_value = [(self.flight, {})]
        conn = Mock()
        conn.cursor.return_value = cursor
        with patch('app.turnaround_standard.connect_db', return_value=conn), \
             patch('app.turnaround_standard.get_recorded_acdm_turnaround', return_value={'flight_pair': 'MU2613', 'craft_no': 'B20AE'}):
            result = get_standard_turnaround_data('B20AE', '2026-11-09')
        self.assertEqual(result['flight_pair'], 'MU2554')
        self.assertEqual(len(result['operations']), 5)
        self.assertFalse(any(has_operation_times(item) for item in result['operations']))

    def test_missing_milestones_do_not_acquire_times_or_completed_operations(self):
        result = build_standard_turnaround(self.flight, {}, 'B20AE')
        self.assertEqual(len(result['operations']), 5)
        self.assertFalse(any(has_operation_times(item) for item in result['operations']))
        self.assertTrue(all(value is None for value in result['milestones'].values()))
        self.assertEqual(result['stand'], '')
        self.assertEqual(result['gate'], '')
        self.assertEqual(result['out_flight'], '')
        self.assertIn('未提供保障作业时刻', result['conclusion']['summary'])

    def test_start_without_end_is_not_declared_complete_when_aircraft_has_left(self):
        result = build_standard_turnaround(self.flight, {'boarding_start': '2026-11-09T12:00:00+08:00',
            'actual_off_block': '2026-11-09T13:00:00+08:00'}, 'B20AE')
        operation = result['operations'][0]
        self.assertEqual(operation['status'], 'in_progress')
        self.assertIsNone(operation['actual_end'])
        self.assertIsNone(operation['plan_end'])

    def test_acdm_nodes_read_existing_door_push_and_takeoff_records_without_hiding_gaps(self):
        raw = {'FLIGHT_ID': '4797239', 'FLIGHT_NO': 'MU2613', 'CRAFT_NO': 'B20AE',
               'EXEC_DATE': '2026-10-10T00:00:00', 'NORMAL_FLIGHT_STAT_CD': 'DEP',
               'REAL_CLOSE_CABIN_TIME': '2026-10-10T14:46:00',
               'REAL_CLOSE_CARGO_TIME': '2026-10-10T14:50:00',
               'REAL_OFF_BRIDGE_TIME': '2026-10-10T14:52:22',
               'REAL_BLOCK_OFF_TIME': '2026-10-10T14:53:00',
               'REAL_LAUNCH_TIME': '2026-10-10T14:52:38',
               'REAL_TAKEOFF_TIME': '2026-10-10T15:08:00'}
        cursor, conn = Mock(), Mock()
        cursor.fetchall.return_value = [tuple(raw.get(key) for key in ACDM_FIELDS)]
        conn.cursor.return_value = cursor
        with patch('app.turnaround_standard.connect_db', return_value=conn):
            result = get_recorded_acdm_turnaround('MU2613', '2026-10-10')
        nodes = {node['key']: node for node in result['flight_nodes']}
        for key, value in [('door_close', '14:46'), ('cargo_close', '14:50'), ('bridge_off', '14:52'),
                           ('aobt', '14:53'), ('pushback', '14:52'), ('atot', '15:08')]:
            self.assertEqual(nodes[key]['actual_time'], value)
        self.assertEqual(nodes['pushback']['actual_at'], '2026-10-10T14:52:38+08:00')
        self.assertEqual(nodes['door_open']['status'], 'pending')
        self.assertEqual(nodes['origin_dep']['actual_time'], '')
        self.assertIn('"REAL_CLOSE_CARGO_TIME"', cursor.execute.call_args.args[0])
        self.assertEqual(len(result['operations']), 5)
        self.assertFalse(any(has_operation_times(item) for item in result['operations']))

    def test_linked_inbound_requires_an_explicit_id_and_the_same_aircraft(self):
        raw = {'FLIGHT_ID': '2', 'FLIGHT_NO': 'MU2613', 'CRAFT_NO': 'B20AE',
               'EXEC_DATE': '2026-10-10', 'NORMAL_FLIGHT_STAT_CD': 'DEP', 'ASSOCIATED_FLIGHT_ID': '1'}
        inbound = {'FLIGHT_ID': '1', 'FLIGHT_NO': 'MU2612', 'CRAFT_NO': 'B-20AE',
                   'EXEC_DATE': '2026-10-09', 'NORMAL_FLIGHT_STAT_CD': 'ARR',
                   'REAL_TAKEOFF_TIME': '2026-10-09T23:50:00', 'REAL_LANDIN_TIME': '2026-10-10T01:00:00',
                   'REAL_BLOCK_ON_TIME': '2026-10-10T01:10:00'}
        for craft in ['B-20AE', 'B0000']:
            cursor, conn = Mock(), Mock()
            cursor.fetchall.return_value = [tuple(raw.get(key) for key in ACDM_FIELDS)]
            cursor.fetchone.return_value = tuple({**inbound, 'CRAFT_NO': craft}.get(key) for key in ACDM_FIELDS)
            conn.cursor.return_value = cursor
            with patch('app.turnaround_standard.connect_db', return_value=conn):
                result = get_recorded_acdm_turnaround('MU2613', '2026-10-10')
            nodes = {node['key']: node for node in result['flight_nodes']}
            self.assertEqual(cursor.execute.call_args.args[1], ('1',))
            self.assertEqual(nodes['origin_dep']['actual_time'], '10-09 23:50' if craft == 'B-20AE' else '')
            self.assertEqual(nodes['inbound_landing']['actual_time'], '01:00' if craft == 'B-20AE' else '')

    def test_standard_enrichment_only_adds_same_identity_data_and_preserves_existing_times(self):
        flight = {**self.flight, 'direction': 'DEP', 'flight_no': 'MU2613', 'flight_date': '2026-10-10'}
        process = {'boarding_start': '2026-10-10T14:23:00'}
        recorded = build_standard_turnaround(flight, {'boarding_start': '2026-10-10T14:24:00',
            'boarding_end': '2026-10-10T14:42:00', 'ext_json': {'REAL_CLOSE_CABIN_TIME': '2026-10-10T14:46:00',
            'REAL_START_CLEAN_TIME': '2026-10-10T13:55:00', 'REAL_END_CLEAN_TIME': '2026-10-10T14:00:00'}}, 'MU2613')
        for day in ['2026-10-10', '2026-10-09']:
            cursor, conn = Mock(), Mock()
            cursor.fetchall.return_value = [(flight, process)]
            conn.cursor.return_value = cursor
            with patch('app.turnaround_standard.connect_db', return_value=conn), \
                 patch('app.turnaround_standard.get_recorded_acdm_turnaround', return_value={**recorded, 'flight_date': day}) as fetch:
                result = get_standard_turnaround_data('MU2613', '2026-10-10')
            fetch.assert_called_once_with('MU2613', '2026-10-10')
            nodes = {node['key']: node for node in result['flight_nodes']}
            self.assertEqual(nodes['boarding_start']['actual_time'], '14:23')
            self.assertEqual(result['operations'][0]['actual_start'], '14:23')
            self.assertEqual(nodes['door_close']['actual_time'], '14:46' if day == '2026-10-10' else '')
            self.assertEqual(len(result['operations']), 5)
            self.assertEqual(sum(has_operation_times(item) for item in result['operations']), 2 if day == '2026-10-10' else 1)

    def test_late_on_block_time_does_not_stretch_the_operation_chart(self):
        result = build_standard_turnaround(self.flight, {'boarding_start': '2026-11-09T14:29:00',
            'boarding_end': '2026-11-09T14:54:00', 'actual_on_block': '2026-11-09T19:56:00'}, 'B20AE')
        self.assertEqual(result['timeline_range'], '14:20 - 15:04')
        self.assertEqual(result['milestones']['aibt'], '19:56')
        self.assertEqual(len(result['operations']), 5)
        self.assertEqual(sum(has_operation_times(item) for item in result['operations']), 1)
        self.assertIn('1 类有时刻记录', result['conclusion']['summary'])

    def test_partial_latest_day_offers_the_old_full_record_without_mixing_dates(self):
        latest = {**self.flight, 'flight_no': 'MU2613', 'direction': 'DEP', 'flight_date': '2026-10-24'}
        historical = build_standard_turnaround({**latest, 'flight_date': '2026-10-10'},
            {'boarding_start': '2026-10-10T14:24:00', 'boarding_end': '2026-10-10T14:42:00',
             'ext_json': {'REAL_START_CLEAN_TIME': '2026-10-10T13:55:00', 'REAL_END_CLEAN_TIME': '2026-10-10T14:00:00'}}, 'MU2613')
        cursor, conn = Mock(), Mock()
        cursor.fetchall.return_value = [(latest, {'boarding_start': '2026-10-24T14:29:00', 'boarding_end': '2026-10-24T14:54:00'})]
        conn.cursor.return_value = cursor
        with patch('app.turnaround_standard.connect_db', return_value=conn), \
             patch('app.turnaround_standard.get_recorded_acdm_turnaround', side_effect=[None, historical]):
            result = get_standard_turnaround_data('MU2613')
        self.assertEqual(result['flight_date'], '2026-10-24')
        self.assertEqual(sum(has_operation_times(item) for item in result['operations']), 1)
        self.assertEqual(result['available_record']['flight_date'], '2026-10-10')
        self.assertEqual(result['available_record']['recorded_operations'], 2)
        self.assertEqual(turnaround_target(result['available_record']['question']), ('MU2613', '2026-10-10'))

    def test_enriched_operation_window_includes_previously_hidden_early_tasks(self):
        flight = {**self.flight, 'direction': 'DEP', 'flight_no': 'MU2613', 'flight_date': '2026-10-10'}
        process = {'boarding_start': '2026-10-10T14:24:00', 'boarding_end': '2026-10-10T14:42:00', 'actual_on_block': '2026-10-10T19:56:00'}
        recorded = build_standard_turnaround(flight, {'tobt': '2026-10-10T15:05:00',
            'ext_json': {'REAL_START_CLEAN_TIME': '2026-10-10T13:55:00', 'REAL_END_CLEAN_TIME': '2026-10-10T14:00:00'}}, 'MU2613')
        cursor, conn = Mock(), Mock()
        cursor.fetchall.return_value = [(flight, process)]
        conn.cursor.return_value = cursor
        with patch('app.turnaround_standard.connect_db', return_value=conn), \
             patch('app.turnaround_standard.get_recorded_acdm_turnaround', return_value=recorded):
            result = get_standard_turnaround_data('MU2613', '2026-10-10')
        self.assertEqual(result['timeline_range'], '13:50 - 15:15')
        self.assertEqual(result['milestones']['tobt'], '15:05')
        self.assertEqual(result['milestones']['aibt'], '19:56')
        self.assertEqual(sum(has_operation_times(item) for item in result['operations']), 2)

    def test_old_turnaround_cache_is_not_reused_for_current_query(self):
        catalog = SimpleNamespace(overrides={})
        planner = Mock()
        planner.parse.return_value = ({'flight_no': 'MU2554'}, 'fresh')
        with patch.object(core, 'settings', SimpleNamespace(data_source='ops_ask')), \
             patch.object(core, 'cache_key', return_value='test'), \
             patch.object(core, 'MEM', {'entries': [{'q': 'test', 'version': 'test', 'parsed': {'intent': 'turnaround', 'flight_no': 'MU2613'}}]}), \
             patch.object(core, 'model_available', return_value=False), \
             patch.object(core, 'Planner', return_value=planner):
            plan, source = core.parse_question('test', catalog)
        self.assertEqual((plan['flight_no'], source), ('MU2554', 'fresh'))

    def test_planner_keeps_aircraft_query_separate_from_matched_flight(self):
        catalog = SimpleNamespace(entities={'std_flight_core': {'bindings': {'flight_no': 'flight_no'}, 'attributes': {'id': {}, 'flight_no': {}}}})
        payload = build_standard_turnaround(self.flight, {'flight_id': 3584}, 'B20AE')
        planner = Planner(catalog)
        with patch('app.turnaround.get_turnaround_data', return_value=payload) as fetch, \
             patch.object(planner.compiler, 'validate', side_effect=lambda plan: plan):
            plan, _ = planner.parse('查询 B20AE 飞机的过站作业甘特图')
        fetch.assert_called_once_with('B20AE', None)
        self.assertEqual(plan['query_identifier'], 'B20AE')
        self.assertEqual(plan['flight_no'], 'MU2554')
        self.assertEqual(plan['conditions'], [{'col': 'id', 'op': '=', 'value': 3584}])
