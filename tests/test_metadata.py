import unittest

from app.metadata import build_graph


class MetadataTests(unittest.TestCase):
    def test_graph_uses_live_columns_and_skips_stale_relations(self):
        schema = {
            'database': 'hbairport01',
            'tables': [
                {'table': 'contract_main', 'est_rows': 1,
                 'primary_key': ['id'],
                 'columns': [
                     {'name': 'id', 'type': 'uuid', 'nullable': False, 'comment': '主键'},
                     {'name': 'enterprise_scc', 'type': 'text', 'nullable': True,
                      'comment': '统一社会信用代码'}]},
                {'table': 'comp_enterprise_ledger', 'est_rows': 2,
                 'primary_key': ['id'],
                 'columns': [
                     {'name': 'id', 'type': 'uuid', 'nullable': False, 'comment': ''},
                     {'name': 'enterprise_scc', 'type': 'text', 'nullable': False,
                      'comment': ''}]},
            ],
            'foreign_keys': [
                {'table': 'public.contract_main', 'column': 'enterprise_scc',
                 'ref_table': 'public.comp_enterprise_ledger',
                 'ref_column': 'enterprise_scc'}],
        }
        graph = build_graph(schema, [
            {'source': 'contract_main.enterprise_scc',
             'target': 'comp_enterprise_ledger.enterprise_scc', 'note': '重复关系'},
            {'source': 'contract_main.deleted_field',
             'target': 'comp_enterprise_ledger.id', 'note': '旧关系'},
        ])
        self.assertEqual(graph['stats']['tables'], 2)
        self.assertEqual(graph['stats']['fields'], 4)
        self.assertEqual(graph['stats']['fks'], 1)
        self.assertEqual(graph['stats']['logical_links'], 0)
        self.assertEqual(len(graph['stats']['skipped_logical_links']), 1)
        self.assertEqual(graph['field_nodes'][1]['comment'], '统一社会信用代码')


if __name__ == '__main__':
    unittest.main()
