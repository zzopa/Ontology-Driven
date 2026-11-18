"""The diagram follows real planning events, without calling a model service."""
from datetime import datetime
import json
import unittest
from unittest.mock import patch

from app.agent.coordinator import ACTUAL, BEIJING, Coordinator, slots
from app.execution import ExecutionTrace
from test_coordinator import ops_catalog, REFS


class QueryFlowTraceTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ops_catalog()

    def test_direct_match_does_not_invent_semantic_steps(self):
        trace = ExecutionTrace()
        plan = Coordinator(self.catalog, references=REFS, trace=trace).plan('昨天南航出港实际执行了多少班')
        self.assertEqual(plan['intent'], 'count')
        rules = [s for s in trace.snapshot() if s['id'].startswith('rule_match_')]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]['state'], 'completed')
        self.assertFalse(any(s['id'].startswith(('semantic_normalize_', 'rule_rematch_', 'model_plan_')) for s in trace.snapshot()))

    def test_normalize_and_rematch_have_separate_real_durations(self):
        clock = [0.0]
        events = []
        def chat(prompt, question, **kwargs):
            self.assertIn('语义标准化助手', prompt)
            clock[0] += 5
            return '查询2026-11-12的平均保障时长'
        with patch('app.execution.perf_counter', side_effect=lambda: clock[0]):
            trace = ExecutionTrace(events.append)
            plan = Coordinator(self.catalog, chat=chat, references=REFS, trace=trace).plan('查11-12的平均保障时常')
        self.assertEqual(plan['metric'], 'avg_turnaround_service_minutes')
        path = [s for s in trace.snapshot() if s['id'].startswith(('rule_match_', 'semantic_normalize_', 'rule_rematch_'))]
        self.assertEqual([s['id'].rsplit('_', 1)[0] for s in path], ['rule_match', 'semantic_normalize', 'rule_rematch'])
        self.assertEqual([s['elapsed'] for s in path], [0, 5, 0])
        self.assertEqual(path[2]['details'], ['查询2026-11-12的平均保障时长'])
        self.assertFalse(any(s['state'] == 'running' for s in trace.snapshot()))
        self.assertTrue(any(e['step']['id'].startswith('semantic_normalize_') and e['step']['state'] == 'running' for e in events))

    def test_normalization_failure_is_recorded_without_provider_error_text(self):
        def chat(*args, **kwargs):
            raise TimeoutError('private provider details')
        trace = ExecutionTrace()
        coord = Coordinator(self.catalog, chat=chat, trace=trace)
        self.assertIsNone(coord._normalize_with_model('业务问题', datetime.now(BEIJING)))
        step = trace.snapshot()[-1]
        self.assertEqual(step['state'], 'failed')
        self.assertNotIn('private provider details', json.dumps(trace.snapshot()))

    def test_model_correction_keeps_each_attempt(self):
        trace = ExecutionTrace()
        coord = Coordinator(self.catalog, references=REFS, trace=trace)
        question = '昨天南航出港实际执行了多少班'
        coord.plan(question)
        replies = iter(['{}', json.dumps({'subject': ACTUAL, 'intent': 'count', 'conditions': []})])
        coord.chat = lambda *args, **kwargs: next(replies)
        anchor = datetime.now(BEIJING)
        plan = coord._model(question, anchor, slots(question, anchor, REFS, self.catalog), ACTUAL)
        self.assertEqual(plan['intent'], 'count')
        attempts = [s for s in trace.snapshot() if s['id'].startswith('model_plan_')]
        self.assertEqual([s['state'] for s in attempts], ['failed', 'completed'])


if __name__ == '__main__':
    unittest.main()
