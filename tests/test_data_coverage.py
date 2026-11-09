import unittest
from app.coverage import DataCoverageService
from fixtures import fixture_catalog


class DataCoverageTests(unittest.TestCase):
    def setUp(self):
        self.catalog = fixture_catalog()
        self.coverage_service = DataCoverageService(self.catalog)

    def test_in_coverage_query(self):
        plan = {
            'subject': 'contract_main',
            'conditions': [{'table': 'contract_main', 'col': 'signing_at', 'op': 'prefix', 'value': '2026-05'}]
        }
        res = self.coverage_service.assess_plan(plan, {'start': '2026-01-01', 'end': '2026-06-30'})
        self.assertEqual(res['status'], 'AVAILABLE')
        self.assertIsNone(res['message'])

    def test_out_of_coverage_future_date(self):
        plan = {
            'subject': 'contract_main',
            'conditions': [{'table': 'contract_main', 'col': 'signing_at', 'op': 'prefix', 'value': '2026-09-01'}]
        }
        res = self.coverage_service.assess_plan(plan, {'start': '2026-01-01', 'end': '2026-06-30'})
        self.assertEqual(res['status'], 'OUT_OF_COVERAGE')
        self.assertIn('超出覆盖范围', res['message'])
        self.assertEqual(res['coverage_end'], '2026-06-30')

    def test_no_coverage_configured_is_available(self):
        plan = {
            'subject': 'contract_main',
            'conditions': [{'table': 'contract_main', 'col': 'signing_at', 'op': 'prefix', 'value': '2026-09-01'}]
        }
        res = self.coverage_service.assess_plan(plan, None)
        self.assertEqual(res['status'], 'AVAILABLE')


if __name__ == '__main__':
    unittest.main()
