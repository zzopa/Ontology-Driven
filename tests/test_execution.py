"""Public milestones and failure retention, with no external model calls."""
from copy import deepcopy
import json
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import core
from app.execution import ExecutionTrace
from app.main import stream_question
from fixtures import fixture_catalog


class ExecutionTests(unittest.TestCase):
    def test_heartbeat_keeps_start_and_running_state(self):
        trace = ExecutionTrace()
        trace.start('batch', '传递', '等待响应')
        started = trace.steps['batch']['started_at']
        trace.update('batch', '仍在等待真实模型响应')
        self.assertEqual(trace.steps['batch']['started_at'], started)
        self.assertEqual(trace.steps['batch']['state'], 'running')
        self.assertEqual(trace.steps['batch']['message'], '仍在等待真实模型响应')

    def test_steps_are_upserted_and_snapshots_are_detached(self):
        events = []
        trace = ExecutionTrace(events.append)
        trace.start('sql', '实时查询', '正在执行')
        trace.finish('sql', '命中 0 条', ['parameterized SELECT'])
        self.assertEqual([event['step']['state'] for event in events], ['running', 'completed'])
        self.assertEqual(len(trace.snapshot()), 1)
        snapshot = trace.snapshot()
        snapshot[0]['details'].append('tampered')
        self.assertEqual(trace.snapshot()[0]['details'], ['parameterized SELECT'])

    def test_failed_active_step_is_not_marked_complete(self):
        trace = ExecutionTrace()
        trace.start('ontology', '匹配业务本体', '正在匹配')
        trace.finish('ontology', '已匹配')
        trace.start('model', '模型辅助解析', '解析中')
        trace.fail_active('主体校验失败')
        self.assertEqual([step['state'] for step in trace.snapshot()], ['completed', 'failed'])

    def test_query_contains_real_steps_even_when_no_data_matches(self):
        catalog = fixture_catalog()
        events = []
        with patch.object(core, 'CATALOG', catalog), patch.object(core, 'NAME_INDEX', {}), \
                patch.object(core, 'ENTERPRISE_NAMES', {}), patch.object(core, 'MEM', {'entries': []}), \
                patch.object(core, 'execute', return_value=[]):
            result = core.do_ask(None, '查找信科公司夏姓员工的信息', events.append,
                                 use_model=False, use_cache=False)
        steps = {step['id']: step for step in result['execution_trace']}
        for name in ('ontology', 'intent', 'model', 'plan', 'relations', 'sql_compile', 'sql_check', 'sql', 'evidence', 'answer', 'done'):
            self.assertIn(name, steps)
        self.assertEqual(steps['model']['state'], 'skipped')
        self.assertEqual(steps['sql']['state'], 'completed')
        self.assertIn('命中 0 条', steps['sql']['message'])
        self.assertEqual(result['main_total'], 0)
        self.assertIn('夏%', result['sql_params'])
        self.assertNotIn('夏%', result['sql'])
        self.assertTrue(any(event['type'] == 'step' for event in events))

    def test_query_failure_reports_the_step_and_does_not_invent_completion(self):
        catalog = fixture_catalog()
        trace = ExecutionTrace()
        with patch.object(core, 'CATALOG', catalog), patch.object(core, 'NAME_INDEX', {}), \
                patch.object(core, 'ENTERPRISE_NAMES', {}), patch.object(core, 'MEM', {'entries': []}), \
                patch.object(core, 'execute', side_effect=ValueError('SQL 检查失败')):
            with self.assertRaisesRegex(ValueError, 'SQL 检查失败'):
                core.do_ask(None, '信科夏姓员工信息', use_model=False, use_cache=False, trace=trace)
        steps = {step['id']: step for step in trace.snapshot()}
        self.assertEqual(steps['sql_check']['state'], 'failed')
        self.assertNotIn('done', steps)

    def test_query_emits_body_before_model_and_only_once_in_final_order(self):
        catalog = fixture_catalog()
        events = []
        rows = [{'bucket': '__main', 'row_data': {'id': 1, 'full_name': '张浩',
                 'enterprise_scc': 'XK'}, 'total_count': 1}]
        def model(system, user, **kwargs):
            self.assertTrue(any(event['type'] == 'answer_delta' for event in events))
            return '{"claims":[{"text":"员工姓名为张浩。","fact_ids":["F1"]}]}'
        with patch.object(core, 'CATALOG', catalog), \
                patch.object(core, 'NAME_INDEX', {'comp_employee': {'张浩'}}), \
                patch.object(core, 'ENTERPRISE_NAMES', {}), \
                patch.object(core, 'MEM', {'entries': []}), \
                patch.object(core, 'execute', return_value=rows), \
                patch.object(core, 'model_available', return_value=True), \
                patch.object(core, 'chat', side_effect=model) as chat:
            result = core.do_ask(None, '查找张浩的情况', events.append, use_cache=False)
        self.assertEqual(chat.call_count, 2, 'one analysis + one composition; skip the planning model')
        body = ''.join(e['delta'] for e in events if e['type'] == 'answer_delta')
        self.assertEqual(body.rstrip('\n'), result['answer'])
        self.assertEqual(body.count('员工姓名为张浩。'), 1)
        self.assertEqual(result['source'], '本体规则·精确身份查询')

    def test_permission_change_blocks_each_new_answer_delta(self):
        catalog = fixture_catalog()
        access = SimpleNamespace(user_id='test-id', revision='initial', unrestricted=True,
                                 catalog=lambda original: original)
        events = []
        rows = [{'bucket': '__main', 'row_data': {'id': 1, 'full_name': '张浩'}, 'total_count': 1}]
        initial = SimpleNamespace(revision='initial')
        changed = SimpleNamespace(revision='changed')
        def model(system, user, **kwargs):
            return '{"claims":[{"text":"员工姓名为张浩。","fact_ids":["F1"]}]}'
        with patch.object(core, 'CATALOG', catalog), \
                patch.object(core, 'load_lookups', return_value=({'comp_employee': {'张浩'}}, {})), \
                patch.object(core, 'MEM', {'entries': []}), \
                patch.object(core, 'Compiler') as compiler, \
                patch.object(core, 'execute', return_value=rows), \
                patch.object(core, 'model_available', return_value=True), \
                patch.object(core, 'chat', side_effect=model), \
                patch('app.business_access.load_access', side_effect=[initial, initial, changed]), \
                self.assertRaisesRegex(ValueError, '业务权限已变化'):
            from app.query import Compiler
            compiler.return_value = Compiler(catalog)
            core.do_ask(None, '查找张浩的情况', events.append, use_cache=False, access=access)
        deltas = [e['delta'] for e in events if e['type'] == 'answer_delta']
        self.assertEqual(len(deltas), 1, 'only the authorized initial summary may be disclosed')
        self.assertNotIn('员工姓名为张浩。', ''.join(deltas))

    def test_ndjson_body_is_delivered_before_worker_returns_final_result(self):
        release, finished = Event(), Event()
        def run(prepared, on_event):
            on_event({'type': 'answer_delta', 'delta': '已核验正文\n'})
            if not release.wait(3):
                raise TimeoutError('first body must arrive before the final result')
            finished.set()
            return {'ok': True, 'answer': '已核验正文'}
        with patch('app.main.run_question', side_effect=run):
            stream = stream_question({'conversation_id': 'test-only'})
            try:
                self.assertEqual(json.loads(next(stream))['type'], 'progress')
                body = json.loads(next(stream))
                self.assertEqual(body, {'type': 'answer_delta', 'delta': '已核验正文\n'})
                self.assertFalse(finished.is_set())
            finally:
                release.set()
            rest = [json.loads(chunk) for chunk in stream]
        self.assertEqual(rest[-1]['type'], 'result')

    def test_unresolved_filters_preserve_a_failed_step_after_model_was_skipped(self):
        trace = ExecutionTrace()
        with patch.object(core, 'CATALOG', fixture_catalog()), patch.object(core, 'NAME_INDEX', {}), \
                patch.object(core, 'ENTERPRISE_NAMES', {}), patch.object(core, 'MEM', {'entries': []}):
            with self.assertRaisesRegex(ValueError, '额外筛选'):
                core.do_ask(None, '信科公司姓夏的员工且部门是财务部', use_model=False, use_cache=False, trace=trace)
        self.assertEqual(trace.snapshot()[-1]['state'], 'failed')
        self.assertNotIn('sql', {step['id'] for step in trace.snapshot()})


if __name__ == '__main__':
    unittest.main()
