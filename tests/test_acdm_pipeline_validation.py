from datetime import date
import json
from pathlib import Path
import unittest

from scripts.ops_ask.validate_acdm_pipeline import reference_sql, validate


class AcdmPipelineValidationTests(unittest.TestCase):
    def test_reference_windows_are_bound_to_the_native_field(self):
        sql = reference_sql("SELECT COUNT(*) FROM t WHERE TRUNC(t.actual_time)=DATE '2026-06-07';")
        self.assertIn('t.actual_time >= :period_start AND t.actual_time < :period_end', sql)
        self.assertNotIn('2026-06-07', sql)

    def test_reference_rejects_unbound_dates_and_writes(self):
        for sql in ["SELECT DATE '2026-06-07' FROM dual", 'DELETE FROM flights']:
            with self.assertRaises(ValueError):
                reference_sql(sql)
        with self.assertRaises(ValueError):
            validate(date(2026, 11, 15), date(2026, 11, 2), Path('unused'))

    def test_every_reference_is_parameterized_and_has_a_unique_metric(self):
        doc = json.loads((Path(__file__).parent / 'fixtures/acdm_oracle_reference.json').read_text(encoding='utf-8'))
        self.assertEqual(len({c['metric'] for c in doc['cases']}), len(doc['cases']))
        for case in doc['cases']:
            with self.subTest(metric=case['metric']):
                sql = reference_sql(doc['sql'][case['reference']])
                self.assertIn(':period_start', sql)
                self.assertIn(':period_end', sql)
