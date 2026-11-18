"""Deterministic planned-vs-operated flight reconciliation & cross-day flight details."""
from __future__ import annotations

from datetime import date, timedelta
import re
from time import perf_counter
from typing import Any

from app.agent.response import compose_document
from app.agent.visual import get_airport_name
from app.query import Compiler, execute, json_safe

SUBJECT = 'mv_acdm_dyn_flight_detail'

CROSS_DAY_RE = re.compile(r'跨日|跨天|跨期|跨自然日|跨零点')
RECONCILE_CUE_RE = re.compile(
    r'为什么|为何|原因|解释|对账|差额|差异|不一致|对不上|不等于|而不是|而非|多出|少了|多了|净差|核对|差[了]?[0-9一二三四五六七八九十]+(?:架次|班)?'
)
EXCLUDE_RE = re.compile(r'报告|简报|看板|预测|预估|甘特|鱼骨图|同比|环比|对标')
PLANNED_CUE_RE = re.compile(r'计划')
ACTUAL_CUE_RE = re.compile(r'实际|执飞|执行')
ALL_CROSS_DAY_CUE_RE = re.compile(r'全部跨日|所有跨日|区间内跨日')

DIRECTION_LABELS = {'DEP': '出港', 'ARR': '进港', 'D': '出港', 'A': '进港'}
DISCREPANCY_META = {
    'cross_out': ('跨出本期（−1）', '计入本期计划，实际执行落在窗口外（对实际执行影响 −1）'),
    'cross_in': ('跨入本期（+1）', '计划落在窗口外，实际执行落在窗口内（对实际执行影响 +1）'),
    'unoperated_non_cancel': ('未执行且未取消（−1）', '计入本期计划，未标记取消且未实际执行（对实际执行影响 −1）'),
    'cancel_yet_operated': ('取消标记但已执行（+1）', '计入本期计划与取消，但标记为已实际执行（对实际执行影响 +1）'),
    'internal_cross_day': ('区间内跨日（0）', '计划日与实际执行日跨日但均在统计窗口内（对区间总数净影响 0）'),
}


def is_cross_day_request(text: str) -> bool:
    """Return True when the question asks for cross-day flights or planned/cancel/operated reconciliation."""
    if not text or EXCLUDE_RE.search(text):
        return False
    if CROSS_DAY_RE.search(text):
        # Do not intercept single-flight turnaround diagnostics mentioning overnight stays.
        if re.search(r'过站|保障|时序|时间轴|时间线', text) and not (PLANNED_CUE_RE.search(text) and ACTUAL_CUE_RE.search(text)):
            return False
        return True
    if PLANNED_CUE_RE.search(text) and ACTUAL_CUE_RE.search(text) and RECONCILE_CUE_RE.search(text):
        return True
    return False


def _detect_mode(text: str) -> str:
    if ALL_CROSS_DAY_CUE_RE.search(text):
        return 'cross_day_list'
    if (PLANNED_CUE_RE.search(text) and ACTUAL_CUE_RE.search(text)) or '取消' in text or RECONCILE_CUE_RE.search(text):
        return 'reconciliation'
    return 'cross_day_list'


def _span_label(start_iso: str, end_excl_iso: str) -> str:
    start_d = date.fromisoformat(start_iso)
    end_d = date.fromisoformat(end_excl_iso) - timedelta(days=1)
    return start_d.isoformat() if start_d == end_d else f'{start_d.isoformat()} 至 {end_d.isoformat()}'


def make_plan(
    coordinator: Any,
    question: str,
    anchor: Any,
    previous: dict[str, Any] | None = None,
    day_mode: str = 'AUTO',
    confirmed_period: list[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, validated cross-day reconciliation plan."""
    from app.agent.analysis import normalize_dates, windows
    from app.agent.coordinator import slots

    catalog = coordinator.catalog
    if SUBJECT not in catalog.entities:
        return coordinator.notice(
            'unsupported',
            '当前授权目录未开放航班明细校准视图（mv_acdm_dyn_flight_detail），无法进行跨日航班对账。',
            question, anchor, {},
        )
    cap = next((c for c in getattr(catalog, 'capabilities', []) if c.get('code') == 'acdm.analysis'), None)
    if cap and cap.get('status') in ('PLANNED', 'UNAVAILABLE'):
        return coordinator.notice('unsupported', '跨日对账分析能力尚未发布或已停用。', question, anchor, {})

    text = normalize_dates(question, anchor)
    if str(day_mode or 'AUTO').strip().upper() == 'OPERATIONAL' or '运营日' in text:
        return coordinator.notice(
            'unsupported',
            '计划与实际执行架次跨日对账按北京时间自然日（plan_day 与 real_exec_day）统计，请切换到自然日口径。',
            question, anchor, {},
        )

    ws, _ = windows(text, anchor)
    period_bounds: list[str] | None = None
    if confirmed_period and len(confirmed_period) == 2:
        period_bounds = list(confirmed_period)
    elif ws:
        if len(ws) > 1:
            return coordinator.notice('clarification_needed', '跨日对账一次请指定一个连续的业务日期或期间。', question, anchor, {}, 'period', coordinator._period_choices(anchor))
        period_bounds = list(ws[0])
    elif isinstance(previous, dict):
        prior_period = ((previous.get('query_context') or {}).get('slots') or {}).get('period')
        if isinstance(prior_period, list) and len(prior_period) == 2:
            period_bounds = list(prior_period)

    if not period_bounds:
        return coordinator.notice(
            'clarification_needed',
            '请确认要核对计划与实际执行差异及跨日航班的业务日期或期间。',
            question, anchor, {}, 'period', coordinator._period_choices(anchor),
        )

    extracted = slots(text, anchor, coordinator.references, catalog)
    prior_cross = previous if isinstance(previous, dict) and previous.get('analysis_kind') == 'cross_day_reconciliation' else None
    is_short_followup = bool(
        prior_cross and not (PLANNED_CUE_RE.search(text) or ACTUAL_CUE_RE.search(text) or CROSS_DAY_RE.search(text) or ALL_CROSS_DAY_CUE_RE.search(text))
    )
    direction = extracted.get('direction')
    if is_short_followup and direction is None and '进出港' not in text:
        direction = ((prior_cross.get('query_context') or {}).get('slots') or {}).get('direction')

    extra_conditions: list[dict[str, Any]] = []
    scope_labels: list[str] = []
    if direction in ('DEP', 'ARR'):
        extra_conditions.append({'table': SUBJECT, 'col': 'direction', 'op': '=', 'value': direction})
        scope_labels.append(DIRECTION_LABELS[direction])
    for ref in extracted.get('references', []):
        if ref.get('role') == 'airline' and 'airline_two_char_cd' in catalog.entities[SUBJECT].get('attributes', {}):
            extra_conditions.append({
                'table': SUBJECT,
                'col': 'airline_two_char_cd',
                'op': ref.get('op', '='),
                'value': ref['value'],
            })
            prefix = '排除' if ref.get('op') == '!=' else ''
            scope_labels.append(f"{prefix}{ref.get('term') or ref['value']}")

    mode = (prior_cross.get('reconcile_mode') or 'reconciliation') if is_short_followup else _detect_mode(text)
    plan = {
        'subject': SUBJECT,
        'intent': 'analysis',
        'analysis_kind': 'cross_day_reconciliation',
        'reconcile_mode': mode,
        'period': period_bounds,
        'conditions': extra_conditions,
        'scope_labels': scope_labels,
        'group_by': [],
        'focus_tables': [],
        'day_mode': 'CALENDAR',
        'query_context': {
            'question': question,
            'anchor': anchor.isoformat(),
            'slots': {
                'period': period_bounds,
                **({'direction': direction} if direction in ('DEP', 'ARR') else {}),
                **({'references': extracted['references']} if extracted.get('references') else {}),
            },
        },
    }
    return plan


def build_statements(catalog: Any, plan: dict[str, Any], access: Any = None):
    """Compile parameterized summary & detail statements via Compiler (enforcing access scope)."""
    compiler = Compiler(catalog, access)
    table_sql = compiler.table(SUBJECT)
    start_iso, end_excl_iso = plan['period']
    extra_conds = list(plan.get('conditions') or [])
    extra_sql, extra_params = ('', [])
    if extra_conds:
        extra_sql, extra_params = compiler.predicate(SUBJECT, extra_conds, 'c', {SUBJECT: 'c'})
    extra_and = f' AND {extra_sql}' if extra_sql else ''

    summary_sql = (
        'WITH params AS (SELECT %s::date AS d_start, %s::date AS d_end) '
        'SELECT '
        "COUNT(*) FILTER (WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' AND c.plan_day >= p.d_start AND c.plan_day < p.d_end) AS planned_count, "
        "COUNT(*) FILTER (WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' AND c.plan_day >= p.d_start AND c.plan_day < p.d_end AND c.abnormal_flight_stat_cd = 'CAN') AS cancel_count, "
        "COUNT(*) FILTER (WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' AND c.real_exec_day >= p.d_start AND c.real_exec_day < p.d_end AND c.is_operated_flight = 'Y') AS operated_count, "
        "COUNT(*) FILTER (WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' AND c.plan_day >= p.d_start AND c.plan_day < p.d_end AND c.is_operated_flight = 'Y') AS planned_and_operated, "
        "COUNT(*) FILTER (WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' AND c.plan_day >= p.d_start AND c.plan_day < p.d_end AND c.is_operated_flight = 'Y' AND (c.real_exec_day IS NULL OR c.real_exec_day < p.d_start OR c.real_exec_day >= p.d_end)) AS cross_out_count, "
        "COUNT(*) FILTER (WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' AND c.real_exec_day >= p.d_start AND c.real_exec_day < p.d_end AND c.is_operated_flight = 'Y' AND (c.plan_day IS NULL OR c.plan_day < p.d_start OR c.plan_day >= p.d_end)) AS cross_in_count, "
        "COUNT(*) FILTER (WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' AND c.plan_day >= p.d_start AND c.plan_day < p.d_end AND COALESCE(c.abnormal_flight_stat_cd, '') <> 'CAN' AND COALESCE(c.is_operated_flight, '') <> 'Y') AS unoperated_non_cancel_count, "
        "COUNT(*) FILTER (WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' AND c.plan_day >= p.d_start AND c.plan_day < p.d_end AND c.abnormal_flight_stat_cd = 'CAN' AND c.is_operated_flight = 'Y') AS cancel_yet_operated_count, "
        "COUNT(*) FILTER (WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' AND c.is_operated_flight = 'Y' AND c.plan_day >= p.d_start AND c.plan_day < p.d_end AND c.real_exec_day >= p.d_start AND c.real_exec_day < p.d_end AND c.plan_day IS DISTINCT FROM c.real_exec_day) AS internal_cross_day_count "
        f'FROM {table_sql} c CROSS JOIN params p '
        "WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' "
        'AND ((c.plan_day >= p.d_start AND c.plan_day < p.d_end) OR (c.real_exec_day >= p.d_start AND c.real_exec_day < p.d_end))'
        + extra_and
    )
    summary_stmt = compiler.statement(summary_sql, (start_iso, end_excl_iso, *extra_params))

    compiler_detail = Compiler(catalog, access)
    detail_table_sql = compiler_detail.table(SUBJECT)
    d_extra_sql, d_extra_params = ('', [])
    if extra_conds:
        d_extra_sql, d_extra_params = compiler_detail.predicate(SUBJECT, extra_conds, 'c', {SUBJECT: 'c'})
    d_extra_and = f' AND {d_extra_sql}' if d_extra_sql else ''

    detail_sql = (
        'WITH params AS (SELECT %s::date AS d_start, %s::date AS d_end), '
        'classified AS ('
        'SELECT '
        'c.flight_id, c.flight_no, c.direction, c.is_off_in, c.airline_two_char_cd, '
        'c.craft_no, c.aircraft_type, c.origin_airport, c.dest_airport, '
        'c.plan_day, c.real_exec_day, c.exec_date, '
        "to_char(c.plan_time AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI') AS plan_time_bj, "
        "to_char(c.real_takeoff_landin_time AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI') AS real_time_bj, "
        'c.flight_status, c.abnormal_flight_stat_cd, c.is_operated_flight, '
        'CASE '
        "WHEN c.is_operated_flight = 'Y' AND c.plan_day >= p.d_start AND c.plan_day < p.d_end AND (c.real_exec_day IS NULL OR c.real_exec_day < p.d_start OR c.real_exec_day >= p.d_end) THEN 'cross_out' "
        "WHEN c.is_operated_flight = 'Y' AND c.real_exec_day >= p.d_start AND c.real_exec_day < p.d_end AND (c.plan_day IS NULL OR c.plan_day < p.d_start OR c.plan_day >= p.d_end) THEN 'cross_in' "
        "WHEN c.plan_day >= p.d_start AND c.plan_day < p.d_end AND COALESCE(c.abnormal_flight_stat_cd, '') <> 'CAN' AND COALESCE(c.is_operated_flight, '') <> 'Y' THEN 'unoperated_non_cancel' "
        "WHEN c.plan_day >= p.d_start AND c.plan_day < p.d_end AND c.abnormal_flight_stat_cd = 'CAN' AND c.is_operated_flight = 'Y' THEN 'cancel_yet_operated' "
        "WHEN c.is_operated_flight = 'Y' AND c.plan_day >= p.d_start AND c.plan_day < p.d_end AND c.real_exec_day >= p.d_start AND c.real_exec_day < p.d_end AND c.plan_day IS DISTINCT FROM c.real_exec_day THEN 'internal_cross_day' "
        'ELSE NULL END AS discrepancy_type, '
        'COALESCE(c.real_takeoff_landin_time, c.plan_time) AS sort_time '
        f'FROM {detail_table_sql} c CROSS JOIN params p '
        "WHERE COALESCE(c.flight_task_cd, '') <> 'A/N' "
        'AND ((c.plan_day >= p.d_start AND c.plan_day < p.d_end) OR (c.real_exec_day >= p.d_start AND c.real_exec_day < p.d_end))'
        + d_extra_and
        + ') '
        'SELECT flight_id, flight_no, direction, is_off_in, airline_two_char_cd, '
        'craft_no, aircraft_type, origin_airport, dest_airport, '
        'plan_day, real_exec_day, exec_date, plan_time_bj, real_time_bj, '
        'flight_status, abnormal_flight_stat_cd, is_operated_flight, discrepancy_type '
        'FROM classified '
        'WHERE discrepancy_type IS NOT NULL '
        "ORDER BY CASE discrepancy_type WHEN 'cross_out' THEN 1 WHEN 'cross_in' THEN 2 "
        "WHEN 'unoperated_non_cancel' THEN 3 WHEN 'cancel_yet_operated' THEN 4 ELSE 5 END, "
        'sort_time ASC, flight_no ASC '
        'LIMIT 200'
    )
    detail_stmt = compiler_detail.statement(detail_sql, (start_iso, end_excl_iso, *d_extra_params))
    return summary_stmt, detail_stmt


def _format_airport(code: Any) -> str:
    raw = str(code or '').strip().upper()
    if not raw:
        return '—'
    zh = get_airport_name(raw)
    if zh and zh != raw:
        return f'{zh}({raw})'
    return raw


def _format_flight_row(row: dict[str, Any]) -> dict[str, Any]:
    dtype = str(row.get('discrepancy_type') or '')
    type_label, impact_note = DISCREPANCY_META.get(dtype, (dtype, ''))
    dir_raw = str(row.get('direction') or row.get('is_off_in') or '').upper()
    dir_label = DIRECTION_LABELS.get(dir_raw, dir_raw or '—')
    origin_txt = _format_airport(row.get('origin_airport'))
    dest_txt = _format_airport(row.get('dest_airport'))
    route = f'{origin_txt} → {dest_txt}' if (row.get('origin_airport') or row.get('dest_airport')) else '—'
    return {
        'discrepancy_type': dtype,
        'cross_type': type_label,
        'flight_no': str(row.get('flight_no') or '—'),
        'direction': dir_label,
        'route': route,
        'plan_day': str(row.get('plan_day') or '—'),
        'plan_time': str(row.get('plan_time_bj') or '—'),
        'real_exec_day': str(row.get('real_exec_day') or '—'),
        'real_time': str(row.get('real_time_bj') or '—'),
        'aircraft': ' / '.join(filter(None, [str(row.get('aircraft_type') or '').strip(), str(row.get('craft_no') or '').strip()])) or '—',
        'impact_note': impact_note,
        'flight_id': row.get('flight_id'),
    }


def _flight_bullet(item: dict[str, Any]) -> str:
    action = '实际起飞' if item['direction'] == '出港' else '实际落地'
    return (
        f"• `{item['flight_no']}`（{item['direction']}，{item['route']}）："
        f"计划 `{item['plan_time']}`（计划日 `{item['plan_day']}`），"
        f"{action} `{item['real_time']}`（实际执行日 `{item['real_exec_day']}`）——{item['impact_note']}"
    )


def run_cross_day(
    conn: Any,
    question: str,
    plan: dict[str, Any],
    catalog: Any,
    trace: Any,
    on_event: Any = None,
    access: Any = None,
) -> dict[str, Any]:
    """Execute planned/cancel/operated reconciliation and cross-day flight details."""
    from app import core
    from app.agent.analysis import blank

    started = perf_counter()
    start_iso, end_excl_iso = plan['period']
    span_label = _span_label(start_iso, end_excl_iso)
    scope_prefix = ('（' + '、'.join(plan.get('scope_labels') or []) + '）') if plan.get('scope_labels') else ''

    trace.start('sql_compile', '编译跨日对账参数化 SQL', '核对计划(plan_day)、取消(plan_day)与实际执行(real_exec_day)及边界跨日航班')
    summary_stmt, detail_stmt = build_statements(catalog, plan, access)
    trace.finish(
        'sql_compile',
        '参数化 SQL 编译完成',
        [summary_stmt.sql, '绑定参数：' + str(json_safe(summary_stmt.params))],
    )

    trace.start('sql', '执行实时跨日对账查询', '读取汇总口径指标及窗口边界跨日航班明细')
    summary_rows = json_safe(execute(conn, summary_stmt))
    summary = summary_rows[0] if summary_rows else {}
    planned = int(summary.get('planned_count') or 0)
    cancelled = int(summary.get('cancel_count') or 0)
    operated = int(summary.get('operated_count') or 0)
    planned_minus_cancel = planned - cancelled
    cross_out = int(summary.get('cross_out_count') or 0)
    cross_in = int(summary.get('cross_in_count') or 0)
    unop_non_cancel = int(summary.get('unoperated_non_cancel_count') or 0)
    cancel_yet_op = int(summary.get('cancel_yet_operated_count') or 0)
    internal_cross = int(summary.get('internal_cross_day_count') or 0)
    net_diff = operated - planned_minus_cancel

    if planned == 0 and cancelled == 0 and operated == 0:
        cov_compiler = Compiler(catalog, access)
        cov_stmt = cov_compiler.statement(
            f'SELECT MIN(plan_day)::text AS min_plan, MAX(plan_day)::text AS max_plan, '
            f'MIN(real_exec_day)::text AS min_real, MAX(real_exec_day)::text AS max_real '
            f'FROM {cov_compiler.table(SUBJECT)}'
        )
        cov_rows = json_safe(execute(conn, cov_stmt))
        cov = cov_rows[0] if cov_rows else {}
        min_d = cov.get('min_plan') or cov.get('min_real')
        max_d = cov.get('max_plan') or cov.get('max_real')
        end_incl_iso = (date.fromisoformat(end_excl_iso) - timedelta(days=1)).isoformat()
        if min_d and max_d and (end_incl_iso < min_d or start_iso > max_d):
            msg = f'查询期间 {span_label} 超出本地已同步数据覆盖范围（当前覆盖：{min_d} 至 {max_d}），无法进行跨日对账。'
            trace.finish('sql', msg)
            return core.business_response(
                question,
                {'outcome': 'out_of_coverage', 'answer': msg, 'plan': json_safe(plan)},
                catalog, trace, perf_counter() - started,
            )
        msg = f'{span_label}{scope_prefix} 未查询到计划或实际执行航班记录，无法进行跨日对账。'
        trace.finish('sql', msg)
        return core.business_response(
            question,
            {'outcome': 'no_data', 'answer': msg, 'plan': json_safe(plan)},
            catalog, trace, perf_counter() - started,
        )

    raw_details = json_safe(execute(conn, detail_stmt))
    formatted = [_format_flight_row(r) for r in raw_details]
    cross_out_rows = [r for r in formatted if r['discrepancy_type'] == 'cross_out']
    cross_in_rows = [r for r in formatted if r['discrepancy_type'] == 'cross_in']
    unop_rows = [r for r in formatted if r['discrepancy_type'] == 'unoperated_non_cancel']
    cancel_op_rows = [r for r in formatted if r['discrepancy_type'] == 'cancel_yet_operated']
    internal_rows = [r for r in formatted if r['discrepancy_type'] == 'internal_cross_day']
    boundary_rows = cross_out_rows + cross_in_rows + unop_rows + cancel_op_rows

    mode = plan.get('reconcile_mode') or 'reconciliation'
    primary_rows = boundary_rows if mode == 'reconciliation' else (boundary_rows + internal_rows)
    trace.finish(
        'sql',
        f'对账完成：计划 {planned:,}、取消 {cancelled:,}、实际执行 {operated:,}（跨出 {cross_out}、跨入 {cross_in}、区间内跨日 {internal_cross}）',
    )

    # Build grounded evidence facts
    db_name = core.settings.db.get('database', 'ops_ask')
    queried_at = date.today().isoformat()
    summary_fact = {
        'id': 'F1',
        'kind': 'aggregate',
        'table': SUBJECT,
        'name': '计划/取消/实际执行跨日口径对账汇总',
        'source': {'database': db_name, 'table': SUBJECT, 'scope': f'{span_label}{scope_prefix}', 'queried_at': queried_at},
        'business_time': span_label,
        'values': {
            'planned_count': planned,
            'cancel_count': cancelled,
            'planned_minus_cancel': planned_minus_cancel,
            'cross_out_count': cross_out,
            'cross_in_count': cross_in,
            'unoperated_non_cancel_count': unop_non_cancel,
            'cancel_yet_operated_count': cancel_yet_op,
            'operated_count': operated,
            'net_diff': net_diff,
            'internal_cross_day_count': internal_cross,
        },
    }
    facts: list[dict[str, Any]] = [summary_fact]
    row_fact_ids: dict[int, str] = {}
    for idx, row in enumerate(primary_rows[:50], 2):
        fid = f'F{idx}'
        row_fact_ids[id(row)] = fid
        facts.append({
            'id': fid,
            'kind': 'record',
            'table': SUBJECT,
            'name': f"{row['cross_type']} · {row['flight_no']}",
            'source': {
                'database': db_name,
                'table': SUBJECT,
                'record_key': {'flight_id': row['flight_id'], 'flight_no': row['flight_no']},
                'queried_at': queried_at,
            },
            'business_time': f"计划 {row['plan_day']} / 实际 {row['real_exec_day']}",
            'values': {
                'cross_type': row['cross_type'],
                'flight_no': row['flight_no'],
                'direction': row['direction'],
                'route': row['route'],
                'plan_day': row['plan_day'],
                'plan_time': row['plan_time'],
                'real_exec_day': row['real_exec_day'],
                'real_time': row['real_time'],
                'impact_note': row['impact_note'],
            },
        })

    # Build deterministic reconciliation equation and structured claims
    terms = [f'计划 {planned:,}', f'− 取消 {cancelled:,}']
    if unop_non_cancel:
        terms.append(f'− 未执行且未取消 {unop_non_cancel:,}')
    if cancel_yet_op:
        terms.append(f'+ 取消标记但已执行 {cancel_yet_op:,}')
    if cross_out or cross_in or net_diff != 0:
        terms.append(f'− 跨出本期 {cross_out:,}')
        terms.append(f'+ 跨入本期 {cross_in:,}')
    equation = ' '.join(terms) + f' = 实际执行 {operated:,} 架次'

    diff_sign = f'+{net_diff}' if net_diff > 0 else str(net_diff)
    if net_diff == 0:
        conclusion = (
            f'**{span_label}{scope_prefix}** 计划 **{planned:,}** 架次、取消 **{cancelled:,}** 架次，'
            f'计划减取消为 **{planned_minus_cancel:,}** 架次，与实际执行 **{operated:,}** 架次完全一致'
            + (f'（其中跨出本期 {cross_out} 班与跨入本期 {cross_in} 班相互抵消）。' if (cross_out or cross_in) else '（窗口边界无净跨日差额）。')
        )
    else:
        conclusion = (
            f'**{span_label}{scope_prefix}** 计划 **{planned:,}** 架次、取消 **{cancelled:,}** 架次，'
            f'按计划口径减取消为 **{planned_minus_cancel:,}** 架次，但实际执行为 **{operated:,}** 架次（净差 **{diff_sign}** 架次）。'
            f'根本原因是统计日期口径不同：`计划架次`与`取消架次`按**计划自然日（`plan_day`）**归属，'
            f'而`实际执行架次`按**实际起降自然日（`real_exec_day`）**归属，窗口首尾的跨日航班导致净偏移 **{diff_sign}** 架次。'
        )

    query_claims: list[dict[str, Any]] = [
        {'text': conclusion, 'fact_ids': ['F1']},
        {'text': f'**精确对账等式**：{equation}（窗口边界跨入 `{cross_in}` 班 − 跨出 `{cross_out}` 班 = 净影响 `{cross_in - cross_out:+d}` 架次）。', 'fact_ids': ['F1']},
    ]

    finding_claims: list[dict[str, Any]] = []
    if cross_out_rows:
        out_fids = [row_fact_ids[id(r)] for r in cross_out_rows if id(r) in row_fact_ids] or ['F1']
        out_lines = [f'**一、跨出本期航班（共 {cross_out} 班，计入本期计划、未计入本期实际执行，影响 −{cross_out}）**：']
        out_lines.extend(_flight_bullet(r) for r in cross_out_rows)
        finding_claims.append({'text': '\n'.join(out_lines), 'fact_ids': out_fids, 'kind': 'findings'})
    if cross_in_rows:
        in_fids = [row_fact_ids[id(r)] for r in cross_in_rows if id(r) in row_fact_ids] or ['F1']
        in_lines = [f'**二、跨入本期航班（共 {cross_in} 班，未计入本期计划、计入本期实际执行，影响 +{cross_in}）**：']
        in_lines.extend(_flight_bullet(r) for r in cross_in_rows)
        finding_claims.append({'text': '\n'.join(in_lines), 'fact_ids': in_fids, 'kind': 'findings'})
    if unop_rows:
        u_fids = [row_fact_ids[id(r)] for r in unop_rows if id(r) in row_fact_ids] or ['F1']
        u_lines = [f'**其他差异 · 未执行且未取消航班（共 {unop_non_cancel} 班，影响 −{unop_non_cancel}）**：']
        u_lines.extend(_flight_bullet(r) for r in unop_rows[:20])
        finding_claims.append({'text': '\n'.join(u_lines), 'fact_ids': u_fids, 'kind': 'findings'})
    if cancel_op_rows:
        co_fids = [row_fact_ids[id(r)] for r in cancel_op_rows if id(r) in row_fact_ids] or ['F1']
        co_lines = [f'**其他差异 · 取消标记但已执行航班（共 {cancel_yet_op} 班，影响 +{cancel_yet_op}）**：']
        co_lines.extend(_flight_bullet(r) for r in cancel_op_rows[:20])
        finding_claims.append({'text': '\n'.join(co_lines), 'fact_ids': co_fids, 'kind': 'findings'})
    if internal_cross > 0:
        internal_hint = '（可在「数据明细 → 关联业务数据」中查看）' if mode == 'reconciliation' else '（已一并在下方跨日航班明细表中列出）'
        finding_claims.append({
            'text': f'补充说明：另有 **{internal_cross}** 班跨日航班的计划日与实际执行日虽跨零点，但均落在 `{span_label}` 区间内部，对区间总数净影响为 `0`{internal_hint}。',
            'fact_ids': ['F1'],
            'kind': 'findings',
        })

    boundary_claim = {
        'text': (
            f'查询口径与数据边界：数据源自 `public.{SUBJECT}`（排除 `flight_task_cd = A/N`）；'
            '计划架次与取消架次按北京时间计划自然日（`plan_day`）归属，实际执行架次按北京时间实际起降自然日（`real_exec_day`）归属。'
            '本结果仅反映本地已同步记录，未证明同步完整性。'
        ),
        'fact_ids': ['F1'],
        'kind': 'limitations',
    }

    claims = query_claims + finding_claims + [boundary_claim]
    query_claim_count = len(query_claims)
    evidence = {
        'facts': facts,
        'coverage': [{
            'table': SUBJECT,
            'name': 'A-CDM校准航班宽表视图',
            'provided': len(primary_rows),
            'total': len(primary_rows),
            'complete': len(raw_details) < 200,
        }],
        'queried_at': queried_at,
    }
    document = compose_document(question, plan, evidence, claims, query_claim_count, chat=None)
    answer_text = '\n\n'.join(c['text'] for c in claims)

    # Display table columns
    display_columns = [
        {'key': 'cross_type', 'label': '跨日类型', 'unit': ''},
        {'key': 'flight_no', 'label': '航班号', 'unit': ''},
        {'key': 'direction', 'label': '进出港', 'unit': ''},
        {'key': 'route', 'label': '航线', 'unit': ''},
        {'key': 'plan_day', 'label': '计划日', 'unit': ''},
        {'key': 'plan_time', 'label': '计划时刻', 'unit': ''},
        {'key': 'real_exec_day', 'label': '实际执行日', 'unit': ''},
        {'key': 'real_time', 'label': '实际起降时刻', 'unit': ''},
        {'key': 'impact_note', 'label': '对账影响', 'unit': ''},
    ]
    clean_primary = [{col['key']: r.get(col['key']) for col in display_columns} for r in primary_rows]

    summary_table_rows = [
        {'对账项': '计划航班架次（按 plan_day）', '架次': planned, '方向影响': '+计划基数', '说明': f'{span_label} 计划自然日内非 A/N 航班'},
        {'对账项': '取消航班架次（按 plan_day）', '架次': -cancelled, '方向影响': '−取消', '说明': 'abnormal_flight_stat_cd = CAN'},
        {'对账项': '计划减取消（理论值）', '架次': planned_minus_cancel, '方向影响': '= 理论应执行', '说明': f'{planned:,} − {cancelled:,}'},
        {'对账项': '跨出本期航班（计划在内、执行在外）', '架次': -cross_out, '方向影响': f'−{cross_out}', '说明': '计入本期计划，未计入本期实际执行'},
        {'对账项': '跨入本期航班（计划在外、执行在内）', '架次': cross_in, '方向影响': f'+{cross_in}', '说明': '未计入本期计划，计入本期实际执行'},
        *(
            [{'对账项': '未执行且未取消航班', '架次': -unop_non_cancel, '方向影响': f'−{unop_non_cancel}', '说明': '计划在内，未取消且未实际执行'}]
            if unop_non_cancel else []
        ),
        *(
            [{'对账项': '取消标记但已执行航班', '架次': cancel_yet_op, '方向影响': f'+{cancel_yet_op}', '说明': '同时标记取消与已执行'}]
            if cancel_yet_op else []
        ),
        {'对账项': '实际执行航班架次（按 real_exec_day）', '架次': operated, '方向影响': f'= 净差 {diff_sign}', '说明': f'{span_label} 实际起降自然日内已执飞航班'},
    ]

    related = [
        {
            'table': 'reconciliation_summary',
            'name': '计划与实际执行口径对账汇总',
            'note': f'{equation}',
            'type': 'analysis',
            'level': 1,
            'count': len(summary_table_rows),
            'rows': summary_table_rows,
        },
    ]
    if internal_rows and mode == 'reconciliation':
        related.append({
            'table': 'internal_cross_day',
            'name': '区间内跨日航班（净影响 0）',
            'note': f'计划日与实际执行日跨日但均在 {span_label} 内，共 {internal_cross} 班',
            'type': 'analysis',
            'level': 1,
            'count': internal_cross,
            'rows': [{col['key']: r.get(col['key']) for col in display_columns} for r in internal_rows[:50]],
        })

    if clean_primary:
        visual_title = (
            f'{span_label}{scope_prefix} 跨日边界航班明细（跨出 {cross_out} / 跨入 {cross_in}，净差 {diff_sign}）'
            if mode == 'reconciliation'
            else f'{span_label}{scope_prefix} 跨日航班明细（共 {len(clean_primary)} 班）'
        )
        visual_meta: dict[str, Any] = {
            'kind': 'detail_table',
            'is_flight': False,
            'title': visual_title,
            'total': len(clean_primary),
            'shown': len(clean_primary),
            'columns': display_columns,
            'rows': clean_primary,
        }
    else:
        visual_meta = {
            'kind': 'stats',
            'title': f'{span_label}{scope_prefix} 计划与实际执行对账',
            'items': [
                {'label': '计划航班', 'value': planned, 'unit': '架次'},
                {'label': '取消航班', 'value': cancelled, 'unit': '架次'},
                {'label': '实际执行', 'value': operated, 'unit': '架次'},
                {'label': '净差额', 'value': net_diff, 'unit': '架次'},
            ],
        }

    if on_event:
        for claim in query_claims:
            on_event({'type': 'answer_delta', 'delta': claim['text'] + '\n'})
        on_event({'type': 'visual_meta', 'data': visual_meta})

    result = blank(question, plan, catalog)
    result.update({
        'source': '运营总控·跨日对账分析',
        'answer': answer_text,
        'claims': claims,
        'answer_document': document,
        'sql': f'-- 1. 口径对账汇总\n{summary_stmt.sql}\n\n-- 2. 跨日及差异航班明细\n{detail_stmt.sql}',
        'sql_params': json_safe([*summary_stmt.params, *detail_stmt.params]),
        'main_total': len(clean_primary) if clean_primary else len(summary_table_rows),
        'main_rows': clean_primary if clean_primary else summary_table_rows,
        'related': related,
        'field_meta': {
            SUBJECT: {col['key']: {'label': col['label'], 'unit': col['unit']} for col in display_columns},
            'reconciliation_summary': {k: {'label': k} for k in ('对账项', '架次', '方向影响', '说明')},
            'internal_cross_day': {col['key']: {'label': col['label'], 'unit': col['unit']} for col in display_columns},
        },
        'graph': {'nodes': [{'id': SUBJECT, 'level': 0, 'main': True}], 'edges': []},
        'visual_meta': visual_meta,
        'evidence': evidence,
        'reconciliation': {
            'period': [start_iso, end_excl_iso],
            'span_label': span_label,
            'planned': planned,
            'cancelled': cancelled,
            'planned_minus_cancel': planned_minus_cancel,
            'cross_out': cross_out,
            'cross_in': cross_in,
            'unoperated_non_cancel': unop_non_cancel,
            'cancel_yet_operated': cancel_yet_op,
            'operated': operated,
            'net_diff': net_diff,
            'internal_cross_day': internal_cross,
            'equation': equation,
        },
        'elapsed': round(perf_counter() - started, 3),
        'execution_trace': trace.snapshot(),
    })
    return result
