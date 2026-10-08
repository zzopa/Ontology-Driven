"""Opt-in integration tests use transaction-local TEMP tables, never business writes."""
import os
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from app import core
from app.config import settings
from app.ontology import Catalog
from app.query import Compiler, Statement, execute
from app.planner import Planner
from fixtures import fixture_catalog


@unittest.skipUnless(os.environ.get('HBASK_INTEGRATION')=='1','set HBASK_INTEGRATION=1 for local PostgreSQL TEMP tests')
class PostgresTests(unittest.TestCase):
    def setUp(self):
        import pg8000.dbapi as pg
        self.conn=pg.connect(**settings.db)
        self.catalog=fixture_catalog('pg_temp')
        self.compiler=Compiler(self.catalog)
        cursor=self.conn.cursor()
        for table in self.catalog.schema['tables']:
            cursor.execute('CREATE TEMP TABLE '+table['name']+' ('+
                ','.join(c['name']+' '+c['type'] for c in table['columns'])+', PRIMARY KEY (id)) ON COMMIT DROP')
        cursor.execute("INSERT INTO comp_employee VALUES (1,'李康平','SCC-XK'),(2,'李康平','OTHER')")
        cursor.execute("INSERT INTO comp_payroll_monthly VALUES (1,1,100,'2026-09'),(2,1,200,'2026-10'),(3,2,800,'2026-10')")
        cursor.execute("INSERT INTO contract_main SELECT n,'C-'||n,10,'2026-01-01','SCC-XK' FROM generate_series(1,60) n")
        cursor.execute("INSERT INTO fund_flow_record SELECT n,n,null,5,10,'CNY','01','2026-01-01','SCC-XK' FROM generate_series(1,60) n")
        cursor.execute("INSERT INTO fund_flow_record VALUES (61,1,null,9,10,'USD','01','2026-01-01','SCC-XK'),(62,1,null,null,10,'CNY','02','2026-01-01','SCC-XK')")
        cursor.execute('INSERT INTO triple_major_meeting_record VALUES (1,%s,%s,%s::jsonb),(2,%s,%s,%s::jsonb)',
            ('M-1','SCC-XK','{"会议名称":"会议一","开始时间":"2026-09-01"}',
             'M-2','SCC-XK','{"会议名称":"会议二","开始时间":"2026-10-01"}'))
        cursor.close()

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_related_count_includes_more_than_fifty_anchors(self):
        plan={'subject':'contract_main','intent':'detail','conditions':[]}
        rows=execute(self.conn,self.compiler.detail(plan,self.catalog.links('contract_main',['fund_flow_record']),50,20))
        self.assertEqual(max(r['total_count'] for r in rows if r['bucket']=='__main'),60)
        self.assertEqual(max(r['total_count'] for r in rows if r['bucket']=='fund_flow_record'),62)

    def test_surname_is_a_company_scoped_prefix_not_contains(self):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO pg_temp.comp_employee VALUES (3,'夏测试','SCC-XK'),(4,'李夏测试','SCC-XK'),(5,'夏其他','OTHER'),(6,'欧阳测试','SCC-XK')")
        cursor.close()
        for question, expected in [('信科公司夏姓员工的信息', '夏测试'), ('信科公司欧阳姓员工的信息', '欧阳测试')]:
            plan = Planner(self.catalog).parse(question)[0]
            result = execute(self.conn, self.compiler.detail(plan, [], 5, 20))
            matches = [r for r in result if r['bucket'] == '__main']
            self.assertEqual(matches[0]['total_count'], 1)
            self.assertEqual(matches[0]['row_data']['full_name'], expected)

    def test_related_aggregate_does_not_duplicate_and_separates_currency(self):
        plan={'subject':'contract_main','intent':'sum','aggregate_target':'fund_flow_record','conditions':[]}
        rows=execute(self.conn,self.compiler.aggregate(plan))
        by_group={(r['currency'],r['transaction_type']):r for r in rows}
        self.assertEqual(by_group['CNY','01']['total'],300)
        self.assertEqual(by_group['USD','01']['total'],9)
        self.assertEqual(by_group['CNY','02']['missing'],1)

    def test_reconciled_target_keeps_company_scope_and_matches_reference_sql(self):
        cursor=self.conn.cursor()
        cursor.execute("INSERT INTO pg_temp.contract_main VALUES (999,'OTHER',20,'2026-01-01','OTHER')")
        cursor.execute("INSERT INTO pg_temp.fund_flow_record VALUES (999,999,null,999,10,'CNY','01','2026-01-01','OTHER')")
        cursor.close()
        def model(*args,**kwargs):
            return json.dumps({'subject':'fund_flow_record','intent':'count',
                               'conditions':[],'focus_tables':[]})
        plan,_=Planner(self.catalog,chat=model).parse('信科合同实际付款数量')
        actual=execute(self.conn,self.compiler.aggregate(plan))[0]['n']
        reference=execute(self.conn,Statement('SELECT COUNT(DISTINCT f.id) AS n '
            'FROM pg_temp.contract_main c JOIN pg_temp.fund_flow_record f ON f.related_contract_id=c.id '
            'WHERE c.enterprise_scc=%s',('SCC-XK',)))[0]['n']
        self.assertEqual(actual,reference)
        self.assertEqual(actual,62)

    def test_related_filter_does_not_duplicate_root(self):
        plan={'subject':'comp_employee','intent':'count','conditions':[
            {'table':'comp_payroll_monthly','col':'amount','op':'>','value':50}]}
        self.assertEqual(execute(self.conn,self.compiler.aggregate(plan))[0]['n'],2)

    def test_salary_identity_reference_sql_matches(self):
        plan={'subject':'comp_payroll_monthly','intent':'sum','conditions':[
            {'table':'comp_employee','col':'full_name','op':'=','value':'李康平'},
            {'table':'comp_employee','col':'enterprise_scc','op':'=','value':'SCC-XK'}]}
        actual=execute(self.conn,self.compiler.aggregate(plan))[0]['total']
        expected=execute(self.conn,Statement("SELECT SUM(amount) AS total FROM pg_temp.comp_payroll_monthly WHERE employee_id=1"))[0]['total']
        self.assertEqual(actual,expected)

    def test_meeting_json_time_reference_matches(self):
        plan={'subject':'triple_major_meeting_record','intent':'count','conditions':[],
              'time_bucket':{'field':'meeting_time','grain':'month'}}
        rows=execute(self.conn,self.compiler.aggregate(plan))
        self.assertEqual([(r['__period'],r['n']) for r in rows],[('2026-09',1),('2026-10',1)])

    def test_pagination_can_reach_last_related_record(self):
        rows=execute(self.conn,self.compiler.page({'subject':'contract_main','intent':'detail','conditions':[]},'fund_flow_record',4,20))
        self.assertEqual(len(rows),2)

    def test_json_stored_as_text_can_still_group(self):
        cursor=self.conn.cursor()
        cursor.execute('ALTER TABLE pg_temp.triple_major_meeting_record ALTER COLUMN fields_json TYPE text USING fields_json::text')
        cursor.close()
        plan={'subject':'triple_major_meeting_record','intent':'count','conditions':[],
              'time_bucket':{'field':'meeting_time','grain':'month'}}
        rows=execute(self.conn,self.compiler.aggregate(plan))
        self.assertEqual([(r['__period'],r['n']) for r in rows],[('2026-09',1),('2026-10',1)])

    def test_same_name_requires_company(self):
        with patch.object(core,'CATALOG',self.catalog),patch.object(core,'NAME_INDEX',{'comp_employee':{'李康平'}}),patch.object(core,'MEM',{'entries':[]}):
            with self.assertRaisesRegex(ValueError,'同名'):
                core.do_ask(self.conn,'李康平情况',use_model=False)

    def test_composite_join_requires_all_keys(self):
        document=deepcopy(self.catalog.overrides)
        document['logical_links']=[{'pairs':[['fund_flow_record.related_contract_id','contract_main.id'],
                                            ['fund_flow_record.our_unit_scc','contract_main.enterprise_scc']]}]
        catalog=Catalog(self.catalog.schema,document,self.catalog.settings)
        cursor=self.conn.cursor()
        cursor.execute("UPDATE pg_temp.fund_flow_record SET our_unit_scc='WRONG' WHERE id=1")
        cursor.close()
        plan={'subject':'contract_main','intent':'count','aggregate_target':'fund_flow_record','conditions':[]}
        self.assertEqual(execute(self.conn,Compiler(catalog).aggregate(plan))[0]['n'],61)
