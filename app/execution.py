"""Public execution milestones, never model-internal reasoning or raw prompts."""
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable

from app.query import Statement, execute


class ExecutionTrace:
    def __init__(self, on_event=None):
        self.on_event = on_event
        self.started = perf_counter()
        self.steps = {}

    def start(self, stage, label, message, details=()):
        self.steps[stage] = {'id': stage, 'label': label, 'state': 'running',
                             'message': message, 'details': list(details),
                             'started_at': round(perf_counter() - self.started, 3), 'elapsed': 0}
        self._emit(stage)

    def finish(self, stage, message, details=(), state='completed'):
        step = self.steps[stage]
        step.update(state=state, message=message, details=list(details),
                    elapsed=round(perf_counter() - self.started - step['started_at'], 3))
        self._emit(stage)

    def update(self, stage, message):
        """A real in-flight heartbeat; preserve the original start time/state."""
        step = self.steps[stage]
        step.update(message=message,
                    elapsed=round(perf_counter() - self.started - step['started_at'], 3))
        self._emit(stage)

    def fail_active(self, message):
        active = False
        for stage, step in self.steps.items():
            if step['state'] == 'running':
                active = True
                self.finish(stage, message, step['details'], state='failed')
        if not active and not any(step['state'] == 'failed' for step in self.steps.values()):
            self.start('query_error', '查询未完成', '检查查询条件与执行结果')
            self.finish('query_error', message, state='failed')

    def _emit(self, stage):
        if self.on_event:
            self.on_event({'type': 'step', 'step': deepcopy(self.steps[stage])})

    def snapshot(self):
        return deepcopy(list(self.steps.values()))


@dataclass
class RetryAttempt:
    """单次自愈重试的证据记录。"""
    attempt: int
    sql: str
    error: str
    corrected_sql: str | None = None
    success: bool = False


class SelfCorrectingExecutor:
    """SQL 执行报错自愈循环：捕获数据库异常后由 LLM 修正 SQL 并重试。

    最大重试次数 2 次。若重试成功，记录自愈日志并正常返回结果；
    若达到最大重试次数仍失败，抛出携带证据链的可解释异常。
    """

    def __init__(self, conn: Any, chat_fn: Callable[[str], str] | None = None,
                 max_retries: int = 2, trace: ExecutionTrace | None = None,
                 access: Any = None) -> None:
        self.conn = conn
        self.chat_fn = chat_fn
        self.max_retries = max_retries
        self.trace = trace
        self.access = access
        self.attempts: list[RetryAttempt] = []

    def execute_with_retry(self, statement: Statement, question: str = '',
                           schema_context: str = '') -> list[dict]:
        """执行 SQL，失败时自动修正并重试。

        返回查询结果行列表。若所有重试均失败，抛出 RuntimeError 携带证据链。
        """
        current_sql = statement.sql
        current_params = statement.params
        last_error = ''

        for attempt_num in range(self.max_retries + 1):
            try:
                rows = execute(self.conn, Statement(current_sql, current_params))
                if attempt_num > 0:
                    self.attempts[-1].success = True
                return rows
            except Exception as exc:
                last_error = str(exc)
                attempt = RetryAttempt(
                    attempt=attempt_num, sql=current_sql, error=last_error,
                    corrected_sql=None, success=False)
                self.attempts.append(attempt)

                if attempt_num >= self.max_retries or self.chat_fn is None:
                    break

                # 构造修正 Prompt 并调用 LLM 生成修正 SQL
                from app.agent.planning import build_correction_prompt
                prompt = build_correction_prompt(
                    question, current_sql, last_error, schema_context)
                corrected = self.chat_fn(prompt)

                # 提取纯 SQL（去除可能的 markdown 围栏）
                corrected = self._extract_sql(corrected)
                if not corrected or not corrected.upper().strip().startswith('SELECT'):
                    break

                # 行级权限强制下推：确保修正后的 SQL 仍包含 RLS 条件
                if self.access is not None:
                    corrected = self._inject_rls(corrected)

                current_sql = corrected
                current_params = ()
                attempt.corrected_sql = corrected

        # 所有重试均失败，抛出携带证据链的异常
        evidence = [{'attempt': a.attempt, 'sql': a.sql, 'error': a.error,
                      'corrected_sql': a.corrected_sql, 'success': a.success}
                    for a in self.attempts]
        raise SelfCorrectionError(
            'SQL 执行失败，已重试 %d 次仍无法修复：%s' % (len(self.attempts), last_error),
            evidence)

    @staticmethod
    def _extract_sql(text: str) -> str:
        """从 LLM 输出中提取纯 SQL 文本。"""
        if not text:
            return ''
        lines = text.strip().splitlines()
        if lines and lines[0].strip().startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        return '\n'.join(lines).strip()

    def _inject_rls(self, sql: str) -> str:
        """对修正后的 SQL 追加行级权限 WHERE 条件，防止越权查询。

        策略：在 SQL 的 WHERE 子句末尾追加 RLS 谓词；
        若无 WHERE 则新建。仅处理顶层 SELECT，子查询不干预。
        """
        from app.business_access import POLICY

        conditions: list[str] = []
        # 遍历策略中所有受控表，若 SQL 引用了该表则注入对应 RLS 条件
        upper_sql = sql.upper()
        for table, spec in POLICY['tables'].items():
            if table.upper() in upper_sql:
                rls = self.access.rls_predicate(table, 't0')
                if rls:
                    conditions.append(rls)
        if not conditions:
            return sql
        rls_clause = ' AND '.join(conditions)
        # 尝试在 WHERE 子句后追加 RLS 条件
        import re
        where_match = re.search(r'\bWHERE\b', sql, re.IGNORECASE)
        if where_match:
            return sql + ' AND ' + rls_clause
        # 无 WHERE：在 FROM/GROUP BY/ORDER BY/LIMIT 前插入 WHERE
        for kw in ('GROUP BY', 'ORDER BY', 'LIMIT', 'HAVING'):
            kw_match = re.search(r'\b' + kw + r'\b', sql, re.IGNORECASE)
            if kw_match:
                return sql[:kw_match.start()] + 'WHERE ' + rls_clause + ' ' + sql[kw_match.start():]
        return sql + '\nWHERE ' + rls_clause


class SelfCorrectionError(RuntimeError):
    """SQL 自愈失败异常，携带完整的重试证据链。"""

    def __init__(self, message: str, evidence: list[dict]) -> None:
        super().__init__(message)
        self.evidence = evidence
