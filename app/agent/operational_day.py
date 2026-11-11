"""Operational Day (业务运营日 04:00 日切) 时间与统计口径支持.

针对民航机场与轮班制业务，午夜 00:00~04:00 的起降航段在运营上归属于前一个业务日。
本模块提供：
1. operational_date: 根据指定时间点解析其归属的运营日
2. operational_window: 将日期/日期范围转换为 [D 04:00:00, D+1 04:00:00) 的起止时间窗口
3. operational_hour: 计算运营时（以 04:00 为第 0 运营时）
4. build_operational_conditions: 为查询计划生成精确的运营日时间边界过滤条件
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_START_HOUR = 4


def get_operational_date(dt: datetime, start_hour: int = DEFAULT_START_HOUR) -> date:
    """计算指定时间所在的业务运营日。
    若时间在 00:00 至 start_hour 之前，归属为上一自然日。
    """
    if dt.hour < start_hour:
        return dt.date() - timedelta(days=1)
    return dt.date()


def get_operational_window(
    start_date: date,
    end_date: Optional[date] = None,
    start_hour: int = DEFAULT_START_HOUR
) -> Tuple[datetime, datetime]:
    """生成指定日期或日期区间的运营日时间窗 [window_start, window_end)."""
    end = end_date or start_date
    window_start = datetime(start_date.year, start_date.month, start_date.day, start_hour, 0, 0)
    # 结束日为包含式，因而下一个切日点为 end_date + 1 天的 start_hour
    window_end = datetime(end.year, end.month, end.day, start_hour, 0, 0) + timedelta(days=1)
    return window_start, window_end


def get_operational_hour(dt: datetime, start_hour: int = DEFAULT_START_HOUR) -> int:
    """计算当前时间对应的运营小时 (0-23).
    04:00 -> 0, 05:00 -> 1, ..., 23:00 -> 19, 00:00 -> 20, ..., 03:00 -> 23.
    """
    return (dt.hour - start_hour) % 24


def build_operational_conditions(
    date_val: str,
    time_field: str,
    start_hour: int = DEFAULT_START_HOUR,
    table: Optional[str] = None
) -> List[Dict[str, Any]]:
    """为特定日期字符串生成运营日上下界区间条件."""
    parsed_date = date.fromisoformat(date_val)
    win_start, win_end = get_operational_window(parsed_date, start_hour=start_hour)
    
    cond_start: Dict[str, Any] = {
        'col': time_field,
        'op': '>=',
        'value': win_start.strftime('%Y-%m-%d %H:%M:%S')
    }
    cond_end: Dict[str, Any] = {
        'col': time_field,
        'op': '<',
        'value': win_end.strftime('%Y-%m-%d %H:%M:%S')
    }
    if table:
        cond_start['table'] = table
        cond_end['table'] = table
    return [cond_start, cond_end]


def resolve_operational_time_field(catalog: Any, table: str, current_col: str, plan: Dict[str, Any]) -> Tuple[str, bool]:
    """Resolve a true timestamp column when translating a natural date filter to an operational window."""
    attrs = (getattr(catalog, 'entities', {}).get(table) or {}).get('attributes', {})
    cur_type = str((attrs.get(current_col) or {}).get('type') or '').lower()
    if 'timestamp' in cur_type and current_col != 'flight_date':
        return current_col, True

    candidates_by_table = {
        'mv_acdm_dyn_flight_detail': (
            ['real_takeoff_landin_time', 'plan_time']
            if current_col == 'exec_date' or plan.get('metric') not in ('flight_total_count', 'planned_flight_count')
            else ['plan_time', 'real_takeoff_landin_time']
        ),
        'std_flight_core': ['flight_scheduled_datetime', 'plan_time'],
        'ops_ask_views.flight_execution': ['execution_time'],
        'ops_ask_views.flight_hourly_movements': ['flight_date'],
    }
    for cand in candidates_by_table.get(table, []):
        ctype = str((attrs.get(cand) or {}).get('type') or '').lower()
        if cand in attrs and 'timestamp' in ctype and cand != 'flight_date':
            return cand, True
    return current_col, False


def apply_operational_day_to_plan(plan: Dict[str, Any], catalog: Any, day_mode: str = 'AUTO') -> Dict[str, Any]:
    """Translate plan date conditions into an operational-day boundary window when enabled & active."""
    if not isinstance(plan, dict) or plan.get('outcome') or plan.get('intent') in ('turnaround', 'acdm_report'):
        return plan
    op_cfg = getattr(catalog, 'operational_day', None) or {}
    if not op_cfg.get('enabled'):
        return plan
    req_mode = str(day_mode or 'AUTO').strip().upper()
    plan_mode = str(plan.get('day_mode') or '').strip().upper()
    if req_mode in ('CALENDAR', 'NATURAL'):
        return plan
    if req_mode != 'OPERATIONAL' and plan_mode != 'OPERATIONAL':
        return plan

    start_hour = max(0, min(23, int(op_cfg.get('startHour', DEFAULT_START_HOUR))))
    conditions = plan.get('conditions') or []
    subject = plan.get('subject') or ''

    date_conds = [
        c for c in conditions
        if isinstance(c, dict) and c.get('op') in ('>=', '<', 'prefix')
        and isinstance(c.get('value'), str) and len(str(c['value'])) >= 10
        and str(c['value'])[:4].isdigit() and str(c['value'])[4] == '-'
    ]
    if not date_conds:
        return plan

    target_table = date_conds[0].get('table') or subject
    target_col = date_conds[0].get('col')
    same_field = [c for c in date_conds if (c.get('table') or subject) == target_table and c.get('col') == target_col]

    start_d: Optional[date] = None
    end_d: Optional[date] = None
    for c in same_field:
        val_date = date.fromisoformat(str(c['value'])[:10])
        if c['op'] == 'prefix':
            start_d = end_d = val_date
        elif c['op'] == '>=':
            start_d = val_date
        elif c['op'] == '<':
            end_d = val_date - timedelta(days=1)

    if start_d is None:
        return plan
    if end_d is None or end_d < start_d:
        end_d = start_d

    ts_col, is_ts = resolve_operational_time_field(catalog, target_table, target_col, plan)
    if not is_ts:
        return plan

    win_start, win_end = get_operational_window(start_d, end_d, start_hour=start_hour)
    kept = [c for c in conditions if c not in same_field]
    kept.extend([
        {'table': target_table, 'col': ts_col, 'op': '>=', 'value': win_start.strftime('%Y-%m-%dT%H:%M:%S+08:00')},
        {'table': target_table, 'col': ts_col, 'op': '<', 'value': win_end.strftime('%Y-%m-%dT%H:%M:%S+08:00')},
    ])
    plan['conditions'] = kept
    plan['day_mode'] = 'OPERATIONAL'
    plan['operational_window'] = {
        'start': win_start.strftime('%Y-%m-%d %H:%M:%S'),
        'end': win_end.strftime('%Y-%m-%d %H:%M:%S'),
        'startHour': start_hour,
        'field': ts_col,
    }
    return plan
