"""Deterministic operations facts and dispatch constraints."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

TZ = timezone(timedelta(hours=8))
# 当前已确认的历史数据只到此日；实时改造后再更新数据范围。
REPLAY_DATA_DATE = '2026-11-10'
KINDS = {'cleaning': '保洁', 'fueling': '加油', 'catering': '配餐', 'loading': '装卸', 'boarding': '登机'}
STATES = {'completed': '完成', 'in_progress': '进行中', 'planned': '待开始', 'missing': '作业未记录', 'risk': '待核查'}


class Conflict(ValueError):
    pass


def now():
    return datetime.now(TZ)


def dt(value):
    if value is None or value == '':
        return None
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)
    except (TypeError, ValueError):
        return None


def iso(value):
    value = dt(value)
    return value.isoformat() if value else None


def replay_time(reference):
    value, limit = reference - timedelta(days=1), dt(REPLAY_DATA_DATE)
    return value if value.date() <= limit.date() else value.replace(year=limit.year, month=limit.month, day=limit.day)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def interval(start, end):
    start, end = dt(start), dt(end)
    if not start or not end or end <= start or end - start > timedelta(days=2):
        raise ValueError('请输入有效的起止时间，区间不得超过48小时')
    return start, end


def overlaps(a, b, c, d):
    return dt(a) < dt(d) and dt(c) < dt(b)


def operation(kind, process, observed, replay=False):
    ext = process.get('ext_json') or {}
    invalid_extension = False
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except ValueError:
            ext = {}
            invalid_extension = True
    if not isinstance(ext, dict):
        ext, invalid_extension = {}, True
    fields = {
        'cleaning': ('real_start_clean_time', 'real_end_clean_time', 'REAL_START_CLEAN_TIME', 'REAL_END_CLEAN_TIME', 'ALTERATE_START_CLEAN_TIME', 'ALTERATE_END_CLEAN_TIME'),
        'fueling': ('real_start_supply_time', 'real_end_supply_time', 'REAL_START_SUPPLY_TIME', 'REAL_END_SUPPLY_TIME', 'ALTERATE_START_SUPPLY_TIME', 'ALTERATE_END_SUPPLY_TIME'),
        'catering': ('real_meal_start_time', 'real_meal_end_time', 'REAL_MEAL_START_TIME', 'REAL_MEAL_END_TIME', 'ALTERATE_MEAL_START_TIME', 'ALTERATE_MEAL_END_TIME'),
        'loading': ('load_start_time', 'load_end_time', 'LOAD_START_TIME', 'LOAD_END_TIME', 'PLAN_LOAD_START_TIME', 'PLAN_LOAD_END_TIME'),
        'boarding': ('boarding_start', 'boarding_end', 'REAL_BEGIN_BOARD_TIME', 'REAL_END_BOARD_TIME', 'ALTERATE_BEGIN_BOARD_TIME', 'ALTERATE_END_BOARD_TIME'),
    }[kind]
    a, b, ea, eb, pa, pb = fields
    start, end = dt(process.get(a) or ext.get(ea)), dt(process.get(b) or ext.get(eb))
    invalid = bool(start and end and end < start)
    censored = bool(replay and ((start and start > observed) or (end and end > observed)))
    if replay:
        start = start if not start or start <= observed else None
        end = end if not end or end <= observed else None
    else:
        invalid = invalid or bool((start and start > observed + timedelta(minutes=1)) or (end and end > observed + timedelta(minutes=1)))
    state = 'risk' if invalid else 'completed' if end else 'in_progress' if start else 'risk' if invalid_extension else 'planned' if ext.get(pa) or ext.get(pb) else 'missing'
    return {'kind': kind, 'name': KINDS[kind], 'actual_start': iso(start), 'actual_end': iso(end),
            'plan_start': iso(ext.get(pa)), 'plan_end': iso(ext.get(pb)), 'status': state,
            'status_label': '时刻待核查' if invalid else '此时无实绩' if state == 'missing' and censored else STATES[state], 'source': '历史实绩按预演时刻截断' if replay else '源系统实绩',
            'invalid_time': invalid, 'invalid_extension': invalid_extension, 'replay_censored': censored}


def make_alert(key, title, terminal, flight_ids, kind='', severity='medium', detail='', resource_id=None):
    return {'id': fingerprint(key)[:28], 'title': title, 'terminal': terminal, 'flight_ids': sorted(set(map(str, flight_ids))),
            'kind': kind, 'severity': severity, 'detail': detail, 'resource_id': resource_id, 'status': 'open'}


def evaluate_alerts(flights, tasks, policies, terminal, observed):
    alerts = []
    for flight in flights:
        if flight.get('cancelled'):
            continue
        for op in flight['operations']:
            policy = policies.get(op['kind'], {})
            if op.get('invalid_extension'):
                alerts.append(make_alert(['extension', flight['id']], '作业扩展资料待核查', terminal, [flight['id']], detail=f"{flight['flight_no']} 的扩展资料格式异常，未补造缺失时刻"))
                continue
            if op.get('invalid_time'):
                alerts.append(make_alert(['invalid', flight['id'], op['kind']], '实绩时刻待核查', terminal, [flight['id']], op['kind'], detail=f"{flight['flight_no']} {op['name']}存在未来或倒序时刻，未计为完成"))
                continue
            if op.get('manual_state') == 'complete':
                continue
            if op.get('manual_state') == 'reported_complete':
                alerts.append(make_alert(['manual_review', flight['id'], op['kind']], '人工报工待核验', terminal, [flight['id']], op['kind'], detail=f"{flight['flight_no']} {op['name']}已报结束，请对照源记录并核验"))
                continue
            if not policy.get('confirmed') or op.get('actual_end'):
                continue
            grace = timedelta(minutes=policy.get('grace_minutes', 5))
            start, end = dt(op.get('plan_start')), dt(op.get('plan_end'))
            if end and observed > end + grace:
                alerts.append(make_alert(['record', flight['id'], op['kind'], iso(start), iso(end)], '结束记录待核查', terminal, [flight['id']], op['kind'], detail=f"{flight['flight_no']} {op['name']}计划结束 {end:%m-%d %H:%M}，尚未收到结束记录；未记录不等于未发生"))
            elif start and not (op.get('actual_start') or op.get('manual_start')) and observed > start + grace:
                alerts.append(make_alert(['record', flight['id'], op['kind'], iso(start), iso(end)], '开始记录待核查', terminal, [flight['id']], op['kind'], detail=f"{flight['flight_no']} {op['name']}计划开始 {start:%m-%d %H:%M}，尚未收到开始记录"))
    active = [t for t in tasks if t['status'] not in ('cancelled', 'complete', 'reported_complete')]
    for resource_id in {t['resource_id'] for t in active}:
        groups, group, group_end = [], [], None
        for task in sorted([t for t in active if t['resource_id'] == resource_id], key=lambda t: dt(t['start_at'])):
            if group and dt(task['start_at']) >= group_end:
                groups.append(group)
                group = []
            group.append(task)
            group_end = max(dt(t['end_at']) for t in group)
        if group:
            groups.append(group)
        for group in groups:
            if len(group) > 1:
                alerts.append(make_alert(['overlap', resource_id, *sorted(t['id'] for t in group)], '班组任务重叠', terminal,
                                         [t['flight_id'] for t in group], group[0]['kind'], 'high',
                                         f"同一班组在 {dt(group[0]['start_at']):%m-%d %H:%M} 起的关联窗口存在{len(group)}项交叠任务；请检查全部关联航班", resource_id))
    return sorted({a['id']: a for a in alerts}.values(), key=lambda a: (a['severity'] != 'high', a['id']))


def transfer_minutes(transfers, origin, destination):
    if not origin or not destination:
        return None
    if origin == destination:
        return 0
    return next((t['minutes'] for t in transfers if t['from_stand'] == origin and t['to_stand'] == destination), None)


def check_candidate(resource, target, tasks, rosters, transfers, start, end, observed, ttl=10):
    """Check both adjacent tasks, including those outside the visible time window."""
    errors, impacts = [], []
    # shortcut: 未建模的航班作业依赖由业务确认窗口约束，具备经审批节点规则后扩展为依赖图。
    deadline = dt(target.get('latest_end_at'))
    if not deadline:
        errors.append('本航班最晚保障结束窗口尚未业务确认')
    elif end > deadline or start < dt(target['start_at']):
        errors.append('拟作业时间超出本航班已确认的可调配窗口')
    if not resource.get('enabled') or resource['kind'] != target['kind']:
        errors.append('班组未启用或作业能力不匹配')
    if not resource.get('qualifications_confirmed') or not dt(resource.get('qualified_until')) or dt(resource['qualified_until']) < end:
        errors.append('资质未确认或有效期不足')
    if not resource.get('equipment_confirmed'):
        errors.append('必要设备未确认')
    confirmed = dt(resource.get('confirmed_at'))
    if not confirmed or confirmed > observed + timedelta(minutes=1) or confirmed < observed - timedelta(minutes=ttl):
        errors.append('班组位置与状态超过有效期，请重新确认')
    if not any(r['resource_id'] == resource['id'] and dt(r['start_at']) <= start and dt(r['end_at']) >= end for r in rosters):
        errors.append('作业窗口不在已确认排班内')
    booked = sorted([t for t in tasks if t['id'] != target['id'] and t['resource_id'] == resource['id'] and t['status'] not in ('cancelled', 'complete', 'reported_complete')], key=lambda t: t['start_at'])
    if any(t.get('work_order_id') and dt(t['end_at']) <= observed for t in booked):
        errors.append('班组存在已过期但未处理的工单，请先由调度协调')
    if any(t['status'] == 'active' and not t.get('actual_end') and dt(t['end_at']) <= observed for t in booked):
        errors.append('班组存在已超过计划结束时间的进行中任务，请先核实结束状态')
    if any(overlaps(start, end, t['start_at'], t['end_at']) for t in booked):
        errors.append('与现有任务时间交叠')
    previous = [t for t in booked if dt(t['end_at']) <= start]
    following = [t for t in booked if dt(t['start_at']) >= end]
    previous = max(previous, key=lambda t: dt(t['end_at'])) if previous else None
    following = min(following, key=lambda t: dt(t['start_at'])) if following else None
    if resource.get('availability', 'unknown') == 'unknown':
        errors.append('班组忙闲状态尚未明确确认')
    elif resource['availability'] == 'busy' and not (previous and previous['status'] == 'active' and dt(previous['end_at']) > observed):
        errors.append('班组报告忙碌，但缺少可确认结束时间的前序作业')
    origin = previous['stand'] if previous and dt(previous['end_at']) > observed else resource.get('position')
    leave = max(observed, dt(previous['end_at'])) if previous else observed
    before = transfer_minutes(transfers, origin, target['stand'])
    if before is None:
        errors.append('缺少到达目标机位的实测转场时间')
    elif leave + timedelta(minutes=before) > start:
        errors.append('无法按计划转场到达')
    if before is not None and not any(r['resource_id'] == resource['id'] and dt(r['start_at']) <= leave and dt(r['end_at']) >= end for r in rosters):
        errors.append('到场转场时间不在已确认排班内')
    if previous:
        impacts.append({'flight_id': previous['flight_id'], 'flight_no': previous['flight_no'], 'relation': '前序任务', 'detail': '保持原安排'})
    after, slack = None, None
    if following:
        after = transfer_minutes(transfers, target['stand'], following['stand'])
        if after is None:
            errors.append('缺少前往后续任务的实测转场时间')
        else:
            slack = (dt(following['start_at']) - end).total_seconds() / 60 - after
            if slack < 0:
                errors.append('无法衔接后续任务')
            travel_start = max(end, dt(following['start_at']) - timedelta(minutes=after))
            if not any(r['resource_id'] == resource['id'] and dt(r['start_at']) <= travel_start and dt(r['end_at']) >= dt(following['end_at']) for r in rosters):
                errors.append('后续任务及转场缺少对应排班')
        impacts.append({'flight_id': following['flight_id'], 'flight_no': following['flight_no'], 'relation': '后续任务',
                        'detail': '缺转场依据' if slack is None else f'转场{after}分钟，衔接余量{slack:g}分钟'})
    return {'eligible': not errors, 'errors': errors, 'impacts': impacts, 'transfer_before': before,
            'transfer_after': after, 'slack_minutes': slack}
