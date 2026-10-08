from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import Mock

from app.evidence import build_evidence, model_evidence, sanitize, validate_claims
from app.management import OntologyStore
from app.metadata import fingerprint, save_snapshot
from app.ontology import Catalog
from app.planner import Planner
from app.query import Compiler
from fixtures import fixture_catalog


class OntologyFlowTests(unittest.TestCase):
    def setUp(self):
        self.catalog=fixture_catalog()
        self.compiler=Compiler(self.catalog)
        self.planner=Planner(self.catalog,{'comp_employee':{'李康平'}},{})

    def test_four_domains_and_discovered_fifth_domain(self):
        cases=[('有多少员工','comp_employee','count'),('合同总金额是多少','contract_main','sum'),
               ('资金总金额是多少','fund_flow_record','sum'),('今年有多少会议','triple_major_meeting_record','count'),
               ('项目预算明细有多少条','project_budget','count')]
        for question,table,intent in cases:
            with self.subTest(question=question):
                plan=self.planner.parse(question)[0]
                self.assertEqual((plan['subject'],plan['intent']),(table,intent))

    def test_identity_and_company_are_both_preserved(self):
        plan=self.planner.parse('查找信科公司李康平的情况')[0]
        self.assertEqual({c['col'] for c in plan['conditions']},{'full_name','enterprise_scc'})

    def test_explicit_surnames_preserve_company_and_skip_model(self):
        cases = [('查找信科公司夏姓员工的信息', '夏'), ('查找信科公司姓夏的员工信息', '夏'),
                 ('信科姓李员工有多少人', '李'), ('信科公司欧阳姓员工的信息', '欧阳'),
                 ('查询信科公司姓氏为欧阳的员工', '欧阳'), ('信科公司夏侯姓员工信息', '夏侯'),
                 ('信科公司姓夏的情况', '夏')]
        chat = Mock(side_effect=AssertionError('complete surname queries do not need a model'))
        planner = Planner(self.catalog, chat=chat)
        for question, surname in cases:
            with self.subTest(question=question):
                plan, source = planner.parse(question)
                self.assertEqual(plan['subject'], 'comp_employee')
                self.assertIn({'col': 'full_name', 'op': 'prefix', 'value': surname}, plan['conditions'])
                self.assertIn({'col': 'enterprise_scc', 'op': '=', 'value': 'SCC-XK'}, plan['conditions'])
                self.assertIn('姓氏', source)
                statement = self.compiler.detail(plan, [], 5, 20)
                self.assertIn(surname + '%', statement.params)
        chat.assert_not_called()

    def test_surname_count_has_correct_grain(self):
        plan = self.planner.parse('信科公司姓夏的员工有几名')[0]
        self.assertEqual((plan['subject'], plan['intent'], plan['aggregate_target']), ('comp_employee', 'count', 'comp_employee'))

    def test_surname_rules_use_ontology_binding_not_a_hardcoded_table(self):
        schema, doc = deepcopy(self.catalog.schema), deepcopy(self.catalog.overrides)
        schema['tables'][0].update(table='staff_directory', name='staff_directory')
        doc['entities']['staff_directory'] = doc['entities'].pop('comp_employee')
        doc['relations'] = {}
        doc['logical_links'] = []
        catalog = Catalog(schema, doc, self.catalog.settings)
        plan = Planner(catalog).parse('查找信科公司夏姓员工的信息')[0]
        self.assertEqual(plan['subject'], 'staff_directory')
        self.assertEqual(plan['conditions'][0], {'col': 'full_name', 'op': 'prefix', 'value': '夏'})

    def test_surname_additional_conditions_never_fall_back_to_all_employees(self):
        for question in ('信科公司夏姓女员工的信息', '查找信科公司姓夏的员工且部门是财务部',
                         '信科公司夏姓员工中已离职的人'):
            with self.subTest(question=question), self.assertRaises(ValueError):
                Planner(self.catalog).parse(question)

    def test_complete_exact_identity_skips_model_but_extra_filters_remain_strict(self):
        bad = lambda *args, **kwargs: '{"subject":"Employee","intent":"detail","conditions":[]}'
        plan, source = Planner(self.catalog, {'comp_employee': {'李康平'}}, chat=bad).parse('信科公司李康平情况')
        self.assertEqual({c['col'] for c in plan['conditions']}, {'full_name', 'enterprise_scc'})
        self.assertIn('精确身份', source)
        with self.assertRaisesRegex(ValueError, '主体'):
            Planner(self.catalog, chat=bad).parse('信科公司姓夏的员工且部门是财务部')

    def test_simple_full_name_query_does_not_wait_for_model(self):
        chat = Mock(side_effect=AssertionError('exact covered name query should not invoke model planning'))
        planner = Planner(self.catalog, {'comp_employee': {'张浩'}}, chat=chat)
        plan, source = planner.parse('查找张浩的情况')
        self.assertEqual(plan['subject'],'comp_employee')
        self.assertIn({'table':'comp_employee','col':'full_name','op':'=','value':'张浩'},plan['conditions'])
        self.assertIn('精确身份',source)
        chat.assert_not_called()

    def test_full_name_with_unparsed_extra_filter_cannot_take_fast_path(self):
        chat = Mock(return_value='{"subject":"Employee","intent":"detail","conditions":[]}')
        planner = Planner(self.catalog, {'comp_employee': {'张浩'}}, chat=chat)
        with self.assertRaisesRegex(ValueError,'主体'):
            planner.parse('查找张浩的情况且部门是财务部')
        chat.assert_called_once()

    def test_multiple_identity_values_do_not_take_single_name_fast_path(self):
        planner = Planner(self.catalog, {'comp_employee': {'张浩', '李文'}})
        plan = planner.rules('查找张浩，李文的情况')
        self.assertFalse(planner.complete_identity_query('查找张浩，李文的情况', plan))

    def test_literal_model_like_is_normalized_without_inventing_wildcards(self):
        def fake(*args, **kwargs):
            return '{"subject":"comp_employee","conditions":[{"col":"full_name","op":"LIKE","value":"夏%"}]}'
        plan = Planner(self.catalog, chat=fake).parse('信科公司姓名以夏开头的员工')[0]
        self.assertIn({'col': 'full_name', 'op': 'prefix', 'value': '夏'}, plan['conditions'])
        for value in ('%夏_%', '夏%明%', '%不存在%'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                def invalid(*args, **kwargs):
                    return json.dumps({'subject':'comp_employee', 'conditions':[{'col':'full_name','op':'like','value':value}]})
                Planner(self.catalog, chat=invalid).parse('员工姓名以夏开头')

    def test_multiple_and_negative_surnames_are_not_silently_rewritten(self):
        for question in ('查找信科公司夏姓员工和李姓员工', '查找信科公司不姓夏的员工'):
            with self.subTest(question=question), self.assertRaises(ValueError):
                self.planner.parse(question)

    def test_unparsed_surname_expression_cannot_become_a_company_wide_query(self):
        with self.assertRaisesRegex(ValueError, '姓氏表达'):
            self.planner.parse('查找信科公司夏姓财务员工的信息')

    def test_model_cannot_drop_extra_filters_from_surname_query(self):
        def fake(*args, **kwargs):
            return '{"subject":"comp_employee","intent":"detail","conditions":[]}'
        with self.assertRaisesRegex(ValueError, '额外筛选'):
            Planner(self.catalog, chat=fake).parse('信科公司夏姓女员工的信息')

    def test_model_can_resolve_extra_surname_predicates_from_real_attributes(self):
        doc, schema = deepcopy(self.catalog.overrides), deepcopy(self.catalog.schema)
        schema['tables'][0]['columns'].append({'name':'gender','type':'text','comment':'性别','nullable':True,'default':None})
        doc['entities']['comp_employee']['attributes'] = {'gender': {'label': '性别', 'enums': {'F': '女', 'M': '男'}}}
        catalog = Catalog(schema, doc, self.catalog.settings)
        def fake(*args, **kwargs):
            return '{"subject":"comp_employee","intent":"detail","conditions":[{"col":"gender","op":"=","value":"F"}]}'
        plan = Planner(catalog, chat=fake).parse('信科公司夏姓女员工的信息')[0]
        self.assertEqual({c['col'] for c in plan['conditions']}, {'gender', 'full_name', 'enterprise_scc'})

    def test_extra_predicate_does_not_account_for_another_missing_filter(self):
        doc, schema = deepcopy(self.catalog.overrides), deepcopy(self.catalog.schema)
        schema['tables'][0]['columns'].append({'name':'department','type':'text','comment':'部门','nullable':True,'default':None})
        catalog = Catalog(schema, doc, self.catalog.settings)
        def fake(*args, **kwargs):
            return '{"subject":"comp_employee","intent":"detail","conditions":[{"col":"department","op":"=","value":"财务部"}]}'
        with self.assertRaisesRegex(ValueError, '额外筛选'):
            Planner(catalog, chat=fake).parse('信科公司姓夏的女员工且部门是财务部')

    def test_invalid_field_and_empty_filter_are_not_discarded(self):
        for field,value in [('missing',1),('full_name','')]:
            with self.assertRaises(ValueError):
                self.compiler.validate({'subject':'comp_employee','intent':'detail',
                    'conditions':[{'col':field,'op':'=','value':value}]})

    def test_values_are_bound_not_interpolated(self):
        value="O'Reilly'; DROP TABLE anything; --"
        plan={'subject':'comp_employee','intent':'detail','conditions':[{'col':'full_name','op':'=','value':value}]}
        statement=self.compiler.detail(plan,[],5,20)
        self.assertNotIn(value,statement.sql)
        self.assertEqual(statement.params,(value,))

    def test_related_scope_has_no_anchor_limit(self):
        plan={'subject':'contract_main','intent':'detail','conditions':[]}
        statement=self.compiler.detail(plan,self.catalog.links('contract_main',['fund_flow_record']),50,20)
        relation_branch=statement.sql.split('UNION ALL')[1]
        self.assertNotIn('LIMIT 50',relation_branch)
        self.assertIn('SELECT DISTINCT',relation_branch)

    def test_currency_and_transaction_direction_are_separate_groups(self):
        plan=self.planner.parse('资金总金额是多少')[0]
        sql=self.compiler.aggregate(plan).sql
        self.assertIn('GROUP BY',sql)
        self.assertIn("'currency'",sql)
        self.assertIn("'transaction_type'",sql)

    def test_balance_snapshots_are_not_summed(self):
        with self.assertRaisesRegex(ValueError,'快照'):
            self.planner.parse('资金账户余额总额是多少')

    def test_meeting_uses_json_business_time_and_month_group(self):
        plan=self.planner.parse('按月份统计会议数量')[0]
        sql=self.compiler.aggregate(plan).sql
        self.assertIn('__period',sql)
        self.assertIn("'开始时间'",sql)
        self.assertNotIn('created_at',sql)

    def test_salary_name_filter_traverses_configured_relation(self):
        plan=self.planner.parse('李康平工资合计')[0]
        statement=self.compiler.aggregate(plan)
        self.assertIn('EXISTS',statement.sql)
        self.assertIn('李康平',statement.params)

    def test_model_cannot_drop_explicit_identity_conditions(self):
        def fake_chat(*args,**kwargs):
            return '{"subject":"comp_employee","intent":"detail","conditions":[],"focus_tables":[]}'
        planner=Planner(self.catalog,{'comp_employee':{'李康平'}},{},fake_chat)
        plan=planner.parse('信科公司李康平情况')[0]
        self.assertEqual(len(plan['conditions']),2)

    def test_model_does_not_compare_alias_with_company_name_field(self):
        def fake_chat(*args,**kwargs):
            return '{"subject":"comp_employee","intent":"detail","conditions":[{"col":"enterprise_name","op":"=","value":"信科公司"}],"group_by":null}'
        planner=Planner(self.catalog,{'comp_employee':{'李康平'}},{},fake_chat)
        plan=planner.parse('信科公司李康平情况')[0]
        self.assertEqual({c['col'] for c in plan['conditions']},{'full_name','enterprise_scc'})

    def test_model_wrong_aggregate_grain_is_corrected(self):
        def fake_chat(*args,**kwargs):
            return '{"subject":"triple_major_meeting_record","intent":"count","aggregate_target":"id","group_by":null}'
        plan=Planner(self.catalog,chat=fake_chat).parse('按月份统计会议数量')[0]
        self.assertEqual(plan['aggregate_target'],'triple_major_meeting_record')

    def test_model_cannot_invent_filters(self):
        def fake_chat(*args,**kwargs):
            return '{"subject":"comp_employee","intent":"detail","conditions":[{"col":"id","op":"=","value":999}]}'
        with self.assertRaisesRegex(ValueError,'筛选值'):
            # An extra department condition needs model parsing; the complete
            # identity-only query no longer consults this fake model.
            Planner(self.catalog,{'comp_employee':{'李康平'}},{},fake_chat).parse('李康平且部门是财务部的情况')

    def test_numeric_substrings_and_wrong_units_are_rejected(self):
        evidence=build_evidence(self.catalog,{'subject':'contract_main','intent':'detail'},[{'id':1,'amount':80}],1,[],[])
        for text in ('金额为8万元','金额为80元'):
            with self.assertRaises(ValueError):
                validate_claims([{'text':text,'fact_ids':['F1']}],evidence)

    def test_facts_have_sources_units_and_observed_relationships(self):
        plan={'subject':'contract_main','intent':'detail'}
        links=self.catalog.links('contract_main',['fund_flow_record'])
        evidence=build_evidence(self.catalog,plan,[{'id':1,'amount':'8'}],1,
            [{'table':'fund_flow_record','rows':[{'id':2,'related_contract_id':1,'transaction_amount':'3'}],'count':1}],links)
        self.assertEqual(evidence['facts'][0]['properties']['amount']['unit'],'万元')
        self.assertEqual(evidence['relationships'][0]['source'],'F2')
        self.assertEqual(evidence['facts'][0]['source']['record_key'],{'id':1})

    def test_answer_rejects_bad_citation_and_invented_number(self):
        evidence={'facts':[{'id':'F1','kind':'aggregate','values':{'amount':8}}]}
        for claim in [{'text':'金额为9元','fact_ids':['F1']},{'text':'金额为8元','fact_ids':['missing']}]:
            with self.assertRaises(ValueError):
                validate_claims([claim],evidence)

    def test_nested_sensitive_values_are_removed(self):
        row={'people':[{'姓名':'测试','联系电话':'secret','id_card':'secret'}]}
        self.assertEqual(sanitize(row,{'id_card'}),{'people':[{'姓名':'测试'}]})

    def test_schema_change_changes_catalog_version(self):
        schema=deepcopy(self.catalog.schema)
        schema['tables'][0]['columns'][1]['comment']='新姓名注释'
        newer=Catalog(schema,self.catalog.overrides,self.catalog.settings)
        self.assertNotEqual(newer.version,self.catalog.version)

    def test_versioned_edit_conflict_and_rollback(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'ontology.json'
            original=self.catalog.overrides
            save_snapshot(path,original)
            store=OntologyStore(path,self.catalog.settings)
            changed=deepcopy(original)
            changed['entities']['comp_employee']['name']='人员档案'
            store.save(changed,fingerprint(original),self.catalog.schema)
            with self.assertRaisesRegex(ValueError,'其他编辑'):
                store.save(original,fingerprint(original),self.catalog.schema)
            store.rollback(store.history()[0]['revision'],fingerprint(changed),self.catalog.schema)
            self.assertEqual(store.load(),original)
