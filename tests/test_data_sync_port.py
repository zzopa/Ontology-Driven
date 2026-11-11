"""Unit tests for the pure-Python port of airport-ops-data-sync transforms and helpers."""
from datetime import date, datetime
from decimal import Decimal
import unittest

from app.sync import oracle_source, pg_sink, transforms


class SyncTransformTests(unittest.TestCase):
    def test_flight_no_normalizer(self):
        self.assertEqual(transforms.normalize_flight_no('CZ', '9301'), 'CZ9301')
        self.assertEqual(transforms.normalize_flight_no('mf', ' 1688 '), 'MF1688')
        self.assertEqual(transforms.normalize_flight_no('CZ', 'CZ9301'), 'CZ9301')
        self.assertIsNone(transforms.normalize_flight_no('CZ', None))
        self.assertIsNone(transforms.normalize_flight_no('CZ', '   '))
        self.assertEqual(transforms.normalize_flight_no(None, '9301'), '9301')

    def test_maps_acdm_departure_matching_java_mapper(self):
        row = {
            'FLIGHT_ID': 1001,
            'AIRLINE_TWO_CHAR_CD': 'MU',
            'FLIGHT_NO': '2501',
            'EXEC_DATE': datetime(2026, 8, 5, 0, 0, 0),
            'IS_OFF_IN': '1',
            'FLIGHT_TASK_CD': 'W/Z',
            'CRAFT_TYPE_ICAO_CD': 'A320',
            'CRAFT_NO': 'B1234',
            'CRAFT_SITE_CD': '101',
            'GATE_CD': 'A12',
            'TAKEOFF_AIRPORT_IATA': 'WUH',
            'ARRIVAL_AIRPORT_IATA': 'PEK',
            'SCHEME_TAKEOFF_TIME': datetime(2026, 8, 5, 8, 0),
            'REAL_TAKEOFF_TIME': datetime(2026, 8, 5, 8, 40),
            'RELEASE_ON_TIME': 'N',
            'UPDATE_TIME': datetime(2026, 8, 5, 9, 0),
        }
        draft = transforms.map_flight_draft(row, 'ACDM_ORACLE', True, airport_code='WUH')
        self.assertIsNotNone(draft)
        self.assertEqual(draft['direction'], 'DEP')
        self.assertEqual(draft['flight_no'], 'MU2501')
        self.assertEqual(draft['biz_key'], 'WUH:2026-08-05:DEP:MU2501')
        self.assertEqual(draft['airport_code'], 'WUH')
        self.assertEqual(draft['flight_date'], date(2026, 8, 5))
        self.assertEqual(draft['flight_date_utc'], datetime(2026, 8, 5, 0, 0, tzinfo=transforms.timezone.utc))
        self.assertEqual(draft['flight_nature'], '正班')
        self.assertIs(draft['is_delayed'], True)
        self.assertEqual(draft['delay_minutes'], 40)
        self.assertIs(draft['release_on_time'], False)
        self.assertIs(draft['takeoff_on_time'], False)

    def test_maps_fims_arrival_and_station_defaults(self):
        row = {
            'FLIGHT_ID': 2002,
            'AIRLINE_TWO_CHAR_CD': 'CZ',
            'FLIGHT_NO': 'CZ3101',
            'EXEC_DATE': datetime(2026, 11, 9, 0, 0, 0),
            'IS_OFF_IN': '0',
            'FLIGHT_TASK_CD': 'C/B',
            'START_STATION': 'PEK',
            'NEAREST_STATION': 'PEK',
            'TERMINAL_STATION': None,
            'DOMESTIC_TERMINAL_CD': 'T3',
            'NEAREST_SCHEME_LANDIN_TIME': datetime(2026, 11, 9, 10, 0),
            'NEAREST_REAL_LANDIN_TIME': datetime(2026, 11, 9, 10, 10),
            'UPDATE_TIME': datetime(2026, 11, 9, 10, 15),
        }
        draft = transforms.map_flight_draft(row, 'FIMS_ORACLE', False, airport_code='WUH')
        self.assertEqual(draft['direction'], 'ARR')
        self.assertEqual(draft['origin_airport'], 'PEK')
        self.assertEqual(draft['dest_airport'], 'WUH')
        self.assertEqual(draft['previous_airport'], 'PEK')
        self.assertEqual(draft['terminal'], 'T3')
        self.assertEqual(draft['flight_nature'], '补班')
        self.assertIs(draft['is_delayed'], False)
        self.assertEqual(draft['delay_minutes'], 0)

    def test_maps_production_outbound_leg_matching_java_mapper(self):
        row = {
            'UUID': 'u1',
            'EXEC_DATE': datetime(2026, 8, 5, 0, 0, 0),
            'AIRLINE_TWO_CHAR_CD': 'MU',
            'AIRPORT_IATA': 'WUH',
            'FLIGHT_NO': '9600',
            'IS_OFF_IN': 'O',
            'LEG': 'WUH-CAN',
            'ADULT_COUNT': 150,
            'CHILDREN_COUNT': 21,
            'BABY_COUNT': 1,
            'LUGGAGE': Decimal('720'),
            'MAIL': Decimal('0'),
            'GOODS': Decimal('0'),
        }
        d = transforms.map_production_draft(row, airport_code='WUH')
        self.assertIsNotNone(d)
        self.assertEqual(d['direction'], 'DEP')
        self.assertEqual(d['flight_no'], 'MU9600')
        self.assertEqual(d['origin_airport'], 'WUH')
        self.assertEqual(d['dest_airport'], 'CAN')
        self.assertIs(d['is_local_leg'], True)
        self.assertEqual(d['baggage_weight'], Decimal('720'))
        self.assertEqual(d['biz_key'], 'WUH:2026-08-05:DEP:MU9600:LWUH-CAN')

    def test_codeshare_mapping_and_identifier_validation(self):
        cs = transforms.map_codeshare_draft({'FLIGHT_ID': 999, 'AIRLINE_TWO_CHAR_CD': 'MF', 'FLIGHT_NO': '8101'})
        self.assertEqual(cs['share_flight_identity'], 'MF8101')
        self.assertEqual(cs['source_flight_id'], '999')
        self.assertEqual(oracle_source.safe_ident('TB_DYN_FLIGHT'), 'TB_DYN_FLIGHT')
        with self.assertRaises(ValueError):
            oracle_source.safe_ident('TB_DYN_FLIGHT; DROP TABLE x')

    def test_terminal_lookup_resolution(self):
        gates = {'A12': 'T3'}
        stands = {'101': 'T2'}
        self.assertEqual(pg_sink._resolve_terminal({'terminal': 'T1', 'gate_code': 'A12', 'stand_code': '101'}, gates, stands), 'T1')
        self.assertEqual(pg_sink._resolve_terminal({'terminal': None, 'gate_code': 'A12', 'stand_code': '101'}, gates, stands), 'T3')
        self.assertEqual(pg_sink._resolve_terminal({'terminal': None, 'gate_code': None, 'stand_code': '101'}, gates, stands), 'T2')


class _FakeSyncCursor:
    def __init__(self, handler):
        self._handler = handler
        self._rows = []
        self.queries = []
        self.closed = False

    def execute(self, sql, params=None):
        norm = ' '.join(sql.split())
        self.queries.append((norm, params))
        res = self._handler(norm, params)
        self._rows = list(res) if res is not None else []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        self.closed = True


class _FakeSyncConn:
    def __init__(self, handler):
        self._handler = handler
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return _FakeSyncCursor(self._handler)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class BulkPgSinkTests(unittest.TestCase):
    @staticmethod
    def _sample_flight_draft(flight_id, flight_no, *, is_dep=True, source_system='FIMS_ORACLE'):
        row = {
            'FLIGHT_ID': flight_id,
            'AIRLINE_TWO_CHAR_CD': flight_no[:2],
            'FLIGHT_NO': flight_no[2:],
            'EXEC_DATE': datetime(2026, 11, 9, 0, 0, 0),
            'IS_OFF_IN': '1' if is_dep else '0',
            'FLIGHT_TASK_CD': 'W/Z',
            'UPDATE_TIME': datetime(2026, 11, 9, 8, 0, 0),
        }
        return transforms.map_flight_draft(row, source_system, is_dep, airport_code='WUH')

    @staticmethod
    def _sample_prod_draft(uuid, flight_no, leg='WUH-CAN'):
        row = {
            'UUID': uuid,
            'EXEC_DATE': datetime(2026, 11, 9, 0, 0, 0),
            'AIRLINE_TWO_CHAR_CD': flight_no[:2],
            'AIRPORT_IATA': 'WUH',
            'FLIGHT_NO': flight_no[2:],
            'IS_OFF_IN': 'O',
            'LEG': leg,
            'ADULT_COUNT': 100,
        }
        return transforms.map_production_draft(row, airport_code='WUH')

    def test_upsert_flight_batch_uses_bulk_and_splits_intra_batch_duplicates(self):
        next_id = {'val': 10}
        executed = []

        def handler(sql, params):
            executed.append((sql, params))
            if sql.startswith('INSERT INTO std_flight_core') and 'RETURNING id' in sql:
                # 51 params per inserted flight
                count = len(params) // 51
                ids = []
                for _ in range(count):
                    ids.append((next_id['val'],))
                    next_id['val'] += 1
                return ids
            return []

        conn = _FakeSyncConn(handler)
        d1 = self._sample_flight_draft(101, 'MU2501')
        d2 = self._sample_flight_draft(102, 'CZ3101')
        d3_dup = self._sample_flight_draft(103, 'MU2501')  # collides on triple_key with d1

        res = pg_sink.upsert_flight_batch(conn, [d1, d2, d3_dup])
        self.assertEqual(res, {'inserted': 2, 'updated': 1, 'skipped': 0, 'failed': 0})
        self.assertEqual(conn.committed, 1)

        core_inserts = [q for q, _ in executed if q.startswith('INSERT INTO std_flight_core')]
        core_updates = [q for q, _ in executed if q.startswith('UPDATE std_flight_core AS t SET')]
        self.assertEqual(len(core_inserts), 1)  # d1 + d2 inserted in one multi-row statement
        self.assertEqual(len(core_updates), 1)  # d3_dup updated in Pass 1

    def test_upsert_flight_batch_falls_back_to_rowwise_on_bulk_error(self):
        executed = []

        def handler(sql, params):
            executed.append((sql, params))
            if sql.startswith('INSERT INTO std_flight_core') and 'RETURNING id' in sql:
                count = len(params) // 51
                if count > 1:
                    raise RuntimeError('simulated bulk constraint failure')
                # fail only bad draft in row-wise retry
                if params[4] == 'CA9999':
                    raise RuntimeError('bad row CA9999')
                return [(77,)]
            return []

        conn = _FakeSyncConn(handler)
        good = self._sample_flight_draft(201, 'MU2501')
        bad = self._sample_flight_draft(202, 'CA9999')
        res = pg_sink.upsert_flight_batch(conn, [good, bad])
        self.assertEqual(res, {'inserted': 1, 'updated': 0, 'skipped': 0, 'failed': 1})
        self.assertEqual(conn.committed, 1)
        sqls = [q for q, _ in executed]
        self.assertIn('ROLLBACK TO SAVEPOINT batch_sp', sqls)
        self.assertIn('ROLLBACK TO SAVEPOINT row_sp', sqls)

    def test_upsert_production_batch_bulk_dedup_and_fallback(self):
        next_pid = {'val': 500}
        executed = []

        def handler(sql, params):
            executed.append((sql, params))
            if sql.startswith('INSERT INTO std_flight_production') and 'RETURNING id' in sql:
                count = len(params) // 42
                if count > 1 and any(params[i * 42 + 5] == 'CA9999' for i in range(count)):
                    raise RuntimeError('simulated bulk prod error')
                if count == 1 and params[5] == 'CA9999':
                    raise RuntimeError('bad prod row')
                ids = []
                for _ in range(count):
                    ids.append((next_pid['val'],))
                    next_pid['val'] += 1
                return ids
            return []

        conn = _FakeSyncConn(handler)
        p1 = self._sample_prod_draft('u1', 'MU2501')
        p2_dup = self._sample_prod_draft('u2', 'MU2501')  # same biz_key -> Pass 1 update
        p3_bad = self._sample_prod_draft('u3', 'CA9999')

        res = pg_sink.upsert_production_batch(conn, [p1, p2_dup, p3_bad])
        self.assertEqual(res, {'inserted': 1, 'updated': 1, 'failed': 1})
        self.assertEqual(conn.committed, 1)

    def test_replace_codeshares_by_source_bulk_and_fallback(self):
        executed = []

        def handler(sql, params):
            executed.append((sql, params))
            if sql.startswith('SELECT DISTINCT ON (source_flight_id)'):
                return [('f1', 11), ('f2', 22)]
            if sql.startswith('INSERT INTO std_flight_link') and len(params) > 2:
                raise RuntimeError('simulated bulk link error')
            return []

        conn = _FakeSyncConn(handler)
        res = pg_sink.replace_codeshares_by_source(
            conn,
            {'f1': ['CZ9001', 'CZ9002'], 'f2': ['CA8001'], 'missing': ['MU1000']},
        )
        self.assertEqual(res, {'flights_touched': 2, 'links_written': 3, 'skipped_no_flight': 1, 'failed': 0})
        self.assertEqual(conn.committed, 1)
        sqls = [q for q, _ in executed]
        self.assertIn('ROLLBACK TO SAVEPOINT cs_batch_sp', sqls)

    def test_ensure_oracle_conn_reconnects_when_ping_fails(self):
        from unittest.mock import patch

        class _Conn:
            def __init__(self, fail_ping=False):
                self.fail_ping = fail_ping
                self.closed = False

            def ping(self):
                if self.fail_ping:
                    raise OSError('DPY-4011 socket closed')

            def close(self):
                self.closed = True

        healthy = _Conn(fail_ping=False)
        self.assertIs(oracle_source.ensure_oracle_conn(healthy, {}), healthy)

        stale = _Conn(fail_ping=True)
        fresh = _Conn(fail_ping=False)
        with patch.object(oracle_source, 'connect_oracle', return_value=fresh) as mock_conn:
            got = oracle_source.ensure_oracle_conn(stale, {'host': 'h'})
        self.assertIs(got, fresh)
        self.assertTrue(stale.closed)
        mock_conn.assert_called_once_with({'host': 'h'})

    def test_map_flight_draft_preserves_native_acdm_fields_and_indicators(self):
        row = {
            'FLIGHT_ID': 8800123,
            'ASSOCIATED_FLIGHT_ID': 8800122,
            'FFID': 'MU-2501-WUH-SHA',
            'AIRLINE_TWO_CHAR_CD': 'MU',
            'FLIGHT_NO': 'MU2501',
            'EXEC_DATE': datetime(2026, 11, 10, 0, 0, 0),
            'PLAN_TIME': datetime(2026, 11, 10, 8, 15, 0),
            'IS_OFF_IN': 'D',
            'FLIGHT_TASK_CD': 'W/Z',
            'ATTRIBUTE': '2403',
            'S_CNL_STAUTS': 'N',
            'ORI_ACT': 'N',
            'FLIGHT_FIRST_ON_TIME': 'N',
            'RELEASE_ON_TIME': 'Y',
            'TAKEOFF_ON_TIME': 'N',
            'DEP_ON_TIME': 'Y',
            'LAND_ON_TIME': 'Y',
            'ARR_ON_TIME': 'N',
            'FLIGHT_ON_TIME': 'N',
            'CLEARANCE_DELAY_TIME': 0,
            'TAKEOFF_DELAY_TIME': 18,
            'DEP_DELAY_TIME': 4,
            'LAND_DELAY_TIME': 0,
            'ARR_DELAY_TIME': 12,
            'FLIGHT_DELAY_TIME': 18,
            'REAL_CLOSE_CABIN_TIME': datetime(2026, 11, 10, 8, 25, 0),
            'REAL_TAXI_OUT_TIME': 14.5,
            'UPDATE_TIME': datetime(2026, 11, 10, 9, 0, 0),
        }
        draft = transforms.map_flight_draft(row, 'ACDM_ORACLE', True, airport_code='WUH')
        self.assertEqual(draft['flight_task_cd'], 'W/Z')
        self.assertEqual(draft['flight_attribute'], '2403')
        self.assertEqual(draft['flight_country_type'], 'D')
        self.assertEqual(draft['associated_flight_id'], 8800122)
        self.assertEqual(draft['cancel_period'], 'N')
        self.assertIsNotNone(draft['plan_time'])
        self.assertFalse(draft['ori_act'])
        self.assertFalse(draft['flight_first_on_time'])
        self.assertTrue(draft['release_on_time'])
        self.assertFalse(draft['takeoff_on_time'])
        self.assertTrue(draft['dep_on_time'])
        self.assertTrue(draft['land_on_time'])
        self.assertFalse(draft['arr_on_time'])
        self.assertFalse(draft['flight_on_time'])
        self.assertEqual(draft['takeoff_delay_minutes'], 18)
        self.assertEqual(draft['arr_delay_minutes'], 12)
        self.assertEqual(draft['real_taxi_out_time'], 14.5)
        self.assertIsNotNone(draft['real_close_cabin_time'])

    def test_map_calc_total_air_bridge_safeguard_drafts(self):
        calc = transforms.map_calc_total_draft({
            'FLIGHT_ID': 12345,
            'FFID': 'CZ-3101',
            'EXEC_DATE': datetime(2026, 11, 10, 0, 0, 0),
            'IS_OFF_IN': '1',
            'AIRLINE_TWO_CHAR_CD': 'CZ',
            'FLIGHT_NO': '3101',
            'DEP_DPSR_DEPE_NUM': 162,
            'BAG_NUM': 94,
            'UPDATE_TIME': datetime(2026, 11, 10, 10, 0, 0),
        })
        self.assertEqual(calc['source_flight_id'], 12345)
        self.assertEqual(calc['flight_no'], 'CZ3101')
        self.assertEqual(calc['direction'], 'DEP')
        self.assertEqual(calc['dep_dpsr_depe_num'], 162)
        self.assertEqual(calc['bag_num'], 94)

        bridge = transforms.map_air_bridge_draft({
            'FLIGHT_ID': 12345,
            'ORDER_CD': 1,
            'AIR_BRIDGE_CD': '116L',
            'AIR_BRIDGE_DURATION': 42.5,
            'AIR_BRIDGE_START_TIME': datetime(2026, 11, 10, 9, 0, 0),
        })
        self.assertEqual(bridge['source_flight_id'], 12345)
        self.assertEqual(bridge['air_bridge_cd'], '116L')
        self.assertEqual(bridge['bridge_duration'], 42.5)

        sg = transforms.map_safeguard_node_draft({
            'FLIGHT_ID': 12345,
            'SAFEGUARD_ORDER_CD': 2,
            'SAFEGUARD_CODE': 'SG09',
            'SAFEGUARD_REAL_TIME': datetime(2026, 11, 10, 9, 12, 0),
        })
        self.assertEqual(sg['source_flight_id'], 12345)
        self.assertEqual(sg['safeguard_order_cd'], 2)
        self.assertEqual(sg['safeguard_code'], 'SG09')


if __name__ == '__main__':
    unittest.main()
