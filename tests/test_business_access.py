import json
import os
from dataclasses import replace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import HTTPException
from app import business_access as business
from app.admin import service
from app.admin.store import AdminStore
from app.query import Compiler
from fixtures import fixture_catalog
from test_admin import http, INITIAL


def access(**changes):
    values = dict(user_id='11111111-1111-1111-1111-111111111111', username='test', display_name='测试',
                  scope='DEPT_ONLY', roles=('reporter',), indicators=None, permissions=None,
                  companies=frozenset({'scc-a'}), revision='revision-1')
    return business.Access(**(values | changes))


class BusinessScopeTests(unittest.TestCase):
    def setUp(self):
        self.catalog = fixture_catalog()

    def test_department_scope_means_company_not_submitter_department(self):
        statement = Compiler(self.catalog, access()).aggregate({'subject': 'contract_main', 'intent': 'count'})
        self.assertIn('lower(trim(a."enterprise_scc")) IN (%s)', statement.sql)
        self.assertNotIn('submitter_department_id', statement.sql)
        self.assertEqual(statement.params, ('scc-a',))

    def test_scope_and_user_params_follow_sql_order(self):
        statement = Compiler(self.catalog, access()).detail({'subject': 'contract_main', 'intent': 'detail',
            'conditions': [{'col': 'contract_no', 'op': '=', 'value': "x' OR true --"}]}, [], 10, 5)
        self.assertEqual(statement.params, ('scc-a', "x' OR true --"))
        self.assertNotIn("x' OR true --", statement.sql)
        self.assertNotIn(':hb_scope_', statement.sql)

    def test_all_related_sources_and_nested_condition_sources_are_scoped(self):
        plan = {'subject': 'contract_main', 'intent': 'detail', 'focus_tables': ['fund_flow_record'],
                'conditions': [{'table': 'fund_flow_record', 'col': 'transaction_amount', 'op': '>', 'value': 2}]}
        compiler = Compiler(self.catalog, access(companies=frozenset({'a','b'})))
        statement = compiler.detail(plan, self.catalog.links('contract_main', ['fund_flow_record']), 5, 5)
        self.assertEqual(statement.sql.count('%s'), len(statement.params))
        self.assertGreaterEqual(statement.sql.count('lower(trim(a."enterprise_scc"))'), 3)
        self.assertGreaterEqual(statement.sql.count('lower(trim(a."our_unit_scc"))'), 2)
        self.assertEqual(statement.params[:4], ('a','b','a','b'))

    def test_admin_and_all_scope_do_not_add_row_limits(self):
        for account in (access(scope='ALL'), access(roles=('admin',),companies=frozenset())):
            statement = Compiler(self.catalog, account).aggregate({'subject':'contract_main','intent':'count'})
            self.assertNotIn(' IN (', statement.sql)
            self.assertEqual(statement.params, ())

    def test_profile_reports_effective_scope_without_changing_original_account_scope(self):
        for configured in ('DEPT_ONLY', 'DEPT_TREE', 'SELF_ONLY', 'ALL'):
            with self.subTest(scope=configured):
                account = access(scope=configured, roles=('admin',))
                profile = account.profile()
                self.assertEqual(profile['data_scope'], 'ALL')
                self.assertEqual(profile['configured_data_scope'], configured)
                self.assertEqual(account.scope, configured)
                self.assertTrue(profile['can_manage'])
                statement = Compiler(self.catalog, account).aggregate({'subject':'contract_main','intent':'count'})
                self.assertEqual(statement.params, ())
        for configured in ('DEPT_ONLY', 'DEPT_TREE', 'SELF_ONLY', 'ALL'):
            with self.subTest(ordinary_scope=configured):
                profile = access(scope=configured).profile()
                self.assertEqual(profile['data_scope'], configured)
                self.assertEqual(profile['configured_data_scope'], configured)
                self.assertFalse(profile['can_manage'])
        named_admin = access(username='admin')
        self.assertEqual(named_admin.profile()['data_scope'], 'DEPT_ONLY')
        self.assertFalse(named_admin.profile()['can_manage'])

    def test_all_scope_still_has_indicator_and_permission_limits(self):
        account = access(scope='ALL', indicators=frozenset({'fund-flow'}))
        self.assertFalse(account.allowed('contract_main'))
        self.assertTrue(account.allowed('fund_flow_record'))
        with self.assertRaises(HTTPException):
            Compiler(self.catalog, account).aggregate({'subject':'contract_main','intent':'count'})

    def test_unknown_business_tables_are_never_inferred_public(self):
        self.assertFalse(access(roles=('admin',)).allowed('app_user'))
        with self.assertRaises(HTTPException):
            Compiler(self.catalog, access(roles=('admin',))).aggregate({'subject':'project_budget','intent':'count'})

    def test_missing_department_does_not_grant_all(self):
        statement = Compiler(self.catalog, access(companies=frozenset())).aggregate({'subject':'contract_main','intent':'count'})
        self.assertIn('WHERE FALSE', statement.sql)

    def test_self_only_requires_submitter_and_company(self):
        self.catalog.tables['contract_main']['columns'].append({'name':'submitter_user_id','type':'uuid'})
        statement = Compiler(self.catalog, access(scope='SELF_ONLY')).aggregate({'subject':'contract_main','intent':'count'})
        self.assertIn('a."submitter_user_id" = %s', statement.sql)
        self.assertEqual(statement.params, ('scc-a', access().user_id))

    def test_catalog_names_and_adjacency_cannot_expose_ungranted_modules(self):
        account = access(indicators=frozenset({'basic-contract'}),permissions=frozenset({'basic-contract:view'}))
        catalog = account.catalog(self.catalog)
        self.assertIn('contract_main',catalog.entities)
        self.assertNotIn('fund_flow_record',catalog.entities)
        self.assertEqual(catalog.adjacency['contract_main'],[])
        self.assertNotIn('fund_flow_record',catalog.context('合同'))
        self.assertIn('fund_flow_record',self.catalog.entities)

    def test_enterprise_tree_uses_original_company_mapping(self):
        group=business.POLICY['group_scc']
        parent='91420116mactdg7e9w'
        child='91370102ma3tmq7r5g'
        sccs=[group,parent,child,'unknown-company']
        self.assertEqual(business.subtree(parent,sccs),frozenset({parent,child}))
        self.assertEqual(business.subtree(group,sccs),frozenset(sccs))

    def test_legacy_empty_and_menu_derived_indicator_rules_match_java(self):
        self.assertIsNone(business.merged_indicators([],[]))
        self.assertIsNone(business.merged_indicators([{'indicator_visibility':'RESTRICTED','indicator_codes_csv':''}],[]))
        self.assertEqual(business.merged_indicators([{'indicator_visibility':'RESTRICTED','indicator_codes_csv':'basic-contract'}],
            ['filling.compensation-offboarding.segment.personnel-master']),
            frozenset({'basic-contract','compensation-offboarding.segment.personnel-master'}))

    def test_segment_grants_cannot_grant_other_segments(self):
        account=access(indicators=frozenset({'compensation-offboarding.segment.personnel-master'}))
        self.assertTrue(account.allowed('comp_employee'))
        self.assertFalse(account.allowed('comp_payroll_monthly'))

    def test_payroll_segment_fields_are_removed_from_sql_and_catalog(self):
        columns=self.catalog.tables['comp_payroll_monthly']['columns']
        columns.extend([{'name':'enterprise_scc','type':'text'},{'name':'cash_incentive_amount','type':'numeric'}])
        self.catalog.entities['comp_payroll_monthly']['attributes']['cash_incentive_amount']={'type':'numeric'}
        account=access(indicators=frozenset({'compensation-offboarding.segment.monthly-payroll'}),
                       permissions=frozenset({'compensation-offboarding.segment.monthly-payroll:view'}))
        sql,_=account.source('comp_payroll_monthly',self.catalog)
        self.assertNotIn('cash_incentive_amount',sql)
        self.assertNotIn('cash_incentive_amount',account.catalog(self.catalog).entities['comp_payroll_monthly']['attributes'])

    def test_page_permissions_change_invalidates_token(self):
        from app import core
        account=access()
        scoped=account.catalog(self.catalog)
        token=core.token_for({'subject':'contract_main','intent':'detail'},scoped,1)
        with patch.object(core,'current_catalog',return_value=self.catalog):
            core.saved_query(token,1,account)
            with self.assertRaises(ValueError):
                core.saved_query(token,1,replace(account,revision='revoked'))
        core.DRILL_QUERIES.pop(token,None)


class BusinessApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=AdminStore(self.temp.name,INITIAL)
        for mock in (patch.object(service,'get_store',return_value=self.store),):
            mock.start();self.addCleanup(mock.stop)

    async def test_guests_and_local_admin_do_not_bypass_business_login(self):
        self.assertEqual((await http('/api/ask','POST',{'question':'合同多少份'}))[0],401)
        self.assertEqual((await http('/api/ask','POST',{'question':'合同多少份'},cookie='hbask_session=obsolete-token'))[0],401)

    async def test_login_sets_shared_http_only_cookie_and_no_password_in_response(self):
        with patch.object(business,'login',return_value=('opaque-business-token',access().profile())):
            status,headers,value=await http('/api/business-auth/login','POST',{'username':'test','password':'secret'})
        self.assertEqual(status,200)
        self.assertIn('hbask_business_session=',headers['set-cookie'])
        self.assertIn('HttpOnly',headers['set-cookie'])
        self.assertNotIn('secret',json.dumps(value))

    async def test_history_snapshot_and_trace_hidden_after_revocation(self):
        account=access()
        cid,mid=self.store.begin_question(account.owner,'合同查询','test')
        self.store.complete_question(mid,{'access_revision':'old','main_rows':[{'private':123}], 'execution_trace':[{'private':456}]})
        with patch.object(business,'identity',return_value={'owner':account.owner,'user':account.profile(),'access':account}):
            status,_,data=await http('/api/business-conversations/'+cid)
        self.assertEqual(status,200)
        self.assertIsNone(data['messages'][0]['result'])
        self.assertIsNone(data['messages'][0]['execution_trace'])

    async def test_business_account_cannot_read_all_users_history(self):
        account=access(roles=('admin',))
        with patch.object(business,'identity',return_value={'owner':account.owner,'user':account.profile(),'access':account}):
            self.assertEqual((await http('/api/business-conversations?scope=all'))[0],403)

    async def test_local_admin_cannot_export_business_snapshot(self):
        account=access()
        cid,mid=self.store.begin_question(account.owner,'业务会话','test')
        self.store.complete_question(mid,{'access_revision':account.revision,'main_rows':[{'private':123}]})
        cookie='hbask_session=obsolete-token'
        self.assertEqual((await http('/api/conversations/'+cid+'?scope=all',cookie=cookie))[0],401)
        self.assertEqual((await http('/api/conversations?scope=all',cookie=cookie))[0],401)

    async def test_bcrypt_existing_account_login_and_disabled_account_denial(self):
        import bcrypt
        from app import core
        password='bcrypt-test-password'
        hashed=bcrypt.hashpw(password.encode(),bcrypt.gensalt(rounds=4)).decode()
        row={'id':access().user_id,'password_hash':hashed,'enabled':True}
        for variant in (hashed,hashed.replace('$2b$','$2a$')):
            with patch.object(core,'connect_db',return_value=SimpleNamespace(close=lambda:None)), \
                 patch.object(business,'execute',return_value=[row | {'password_hash':variant}]), \
                 patch.object(business,'load_access',return_value=access()):
                token,profile=business.login('test',password,'127.0.0.1')
                self.assertEqual(profile['id'],access().user_id)
                with self.store.db() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM business_credentials').fetchone()[0],1)
                business.logout(token)
        with patch.object(core,'connect_db',return_value=SimpleNamespace(close=lambda:None)), \
             patch.object(business,'execute',return_value=[row | {'enabled':False}]):
            with self.assertRaises(HTTPException) as error:
                business.login('test',password,'127.0.0.1')
            self.assertEqual(error.exception.status_code,401)

    async def test_pre_migration_local_snapshots_cannot_bypass_business_permissions(self):
        cid,mid=self.store.begin_question('user:legacy-id','旧查询','test')
        self.store.complete_question(mid,{'main_rows':[{'private':123}]})
        self.assertEqual((await http('/api/conversations/'+cid,cookie='hbask_session=obsolete-token'))[0],401)
        account=access(roles=('admin',))
        with patch.object(business,'identity',return_value={'owner':account.owner,'user':account.profile(),'access':account}):
            self.assertEqual((await http('/api/conversations/'+cid))[0],400)


@unittest.skipUnless(os.environ.get('HBASK_INTEGRATION')=='1','opt-in PostgreSQL TEMP-table tests')
class BusinessPostgresTests(unittest.TestCase):
    def setUp(self):
        from test_postgres_integration import PostgresTests
        PostgresTests.setUp(self)
        self.account=access(companies=frozenset({'scc-xk'}))
        self.compiler=Compiler(self.catalog,self.account)
        cur=self.conn.cursor()
        cur.execute("INSERT INTO contract_main VALUES (100,'OTHER',999,'2026-01-01','OTHER')")
        cur.execute("INSERT INTO fund_flow_record VALUES (100,1,null,999,0,'CNY','01','2026-01-01','OTHER')")
        cur.execute("INSERT INTO fund_flow_record VALUES (101,100,null,777,0,'CNY','01','2026-01-01','SCC-XK')")
        cur.close()

    def tearDown(self):
        self.conn.rollback();self.conn.close()

    def test_count_and_sum_only_include_authorized_companies(self):
        from app.query import execute
        plan={'subject':'contract_main','intent':'count'}
        self.assertEqual(execute(self.conn,self.compiler.aggregate(plan))[0]['n'],60)
        plan['intent']='sum'
        self.assertEqual(execute(self.conn,self.compiler.aggregate(plan))[0]['total'],600)

    def test_related_records_cannot_leak_other_company_rows(self):
        from app.query import execute
        plan={'subject':'contract_main','intent':'detail'}
        rows=execute(self.conn,self.compiler.detail(plan,self.catalog.links('contract_main',['fund_flow_record']),100,100))
        main=[r for r in rows if r['bucket']=='__main']
        related=[r for r in rows if r['bucket']=='fund_flow_record']
        self.assertEqual(len(main),60)
        self.assertEqual(len(related),62)
        self.assertFalse(any(r['row_data']['id'] in (100,101) for r in related))

    def test_related_exists_filter_cannot_reveal_unauthorized_row_existence(self):
        from app.query import execute
        plan={'subject':'contract_main','intent':'count','conditions':[
            {'table':'fund_flow_record','col':'transaction_amount','op':'>','value':900}]}
        self.assertEqual(execute(self.conn,self.compiler.aggregate(plan))[0]['n'],0)

    def test_main_and_related_pagination_are_scoped(self):
        from app.query import execute
        plan={'subject':'contract_main','intent':'detail'}
        rows=execute(self.conn,self.compiler.page(plan,'contract_main',1,200))
        self.assertEqual(len(rows),60)
        rows=execute(self.conn,self.compiler.page(plan,'fund_flow_record',1,200))
        self.assertEqual(len(rows),62)

    def test_multi_scope_parameter_order_and_normalization(self):
        from app.query import execute
        compiler=Compiler(self.catalog,access(companies=frozenset({'other','scc-xk'})))
        rows=execute(self.conn,compiler.aggregate({'subject':'contract_main','intent':'count',
            'conditions':[{'col':'contract_no','op':'=','value':'OTHER'}]}))
        self.assertEqual(rows[0]['n'],1)

    def test_empty_company_scope_returns_no_records(self):
        from app.query import execute
        compiler=Compiler(self.catalog,access(companies=frozenset()))
        self.assertEqual(execute(self.conn,compiler.aggregate({'subject':'contract_main','intent':'count'}))[0]['n'],0)

    def test_full_question_uses_scoped_names_evidence_and_related_rows(self):
        from app import core
        with patch.object(core,'current_catalog',return_value=self.catalog), \
             patch.object(core,'NAME_INDEX',{'comp_employee':{'UNAUTHORIZED-NAME'}}), \
             patch.object(business,'load_access',return_value=self.account):
            result=core.do_ask(self.conn,'查找信科公司李康平的情况',use_model=False,use_cache=False,access=self.account)
        self.assertEqual(result['main_total'],1)
        self.assertEqual(result['main_rows'][0]['enterprise_scc'],'SCC-XK')
        self.assertEqual(result['related'][0]['count'],2)
        self.assertNotIn('UNAUTHORIZED-NAME',json.dumps(result))
        self.assertEqual(result['access_revision'],self.account.revision)
        for key in ('drill_token','related_token'):
            core.DRILL_QUERIES.pop(result.get(key),None)

    def test_admin_question_reports_the_same_all_scope_as_its_query(self):
        from app import core
        account = access(scope='DEPT_ONLY', roles=('admin',), companies=frozenset())
        with patch.object(core,'current_catalog',return_value=self.catalog), \
             patch.object(business,'load_access',return_value=account):
            result = core.do_ask(self.conn,'合同有多少份',use_model=False,use_cache=False,access=account)
        self.assertEqual(result['data_scope'], 'ALL')
        self.assertEqual(result['main_total'], 61)
        for key in ('drill_token','related_token'):
            core.DRILL_QUERIES.pop(result.get(key),None)


if __name__=='__main__':
    unittest.main()
