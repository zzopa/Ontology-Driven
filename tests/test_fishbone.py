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


if __name__ == '__main__':
    unittest.main()
