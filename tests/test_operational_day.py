"""Tests for Operational Day (04:00 日切) calculation and window constraints."""
import unittest
from datetime import date, datetime
from app.agent.operational_day import (
    get_operational_date,
    get_operational_window,
    get_operational_hour,
    build_operational_conditions
)


class OperationalDayTests(unittest.TestCase):

    def test_operational_date_before_and_after_cutoff(self):
        # 2026-05-18 02:30 -> still belongs to operational day 2026-05-17
        dt_night = datetime(2026, 5, 18, 2, 30, 0)
        self.assertEqual(get_operational_date(dt_night, start_hour=4), date(2026, 5, 17))

        # 2026-05-18 04:00 -> belongs to operational day 2026-05-18
        dt_morning = datetime(2026, 5, 18, 4, 0, 0)
        self.assertEqual(get_operational_date(dt_morning, start_hour=4), date(2026, 5, 18))

        # 2026-05-18 23:59 -> belongs to operational day 2026-05-18
        dt_late = datetime(2026, 5, 18, 23, 59, 59)
        self.assertEqual(get_operational_date(dt_late, start_hour=4), date(2026, 5, 18))

    def test_operational_window_single_day(self):
        d = date(2026, 5, 18)
        win_start, win_end = get_operational_window(d, start_hour=4)
        self.assertEqual(win_start, datetime(2026, 5, 18, 4, 0, 0))
        self.assertEqual(win_end, datetime(2026, 5, 19, 4, 0, 0))

    def test_operational_window_range(self):
        d_start = date(2026, 5, 18)
        d_end = date(2026, 5, 20)
        win_start, win_end = get_operational_window(d_start, d_end, start_hour=4)
        self.assertEqual(win_start, datetime(2026, 5, 18, 4, 0, 0))
        self.assertEqual(win_end, datetime(2026, 5, 21, 4, 0, 0))

    def test_operational_hour_indexing(self):
        self.assertEqual(get_operational_hour(datetime(2026, 5, 18, 4, 0, 0)), 0)
        self.assertEqual(get_operational_hour(datetime(2026, 5, 18, 5, 15, 0)), 1)
        self.assertEqual(get_operational_hour(datetime(2026, 5, 18, 23, 0, 0)), 19)
        self.assertEqual(get_operational_hour(datetime(2026, 5, 18, 0, 30, 0)), 20)
        self.assertEqual(get_operational_hour(datetime(2026, 5, 18, 3, 59, 59)), 23)

    def test_build_operational_conditions(self):
        conds = build_operational_conditions('2026-05-18', 'flight_time', start_hour=4, table='flight_records')
        self.assertEqual(len(conds), 2)
        self.assertEqual(conds[0], {
            'table': 'flight_records',
            'col': 'flight_time',
            'op': '>=',
            'value': '2026-05-18 04:00:00'
        })
        self.assertEqual(conds[1], {
            'table': 'flight_records',
            'col': 'flight_time',
            'op': '<',
            'value': '2026-05-19 04:00:00'
        })


if __name__ == '__main__':
    unittest.main()
