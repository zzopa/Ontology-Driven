"""Read-only operations showcase backed by one explicit synthetic JSON fixture."""
from datetime import timedelta
import json
from math import isfinite
from pathlib import Path

from fastapi import HTTPException

from .domain import dt, fingerprint, iso
from .service import OpsService

FIXTURE_PATH = Path(__file__).resolve().parents[2] / 'data' / 'ops-test-data.json'


def load_fixture():
    value = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
    if value['meta']['data_mode'] != 'static-test' or value['schema_version'] != 1:
        raise ValueError('静态测试数据格式或版本无效')
    for flight in value['flights']:
        state = flight.get('aircraft_state')
        if state and state.get('code') not in {'parked', 'cleaning', 'fueling', 'catering', 'loading', 'boarding', 'waiting_hangar', 'taxiing', 'taking_off'}:
            raise ValueError('静态测试飞机状态无效')
        motion = flight.get('motion')
        if motion is not None:
            duration, path = motion.get('duration_ms'), motion.get('path')
            numeric = lambda n: isinstance(n, (int, float)) and not isinstance(n, bool) and isfinite(n)
            if (not numeric(duration) or duration <= 0 or not isinstance(path, list) or len(path) < 2 or
                    any(not isinstance(p, list) or len(p) != 3 or not all(numeric(n) for n in p) for p in path) or
                    any(a == b for a, b in zip(path, path[1:]))):
                raise ValueError('静态测试飞机轨迹无效')
    return value


def in_terminal(row, terminal):
    return terminal == 'ALL' or row['terminal'] == terminal


def ledger(data, terminal):
    return {**{key: [row for row in data[key] if in_terminal(row, terminal)]
               for key in ('resources', 'rosters', 'tasks', 'transfers')},
            'policies': data['policies'], 'settings': data['settings'], 'grants': []}


def snapshot(data, terminal, minutes=90):
    view = ledger(data, terminal)
    observed = dt(data['meta']['captured_at'])
    end = observed + timedelta(minutes=minutes)
    flights = [f for f in data['flights'] if in_terminal(f, terminal) and
               (observed - timedelta(minutes=30) <= dt(f['target_at']) <= end or
                any(o['status'] in ('in_progress', 'risk') for o in f['operations']))]
    ids = {f['id'] for f in flights}
    alerts = [a for a in data['alerts'] if in_terminal(a, terminal) and set(a['flight_ids']) & ids]
    risk_ids = {fid for a in alerts for fid in a['flight_ids']}
    for flight in flights:
        flight['risk'] = flight['id'] in risk_ids
    load = OpsService.workload(view, observed, minutes)
    if terminal == 'ALL':
        by_terminal = [OpsService.workload(ledger(data, name), observed, minutes) for name in data['meta']['terminals']]
        load = [{**row, **{key: None if any(rows[i][key] is None for rows in by_terminal) else sum(rows[i][key] for rows in by_terminal)
                          for key in ('demand', 'available', 'gap')}, 'basis': '各航站楼分别核算后汇总，不假设班组可跨楼替代'}
                for i, row in enumerate(load)]
    gaps = {x['start_at']: sum(y['gap'] or 0 for y in load if y['start_at'] == x['start_at']) for x in load}
    plans = [p for p in data['proposals'] if in_terminal(p, terminal) and p['state'] == 'pending']
    stands = [s for s in data['stands'] if in_terminal(s, terminal)]
    perception = data.get('perception')
    if perception:
        perception = {**perception, 'scenarios': [s for s in perception['scenarios'] if in_terminal(s, terminal)]}
    return {**view, 'mode': 'replay', 'data_mode': 'static-test', 'read_only': True,
            'snapshot_id': fingerprint({'meta': data['meta'], 'terminal': terminal, 'minutes': minutes, 'flights': flights, 'ledger': view, 'stands': stands, 'perception': perception}),
            'terminal': terminal, 'captured_at': iso(observed), 'real_time': iso(observed), 'clock_offset_days': 0,
            'minutes': minutes, 'window_end': iso(end), 'flights': flights,
            'stands': stands, 'alerts': alerts, 'workload': load,
            'perception': perception, 'proposals': [p for p in data['proposals'] if in_terminal(p, terminal)],
            'source': {'fresh': False, 'jobs': {}, 'checked_at': iso(observed), 'message': data['meta']['description']},
            'worker': {'active': False, 'heartbeat': None, 'message': '静态 JSON 测试展示；未运行实时值守'},
            'metrics': {'flights': len(flights), 'in_progress': sum(any(o['status'] in ('in_progress', 'risk') for o in f['operations']) for f in flights),
                        'attention': len(risk_ids & ids), 'resource_gaps': max(gaps.values(), default=0), 'pending': len(plans)}}


def detail(data, terminal, ident):
    flight = next((f for f in data['flights'] if in_terminal(f, terminal) and f['id'] == ident), None)
    if flight is None:
        raise HTTPException(404, '静态测试场景中没有该航班，请重新选择测试航班')
    from app.turnaround_standard import build_standard_turnaround
    keys = {'cleaning': ('REAL_START_CLEAN_TIME', 'REAL_END_CLEAN_TIME', 'ALTERATE_START_CLEAN_TIME', 'ALTERATE_END_CLEAN_TIME'),
            'fueling': ('REAL_START_SUPPLY_TIME', 'REAL_END_SUPPLY_TIME', 'ALTERATE_START_SUPPLY_TIME', 'ALTERATE_END_SUPPLY_TIME'),
            'catering': ('REAL_MEAL_START_TIME', 'REAL_MEAL_END_TIME', 'ALTERATE_MEAL_START_TIME', 'ALTERATE_MEAL_END_TIME'),
            'loading': ('LOAD_START_TIME', 'LOAD_END_TIME', 'PLAN_LOAD_START_TIME', 'PLAN_LOAD_END_TIME'),
            'boarding': ('REAL_BEGIN_BOARD_TIME', 'REAL_END_BOARD_TIME', 'ALTERATE_BEGIN_BOARD_TIME', 'ALTERATE_END_BOARD_TIME')}
    process = {'actual_on_block': flight['aibt'], 'scheduled_off_block': flight['sobt'], 'tobt': flight['tobt'], 'tsat': flight['tsat'],
               'ext_json': {**flight.get('milestone_records', {}), **{key: op[field] for op in flight['operations'] for key, field in zip(keys[op['kind']], ('actual_start', 'actual_end', 'plan_start', 'plan_end'))}}}
    board = next(o for o in flight['operations'] if o['kind'] == 'boarding')
    process.update(boarding_start=board['actual_start'], boarding_end=board['actual_end'])
    value = build_standard_turnaround({**flight, 'stand_code': flight['stand'], 'gate_code': flight['gate']}, process, flight['flight_no'], True)
    value.pop('source_row', None)
    value.pop('process_row', None)
    value.update(source_table='ops-test-data.json', source_label='静态 JSON 测试数据',
                 selection_note='固定虚构测试场景 · ' + data['meta']['captured_at'] + ' · 非真实航班实绩',
                 flight_nodes_source='静态 JSON 中的测试里程碑，非实时来源')
    value['conclusion']['meta']['evidence'] = '静态 JSON 测试数据，仅用于页面展示'
    return {'ok': True, 'data_mode': 'static-test', 'read_only': True, 'flight': flight, 'turnaround': value,
            'linked_flights': [], 'quality_notes': [], 'source': {'fresh': False, 'message': data['meta']['description']}}


def response(data, path, terminal, query, actor):
    if terminal != 'ALL' and terminal not in data['meta']['terminals']:
        raise HTTPException(400, '静态测试场景仅配置了' + '、'.join(data['meta']['terminals']))
    if path in ('/snapshot', '/flights'):
        minutes = int(query.get('minutes', '90'))
        if minutes not in (30, 60, 90, 120, 180):
            raise ValueError('时间窗口请选择30、60、90、120或180分钟')
        return {'ok': True, **snapshot(data, terminal, minutes)}
    if path.startswith('/flights/'):
        return detail(data, terminal, path[len('/flights/'):])
    if path == '/resources':
        return {'ok': True, **ledger(data, terminal)}
    if path in ('/alerts', '/rosters', '/proposals'):
        key = path[1:]
        return {'ok': True, key: [r for r in data[key] if in_terminal(r, terminal)]}
    if path == '/work-orders':
        resources = [r for r in data['resources'] if in_terminal(r, terminal)]
        allowed = None if actor['resource_ids'] is None or 'viewer' in actor['roles'] else actor['resource_ids']
        orders = [o for o in data['work_orders'] if in_terminal(o, terminal) and (allowed is None or o['resource_id'] in allowed)]
        tasks = [t for t in data['tasks'] if in_terminal(t, terminal) and not t.get('work_order_id') and (allowed is None or t['resource_id'] in allowed)]
        if allowed is not None:
            resources = [r for r in resources if r['id'] in allowed]
        ids = {o['id'] for o in orders}
        flight_ids = {o['flight_id'] for o in orders} | {t['flight_id'] for t in tasks}
        return {'ok': True, 'orders': orders, 'resources': resources, 'manual_tasks': tasks,
                'events': [e for e in data['events'] if e['target'] in ids],
                'source_flights': [f for f in data['flights'] if f['id'] in flight_ids and in_terminal(f, terminal)], 'source_error': ''}
    if path == '/flight-options':
        q, date = query.get('q', '').upper(), query.get('date', '')
        flights = [f for f in data['flights'] if in_terminal(f, terminal) and q in f['flight_no'] and (not date or f['flight_date'] == date)]
        return {'ok': True, 'flights': flights, 'total': len(flights)}
    if path == '/users':
        return {'ok': True, 'users': []}
    raise HTTPException(404, '静态测试展示中没有该接口')
