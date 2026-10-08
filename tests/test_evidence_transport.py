"""No external calls: exact data delivery, truthful coverage and safe summaries."""
from copy import deepcopy
from dataclasses import replace
import json
from io import BytesIO
import unittest
from threading import Event
from unittest.mock import patch

from app import core, llm
from app.evidence import build_evidence, fallback_claims, model_evidence, validate_claims
from app.evidence_transport import dumps, evidence_batches, restore_batches
from app.execution import ExecutionTrace
from app.llm import current_model, model_context
from fixtures import fixture_catalog


class EvidenceTransportTests(unittest.TestCase):
    def setUp(self):
        self.catalog = fixture_catalog()
        self.plan = {'subject': 'comp_employee', 'intent': 'detail', 'conditions': []}
        self.main = [{'id': i + 1, 'full_name': name, 'enterprise_scc': 'SCC-XK'}
                     for i, name in enumerate(['李文', '李凯', '李琰', '李静', '李康平',
                                              '李宇航', '李瑛', '李艳红', '李永辉', '李俊杰'])]
        rows = [{'id': i + 100, 'employee_id': i % 10 + 1, 'amount': str(i + 20),
                 'payroll_month': '2026-09', 'details': '原始值-' + '测试' * 80} for i in range(80)]
        self.evidence = build_evidence(self.catalog, self.plan, self.main, 10,
            [{'table': 'comp_payroll_monthly', 'rows': rows, 'count': 120}],
            self.catalog.links('comp_employee', ['comp_payroll_monthly']))

    def assert_lossless(self, evidence, budget):
        original = deepcopy(evidence)
        batches = evidence_batches(evidence, budget)
        facts, edges = restore_batches(batches)
        self.assertEqual({f['id']: f for f in facts}, {f['id']: f for f in evidence['facts']})
        self.assertEqual(sorted(dumps(e) for e in edges), sorted(dumps(e) for e in evidence['relationships']))
        self.assertEqual(evidence, original)
        self.assertTrue(all(len(dumps(b.payload)) <= budget for b in batches))
        return batches

    def test_all_records_and_relationships_survive_small_budget(self):
        batches = self.assert_lossless(self.evidence, 6000)
        self.assertGreater(len(batches), 1)
        self.assertEqual(sum(f['table'] == 'comp_employee' for f in batches[0].payload['facts']), 10)
        for batch in batches:
            for c in batch.payload['coverage']:
                actual = sum(f['table'] == c['table'] for f in batch.payload['facts'])
                self.assertEqual(c['provided'], actual)
                self.assertEqual(c['complete'], actual == c['total'])
            self.assertFalse(batch.payload['scope']['query_complete_records'])

    def test_many_relationships_cannot_evict_primary_records(self):
        ev = deepcopy(self.evidence)
        ev['relationships'] *= 8
        self.assert_lossless(ev, 6000)

    def test_related_record_batches_include_join_context_and_actual_endpoint_names(self):
        batches = self.assert_lossless(self.evidence, 9000)
        related = [b for b in batches if any(f['table']=='comp_payroll_monthly' for f in b.payload['facts'])]
        self.assertTrue(all(b.payload['relationships'] for b in related))
        self.assertTrue(any(b.payload['fact_references'] for b in related))
        for batch in related:
            ids = {f['id'] for f in batch.evidence['facts']}
            for edge in batch.payload['relationships']:
                self.assertIn(edge['source'],ids)
                self.assertIn(edge['target'],ids)

    def test_long_nested_values_are_not_truncated_or_stringified(self):
        ev = deepcopy(self.evidence)
        value = {'备注': '包含引号"换行\n' * 1200, 'items': [None, True, 3.14, {'完整': '末尾标记'}]}
        ev['facts'][4]['properties']['long_json'] = {'value': value, 'label': '完整JSON', 'unit': ''}
        batches = self.assert_lossless(ev, 6000)
        self.assertTrue(any(b.payload['record_fragments'] for b in batches))
        restored, _ = restore_batches(batches)
        fact = next(f for f in restored if f['id'] == 'F5')
        self.assertEqual(fact['properties']['long_json']['value'], value)

    def test_small_value_over_2500_characters_is_sent_whole(self):
        ev = build_evidence(self.catalog, self.plan,
            [{'id': 1, 'full_name': '测试', 'note': '长字段' * 1200 + '结尾'}], 1, [], [])
        batches = self.assert_lossless(ev, 12000)
        self.assertEqual(len(batches), 1)
        self.assertFalse(batches[0].payload['record_fragments'])

    def test_null_empty_zero_false_and_sensitive_fields_keep_their_meaning(self):
        ev = build_evidence(self.catalog, self.plan, [{'id':1,'full_name':'测试',
            'end_date':None,'note':'','amount':0,'enabled':False,'mobile':'hidden'}],1,[],[])
        props = ev['facts'][0]['properties']
        self.assertIsNone(props['end_date']['value'])
        self.assertEqual(props['note']['value'],'')
        self.assertEqual(props['amount']['value'],0)
        self.assertIs(props['enabled']['value'],False)
        self.assertNotIn('mobile',props)
        self.assert_lossless(ev,6000)

    def test_single_payload_helper_refuses_to_silently_return_first_batch(self):
        with self.assertRaisesRegex(ValueError, '不能只发送第一批'):
            model_evidence(self.evidence, 6000)

    def test_missing_relation_endpoint_is_not_silently_dropped(self):
        ev = deepcopy(self.evidence)
        ev['relationships'][0]['target'] = 'missing'
        with self.assertRaisesRegex(ValueError, '端点'):
            evidence_batches(ev, 6000)

    def test_bad_budget_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, '不能通过丢弃'):
            evidence_batches(self.evidence, 1000)

    def test_deterministic_summary_lists_all_primary_identities(self):
        claims, text = fallback_claims(self.catalog, self.plan, self.evidence, 10)
        for row in self.main:
            self.assertIn(row['full_name'], text)
        self.assertIn('命中 10 条', text)
        self.assertIn('80/120', text)
        self.assertEqual(claims[0]['fact_ids'], ['F%d' % i for i in range(1, 11)])

    def test_wrong_person_count_cannot_be_supported_by_id_or_status_one(self):
        with self.assertRaisesRegex(ValueError, '总数不一致'):
            validate_claims([{'text': '信科公司有1名李姓员工，姓名为李文', 'fact_ids': ['F1']}], self.evidence)
        accepted = validate_claims([{'text': '信科公司有10名李姓员工', 'fact_ids': ['F1']}], self.evidence)
        self.assertEqual(len(accepted), 1)

    def run_answer(self, fake):
        report, trace = {}, ExecutionTrace()
        cfg = replace(core.settings, evidence_budget=6000, evidence_workers=2, evidence_retries=0)
        with patch.object(core, 'settings', cfg), patch.object(core, 'model_available', return_value=True), \
                patch.object(core, 'chat', side_effect=fake):
            answer = core.answer(self.catalog, self.plan, self.evidence, 10, True,
                                 '查找信科公司李姓员工情况', trace, report)
        return answer, report, trace

    def test_all_batches_are_called_and_worker_model_context_is_preserved(self):
        captured = []
        def fake(system, user, **kwargs):
            self.assertEqual(current_model()['model'], 'frozen-request-model')
            captured.append(json.loads(user)['evidence'])
            return '{"claims":[]}'
        with model_context({'url': 'https://example.invalid', 'key': 'test-only', 'model': 'frozen-request-model'}):
            (_, text, validation), report, trace = self.run_answer(fake)
        self.assertEqual(len(captured), report['batch_count'])
        self.assertTrue(report['complete_queried_data'])
        self.assertEqual(report['sent_fact_count'], 90)
        self.assertEqual(report['sent_relationship_count'], 80)
        facts, edges = restore_batches(captured)
        self.assertEqual(len(facts), 90)
        self.assertEqual(len(edges), 80)
        self.assertIn('李康平', text)
        self.assertEqual(validation, 'deterministic')
        self.assertTrue(all(s['state'] == 'completed' for s in trace.snapshot()))

    def test_failed_batch_does_not_stop_remaining_data_and_is_reported(self):
        captured = []
        def fake(system, user, **kwargs):
            batch = json.loads(user)['evidence']
            captured.append(batch['batch']['index'])
            if batch['batch']['index'] == 2:
                raise TimeoutError('test timeout')
            return '{"claims":[]}'
        (_, text, validation), report, trace = self.run_answer(fake)
        self.assertEqual(len(captured), report['batch_count'])
        self.assertFalse(report['complete_queried_data'])
        self.assertEqual(report['failed_batches'], [2])
        self.assertIn('未确认完成', text)
        self.assertIn('李康平', text)
        self.assertEqual(validation, 'evidence_fallback')
        self.assertEqual(trace.steps['answer_batch_2']['state'], 'failed')

    def test_wrong_model_count_is_rejected_but_actual_names_survive(self):
        def fake(system, user, **kwargs):
            batch = json.loads(user)['evidence']
            if any(f['id'] == 'F1' for f in batch['facts']):
                return '{"claims":[{"text":"信科公司有1名李姓员工，姓名为李文","fact_ids":["F1"]}]}'
            return '{"claims":[]}'
        (_, text, validation), report, _ = self.run_answer(fake)
        self.assertNotIn('有1名', text)
        self.assertIn('命中 10 条', text)
        self.assertIn('李康平', text)
        self.assertTrue(report['complete_queried_data'])  # Received, but invalid answer rejected.
        self.assertEqual(validation, 'evidence_fallback')

    def test_initial_summary_streams_before_any_model_request(self):
        streamed=[]
        def fake(system,user,**kwargs):
            self.assertTrue(streamed)
            self.assertIn('李康平',streamed[0]['text'])
            return '{"claims":[]}'
        report={}
        cfg=replace(core.settings,evidence_budget=6000,evidence_workers=2,evidence_retries=0)
        with patch.object(core,'settings',cfg),patch.object(core,'model_available',return_value=True), \
                patch.object(core,'chat',side_effect=fake):
            _,text,_=core.answer(self.catalog,self.plan,self.evidence,10,True,on_claim=streamed.append,delivery=report)
        self.assertEqual('\n'.join(c['text'] for c in streamed),text)
        self.assertTrue(report['complete_queried_data'])

    def test_checked_fast_batch_streams_while_slow_batch_is_still_running(self):
        released,slow_done=Event(),Event()
        streamed=[]
        fast_text='薪酬记录来自本次数据库查询。'
        slow_text='员工姓名来自本次数据库查询。'
        def fake(system,user,**kwargs):
            batch=json.loads(user)['evidence']
            i=batch['batch']['index']
            if i==1:
                self.assertTrue(released.wait(3),'a checked fast batch must stream before a slow provider completes')
                slow_done.set()
                return json.dumps({'claims':[{'text':slow_text,'fact_ids':[batch['facts'][0]['id']]}]},ensure_ascii=False)
            if i==2:
                return json.dumps({'claims':[{'text':fast_text,'fact_ids':[batch['facts'][0]['id']]}]},ensure_ascii=False)
            return '{"claims":[]}'
        def receive(claim):
            streamed.append(claim)
            if claim['text']==fast_text:
                self.assertFalse(slow_done.is_set())
                released.set()
        report={}
        cfg=replace(core.settings,evidence_budget=6000,evidence_workers=2,evidence_retries=0)
        with patch.object(core,'settings',cfg),patch.object(core,'model_available',return_value=True), \
                patch.object(core,'chat',side_effect=fake):
            claims,text,_=core.answer(self.catalog,self.plan,self.evidence,10,True,on_claim=receive,delivery=report)
        self.assertEqual(report['failed_batches'],[])
        self.assertTrue(slow_done.is_set())
        self.assertEqual([c['text'] for c in streamed],[c['text'] for c in claims])
        self.assertEqual('\n'.join(c['text'] for c in streamed),text)
        self.assertLess(text.index(fast_text),text.index(slow_text))

    def test_unchecked_claims_are_never_streamed_and_valid_text_is_deduplicated(self):
        streamed=[]
        good='薪酬记录来自本次数据库查询。'
        def fake(system,user,**kwargs):
            batch=json.loads(user)['evidence']
            if batch['batch']['index']==1:
                return '{"claims":[{"text":"信科公司有1名李姓员工","fact_ids":["F1"]}]}'
            if batch['facts']:
                return json.dumps({'claims':[{'text':good,'fact_ids':[batch['facts'][0]['id']]}]},ensure_ascii=False)
            return '{"claims":[]}'
        cfg=replace(core.settings,evidence_budget=6000,evidence_workers=2,evidence_retries=0)
        with patch.object(core,'settings',cfg),patch.object(core,'model_available',return_value=True), \
                patch.object(core,'chat',side_effect=fake):
            _,text,_=core.answer(self.catalog,self.plan,self.evidence,10,True,on_claim=streamed.append)
        self.assertNotIn('有1名',text)
        self.assertEqual(sum(c['text']==good for c in streamed),1)
        self.assertEqual('\n'.join(c['text'] for c in streamed),text)

    def test_stream_callback_failure_is_not_swallowed_as_model_failure(self):
        def deny(claim):
            raise ValueError('业务权限已变化')
        with patch.object(core,'chat') as model, self.assertRaisesRegex(ValueError,'权限'):
            core.answer(self.catalog,self.plan,self.evidence,10,True,on_claim=deny)
        model.assert_not_called()

    def test_delivery_warning_cannot_duplicate_final_or_streamed_body(self):
        streamed=[]
        warning='部分证据的模型传递未确认完成，以上保留数据库实际命中结果，不能视为全部关联数据的模型解读。'
        def fake(system,user,**kwargs):
            batch=json.loads(user)['evidence']
            if batch['batch']['index']==1:
                return json.dumps({'claims':[{'text':warning,'fact_ids':[batch['facts'][0]['id']]}]},ensure_ascii=False)
            if batch['batch']['index']==2:
                raise ValueError('test provider failure')
            return '{"claims":[]}'
        cfg=replace(core.settings,evidence_budget=6000,evidence_workers=2,evidence_retries=0)
        with patch.object(core,'settings',cfg),patch.object(core,'model_available',return_value=True), \
                patch.object(core,'chat',side_effect=fake):
            _,text,_=core.answer(self.catalog,self.plan,self.evidence,10,True,on_claim=streamed.append)
        self.assertEqual(text.count(warning),1)
        self.assertEqual('\n'.join(c['text'] for c in streamed),text)

    def test_transient_failure_retries_identical_payload_once(self):
        captured = {}
        def fake(system, user, **kwargs):
            i = json.loads(user)['evidence']['batch']['index']
            captured.setdefault(i, []).append(user)
            if i == 2 and len(captured[i]) == 1:
                raise TimeoutError('temporary')
            return '{"claims":[]}'
        cfg = replace(core.settings, evidence_budget=6000, evidence_workers=2, evidence_retries=1)
        report, trace = {}, ExecutionTrace()
        with patch.object(core, 'settings', cfg), patch.object(core, 'model_available', return_value=True), \
                patch.object(core, 'chat', side_effect=fake):
            _, text, _ = core.answer(self.catalog, self.plan, self.evidence, 10, True, '', trace, report)
        self.assertTrue(report['complete_queried_data'])
        self.assertEqual(report['retry_count'], 1)
        self.assertEqual(report['failed_batches'], [])
        self.assertEqual(captured[2][0], captured[2][1])
        self.assertIn('李康平', text)
        self.assertEqual(trace.steps['answer_batch_2']['state'], 'completed')

    def test_database_preview_limit_remains_explicit_not_full_scope(self):
        ev = build_evidence(self.catalog, self.plan, self.main[:5], 10, [], [])
        _, text = fallback_claims(self.catalog, self.plan, ev, 10)
        self.assertIn('读取 5 / 10', text)
        self.assertFalse(ev['scope']['complete_records'])

    def test_non_thinking_parameter_only_applies_to_verified_provider(self):
        for url, model, enabled in (
                ('https://api.siliconflow.cn/v1/chat/completions','deepseek-ai/DeepSeek-V4-Flash',True),
                ('https://api.siliconflow.cn/v1/chat/completions','Qwen/Qwen3.8-27B',True),
                ('https://api.siliconflow.cn/v1/chat/completions','Qwen/Qwen3-Thinking',False),
                ('https://example.invalid/v1/chat/completions','deepseek-ai/DeepSeek-V4-Flash',False),
                ('https://api.siliconflow.cn/v1/chat/completions','other-model',False)):
            with self.subTest(url=url), model_context({'url':url,'key':'test-only','model':model}), \
                    patch.object(llm,'open_request',return_value=BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')) as opened:
                llm.chat('system','user',enable_thinking=False)
                payload=json.loads(opened.call_args.args[0].data)
                self.assertEqual('enable_thinking' in payload,enabled)
                if enabled:
                    self.assertIs(payload['enable_thinking'],False)


if __name__ == '__main__':
    unittest.main()
