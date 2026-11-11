"""Unit tests for A-CDM flight milestone fishbone diagram metadata builder."""
import unittest
from app.agent.fishbone import (
    is_flight_row,
    resolve_direction,
    build_fishbone_meta,
    format_node_time
)
from app.agent.visual import build_visual_meta


class FishboneBuilderTests(unittest.TestCase):

    def test_format_node_time(self):
        self.assertEqual(format_node_time('2026-10-03 21:45:00'), '21:45')
        self.assertEqual(format_node_time('2026-10-03T19:30:15'), '19:30')
        self.assertEqual(format_node_time(None), '')

    def test_departure_flight_milestones(self):
        dep_row = {
            'FLIGHT_NO': 'MU2503',
            'NORMAL_FLIGHT_STAT_CD': 'DEP',
            'EXEC_DATE': '2026-10-09 00:00:00',
            'TAKEOFF_AIRPORT_IATA': 'WUH',
            'ARRIVAL_AIRPORT_IATA': 'PEK',
            'CRAFT_SITE_CD': '112',
            'GATE_CD': '12',
            'CRAFT_TYPE_ICAO_CD': 'B738',
            'CHECKIN_START_TIME': '2026-10-09 07:00:00',
            'CHECKIN_END_TIME': '2026-10-09 08:30:00',
            'REAL_BEGIN_BOARD_TIME': '2026-10-09 08:35:00',
            'ALTERATE_BEGIN_BOARD_TIME': '2026-10-09 08:30:00',
            'REAL_END_BOARD_TIME': '2026-10-09 08:50:00',
            'PLAN_CLOSE_DOOR_TIME': '2026-10-09 08:55:00',
            'REAL_OFF_BRIDGE_TIME': '2026-10-09 08:52:00',
            'TARGT_BLOCK_OFF_TIME': '2026-10-09 09:00:00',
            'REQUEST_LAUNCH_TIME': '2026-10-09 08:58:00',
            'REAL_BLOCK_OFF_TIME': '2026-10-09 09:02:00',
            'SCHEME_TAKEOFF_TIME': '2026-10-09 09:10:00',
            'REAL_TAKEOFF_TIME': '2026-10-09 09:12:00',
            'REAL_LANDIN_TIME': '2026-10-09 11:20:00',
        }

        self.assertTrue(is_flight_row(dep_row))
        self.assertEqual(resolve_direction(dep_row), 'DEP')

        meta = build_fishbone_meta(dep_row)
        self.assertEqual(meta['kind'], 'fishbone')
        self.assertEqual(meta['flight_no'], 'MU2503')
        self.assertEqual(meta['direction'], 'DEP')
        self.assertEqual(meta['origin'], 'WUH')
        self.assertEqual(meta['destination'], 'PEK')

        node_keys = [n['key'] for n in meta['nodes']]
        self.assertIn('checkin_start', node_keys)
        self.assertIn('boarding_start', node_keys)
        self.assertIn('aobt', node_keys)
        self.assertIn('atot', node_keys)
        self.assertIn('aldt', node_keys)

        # Check visual builder dispatch
        plan = {'intent': 'detail', 'subject': 'ACDM.TB_DYN_FLIGHT'}
        visual = build_visual_meta(plan, [dep_row], 1)
        self.assertEqual(visual['kind'], 'fishbone')
        self.assertEqual(visual['flight_no'], 'MU2503')

    def test_arrival_flight_milestones(self):
        arr_row = {
            'flight_no': 'ZH8172',
            'direction': 'ARR',
            'flight_date': '2026-10-04',
            'origin_city': 'SZX',
            'dest_city': 'WUH',
            'scheduled_departure': '2026-10-04 14:15:00',
            'actual_departure': '2026-10-04 14:19:00',
            'scheduled_arrival': '2026-10-04 16:05:00',
            'actual_arrival': '2026-10-04 15:46:00',
            'actual_on_block': '2026-10-04 16:03:00',
            'ENTRY_GROUND_TIME': '2026-10-04 15:50:00',
            'REAL_BY_BRIDGE_TIME': '2026-10-04 16:04:00',
        }

        self.assertTrue(is_flight_row(arr_row))
        self.assertEqual(resolve_direction(arr_row), 'ARR')

        meta = build_fishbone_meta(arr_row)
        self.assertEqual(meta['kind'], 'fishbone')
        self.assertEqual(meta['direction'], 'ARR')
        node_keys = [n['key'] for n in meta['nodes']]
        self.assertIn('origin_dep', node_keys)
        self.assertIn('aldt', node_keys)
        self.assertIn('aibt', node_keys)

    def test_non_flight_row_does_not_trigger_fishbone(self):
        contract_row = {
            'id': 101,
            'contract_name': '机场T3航站楼弱电维保服务',
            'total_amount': 500000.0,
            'enterprise_name': '信科公司'
        }
        self.assertFalse(is_flight_row(contract_row))
        self.assertFalse(is_flight_row({'flight_id': 123, 'cargo_weight': 42}))

    def test_complete_flow_keeps_missing_nodes_and_all_existing_departure_nodes(self):
        meta = build_fishbone_meta({'flight_no': 'MU2613', 'direction': 'DEP', 'flight_date': '2026-10-10'})
        nodes = {node['key']: node for node in meta['nodes']}
        for key in ['origin_dep', 'inbound_landing', 'aibt', 'door_open', 'boarding_start', 'boarding_end',
                    'door_close', 'cargo_close', 'aobt', 'pushback', 'atot', 'checkin_start', 'checkin_end',
                    'ground_service', 'urge_boarding', 'bridge_off', 'tobt', 'aldt']:
            self.assertIn(key, nodes)
        self.assertTrue(all(node['status'] == 'pending' for node in nodes.values()))
        self.assertTrue(all(not node['actual_time'] for node in nodes.values()))

    def test_estimates_and_target_times_never_become_actual_completions(self):
        meta = build_fishbone_meta({'flight_no': 'MU2613', 'direction': 'DEP', 'flight_date': '2026-10-10',
            'ALTERATE_TAKEOFF_TIME': '2026-10-10 15:05:00', 'ALTERATE_LANDIN_TIME': '2026-10-10 19:37:18',
            'TARGT_BLOCK_OFF_TIME': '2026-10-10 15:05:00', 'tsat': '2026-10-10 14:55:00',
            'REAL_TAKEOFF_TIME': 'invalid'})
        nodes = {node['key']: node for node in meta['nodes']}
        for key in ['atot', 'aldt', 'tobt', 'tsat']:
            self.assertEqual(nodes[key]['actual_time'], '')
            self.assertNotEqual(nodes[key]['status'], 'completed')
        self.assertEqual(nodes['atot']['estimated_time'], '15:05')
        self.assertEqual(nodes['aldt']['estimated_time'], '19:37')
        self.assertEqual(nodes['tobt']['plan_time'], '15:05')
        self.assertEqual(nodes['tsat']['plan_time'], '14:55')

    def test_previous_and_next_flights_are_not_inferred_from_this_flights_takeoff(self):
        dep = build_fishbone_meta({'flight_no': 'MU2613', 'direction': 'DEP', 'REAL_TAKEOFF_TIME': '2026-10-10 15:08:00'})
        nodes = {node['key']: node for node in dep['nodes']}
        self.assertEqual(nodes['origin_dep']['actual_time'], '')
        self.assertEqual(nodes['inbound_landing']['actual_time'], '')
        self.assertEqual(nodes['atot']['actual_time'], '15:08')
        arr = build_fishbone_meta({'flight_no': 'MU2612', 'direction': 'ARR', 'REAL_TAKEOFF_TIME': '2026-10-10 12:08:00', 'REAL_LANDIN_TIME': '2026-10-10 13:28:00'})
        nodes = {node['key']: node for node in arr['nodes']}
        self.assertEqual(nodes['origin_dep']['actual_time'], '12:08')
        self.assertEqual(nodes['aldt']['actual_time'], '13:28')
        self.assertEqual(nodes['atot']['actual_time'], '')

    def test_cross_day_nodes_keep_dates_and_convert_utc_to_business_time(self):
        meta = build_fishbone_meta({'flight_no': 'MU2613', 'direction': 'DEP', 'flight_date': '2026-10-10',
            'prev_actual_departure': '2026-10-09T15:55:00Z', 'actual_departure': '2026-10-10T07:08:00Z'})
        nodes = {node['key']: node for node in meta['nodes']}
        self.assertEqual(nodes['origin_dep']['actual_time'], '10-09 23:55')
        self.assertEqual(nodes['atot']['actual_time'], '15:08')
        self.assertEqual(nodes['atot']['actual_at'], '2026-10-10T15:08:00+08:00')

    def test_standard_flight_nodes_use_only_its_matching_returned_process(self):
        row = {'id': 7, 'flight_no': 'MU2613', 'direction': 'DEP', 'flight_date': '2026-10-10'}
        visual = build_visual_meta({'intent': 'detail', 'subject': 'std_flight_core'}, [row], 1,
            related=[{'table': 'std_flight_process', 'rows': [
                {'flight_id': 8, 'boarding_start': '2026-10-10 09:00:00'},
                {'flight_id': 7, 'boarding_start': '2026-10-10 14:24:07', 'ext_json': {'REAL_CLOSE_CABIN_TIME': '2026-10-10 14:46:00'}}]}])
        nodes = {node['key']: node for node in visual['nodes']}
        self.assertEqual(nodes['boarding_start']['actual_time'], '14:24')
        self.assertEqual(nodes['boarding_start']['plan_time'], '')
        self.assertEqual(nodes['door_close']['actual_time'], '14:46')

    def test_flight_number_aggregates_still_render_statistics_instead_of_empty_milestones(self):
        result = build_visual_meta({'intent': 'count', 'subject': 'std_flight_core'},
            [{'flight_no': 'MU2613', 'n': 2}, {'flight_no': 'MU2614', 'n': 3}], 5)
        self.assertEqual(result['kind'], 'donut')


if __name__ == '__main__':
    unittest.main()
