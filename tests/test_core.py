import json
import unittest

from app import core
from app.main import allowed_client
from app.evidence import sanitize
from app.query import Compiler
from fixtures import fixture_catalog


class CoreTests(unittest.TestCase):
    def test_model_rows_keep_complete_records_and_omit_empty_columns(self):
        rows = [{'name': '甲', 'amount': 10, 'empty': None},
                {'name': '乙', 'amount': 20, 'empty': None}]
        packed, count, _ = core.model_rows(rows, 1000)
        data = json.loads(packed)
        self.assertEqual(count, 2)
        self.assertEqual(data['columns'], ['name', 'amount'])
        self.assertEqual(data['rows'], [['甲', 10], ['乙', 20]])

    def test_model_rows_do_not_cut_json_mid_record(self):
        packed, count, _ = core.model_rows([{'value': 'a' * 100}], 20)
        json.loads(packed)
        self.assertEqual(count, 0)

    def test_sensitive_fields_do_not_leave_server(self):
        result = sanitize({'full_name': '测试', 'id_card': 'hidden', 'mobile': 'hidden'},
                          {'id_card', 'mobile'})
        self.assertEqual(result, {'full_name': '测试'})

    def test_count_sql_and_detail_limit(self):
        compiler = Compiler(fixture_catalog())
        plan = {'subject':'comp_employee','conditions':[],'intent':'count'}
        self.assertNotIn('LIMIT', compiler.aggregate(plan).sql)
        plan['intent']='detail'
        detail = compiler.detail(plan,[],core.GRAPH_MAIN_LIMIT,core.GRAPH_RELATED_LIMIT)
        self.assertIn('LIMIT %d' % core.GRAPH_MAIN_LIMIT, detail.sql)

    def test_lan_access_scope(self):
        from app.config import settings
        self.assertTrue(allowed_client('127.0.0.1'))
        self.assertTrue(allowed_client(str(settings.lan_cidr.network_address+25)))
        self.assertFalse(allowed_client('203.0.113.25'))
        self.assertFalse(allowed_client('172.21.16.1'))


if __name__ == '__main__':
    unittest.main()
