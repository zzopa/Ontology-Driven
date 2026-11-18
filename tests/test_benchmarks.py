import unittest
from datetime import date
from app.agent.analysis import benchmark_sources
from app.benchmarks import demo_rows, register, DATASET, TABLE


class BenchmarkTests(unittest.TestCase):
    def test_demo_is_repeatable_and_consistent(self):
        rows = list(demo_rows())
        self.assertEqual(len(rows), 93)
        self.assertEqual(rows, list(demo_rows()))
        self.assertEqual(len({(r[0], r[1]) for r in rows}), 93)
        for day, code, _, flights, pax, seats, planned, operated, simulated, dataset, _ in rows:
            self.assertIn(code, ('CSX', 'NKG', 'CGO'))
            self.assertTrue(date(2026, 10, 16) <= day <= date(2026, 11, 15))
            self.assertTrue(0 < operated <= flights and operated <= planned)
            self.assertTrue(0 < pax <= seats)
            self.assertTrue(simulated)
            self.assertEqual(dataset, DATASET)

    def test_registration_is_idempotent(self):
        doc = {'entities': {}}
        self.assertEqual(register(doc), register(doc))
        self.assertIn(TABLE, doc['entities'])
        self.assertEqual(len(doc['metrics']), 6)
        self.assertEqual(len(doc['capabilities']), 3)

    def test_source_labels_use_returned_dataset_and_simulation_flag(self):
        result = {'outcome': 'answered', 'evidence': {'facts': [
            {'source': {'database': 'ops_ask', 'table': TABLE}},
        ]}, 'main_rows': [
            {'dataset_id': DATASET, 'is_simulated': True},
            {'dataset_id': 'reported-source', 'is_simulated': False},
        ]}
        self.assertEqual(benchmark_sources(result), [
            f'固定种子生成的模拟测试数据（ops_ask.{TABLE}，数据集 {DATASET}）',
            f'本地对标数据（ops_ask.{TABLE}，数据集 reported-source）',
        ])
        self.assertEqual(benchmark_sources({**result, 'outcome': 'no_data'}), [])
