"""行级数据权限（RLS）强制下推测试：验证 SemanticEngine 生成的 SQL 包含权限过滤条件。"""
from app.business_access import Access
from app.semantic_engine import SemanticEngine


def _make_access(scope='DEPT_ONLY', companies=None, department_ids=None):
    """构造测试用 Access 对象。"""
    if companies is None:
        companies = {'scc001'}
    return Access(
        user_id='u1', username='test', display_name='Test User',
        scope=scope, roles=('user',), indicators=None, permissions=None,
        companies=frozenset(companies),
        revision='rev1',
        department_ids=frozenset(department_ids or []),
    )


def _make_admin_access():
    """构造无限制管理员 Access。"""
    return Access(
        user_id='admin1', username='admin', display_name='Admin',
        scope='ALL', roles=('admin',), indicators=None, permissions=None,
        companies=frozenset({'scc001'}), revision='rev1',
    )


class TestRlsInjectionInSemanticEngine:
    """SemanticEngine.compile 生成的 SQL 必须包含行级权限条件。"""

    def test_dept_only_user_scc_injected(self):
        access = _make_access(scope='DEPT_ONLY', companies={'scc001', 'scc002'})
        engine = SemanticEngine(access=access)
        sql = engine.compile({'cube': 'contracts', 'measures': ['total_amount']})
        assert 'scc001' in sql
        assert 'scc002' in sql
        assert 'enterprise_scc' in sql
        assert 'WHERE' in sql

    def test_self_only_user_submitter_injected(self):
        access = _make_access(scope='SELF_ONLY', companies={'scc001'})
        engine = SemanticEngine(access=access)
        sql = engine.compile({'cube': 'contracts', 'measures': ['total_amount']})
        assert 'submitter_user_id' in sql
        assert 'u1' in sql
        assert 'scc001' in sql

    def test_rls_and_user_filters_combined_with_and(self):
        access = _make_access(scope='DEPT_ONLY', companies={'scc001'})
        engine = SemanticEngine(access=access)
        sql = engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount'],
            'filters': [{'member': 'phase', 'op': '=', 'value': '执行中'}],
        })
        assert 'contract_phase' in sql
        assert 'scc001' in sql
        assert ' AND ' in sql

    def test_rls_with_dimensions_and_time(self):
        access = _make_access(scope='DEPT_ONLY', companies={'scc001'})
        engine = SemanticEngine(access=access)
        sql = engine.compile({
            'cube': 'contracts',
            'measures': ['total_amount', 'record_count'],
            'dimensions': ['enterprise'],
            'time_dimension': {'name': 'signing_at', 'granularity': 'month'},
            'filters': [{'member': 'phase', 'op': '=', 'value': '执行中'}],
        })
        assert 'scc001' in sql
        assert 'contract_phase' in sql
        assert 'GROUP BY' in sql
        assert 'DATE_TRUNC' in sql

    def test_admin_user_no_rls_injected(self):
        access = _make_admin_access()
        engine = SemanticEngine(access=access)
        sql = engine.compile({'cube': 'contracts', 'measures': ['total_amount']})
        assert 'scc001' not in sql
        assert 'WHERE' not in sql

    def test_no_access_no_rls(self):
        engine = SemanticEngine()
        sql = engine.compile({'cube': 'contracts', 'measures': ['total_amount']})
        assert 'scc001' not in sql
        assert 'WHERE' not in sql

    def test_rls_applied_to_fund_flow_cube(self):
        access = _make_access(scope='DEPT_ONLY', companies={'scc001'})
        engine = SemanticEngine(access=access)
        sql = engine.compile({'cube': 'fund_flow', 'measures': ['transaction_amount_sum']})
        assert 'scc001' in sql


class TestRlsPredicate:
    """直接测试 Access.rls_predicate 方法。"""

    def test_unrestricted_returns_none(self):
        access = _make_admin_access()
        assert access.rls_predicate('contract_main') is None

    def test_table_not_in_policy_returns_none(self):
        access = _make_access()
        assert access.rls_predicate('nonexistent_table') is None

    def test_dept_only_returns_scc_condition(self):
        access = _make_access(scope='DEPT_ONLY', companies={'scc001', 'scc002'})
        rls = access.rls_predicate('contract_main')
        assert rls is not None
        assert 'scc001' in rls
        assert 'scc002' in rls
        assert 'enterprise_scc' in rls
        assert 'IN' in rls

    def test_self_only_includes_submitter(self):
        access = _make_access(scope='SELF_ONLY', companies={'scc001'})
        rls = access.rls_predicate('contract_main')
        assert rls is not None
        assert 'submitter_user_id' in rls
        assert 'u1' in rls

    def test_custom_alias(self):
        access = _make_access(scope='DEPT_ONLY', companies={'scc001'})
        rls = access.rls_predicate('contract_main', alias='a')
        assert 'a.' in rls
        assert 't0.' not in rls

    def test_no_companies_returns_false(self):
        access = _make_access(scope='DEPT_ONLY', companies=set())
        rls = access.rls_predicate('contract_main')
        assert rls is not None
        assert 'FALSE' in rls
