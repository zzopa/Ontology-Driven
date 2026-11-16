"""测试无本体对照问题的快速拦截与提示回复机制。

验证：
1. 输入问题没有业务本体对照时，快速提示「回复回复不了」，不进行无效大模型与SQL轮询查询。
2. 正常具有本体对照（实体、指标、维度、航班、过站保障、报告等）的问题不受影响。
"""
from time import perf_counter
import unittest

from app import core


class NoOntologyMatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core.initialize()
        cls.catalog = core.current_catalog()
        cls.conn = core.connect_db()
        cls.references = core.load_ops_references(cls.conn, cls.catalog)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_has_ontology_match_distinguishes_correctly(self):
        non_ontology_questions = [
            '今天天气怎么样',
            '红烧肉怎么做',
            '今天星期几',
            '你是谁',
            '讲个笑话',
            '武汉今天下雨吗',
            '写一段Python代码',
            '1+1等于几',
            '你好',
            '帮我订两张电影票',
            '12345678',
            '测试一下',
            '今天油价多少钱一升',
        ]
        for q in non_ontology_questions:
            with self.subTest(question=q):
                self.assertFalse(
                    self.catalog.has_ontology_match(q, references=self.references),
                    f"'{q}' 应被识别为无本体对照",
                )

        ontology_questions = [
            '今天有哪些航班',
            'CZ3101在哪个机位',
            '放行正常率是多少',
            '南航昨天进港航班数量',
            'B5916过站保障情况',
            '今天运行日报',
            '能查什么',
            '可以查什么',
            '支持哪些',
            '昨天出港延误有几班',
            '查一下廊桥使用情况',
            '天河机场跑道有几条',
            '昨天进港总数',
            '厦航在哪个登机口',
            'MU2501起飞了吗',
            'B-1234明天有任务吗',
            '机位周转率',
            '靠桥率',
            '行李装卸进度',
        ]
        for q in ontology_questions:
            with self.subTest(question=q):
                self.assertTrue(
                    self.catalog.has_ontology_match(q, references=self.references),
                    f"'{q}' 应被识别为有本体对照",
                )

    def test_do_ask_fast_returns_on_no_match(self):
        questions = [
            '今天天气怎么样',
            '红烧肉怎么做',
            '你是谁',
            '讲个笑话',
        ]
        for q in questions:
            t0 = perf_counter()
            res = core.do_ask(self.conn, q, use_model=True, use_cache=False)
            elapsed = perf_counter() - t0

            self.assertEqual(res.get('outcome'), 'unsupported')
            self.assertIn('回复回复不了', res.get('answer', ''))
            self.assertLess(elapsed, 0.3, f"无本体对照问题应该毫秒级返回，不转圈，实际耗时 {elapsed}s")
            self.assertEqual(res.get('sql'), '')
            self.assertEqual(res.get('main_total'), 0)

            # 验证执行追踪记录了明确的无本体对照说明
            trace_steps = {s['id']: s for s in res.get('execution_trace', [])}
            self.assertIn('ontology', trace_steps)
            self.assertEqual(trace_steps['ontology']['state'], 'completed')
            self.assertEqual(trace_steps['ontology']['message'], '未匹配到业务本体对照')

    def test_multiturn_context_preserves_match(self):
        # 追问或澄清场景即使单独句子简短，也继承上下文
        prev_plan = {'subject': 'std_flight_core', 'intent': 'detail', 'conditions': []}
        self.assertTrue(
            self.catalog.has_ontology_match('那昨天呢', references=self.references, previous_plan=prev_plan)
        )
        clarification = {'original_question': 'MU2501起飞了吗'}
        self.assertTrue(
            self.catalog.has_ontology_match('实际执行', references=self.references, clarification=clarification)
        )


if __name__ == '__main__':
    unittest.main()
