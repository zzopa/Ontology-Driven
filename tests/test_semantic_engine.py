"""SemanticEngine 单元测试：验证指标语义层 SQL 编译的正确性与异常保护。"""
import json
import unittest
from pathlib import Path

from app.semantic_engine import SemanticEngine
from app.agent.planning import classify_intent


class SemanticEngineStandardAggregationTests(unittest.TestCase):
    """标准聚合与多维度分组的 SQL 编译。"""

    def setUp(self):
        self.engine = SemanticEngine()

    def test_single_measure_without_dimensions(self):
        sql = self.engine.compile({'cube': 'contracts', 'measures': ['total_amount']})
        self.assertIn('SUM(t0."lump_sum_amount_wan")', sql)
        self.assertIn('"total_amount"', sql)
        self.assertIn('FROM "public"."contract_main" AS t0', sql)
        self.assertNotIn('GROUP BY', sql)
        self.assertNotIn('WHERE', sql)

    def test_count_measure_uses_count_not_sum(self):
        sql = self.engine.compile({'cube': 'contracts', 'measures': ['record_count']})
        self.assertIn('COUNT(t0."1")', sql)
        self.assertNotIn('SUM', sql)

    def test_count_distinct_measure(self):
        sql = self.engine.compile({'cube': 'contracts', 'measures': ['distinct_enterprise_count']})
        self.assertIn('COUNT(DISTINCT t0."enterprise_scc")', sql)

    def test_multiple_measures_and_dimensions(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount', 'record_count'],
            'dimensions': ['enterprise', 'category'],
        })
        self.assertIn('t0."enterprise_name" AS "enterprise"', sql)
        self.assertIn('t0."contract_category_level1" AS "category"', sql)
        self.assertIn('SUM(t0."lump_sum_amount_wan") AS "total_amount"', sql)
        self.assertIn('COUNT(t0."1") AS "record_count"', sql)
        self.assertIn('GROUP BY', sql)
        self.assertIn('t0."enterprise_name"', sql)
        self.assertIn('t0."contract_category_level1"', sql)

    def test_order_by_measure_desc(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'dimensions': ['enterprise'],
            'order_by': [{'member': 'total_amount', 'direction': 'desc'}],
        })
        self.assertIn('ORDER BY', sql)
        self.assertIn('"total_amount" DESC', sql)

    def test_limit_clause(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'dimensions': ['enterprise'],
            'limit': 10,
        })
        self.assertIn('LIMIT 10', sql)

    def test_multiple_cubes(self):
        sql_ff = self.engine.compile({'cube': 'fund_flow', 'measures': ['transaction_amount_sum']})
        self.assertIn('fund_flow_record', sql_ff)
        sql_pr = self.engine.compile({'cube': 'payroll', 'measures': ['gross_pay_sum']})
        self.assertIn('comp_payroll_monthly', sql_pr)

    def test_numeric_text_measure_wraps_with_case(self):
        sql = self.engine.compile({'cube': 'payroll', 'measures': ['gross_pay_sum']})
        self.assertIn('CASE WHEN', sql)
        self.assertIn('::numeric', sql)


class SemanticEngineTimeDimensionTests(unittest.TestCase):
    """带时间维度截断聚合的 SQL 编译。"""

    def setUp(self):
        self.engine = SemanticEngine()

    def test_month_granularity(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'time_dimension': {'name': 'signing_at', 'granularity': 'month'},
        })
        self.assertIn("DATE_TRUNC('month', t0.\"signing_at\")", sql)
        self.assertIn('AS "signing_at_month"', sql)
        self.assertIn('GROUP BY', sql)
        self.assertIn("DATE_TRUNC('month'", sql)

    def test_quarter_granularity(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'time_dimension': {'name': 'signing_at', 'granularity': 'quarter'},
        })
        self.assertIn("DATE_TRUNC('quarter', t0.\"signing_at\")", sql)
        self.assertIn('AS "signing_at_quarter"', sql)

    def test_year_granularity(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'time_dimension': {'name': 'signing_at', 'granularity': 'year'},
        })
        self.assertIn("DATE_TRUNC('year', t0.\"signing_at\")", sql)
        self.assertIn('AS "signing_at_year"', sql)

    def test_time_dimension_with_dimension(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount', 'record_count'],
            'dimensions': ['enterprise'],
            'time_dimension': {'name': 'signing_at', 'granularity': 'month'},
        })
        self.assertIn('t0."enterprise_name" AS "enterprise"', sql)
        self.assertIn("DATE_TRUNC('month'", sql)
        self.assertIn('GROUP BY', sql)
        self.assertLess(sql.index('"enterprise"'), sql.index('DATE_TRUNC'))

    def test_unsupported_granularity_raises(self):
        with self.assertRaisesRegex(ValueError, '时间粒度'):
            self.engine.compile({
                'cube': 'contracts',
                'measures': ['total_amount'],
                'time_dimension': {'name': 'signing_at', 'granularity': 'hour'},
            })

    def test_unknown_time_dimension_raises(self):
        with self.assertRaisesRegex(ValueError, '时间维度'):
            self.engine.compile({
                'cube': 'contracts',
                'measures': ['total_amount'],
                'time_dimension': {'name': 'nonexistent', 'granularity': 'month'},
            })


class SemanticEngineFilterTests(unittest.TestCase):
    """带复杂过滤条件的组合场景。"""

    def setUp(self):
        self.engine = SemanticEngine()

    def test_equality_filter(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'filters': [{'member': 'phase', 'op': '=', 'value': '执行中'}],
        })
        self.assertIn("WHERE", sql)
        self.assertIn("t0.\"contract_phase\" = '执行中'", sql)

    def test_in_filter(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'filters': [{'member': 'phase', 'op': 'in', 'value': ['执行中', '已完成']}],
        })
        self.assertIn("IN (", sql)
        self.assertIn("'执行中'", sql)
        self.assertIn("'已完成'", sql)

    def test_comparison_filter(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'filters': [{'member': 'signing_at', 'op': '>=', 'value': '2024-01-01'}],
        })
        self.assertIn("t0.\"signing_at\" >= '2024-01-01'", sql)

    def test_like_filter(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'filters': [{'member': 'enterprise', 'op': 'like', 'value': '%科技%'}],
        })
        self.assertIn('LIKE', sql)
        self.assertIn('%科技%', sql)

    def test_is_null_filter(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'filters': [{'member': 'phase', 'op': 'is_null'}],
        })
        self.assertIn('IS NULL', sql)

    def test_is_not_null_filter(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'filters': [{'member': 'phase', 'op': 'is_not_null'}],
        })
        self.assertIn('IS NOT NULL', sql)

    def test_multiple_filters_combined_with_and(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'dimensions': ['enterprise'],
            'filters': [
                {'member': 'phase', 'op': '=', 'value': '执行中'},
                {'member': 'currency', 'op': '=', 'value': 'CNY'},
            ],
            'time_dimension': {'name': 'signing_at', 'granularity': 'month'},
            'order_by': [{'member': 'total_amount', 'direction': 'desc'}],
            'limit': 20,
        })
        self.assertIn("contract_phase\" = '执行中'", sql)
        self.assertIn("currency_code\" = 'CNY'", sql)
        self.assertIn(' AND ', sql)
        self.assertIn("DATE_TRUNC('month'", sql)
        self.assertIn('ORDER BY', sql)
        self.assertIn('LIMIT 20', sql)

    def test_filter_value_with_single_quote_escaped(self):
        sql = self.engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'filters': [{'member': 'enterprise', 'op': '=', "value": "O'Brien"}],
        })
        self.assertIn("O''Brien", sql)
        self.assertNotIn("O'Brien", sql)

    def test_filter_on_undefined_dimension_raises(self):
        with self.assertRaisesRegex(ValueError, '过滤维度'):
            self.engine.compile({
                'cube': 'contracts',
                'measures': ['total_amount'],
                'filters': [{'member': 'nonexistent', 'op': '=', 'value': 'x'}],
            })

    def test_invalid_filter_op_raises(self):
        with self.assertRaisesRegex(ValueError, '过滤运算符'):
            self.engine.compile({
                'cube': 'contracts',
                'measures': ['total_amount'],
                'filters': [{'member': 'phase', 'op': 'contains', 'value': 'x'}],
            })


class SemanticEngineValidationTests(unittest.TestCase):
    """非法指标名或不存在的维度的异常抛出保护。"""

    def setUp(self):
        self.engine = SemanticEngine()

    def test_unknown_cube_raises(self):
        with self.assertRaisesRegex(ValueError, '未知的指标 Cube'):
            self.engine.compile({'cube': 'nonexistent', 'measures': ['total_amount']})

    def test_missing_cube_raises(self):
        with self.assertRaisesRegex(ValueError, '缺少 cube'):
            self.engine.compile({'measures': ['total_amount']})

    def test_unknown_measure_raises(self):
        with self.assertRaisesRegex(ValueError, '指标'):
            self.engine.compile({'cube': 'contracts', 'measures': ['nonexistent_measure']})

    def test_unknown_dimension_raises(self):
        with self.assertRaisesRegex(ValueError, '维度'):
            self.engine.compile({
                'cube': 'contracts',
                'measures': ['total_amount'],
                'dimensions': ['nonexistent_dim'],
            })

    def test_empty_measures_raises(self):
        with self.assertRaisesRegex(ValueError, '至少一个 measure'):
            self.engine.compile({'cube': 'contracts', 'measures': []})

    def test_missing_measures_raises(self):
        with self.assertRaisesRegex(ValueError, '至少一个 measure'):
            self.engine.compile({'cube': 'contracts'})

    def test_non_dict_spec_raises(self):
        with self.assertRaisesRegex(ValueError, 'JSON 对象'):
            self.engine.compile('not a dict')  # type: ignore[arg-type]

    def test_invalid_limit_raises(self):
        with self.assertRaisesRegex(ValueError, 'limit'):
            self.engine.compile({
                'cube': 'contracts',
                'measures': ['total_amount'],
                'limit': -1,
            })

    def test_invalid_order_direction_raises(self):
        with self.assertRaisesRegex(ValueError, '排序方向'):
            self.engine.compile({
                'cube': 'contracts',
                'measures': ['total_amount'],
                'order_by': [{'member': 'total_amount', 'direction': 'random'}],
            })

    def test_order_by_unknown_member_raises(self):
        with self.assertRaisesRegex(ValueError, '排序字段'):
            self.engine.compile({
                'cube': 'contracts',
                'measures': ['total_amount'],
                'order_by': [{'member': 'nonexistent', 'direction': 'desc'}],
            })

    def test_time_dimension_missing_granularity_raises(self):
        with self.assertRaisesRegex(ValueError, 'name 和 granularity'):
            self.engine.compile({
                'cube': 'contracts',
                'measures': ['total_amount'],
                'time_dimension': {'name': 'signing_at'},
            })


class IntentClassificationTests(unittest.TestCase):
    """双路由意图分发：classify_intent 关键词分类。"""

    def test_metric_aggregation_intent(self):
        self.assertEqual(classify_intent('按月统计各企业合同总金额'), 'metric_aggregation')
        self.assertEqual(classify_intent('各企业的合同总金额趋势'), 'metric_aggregation')
        self.assertEqual(classify_intent('按季度汇总合同总金额'), 'metric_aggregation')

    def test_graph_entity_qa_intent(self):
        self.assertEqual(classify_intent('查找张三的详细档案'), 'graph_entity_qa')
        self.assertEqual(classify_intent('信科合同关联的资金流水明细'), 'graph_entity_qa')
        self.assertEqual(classify_intent('列出合同号为HT001的详细信息'), 'graph_entity_qa')

    def test_mixed_intent_prefers_graph(self):
        self.assertEqual(classify_intent('按月统计合同明细'), 'graph_entity_qa')

    def test_empty_question_raises(self):
        with self.assertRaises(ValueError):
            classify_intent('')


if __name__ == '__main__':
    unittest.main()
