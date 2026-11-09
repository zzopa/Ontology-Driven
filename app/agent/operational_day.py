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
