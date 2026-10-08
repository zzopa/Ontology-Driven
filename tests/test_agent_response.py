"""Evidence/analysis composition, without external models or business writes."""
from copy import deepcopy
from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

from app import core
from app.agent.prompts import ANALYSIS_PROMPT, COMPOSITION_PROMPT
from app.agent.response import claim_entries, compose_document, validate_layout
from app.evidence import build_evidence, validate_claims
from app.execution import ExecutionTrace
from fixtures import fixture_catalog


class ResponseAgentTests(unittest.TestCase):
    def setUp(self):
        self.claims=[{'text':'实际查询结果。','fact_ids':['F1']},
            {'text':'主要发现。','fact_ids':['F1']},
            {'text':'其他发现。','fact_ids':['F2']},
            {'text':'不能确定完整状态。','fact_ids':['F2'],'kind':'limitations'}]
        self.plan={'subject':'arbitrary_entity','intent':'detail','conditions':[]}
        self.evidence={'coverage':[{'table':'arbitrary_entity','provided':2,'total':5}]}
        self.layout={'sections':[{'kind':'query_facts','claim_ids':['C1']},
            {'kind':'findings','claim_ids':['C3','C2']},
            {'kind':'limitations','claim_ids':['C4']}]}

    def test_model_reorders_existing_claims_and_does_not_mutate_inputs(self):
        before=deepcopy((self.claims,self.plan,self.evidence))
        def model(system,user,**kwargs):
            self.assertEqual(system,COMPOSITION_PROMPT)
            payload=json.loads(user)
            self.assertEqual(len(payload['claims']),4)
            self.assertEqual(payload['coverage'],self.evidence['coverage'])
            return json.dumps(self.layout)
        trace=ExecutionTrace()
        doc=compose_document('任意业务问题',self.plan,self.evidence,self.claims,1,model,trace=trace)
        self.assertEqual(doc['mode'],'model_composed')
        self.assertEqual(doc['sections'][1]['claim_ids'],['C3','C2'])
        self.assertEqual((self.claims,self.plan,self.evidence),before)
        self.assertFalse(doc['semantic_proof'])
        self.assertEqual(trace.steps['answer_layout']['state'],'completed')

    def test_forged_omitted_duplicate_reclassified_or_new_text_is_rejected(self):
        invalid=[]
        for ids in (['C3'],['C2','C2','C3'],['C2','C99']):
            value=deepcopy(self.layout)
            value['sections'][1]['claim_ids']=ids
            invalid.append(value)
        for key,value in (('kind','interpretation'),('text','虚构的新正文')):
            bad=deepcopy(self.layout)
            bad['sections'][1][key]=value
            invalid.append(bad)
        bad=deepcopy(self.layout)
        bad['answer']='新数字和结论'
        invalid.append(bad)
        bad=deepcopy(self.layout)
        bad['sections'].reverse()
        invalid.append(bad)
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_layout(value,claim_entries(self.claims,1))

    def test_invalid_layout_falls_back_without_losing_claims(self):
        doc=compose_document('问题',self.plan,self.evidence,self.claims,1,Mock(return_value='{"sections":[]}'))
        self.assertEqual(doc['composition_reason'],'invalid_layout')
        self.assertEqual([cid for s in doc['sections'] for cid in s['claim_ids']],['C1','C2','C3','C4'])

    def test_timeout_preserves_answer_and_does_not_retry(self):
        model=Mock(side_effect=TimeoutError('provider unavailable'))
        doc=compose_document('问题',self.plan,self.evidence,self.claims,1,model)
        self.assertEqual(doc['composition_reason'],'provider_error')
        self.assertEqual(doc['claim_count'],4)
        model.assert_called_once()

    def test_over_budget_keeps_all_claims_without_sending_a_subset(self):
        claims=deepcopy(self.claims)
        claims[1]['text']='完整原文'*1500
        model=Mock()
        doc=compose_document('问题',self.plan,self.evidence,claims,1,model,budget=2048)
        model.assert_not_called()
        self.assertEqual(doc['composition_reason'],'budget_exceeded')
        self.assertEqual(doc['claim_count'],4)
        self.assertEqual(claims[1]['text'],'完整原文'*1500)

    def test_empty_or_query_only_reports_need_no_composition_call(self):
        model=Mock()
        for claims,count in (([],0),(self.claims[:1],1)):
            doc=compose_document('问题',self.plan,self.evidence,claims,count,model)
            self.assertEqual(doc['claim_count'],len(claims))
        model.assert_not_called()

    def test_interpretations_and_suggestions_cannot_claim_to_be_database_facts(self):
        ev={'facts':[{'id':'F1','kind':'aggregate','values':{'n':3}}]}
        for kind in ('interpretation','limitations','recommendations'):
            accepted=validate_claims([{'text':'建议核对已有数据。','fact_ids':['F1'],'kind':kind}],ev)
            self.assertEqual(accepted[0]['kind'],kind)
        for kind in ('query_facts','unknown',[],{}):
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                validate_claims([{'text':'核对数据。','fact_ids':['F1'],'kind':kind}],ev)

    def test_aggregates_from_different_domains_reach_generic_analysis(self):
        cat=fixture_catalog()
        for subject,total in (('contract_main',3),('project_budget',2),('fund_flow_record',4)):
            plan={'subject':subject,'intent':'count','conditions':[]}
            ev=build_evidence(cat,plan,[{'n':total}],total,[],[])
            captured=[]
            def model(system,user,**kwargs):
                self.assertEqual(system,ANALYSIS_PROMPT)
                captured.append(json.loads(user)['evidence'])
                return '{"claims":[{"text":"统计值来自数据库计算。","fact_ids":["A1"],"kind":"interpretation"}]}'
            with self.subTest(subject=subject),patch.object(core,'model_available',return_value=True), \
                    patch.object(core,'chat',side_effect=model):
                claims,text,_=core.answer(cat,plan,ev,total,True)
            self.assertTrue(captured)
            self.assertEqual(claims[-1]['kind'],'interpretation')
            self.assertIn('统计值来自数据库计算',text)

    def test_pipeline_keeps_original_stream_and_adds_composed_document(self):
        cat=fixture_catalog()
        events=[]
        rows=[{'bucket':'__main','row_data':{'id':1,'full_name':'测试员工'},'total_count':1}]
        def model(system,user,**kwargs):
            if system==COMPOSITION_PROMPT:
                payload=json.loads(user)
                return json.dumps({'sections':[
                    {'kind':kind,'claim_ids':[c['id'] for c in payload['claims'] if c['kind']==kind]}
                    for kind in ('query_facts','interpretation')]})
            return '{"claims":[{"text":"档案姓名来自本次查询。","fact_ids":["F1"],"kind":"interpretation"}]}'
        with patch.object(core,'CATALOG',cat),patch.object(core,'NAME_INDEX',{'comp_employee':{'测试员工'}}), \
                patch.object(core,'ENTERPRISE_NAMES',{}),patch.object(core,'MEM',{'entries':[]}), \
                patch.object(core,'execute',return_value=rows),patch.object(core,'model_available',return_value=True), \
                patch.object(core,'chat',side_effect=model) as chat:
            result=core.do_ask(None,'查找测试员工的情况',events.append,use_cache=False)
        self.assertEqual(chat.call_count,2)
        self.assertEqual(''.join(e['delta'] for e in events if e['type']=='answer_delta').rstrip('\n'),result['answer'])
        self.assertEqual(result['answer_document']['mode'],'model_composed')
        self.assertIn(result['query_summary'],result['answer'])
        self.assertIn('composition',result['timings'])

    def test_permissions_revoked_during_composition_block_the_final_snapshot(self):
        cat=fixture_catalog()
        access=SimpleNamespace(user_id='test-only',revision='initial',unrestricted=True,
                               catalog=lambda original:original)
        changed=[False]
        def model(system,user,**kwargs):
            if system==COMPOSITION_PROMPT:
                changed[0]=True
                raise TimeoutError('composition failed while permissions changed')
            return '{"claims":[{"text":"档案信息来自查询。","fact_ids":["F1"]}]}'
        def latest(*args):
            return SimpleNamespace(revision='revoked' if changed[0] else 'initial')
        rows=[{'bucket':'__main','row_data':{'id':1,'full_name':'测试员工'},'total_count':1}]
        with patch.object(core,'CATALOG',cat),patch.object(core,'MEM',{'entries':[]}), \
                patch.object(core,'load_lookups',return_value=({'comp_employee':{'测试员工'}},{})), \
                patch.object(core,'Compiler',return_value=core.Compiler(cat)), \
                patch.object(core,'execute',return_value=rows),patch.object(core,'model_available',return_value=True), \
                patch.object(core,'chat',side_effect=model),patch('app.business_access.load_access',side_effect=latest), \
                self.assertRaisesRegex(ValueError,'业务权限已变化'):
            core.do_ask(None,'查找测试员工的情况',lambda event:None,use_cache=False,access=access)
        self.assertTrue(changed[0])


if __name__=='__main__':
    unittest.main()
