"""A-CDM 航班时间节点鱼骨图 (Fishbone Milestone Diagram) 元数据生成器.

针对民航机场 A-CDM 协同运行保障，从航班明细行中提取进出港全生命周期里程碑节点：
- 出港(DEP): 开始值机 -> 结束值机 -> 机坪保障(供油/保洁) -> 开始登机 -> 催促登机 -> 登机结束 -> 关客舱门 -> 撤离廊桥 -> 目标推出(TOBT) -> 请求开车推出 -> 撤轮挡(AOBT) -> 跑道起飞(ATOT) -> 目的站落地(ALDT)
- 进港(ARR): 前站起飞(ATOT) -> 预报降落(ELDT) -> 跑道接地(ALDT) -> 跑道脱离/入地 -> 挡轮挡(AIBT) -> 靠接廊桥 -> 开客舱门 -> 首件行李提取
"""
from datetime import datetime
from typing import Any, Dict, List, Optional


def safe_str(val: Any) -> str:
    if val is None:
        return ''
    return str(val).strip()


def format_node_time(raw: Any) -> str:
    """格式化时间为 HH:MM 格式，跨天保留 MM-DD HH:MM."""
    if not raw:
        return ''
    s = str(raw).strip()
    try:
        # Handles '2026-10-03 21:45:00' or ISO format
        if ' ' in s:
            parts = s.split(' ')
            t_part = parts[1]
            return ':'.join(t_part.split(':')[:2])
        elif 'T' in s:
            parts = s.split('T')
            t_part = parts[1]
            return ':'.join(t_part.split(':')[:2])
    except Exception:
        pass
    return s[:16]


def parse_dt(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s.split('.')[0], fmt)
        except Exception:
            continue
    return None


# 节点定义：(key, name, code, category, position, [plan_fields], [actual_fields], note)
DEP_MILESTONES = [
    (
        'checkin_start', '旅客值机', 'CHK_S', 'passenger', 'top',
        ['CHECKIN_START_TIME', 'checkin_sched_begin'],
        ['CHECKIN_START_TIME', 'checkin_act_begin'],
        '开放值机柜台办理'
    ),
    (
        'checkin_end', '值机截止', 'CHK_E', 'passenger', 'bottom',
        ['CHECKIN_END_TIME', 'checkin_sched_end'],
        ['CHECKIN_END_TIME', 'checkin_act_end'],
        '航班截止办理值机'
    ),
    (
        'ground_service', '机坪保障', 'RAMP', 'ramp', 'bottom',
        ['REAL_START_SUPPLY_TIME', 'REAL_START_CLEAN_TIME'],
        ['REAL_END_SUPPLY_TIME', 'REAL_END_CLEAN_TIME', 'REAL_MEAL_START_TIME'],
        '加油、保洁、配餐保障'
    ),
    (
        'boarding_start', '旅客登机', 'BRD_S', 'passenger', 'top',
        ['ALTERATE_BEGIN_BOARD_TIME', 'boarding_start'],
        ['REAL_BEGIN_BOARD_TIME', 'boarding_start'],
        '登机口开启并开始登机'
    ),
    (
        'urge_boarding', '催促登机', 'URG', 'passenger', 'top',
        [],
        ['URGE_BOARD_TIME', 'last_call'],
        '广播催促末批旅客登机'
    ),
    (
        'boarding_end', '登机结束', 'BRD_E', 'passenger', 'bottom',
        ['ALTERATE_END_BOARD_TIME', 'boarding_end'],
        ['REAL_END_BOARD_TIME', 'boarding_end'],
        '登机口关闭'
    ),
    (
        'door_close', '客舱关门', 'CLS', 'control', 'top',
        ['PLAN_CLOSE_DOOR_TIME'],
        ['REAL_CLOSE_CABIN_TIME', 'ALTERATE_CLOSE_CABIN_TIME'],
        '客舱舱门关闭完毕'
    ),
    (
        'bridge_off', '撤除廊桥', 'BRG_OFF', 'ramp', 'bottom',
        ['PLAN_OFF_BRIDGE_TIME'],
        ['REAL_OFF_BRIDGE_TIME'],
        '客梯车/登机廊桥脱开'
    ),
    (
        'tobt', '目标推出', 'TOBT', 'control', 'top',
        ['COUNT_BLOCK_OFF_TIME', 'TARGT_BLOCK_OFF_TIME', 'tobt'],
        ['AIRPORT_TARGT_BLOCK_OFF_TIME', 'tsat'],
        'A-CDM 目标撤轮挡时刻'
    ),
    (
        'push_request', '申请推出', 'REQ', 'control', 'bottom',
        [],
        ['REQUEST_LAUNCH_TIME', 'REAL_DRIVE_TIME', 'REAL_LAUNCH_TIME'],
        '机组向塔台申请推出滑行'
    ),
    (
        'aobt', '撤轮挡推出', 'AOBT', 'runway', 'top',
        ['COUNT_BLOCK_OFF_TIME', 'scheduled_off_block'],
        ['REAL_BLOCK_OFF_TIME', 'actual_off_block'],
        '撤去飞机轮挡开始推出'
    ),
    (
        'atot', '跑道起飞', 'ATOT', 'runway', 'bottom',
        ['SCHEME_TAKEOFF_TIME', 'ALTERATE_TAKEOFF_TIME', 'scheduled_departure'],
        ['REAL_TAKEOFF_TIME', 'actual_departure', 'COUNT_TAKE_OFF_TIME'],
        '跑道滑跑离地起飞'
    ),
    (
        'aldt', '目的站降落', 'ALDT', 'runway', 'top',
        ['SCHEME_LANDIN_TIME', 'scheduled_arrival'],
        ['REAL_LANDIN_TIME', 'actual_arrival', 'ALTERATE_LANDIN_TIME'],
        '飞抵目的地机场降落'
    ),
]

ARR_MILESTONES = [
    (
        'origin_dep', '前站起飞', 'DEP', 'runway', 'bottom',
        ['SCHEME_TAKEOFF_TIME', 'scheduled_departure'],
        ['REAL_TAKEOFF_TIME', 'actual_departure'],
        '始发站离地起飞'
    ),
    (
        'eldt', '预报降落', 'ELDT', 'control', 'top',
        ['SCHEME_LANDIN_TIME', 'scheduled_arrival'],
        ['ALTERATE_LANDIN_TIME', 'estimated_arrival'],
        '本场终端区预报落地'
    ),
    (
        'aldt', '跑道接地', 'ALDT', 'runway', 'bottom',
        ['SCHEME_LANDIN_TIME', 'scheduled_arrival'],
        ['REAL_LANDIN_TIME', 'actual_arrival'],
        '跑道主轮接地'
    ),
    (
        'runway_exit', '脱离跑道', 'EXIT', 'runway', 'bottom',
        [],
        ['ENTRY_GROUND_TIME'],
        '脱离跑道进入滑行道'
    ),
    (
        'aibt', '挡轮挡靠位', 'AIBT', 'ramp', 'top',
        ['ALTERATE_BLOCK_ON_TIME', 'scheduled_on_block'],
        ['REAL_BLOCK_ON_TIME', 'actual_on_block'],
        '进入机位挡好轮挡'
    ),
    (
        'bridge_on', '靠接廊桥', 'BRG_ON', 'ramp', 'bottom',
        [],
        ['REAL_BY_BRIDGE_TIME'],
        '旅客廊桥靠接完毕'
    ),
    (
        'door_open', '客舱开门', 'OPN', 'passenger', 'top',
        [],
        ['REAL_OPEN_CABIN_TIME'],
        '客舱舱门开启下客'
    ),
    (
        'baggage', '行李提取', 'BAG', 'passenger', 'bottom',
        [],
        ['FIRST_CONTACT_TIME', 'first_baggage', 'last_baggage'],
        '行李运达旅客提取转盘'
    ),
]


def is_flight_row(row: Dict[str, Any]) -> bool:
    """判断是否为包含航班时间节点的业务行."""
    if not isinstance(row, dict):
        return False
    keys = set(row.keys())
    flight_id_keys = {'FLIGHT_NO', 'flight_no', 'FLIGHT_ID', 'flight_id', 'FFID', 'flight_identity'}
    time_keys = {
        'SCHEME_TAKEOFF_TIME', 'REAL_TAKEOFF_TIME', 'REAL_LANDIN_TIME',
        'REAL_BLOCK_OFF_TIME', 'REAL_BLOCK_ON_TIME', 'CHECKIN_START_TIME',
        'REAL_BEGIN_BOARD_TIME', 'PLAN_CLOSE_DOOR_TIME', 'scheduled_departure',
        'actual_departure', 'scheduled_arrival', 'actual_arrival', 'actual_off_block'
    }
    has_flight_id = bool(keys & flight_id_keys)
    has_time = bool(keys & time_keys)
    return has_flight_id or has_time


def resolve_direction(row: Dict[str, Any]) -> str:
    """提取进出港方向 (DEP 或 ARR)."""
    val = (
        row.get('NORMAL_FLIGHT_STAT_CD') or
        row.get('IS_OFF_IN') or
        row.get('direction') or
        row.get('DIRECTION') or
        ''
    )
    val_str = str(val).upper().strip()
    if val_str in ('DEP', 'D', '出港', '离港', 'DEPARTURE'):
        return 'DEP'
    if val_str in ('ARR', 'A', '进港', '到港', 'ARRIVAL'):
        return 'ARR'
    # 启发式判断：是否有起飞 vs 降落时间
    if row.get('REAL_TAKEOFF_TIME') or row.get('SCHEME_TAKEOFF_TIME') or row.get('actual_departure'):
        return 'DEP'
    return 'ARR'


def build_fishbone_meta(
    row: Dict[str, Any],
    plan: Optional[Dict[str, Any]] = None,
    total_flights: int = 1
) -> Dict[str, Any]:
    """根据航班数据行生成鱼骨图元数据."""
    direction = resolve_direction(row)
    flight_no = (
        safe_str(row.get('FLIGHT_KEY')) or
        (f"{safe_str(row.get('AIRLINE_TWO_CHAR_CD'))}{safe_str(row.get('FLIGHT_NO'))}" if row.get('AIRLINE_TWO_CHAR_CD') and row.get('FLIGHT_NO') and not str(row.get('FLIGHT_NO', '')).startswith(str(row.get('AIRLINE_TWO_CHAR_CD', ''))) else '') or
        safe_str(row.get('flight_no')) or
        safe_str(row.get('FLIGHT_NO')) or
        (plan.get('code') if plan else '') or
        '航班'
    )
    flight_date = (
        safe_str(row.get('EXEC_DATE')) or
        safe_str(row.get('flight_date')) or
        ''
    )[:10]

    specs = DEP_MILESTONES if direction == 'DEP' else ARR_MILESTONES
    nodes: List[Dict[str, Any]] = []

    for key, name, code, category, position, plan_keys, act_keys, note in specs:
        plan_val = None
        for pk in plan_keys:
            if row.get(pk):
                plan_val = row[pk]
                break

        act_val = None
        for ak in act_keys:
            if row.get(ak):
                act_val = row[ak]
                break

        # 如果计划与实际均为空，跳过该无数据节点以保持鱼骨清晰
        if not plan_val and not act_val:
            continue

        plan_dt = parse_dt(plan_val)
        act_dt = parse_dt(act_val)

        status = 'completed'
        status_label = '已完成'
        delay_minutes = 0

        if act_dt and plan_dt:
            diff = (act_dt - plan_dt).total_seconds() / 60.0
            delay_minutes = int(diff)
            if delay_minutes > 15:
                status = 'delayed'
                status_label = f'延误 {delay_minutes} 分钟'
            elif delay_minutes < -5:
                status = 'completed'
                status_label = f'提前 {abs(delay_minutes)} 分钟'
            else:
                status = 'completed'
                status_label = '准点'
        elif act_dt:
            status = 'completed'
            status_label = '已完成'
        elif plan_dt:
            status = 'planned'
            status_label = '计划中'

        nodes.append({
            'key': key,
            'name': name,
            'code': code,
            'category': category,
            'position': position,
            'plan_time': format_node_time(plan_val),
            'actual_time': format_node_time(act_val),
            'time_display': format_node_time(act_val or plan_val),
            'status': status,
            'status_label': status_label,
            'delay_minutes': delay_minutes,
            'note': note
        })

    # 提取航班基础信息
    craft_type = safe_str(row.get('CRAFT_TYPE_ICAO_CD') or row.get('aircraft_type'))
    craft_no = safe_str(row.get('CRAFT_NO') or row.get('aircraft_reg'))
    stand = safe_str(row.get('CRAFT_SITE_CD') or row.get('stand_code'))
    gate = safe_str(row.get('GATE_CD') or row.get('gate_code'))
    origin = safe_str(row.get('TAKEOFF_AIRPORT_IATA') or row.get('origin_city') or row.get('origin_airport'))
    dest = safe_str(row.get('ARRIVAL_AIRPORT_IATA') or row.get('dest_city') or row.get('dest_airport'))
    status_text = safe_str(row.get('NORMAL_FLIGHT_STAT_CD') or row.get('flight_status') or ('出港' if direction == 'DEP' else '进港'))

    summary_parts = []
    if flight_date:
        summary_parts.append(f"{flight_date}")
    if origin and dest:
        summary_parts.append(f"{origin} -> {dest}")
    if stand:
        summary_parts.append(f"{stand} 号机位")
    if gate:
        summary_parts.append(f"{gate} 登机口")
    if craft_type:
        summary_parts.append(f"机型 {craft_type}")

    return {
        'kind': 'fishbone',
        'title': f'{flight_no} 航班保障时间节点鱼骨图',
        'flight_no': flight_no,
        'direction': direction,
        'direction_label': '出港航班' if direction == 'DEP' else '进港航班',
        'flight_date': flight_date,
        'aircraft_type': craft_type,
        'aircraft_reg': craft_no,
        'stand': stand,
        'gate': gate,
        'origin': origin,
        'destination': dest,
        'status': status_text,
        'summary': ' · '.join(summary_parts) if summary_parts else 'A-CDM 运行保障节点全流程追踪',
        'total_flights': total_flights,
        'nodes': nodes
    }
