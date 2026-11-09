"""SQL 执行报错自愈循环测试：验证 SelfCorrectingExecutor 的捕获-修正-重试机制。"""
from unittest.mock import patch, MagicMock

from app.execution import SelfCorrectingExecutor, SelfCorrectionError
from app.query import Statement


class TestSelfCorrectionSuccess:
    """首次执行成功时无需重试。"""

    def test_first_attempt_success(self):
        mock_conn = MagicMock()
        expected_rows = [{'id': 1, 'name': 'test'}]
        with patch('app.execution.execute', return_value=expected_rows) as mock_exec:
            executor = SelfCorrectingExecutor(mock_conn, chat_fn=None)
            rows = executor.execute_with_retry(
                Statement('SELECT * FROM contract_main'), '查询合同')

        assert rows == expected_rows
        assert mock_exec.call_count == 1
        assert len(executor.attempts) == 0


class TestSelfCorrectionRetrySuccess:
    """首次失败后 LLM 修正 SQL 并重试成功。"""

    def test_retry_success_after_column_error(self):
        bad_sql = 'SELECT * FROM contract_main WHERE bad_column = 1'
        good_sql = 'SELECT * FROM contract_main WHERE enterprise_scc = \'test\''

        call_count = [0]

        def mock_execute(conn, statement, preflight=False):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception('column "bad_column" does not exist')
            return [{'id': 1, 'enterprise_scc': 'test'}]

        def mock_chat(prompt):
            assert 'bad_column' in prompt
            assert 'SELECT * FROM contract_main' in prompt
            return good_sql

        with patch('app.execution.execute', side_effect=mock_execute):
            executor = SelfCorrectingExecutor(
                MagicMock(), chat_fn=mock_chat, max_retries=2)
            rows = executor.execute_with_retry(
                Statement(bad_sql), '查询合同', 'contract_main: enterprise_scc, ...')

        assert rows == [{'id': 1, 'enterprise_scc': 'test'}]
        assert call_count[0] == 2
        assert len(executor.attempts) == 1
        assert executor.attempts[0].success is True
        assert executor.attempts[0].corrected_sql == good_sql

    def test_retry_success_with_markdown_fenced_sql(self):
        bad_sql = 'SELECT * FROM nonexistent_table'
        good_sql_raw = '```sql\nSELECT * FROM contract_main\n```'

        call_count = [0]

        def mock_execute(conn, statement, preflight=False):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception('relation "nonexistent_table" does not exist')
            return [{'id': 1}]

        with patch('app.execution.execute', side_effect=mock_execute):
            executor = SelfCorrectingExecutor(
                MagicMock(), chat_fn=lambda p: good_sql_raw, max_retries=2)
            rows = executor.execute_with_retry(
                Statement(bad_sql), '查询')

        assert rows == [{'id': 1}]
        assert call_count[0] == 2
        assert 'SELECT * FROM contract_main' in executor.attempts[0].corrected_sql


class TestSelfCorrectionExhaustedRetries:
    """达到最大重试次数仍失败时抛出携带证据链的异常。"""

    def test_all_retries_fail_raises_self_correction_error(self):
        bad_sql = 'SELECT * FROM nonexistent_table'

        def mock_execute(conn, statement, preflight=False):
            raise Exception('relation "nonexistent_table" does not exist')

        def mock_chat(prompt):
            return 'SELECT * FROM still_nonexistent'

        with patch('app.execution.execute', side_effect=mock_execute):
            executor = SelfCorrectingExecutor(
                MagicMock(), chat_fn=mock_chat, max_retries=2)
            try:
                executor.execute_with_retry(
                    Statement(bad_sql), '查询')
                assert False, '应该抛出 SelfCorrectionError'
            except SelfCorrectionError as e:
                assert 'SQL 执行失败' in str(e)
                assert len(e.evidence) == 3  # 初始 + 2 次重试
                assert all(not a['success'] for a in e.evidence)

    def test_no_chat_fn_raises_after_first_failure(self):
        bad_sql = 'SELECT * FROM nonexistent_table'

        with patch('app.execution.execute',
                   side_effect=Exception('relation does not exist')):
            executor = SelfCorrectingExecutor(
                MagicMock(), chat_fn=None, max_retries=2)
            try:
                executor.execute_with_retry(Statement(bad_sql), '查询')
                assert False, '应该抛出 SelfCorrectionError'
            except SelfCorrectionError as e:
                assert len(e.evidence) == 1

    def test_llm_returns_non_select_breaks_retry(self):
        bad_sql = 'SELECT * FROM nonexistent_table'

        with patch('app.execution.execute',
                   side_effect=Exception('relation does not exist')):
            executor = SelfCorrectingExecutor(
                MagicMock(), chat_fn=lambda p: 'DELETE FROM table', max_retries=2)
            try:
                executor.execute_with_retry(Statement(bad_sql), '查询')
                assert False, '应该抛出 SelfCorrectionError'
            except SelfCorrectionError as e:
                assert len(e.evidence) == 1


class TestSelfCorrectionEvidenceChain:
    """验证自愈过程中的证据链记录完整性。"""

    def test_attempt_records_contain_sql_and_error(self):
        bad_sql = 'SELECT bad_col FROM contract_main'

        def mock_execute(conn, statement, preflight=False):
            raise Exception('column "bad_col" does not exist\nLINE 1: SELECT bad_col')

        def mock_chat(prompt):
            return 'SELECT enterprise_scc FROM contract_main'

        with patch('app.execution.execute', side_effect=mock_execute):
            executor = SelfCorrectingExecutor(
                MagicMock(), chat_fn=mock_chat, max_retries=1)
            try:
                executor.execute_with_retry(Statement(bad_sql), '查询')
            except SelfCorrectionError:
                pass

        attempt = executor.attempts[0]
        assert attempt.attempt == 0
        assert 'bad_col' in attempt.sql
        assert 'does not exist' in attempt.error
        assert 'enterprise_scc' in attempt.corrected_sql
        assert attempt.success is False


class TestRlsInjectionInCorrectedSql:
    """验证修正后的 SQL 会被注入行级权限条件。"""

    def test_rls_injected_into_corrected_sql_with_where(self):
        from app.business_access import Access

        access = Access(
            user_id='u1', username='test', display_name='Test',
            scope='DEPT_ONLY', roles=('user',), indicators=None,
            permissions=None, companies=frozenset({'scc001'}),
            revision='r1')

        bad_sql = 'SELECT * FROM contract_main WHERE bad_col = 1'
        good_sql = 'SELECT * FROM contract_main WHERE enterprise_scc = \'X\''

        call_count = [0]

        def mock_execute(conn, statement, preflight=False):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception('column "bad_col" does not exist')
            return [{'id': 1}]

        with patch('app.execution.execute', side_effect=mock_execute):
            executor = SelfCorrectingExecutor(
                MagicMock(), chat_fn=lambda p: good_sql, max_retries=2,
                access=access)
            rows = executor.execute_with_retry(Statement(bad_sql), '查询')

        assert rows == [{'id': 1}]
        # 第二次执行的 SQL 应包含 RLS 条件
        assert 'scc001' in executor.attempts[0].corrected_sql

    def test_rls_injected_into_corrected_sql_without_where(self):
        from app.business_access import Access

        access = Access(
            user_id='u1', username='test', display_name='Test',
            scope='DEPT_ONLY', roles=('user',), indicators=None,
            permissions=None, companies=frozenset({'scc001'}),
            revision='r1')

        bad_sql = 'SELECT * FROM contract_main WHERE bad_col = 1'
        good_sql = 'SELECT * FROM contract_main'

        call_count = [0]

        def mock_execute(conn, statement, preflight=False):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception('column "bad_col" does not exist')
            return [{'id': 1}]

        with patch('app.execution.execute', side_effect=mock_execute):
            executor = SelfCorrectingExecutor(
                MagicMock(), chat_fn=lambda p: good_sql, max_retries=2,
                access=access)
            rows = executor.execute_with_retry(Statement(bad_sql), '查询')

        assert rows == [{'id': 1}]
        assert 'WHERE' in executor.attempts[0].corrected_sql
        assert 'scc001' in executor.attempts[0].corrected_sql
