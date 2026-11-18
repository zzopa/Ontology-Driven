"""Load factor must use passengers and seats from the same eligible flights."""
import sqlite3
import unittest

from scripts.ops_ask.seed_acdm_handbook import METRICS


class LoadFactorTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('''CREATE TABLE flights (is_off_in TEXT, is_operated_flight TEXT,
            flight_task_cd TEXT, fpa_available_seat INTEGER, dep_dpsr_depe_num INTEGER,
            fpa_adult_count INTEGER, fpa_children_count INTEGER, fpa_baby_count INTEGER)''')
        self.metrics = {m['code']: m for m in METRICS}

    def value(self, code):
        expression = self.metrics[code]['aggregate_expression']
        return self.db.execute('SELECT ' + expression + ' FROM flights c').fetchone()[0]

    def test_missing_zero_and_negative_seats_exclude_the_same_passengers(self):
        self.db.executemany('INSERT INTO flights VALUES (?,?,?,?,?,?,?,?)', [
            ('D', 'Y', 'W/Z', 100, 80, 60, 0, 0),
            ('D', 'Y', 'W/Z', 300, 210, 200, 0, 0),
            ('A', 'Y', 'W/Z', 200, 0, 100, 0, 0),
            ('D', 'Y', 'W/Z', 0, 50, 0, 0, 0),
            ('D', 'Y', 'W/Z', None, 25, 0, 0, 0),
            ('D', 'Y', 'W/Z', -50, 10, 0, 0, 0),
            ('A', 'Y', 'W/Z', None, 0, 30, 0, 0),
            ('D', 'N', 'W/Z', 100, 100, 0, 0, 0),
            ('D', 'Y', 'A/N', 100, 100, 0, 0, 0),
        ])
        self.assertEqual(self.value('total_load_factor'), 65)
        self.assertEqual(self.value('dep_load_factor'), 72.5)
        self.assertEqual(self.value('arr_load_factor'), 50)
        self.assertEqual(self.value('pax_total_sum'), 505)

    def test_no_valid_seat_sample_returns_unknown_instead_of_zero_or_infinity(self):
        self.db.execute("INSERT INTO flights VALUES ('D','Y','W/Z',NULL,80,0,0,0)")
        for code in ('total_load_factor', 'dep_load_factor', 'arr_load_factor'):
            with self.subTest(metric=code):
                self.assertIsNone(self.value(code))

    def test_published_definitions_keep_the_seat_condition(self):
        from test_acdm_handbook import _load_ops_catalog
        catalog = _load_ops_catalog()[-1]
        for code in ('total_load_factor', 'dep_load_factor', 'arr_load_factor'):
            self.assertEqual(catalog.metrics[code]['aggregate_expression'], self.metrics[code]['aggregate_expression'])
            self.assertEqual(catalog.metrics[code]['default_filters']['fpa_available_seat'], {'op': '>', 'value': 0})
            self.assertEqual(catalog.metrics[code]['default_filters']['is_operated_flight'], 'Y')
            if code != 'total_load_factor':
                self.assertEqual(catalog.metrics[code]['default_filters']['direction'], 'DEP' if code == 'dep_load_factor' else 'ARR')
