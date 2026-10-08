"""Anchor/grain reconciliation by topology, including renamed tables."""
from copy import deepcopy
import json
import unittest

from app.agent.planning import reconcile_anchor
from app.ontology import Catalog
from app.planner import Planner
from app.query import Compiler
from fixtures import fixture_catalog


class AgentPlanningTests(unittest.TestCase):
    def test_related_model_target_preserves_scope_and_statistics_grain(self):
        cat=fixture_catalog()
        model=lambda *args,**kwargs: json.dumps({'subject':'fund_flow_record','intent':'count',
                                                'conditions':[],'focus_tables':[],'aggregate_target':None})
        plan,source=Planner(cat,chat=model).parse('信科合同实际付款数量')
        self.assertEqual(plan['subject'],'contract_main')
        self.assertEqual(plan['aggregate_target'],'fund_flow_record')
        self.assertIn({'col':'enterprise_scc','op':'=','value':'SCC-XK'},plan['conditions'])
        self.assertEqual(plan['plan_roles']['analysis_target'],'fund_flow_record')
        statement=Compiler(cat).aggregate(plan)
        self.assertIn('SCC-XK',statement.params)
        self.assertNotIn('SCC-XK',statement.sql)

    def test_missing_predicate_table_stays_on_original_target_not_anchor(self):
        cat=fixture_catalog()
        local={'subject':'contract_main','focus_tables':['fund_flow_record']}
        proposed={'subject':'fund_flow_record','intent':'count','aggregate_target':None,
                  'conditions':[{'table':None,'col':'currency','op':'=','value':'CNY'}],'focus_tables':[]}
        before=deepcopy(proposed)
        result=reconcile_anchor(cat,local,proposed)
        self.assertEqual(result['conditions'][0]['table'],'fund_flow_record')
        self.assertEqual(result['aggregate_target'],'fund_flow_record')
        self.assertEqual(proposed,before)
        Compiler(cat).validate(result)

    def test_connectivity_without_explicitly_requested_relation_is_not_enough(self):
        cat=fixture_catalog()
        with self.assertRaisesRegex(ValueError,'明确请求'):
            reconcile_anchor(cat,{'subject':'contract_main','focus_tables':[]},
                             {'subject':'fund_flow_record','conditions':[]})

    def test_rebasing_never_repairs_unknown_fields_by_guessing(self):
        cat=fixture_catalog()
        result=reconcile_anchor(cat,{'subject':'contract_main','focus_tables':['fund_flow_record']},
            {'subject':'fund_flow_record','intent':'detail','conditions':[
                {'col':'contract_no','op':'=','value':'fake'}],'focus_tables':[]})
        with self.assertRaisesRegex(ValueError,'字段映射'):
            Compiler(cat).validate(result)

    def test_same_algorithm_works_after_all_physical_tables_are_renamed(self):
        original=fixture_catalog()
        names={name:'domain_object_%d'%i for i,name in enumerate(original.entities)}
        schema,doc=deepcopy(original.schema),deepcopy(original.overrides)
        for table in schema['tables']:
            table['name']=table['table']=names[table['table']]
        doc['entities']={names[t]:spec for t,spec in doc['entities'].items()}
        doc['relations']={names[root]:{names[target]:spec for target,spec in targets.items()}
                          for root,targets in doc['relations'].items()}
        for edge in doc['logical_links']:
            for key in ('source','target'):
                table,field=edge[key].split('.',1)
                edge[key]=names[table]+'.'+field
        cat=Catalog(schema,doc,original.settings)
        for root,children in doc['relations'].items():
            for target in children:
                with self.subTest(root=root,target=target):
                    local={'subject':root,'focus_tables':[target]}
                    proposed={'subject':target,'intent':'count','conditions':[],'focus_tables':[]}
                    result=reconcile_anchor(cat,local,proposed)
                    self.assertEqual(result['subject'],root)
                    self.assertEqual(result['aggregate_target'],target)
                    Compiler(cat).validate(result)
                    self.assertIn(target,Compiler(cat).aggregate(result).sql)


if __name__=='__main__':
    unittest.main()
