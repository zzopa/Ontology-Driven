"""多轮对话上下文继承与指代消解测试。"""
from app.agent.planning import ConversationContext, resolve_coreference
from app.query import merge_plans


class TestTimeOverwriteCoreference:
    """时间切片覆写：如"那今年呢"继承非时间条件，仅覆写时间维度。"""

    def test_time_overwrite_basic(self):
        prev = ConversationContext(
            question='查询去年各航站楼维修合同',
            subject='合同',
            intent='aggregate',
            conditions=[
                {'table': 'contract_main', 'col': 'contract_category_level1', 'op': '=', 'value': '维修'},
                {'table': 'contract_main', 'col': 'signing_at', 'op': '>=', 'value': '2024-01-01'},
                {'table': 'contract_main', 'col': 'signing_at', 'op': '<=', 'value': '2024-12-31'},
            ],
            focus_tables=['contract_main'],
        )
        ctx = resolve_coreference('那今年呢', prev)
        assert ctx is not None
        assert ctx.time_overwrite is True
        assert ctx.new_time_period == '今年'
        assert ctx.subject == '合同'
        assert ctx.intent == 'aggregate'
        # 非时间条件保留
        non_time = [c for c in ctx.conditions if c['col'] != 'signing_at']
        assert len(non_time) == 1
        assert non_time[0]['col'] == 'contract_category_level1'

    def test_time_overwrite_last_year(self):
        prev = ConversationContext(
            question='查询今年合同总金额',
            subject='合同',
            conditions=[
                {'table': 'contract_main', 'col': 'signing_at', 'op': '>=', 'value': '2025-01-01'},
            ],
        )
        ctx = resolve_coreference('换成去年看看', prev)
        assert ctx is not None
        assert ctx.time_overwrite is True
        assert ctx.new_time_period == '去年'
        # 时间条件被剥离
        assert len(ctx.conditions) == 0

    def test_time_overwrite_preserves_non_time_filter(self):
        prev = ConversationContext(
            question='查询执行中的合同',
            conditions=[
                {'table': 'contract_main', 'col': 'contract_phase', 'op': '=', 'value': '执行中'},
                {'table': 'contract_main', 'col': 'signing_at', 'op': '>=', 'value': '2025-01-01'},
            ],
        )
        ctx = resolve_coreference('那去年呢', prev)
        assert ctx is not None
        assert ctx.time_overwrite is True
        assert len(ctx.conditions) == 1
        assert ctx.conditions[0]['col'] == 'contract_phase'


class TestConditionAppendCoreference:
    """条件追加深入：如"把其中的超期合同列出来"继承已有条件。"""

    def test_condition_append_basic(self):
        prev = ConversationContext(
            question='查询所有合同',
            subject='合同',
            intent='list',
            conditions=[
                {'table': 'contract_main', 'col': 'contract_category_level1', 'op': '=', 'value': '维修'},
            ],
            focus_tables=['contract_main'],
        )
        ctx = resolve_coreference('把其中的超期合同列出来', prev)
        assert ctx is not None
        assert ctx.condition_append is True
        assert ctx.subject == '合同'
        assert ctx.intent == 'list'
        # 继承全部已有条件
        assert len(ctx.conditions) == 1
        assert ctx.conditions[0]['col'] == 'contract_category_level1'

    def test_condition_append_with_existing_filters(self):
        prev = ConversationContext(
            question='查询执行中的维修合同',
            subject='合同',
            conditions=[
                {'table': 'contract_main', 'col': 'contract_phase', 'op': '=', 'value': '执行中'},
                {'table': 'contract_main', 'col': 'contract_category_level1', 'op': '=', 'value': '维修'},
            ],
        )
        ctx = resolve_coreference('再筛选金额大于100万的', prev)
        assert ctx is not None
        assert ctx.condition_append is True
        assert len(ctx.conditions) == 2


class TestNoCoreference:
    """不匹配追问模式时返回 None。"""

    def test_no_prev_returns_none(self):
        assert resolve_coreference('查询合同', None) is None

    def test_new_question_returns_none(self):
        prev = ConversationContext(question='查询合同', subject='合同')
        assert resolve_coreference('查询资金流水明细', prev) is None

    def test_empty_question_returns_none(self):
        prev = ConversationContext(question='查询合同', subject='合同')
        assert resolve_coreference('', prev) is None
        assert resolve_coreference('   ', prev) is None


class TestMergePlans:
    """merge_plans 查询计划合并逻辑。"""

    def test_merge_time_bucket_override(self):
        base = {
            'subject': '合同',
            'intent': 'aggregate',
            'conditions': [{'table': 't', 'col': 'phase', 'op': '=', 'value': '执行中'}],
            'time_bucket': {'column': 'signing_at', 'period': '2024'},
            'focus_tables': ['contract_main'],
        }
        overrides = {
            'time_bucket': {'column': 'signing_at', 'period': '2025'},
        }
        merged = merge_plans(base, overrides)
        assert merged['time_bucket']['period'] == '2025'
        assert merged['subject'] == '合同'
        assert len(merged['conditions']) == 1

    def test_merge_conditions_dedup(self):
        base = {
            'subject': '合同',
            'conditions': [
                {'table': 'contract_main', 'col': 'phase', 'op': '=', 'value': '执行中'},
                {'table': 'contract_main', 'col': 'category', 'op': '=', 'value': '维修'},
            ],
        }
        overrides = {
            'conditions': [
                {'table': 'contract_main', 'col': 'phase', 'op': '=', 'value': '已完成'},
            ],
        }
        merged = merge_plans(base, overrides)
        # 同 table+col+op 的条件以 overrides 为准，base 中被移除
        assert len(merged['conditions']) == 2
        phase_conds = [c for c in merged['conditions'] if c['col'] == 'phase']
        assert len(phase_conds) == 1
        assert phase_conds[0]['value'] == '已完成'
        category_conds = [c for c in merged['conditions'] if c['col'] == 'category']
        assert len(category_conds) == 1

    def test_merge_focus_tables_override(self):
        base = {
            'subject': '合同',
            'focus_tables': ['contract_main'],
        }
        overrides = {
            'focus_tables': ['fund_flow_record'],
        }
        merged = merge_plans(base, overrides)
        assert merged['focus_tables'] == ['fund_flow_record']

    def test_merge_no_override_keeps_base(self):
        base = {
            'subject': '合同',
            'conditions': [{'table': 't', 'col': 'c', 'op': '=', 'value': 'v'}],
            'focus_tables': ['contract_main'],
            'aggregate_target': 'amount',
            'metric': 'sum',
        }
        merged = merge_plans(base, {})
        assert merged['subject'] == '合同'
        assert merged['focus_tables'] == ['contract_main']
        assert merged['aggregate_target'] == 'amount'
        assert merged['metric'] == 'sum'
        assert len(merged['conditions']) == 1

    def test_merge_aggregate_target_override(self):
        base = {'subject': '合同', 'aggregate_target': 'amount'}
        overrides = {'aggregate_target': 'count'}
        merged = merge_plans(base, overrides)
        assert merged['aggregate_target'] == 'count'
