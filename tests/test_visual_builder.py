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


if __name__ == '__main__':
    unittest.main()
