"""A-CDM 航班时间节点鱼骨图 (Fishbone Milestone Diagram) 元数据生成器.

针对民航机场 A-CDM 协同运行保障，从航班明细行中提取进出港全生命周期里程碑节点：
- 出港(DEP): 开始值机 -> 结束值机 -> 机坪保障(供油/保洁) -> 开始登机 -> 催促登机 -> 登机结束 -> 关客舱门 -> 撤离廊桥 -> 目标推出(TOBT) -> 请求开车推出 -> 撤轮挡(AOBT) -> 跑道起飞(ATOT) -> 目的站落地(ALDT)
- 进港(ARR): 前站起飞(ATOT) -> 预报降落(ELDT) -> 跑道接地(ALDT) -> 跑道脱离/入地 -> 挡轮挡(AIBT) -> 靠接廊桥 -> 开客舱门 -> 首件行李提取
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def safe_str(val: Any) -> str:
    if val is None:
        return ''
    return str(val).strip()


BUSINESS_TZ = timezone(timedelta(hours=8))


def format_node_time(raw: Any, business_date: str = '') -> str:
    """格式化时间为 HH:MM 格式，跨天保留 MM-DD HH:MM."""
    if not raw:
        return ''
    value = parse_dt(raw)
    if value:
        return value.strftime('%m-%d %H:%M' if business_date and value.date().isoformat() != business_date else '%H:%M')
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
    try:
        value = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return value.replace(tzinfo=BUSINESS_TZ) if value.tzinfo is None else value.astimezone(BUSINESS_TZ)
    except ValueError:
        pass
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
        ['ALTERATE_START_SUPPLY_TIME', 'ALTERATE_START_CLEAN_TIME', 'alterate_start_supply_time', 'alterate_start_clean_time'],
        ['REAL_END_SUPPLY_TIME', 'REAL_END_CLEAN_TIME', 'REAL_MEAL_START_TIME', 'real_end_supply_time', 'real_end_clean_time', 'real_meal_start_time', 'real_start_supply_time', 'real_start_clean_time'],
        '加油、保洁、配餐保障'
    ),
    (
        'boarding_start', '开始登机', 'BRD_S', 'passenger', 'top',
        ['ALTERATE_BEGIN_BOARD_TIME'],
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
        'boarding_end', '登机完成', 'BRD_E', 'passenger', 'bottom',
        ['ALTERATE_END_BOARD_TIME'],
        ['REAL_END_BOARD_TIME', 'boarding_end'],
        '登机口关闭'
    ),
    (
        'door_close', '客舱关门', 'CLS', 'control', 'top',
        ['PLAN_CLOSE_DOOR_TIME', 'alterate_close_cabin_time'],
        ['REAL_CLOSE_CABIN_TIME', 'cabin_close', 'real_close_cabin_time'],
        '客舱舱门关闭完毕'
    ),
    (
        'bridge_off', '撤除廊桥', 'BRG_OFF', 'ramp', 'bottom',
        ['PLAN_OFF_BRIDGE_TIME', 'plan_off_bridge_time'],
        ['REAL_OFF_BRIDGE_TIME', 'real_off_bridge_time'],
        '客梯车/登机廊桥脱开'
    ),
    (
        'tobt', '目标推出', 'TOBT', 'control', 'top',
        ['tobt', 'TARGT_BLOCK_OFF_TIME', 'AIRPORT_TARGT_BLOCK_OFF_TIME', 'COUNT_BLOCK_OFF_TIME', 'count_block_off_time'],
        [],
        'A-CDM 目标撤轮挡时刻'
    ),
    (
        'push_request', '申请推出', 'REQ', 'control', 'bottom',
        [],
        ['REQUEST_LAUNCH_TIME', 'request_launch_time'],
        '机组向塔台申请推出滑行'
    ),
    (
        'aobt', '撤轮挡', 'AOBT', 'runway', 'top',
        ['COUNT_BLOCK_OFF_TIME', 'scheduled_off_block'],
        ['REAL_BLOCK_OFF_TIME', 'actual_off_block'],
        '撤去飞机轮挡时刻；推出另列节点'
    ),
    (
        'atot', '跑道起飞', 'ATOT', 'runway', 'bottom',
        ['SCHEME_TAKEOFF_TIME', 'ALTERATE_TAKEOFF_TIME', 'scheduled_departure'],
        ['actual_departure', 'REAL_TAKEOFF_TIME'],
        '跑道滑跑离地起飞'
    ),
    (
        'aldt', '目的站降落', 'ALDT', 'runway', 'top',
        ['SCHEME_LANDIN_TIME', 'scheduled_arrival'],
        ['actual_arrival', 'REAL_LANDIN_TIME'],
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
        ['ENTRY_GROUND_TIME', 'real_taxi_in_time'],
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
        ['ALTERATE_BY_BRIDGE_TIME', 'alterate_by_bridge_time'],
        ['REAL_BY_BRIDGE_TIME', 'real_by_bridge_time'],
        '旅客廊桥靠接完毕'
    ),
    (
        'door_open', '客舱开门', 'OPN', 'passenger', 'top',
        ['ALTERATE_OPEN_CABIN_TIME', 'alterate_open_cabin_time'],
        ['REAL_OPEN_CABIN_TIME', 'real_open_cabin_time'],
        '客舱舱门开启下客'
    ),
    (
        'baggage', '行李提取', 'BAG', 'passenger', 'bottom',
        [],
        ['FIRST_CONTACT_TIME', 'first_baggage', 'last_baggage'],
        '行李运达旅客提取转盘'
    ),
]


EXTRA_MILESTONES = [
    ('inbound_landing', '落地', 'IN_ALDT', 'runway', 'bottom',
     ['inbound_scheduled_arrival'], ['inbound_actual_arrival'], '前序进港航班在本场落地；未关联时保留空位'),
    ('cargo_open', '货舱开门', 'CARGO_OPN', 'ramp', 'bottom',
     [], ['REAL_OPEN_CARGO_TIME', 'cargo_open', 'real_open_cargo_time'], '货舱开启记录'),
    ('cargo_close', '货舱关门', 'CARGO_CLS', 'ramp', 'top',
     [], ['REAL_CLOSE_CARGO_TIME', 'cargo_close', 'real_close_cargo_time'], '货舱关闭记录'),
    ('pushback', '推出', 'PUSH', 'control', 'bottom',
     [], ['REAL_LAUNCH_TIME', 'pushback', 'real_launch_time'], '实际推出记录'),
    ('engine_start', '开车', 'ASAT', 'control', 'top',
     [], ['REAL_DRIVE_TIME', 'engine_start', 'real_drive_time'], '实际开车记录'),
    ('tsat', '目标开车批准', 'TSAT', 'control', 'top',
     ['tsat', 'TARGT_DRIVE_TIME', 'targt_drive_time'], [], '目标时刻不表示实际开车或完成'),
]

NODE_ORDER = ['origin_dep', 'eldt', 'inbound_landing', 'runway_exit', 'aibt', 'bridge_on',
              'door_open', 'cargo_open', 'checkin_start', 'checkin_end', 'ground_service',
              'boarding_start', 'urge_boarding', 'boarding_end', 'door_close', 'cargo_close',
              'bridge_off', 'tobt', 'tsat', 'push_request', 'aobt', 'pushback', 'engine_start',
              'atot', 'aldt', 'baggage']

ESTIMATED_FIELDS = {
    'origin_dep': ['prev_estimated_departure'],
    'inbound_landing': ['inbound_estimated_arrival'],
    'eldt': ['estimated_arrival', 'ALTERATE_LANDIN_TIME'],
    'aibt': ['estimated_on_block', 'ALTERATE_BLOCK_ON_TIME'],
    'door_open': ['ALTERATE_OPEN_CABIN_TIME', 'alterate_open_cabin_time'],
    'bridge_on': ['ALTERATE_BY_BRIDGE_TIME', 'alterate_by_bridge_time'],
    'door_close': ['ALTERATE_CLOSE_CABIN_TIME', 'alterate_close_cabin_time'],
    'bridge_off': ['PLAN_OFF_BRIDGE_TIME', 'plan_off_bridge_time'],
    'cargo_open': ['ALTERATE_OPEN_CARGO_TIME'],
    'cargo_close': ['ALTERATE_CLOSE_CARGO_TIME'],
    'boarding_start': ['ALTERATE_BEGIN_BOARD_TIME'],
    'boarding_end': ['ALTERATE_END_BOARD_TIME'],
    'aobt': ['estimated_off_block', 'ALTERATE_BLOCK_OFF_TIME'],
    'atot': ['estimated_departure', 'ALTERATE_TAKEOFF_TIME', 'COUNT_TAKE_OFF_TIME'],
    'aldt': ['estimated_arrival', 'ALTERATE_LANDIN_TIME'],
}


def flight_node_specs(direction):
    specs = {item[0]: item for item in [*DEP_MILESTONES, *ARR_MILESTONES, *EXTRA_MILESTONES]}
    if direction == 'DEP':
        specs['aldt'] = next(item for item in DEP_MILESTONES if item[0] == 'aldt')
        base = specs['origin_dep']
        specs['origin_dep'] = (*base[:5], ['prev_scheduled_departure', 'START_SCHEME_TAKEOFF_TIME'],
                               ['prev_actual_departure'], '前序进港航班的前站起飞；不使用本航班起飞时间代替')
        specs.pop('eldt')
    else:
        specs.pop('inbound_landing')
        base = specs['atot']
        specs['atot'] = (*base[:5], ['next_scheduled_departure'], ['next_actual_departure'],
                         '后续出港航班起飞；未关联时保留空位')
    # ALTERATE fields describe estimates, never evidence of an actual event.
    for key in ('boarding_start', 'boarding_end', 'door_open', 'aibt'):
        item = specs[key]
        specs[key] = (*item[:5], [field for field in item[5] if not field.startswith('ALTERATE_')], *item[6:])
    if direction == 'DEP':
        specs.pop('baggage', None)
    order = NODE_ORDER if direction == 'DEP' else ['origin_dep', 'eldt', 'aldt', *[key for key in NODE_ORDER if key not in ('origin_dep', 'eldt', 'aldt')]]
    return [specs[key] for key in order if key in specs]


def _sort_flight_nodes_chronologically(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order timed nodes strictly by actual/estimated/plan timestamp while keeping untimed nodes near their predecessor."""
    if len(nodes) <= 1:
        return nodes
    indexed = []
    for idx, node in enumerate(nodes):
        raw_ts = node.get('actual_at') or node.get('estimated_at') or node.get('plan_at')
        dt = parse_dt(raw_ts)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=BUSINESS_TZ)
        indexed.append((idx, node, dt))

    last_timed_idx = None
    anchored_after: Dict[Optional[int], List[Dict[str, Any]]] = {}
    timed: List[tuple] = []
    for idx, node, dt in indexed:
        if dt is not None:
            timed.append((dt, idx, node))
            last_timed_idx = idx
        else:
            anchored_after.setdefault(last_timed_idx, []).append(node)

    if not timed:
        return nodes
    timed.sort(key=lambda item: (item[0], item[1]))
    ordered = list(anchored_after.get(None, []))
    for _, idx, node in timed:
        ordered.append(node)
        ordered.extend(anchored_after.get(idx, []))
    return ordered


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
    has_flight_id = bool(keys & (flight_id_keys - {'flight_id', 'FLIGHT_ID'}))
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

    specs = flight_node_specs(direction)
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
        if key == 'eldt':
            act_val = None

        estimate_keys = ESTIMATED_FIELDS.get(key, [])
        if direction == 'ARR' and key == 'origin_dep':
            estimate_keys = ['estimated_departure', 'ALTERATE_TAKEOFF_TIME']
        elif direction == 'ARR' and key == 'atot':
            estimate_keys = ['next_estimated_departure']
        estimated_val = next((row[field] for field in estimate_keys if row.get(field)), None)

        plan_dt = parse_dt(plan_val)
        act_dt = parse_dt(act_val)
        estimated_dt = parse_dt(estimated_val)

        status = 'pending'
        status_label = '未记录'
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
            status_label = '有实际记录'
        elif plan_dt:
            status = 'planned'
            status_label = '有计划时刻'
        elif estimated_dt:
            status = 'planned'
            status_label = '有预计时刻'
        if code in ('TOBT', 'TSAT'):
            status, status_label = ('planned', '目标时刻') if plan_dt else ('pending', '未记录')

        nodes.append({
            'key': key,
            'name': name,
            'code': code,
            'category': category,
            'position': position,
            'plan_time': format_node_time(plan_dt, flight_date),
            'estimated_time': format_node_time(estimated_dt, flight_date),
            'actual_time': format_node_time(act_dt, flight_date),
            'plan_at': plan_dt.isoformat() if plan_dt else '',
            'estimated_at': estimated_dt.isoformat() if estimated_dt else '',
            'actual_at': act_dt.isoformat() if act_dt else '',
            'time_display': format_node_time(act_dt or estimated_dt or plan_dt, flight_date),
            'status': status,
            'status_label': status_label,
            'delay_minutes': delay_minutes,
            'note': note
        })

    if direction == 'DEP':
        nodes = [
            n for n in nodes
            if n['key'] != 'runway_exit' or n['actual_at'] or n['estimated_at'] or n['plan_at']
        ]
    nodes = _sort_flight_nodes_chronologically(nodes)

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
