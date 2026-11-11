"""Read turnaround identity and recorded milestones from the active standard model."""
from datetime import datetime, timedelta, timezone
import json
import re

from app.core import connect_db
from app.agent.fishbone import build_fishbone_meta

BUSINESS_TZ = timezone(timedelta(hours=8))
ACDM_TABLE = 'acdm.tb_dyn_flight'
ACDM_FIELDS = ('FLIGHT_ID', 'FLIGHT_NO', 'CRAFT_NO', 'EXEC_DATE', 'NORMAL_FLIGHT_STAT_CD',
    'CRAFT_SITE_CD', 'GATE_CD', 'REAL_BLOCK_ON_TIME', 'ALTERATE_BLOCK_OFF_TIME', 'REAL_BLOCK_OFF_TIME',
    'TARGT_BLOCK_OFF_TIME', 'TARGT_DRIVE_TIME', 'REAL_BEGIN_BOARD_TIME', 'REAL_END_BOARD_TIME',
    'ALTERATE_BEGIN_BOARD_TIME', 'ALTERATE_END_BOARD_TIME', 'LOAD_START_TIME', 'LOAD_END_TIME',
    'REAL_START_CLEAN_TIME', 'REAL_END_CLEAN_TIME', 'ALTERATE_START_CLEAN_TIME', 'ALTERATE_END_CLEAN_TIME',
    'REAL_START_SUPPLY_TIME', 'REAL_END_SUPPLY_TIME', 'ALTERATE_START_SUPPLY_TIME', 'ALTERATE_END_SUPPLY_TIME',
    'REAL_MEAL_START_TIME', 'REAL_MEAL_END_TIME', 'ALTERATE_MEAL_START_TIME', 'ALTERATE_MEAL_END_TIME',
    'ASSOCIATED_FLIGHT_ID', 'SCHEME_TAKEOFF_TIME', 'ALTERATE_TAKEOFF_TIME', 'REAL_TAKEOFF_TIME',
    'SCHEME_LANDIN_TIME', 'ALTERATE_LANDIN_TIME', 'REAL_LANDIN_TIME', 'START_SCHEME_TAKEOFF_TIME',
    'ALTERATE_BLOCK_ON_TIME', 'COUNT_BLOCK_OFF_TIME', 'AIRPORT_TARGT_BLOCK_OFF_TIME',
    'REAL_BY_BRIDGE_TIME', 'PLAN_OFF_BRIDGE_TIME', 'REAL_OFF_BRIDGE_TIME',
    'ALTERATE_OPEN_CABIN_TIME', 'REAL_OPEN_CABIN_TIME', 'PLAN_CLOSE_DOOR_TIME',
    'ALTERATE_CLOSE_CABIN_TIME', 'REAL_CLOSE_CABIN_TIME',
    'ALTERATE_OPEN_CARGO_TIME', 'REAL_OPEN_CARGO_TIME', 'ALTERATE_CLOSE_CARGO_TIME', 'REAL_CLOSE_CARGO_TIME',
    'URGE_BOARD_TIME', 'REQUEST_LAUNCH_TIME', 'REAL_DRIVE_TIME', 'REAL_LAUNCH_TIME')


def turnaround_target(question):
    tail = re.search(r'(?<![A-Z0-9])(B-?[A-Z0-9]{4,5})(?![A-Z0-9])', question.upper())
    flight = re.search(r'(?<![A-Z0-9])((?:[A-Z][A-Z0-9]|[0-9][A-Z])\d{3,4})(?![A-Z0-9])', question.upper())
    identifier = tail.group(1) if tail else flight.group(1) if flight else None
    if not identifier:
        return None, None
    days = re.findall(r'\d{4}-\d{2}-\d{2}', question)
    if len(days) > 1 or re.search(r'(?:最近|近)\s*\d+\s*[天日]|本周|上周|本月|上月', question):
        raise ValueError('过站甘特图需要一个业务日，请指定日期，例如 2026-11-09')
    if not days and re.search(r'\d\s*[年月/]\s*\d', question):
        raise ValueError('请将过站业务日写成 YYYY-MM-DD，避免忽略日期条件')
    flight_date = days[0] if days else None
    day = bool(days)
    if not day:
        for word, offset in [('前天', 2), ('昨天', 1), ('今天', 0)]:
            if word in question:
                flight_date = (datetime.now(BUSINESS_TZ).date() - timedelta(days=offset)).isoformat()
                break
    return identifier, flight_date


def _dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
    return result.replace(tzinfo=BUSINESS_TZ) if result.tzinfo is None else result.astimezone(BUSINESS_TZ)


def _clock(value):
    return value.strftime('%H:%M') if value else None


def has_operation_times(operation):
    return any(operation.get(key) for key in ('actual_start', 'actual_end', 'plan_start', 'plan_end'))


def operation_timeline(operations, nodes):
    times = [_dt(operation.get(key + '_at')) for operation in operations
             for key in ('actual_start', 'actual_end', 'plan_start', 'plan_end')]
    times.extend(_dt(node.get('plan_at')) for node in nodes if node['key'] in ('tobt', 'tsat', 'aobt'))
    times = [value for value in times if value]
    if not times:
        return '', []
    first, last = min(times), max(times)
    first = first.replace(minute=first.minute // 10 * 10, second=0, microsecond=0)
    last = last.replace(second=0, microsecond=0) + timedelta(minutes=10)
    ticks, tick = [], first
    while tick <= last and len(ticks) < 100:
        ticks.append(_clock(tick))
        tick += timedelta(minutes=10)
    return f'{_clock(first)} - {_clock(last)}', ticks


def record_option(payload):
    day, number = payload['flight_date'], payload['flight_pair']
    return {'flight_date': day, 'flight_no': number, 'source_label': payload.get('source_label', 'ACDM 保障记录'),
            'recorded_operations': sum(has_operation_times(operation) for operation in payload['operations']),
            'question': f'{number} {day} 过站保障情况'}


def turnaround_identity(number, craft, identifier):
    if craft and identifier.replace('-', '').upper() == craft.replace('-', '').upper():
        return f'飞机 {craft}，对应航班 {number}'
    return f'航班 {number}，执飞机号 {craft or "未提供"}'


def get_standard_turnaround_data(identifier, flight_date=None):
    identifier = identifier.strip().upper()
    tail = bool(re.fullmatch(r'B-?[A-Z0-9]{4,5}', identifier))
    predicate = "replace(upper(f.aircraft_reg),'-','') = %s" if tail else 'upper(f.flight_no) = %s'
    params = [identifier.replace('-', '') if tail else identifier]
    if flight_date:
        flight_date = datetime.strptime(flight_date, '%Y-%m-%d').date().isoformat()
        predicate += ' AND f.flight_date::date = %s::date'
        params.append(flight_date)
    conn = connect_db()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT to_jsonb(f), COALESCE(to_jsonb(p), \'{}\'::jsonb) '
                           'FROM std_flight_core f LEFT JOIN std_flight_process p ON p.flight_id=f.id '
                           'WHERE f.deleted_at IS NULL AND COALESCE(f.is_virtual,false)=false AND ' + predicate +
                           ' ORDER BY f.flight_date DESC NULLS LAST,f.id DESC LIMIT 2', tuple(params))
            rows = [(json.loads(f) if isinstance(f, str) else f,
                     json.loads(p) if isinstance(p, str) else p) for f, p in cursor.fetchall()]
        finally:
            cursor.close()
    finally:
        conn.close()
    if not rows:
        recorded = get_recorded_acdm_turnaround(identifier, flight_date)
        if recorded:
            return recorded
        raise ValueError(f'当前数据库的标准航班及 ACDM 保障记录中未找到 {identifier}' + (f' 在 {flight_date} 的记录' if flight_date else ' 的记录'))
    if len(rows) > 1 and str(rows[0][0].get('flight_date'))[:10] == str(rows[1][0].get('flight_date'))[:10]:
        candidates = '、'.join(row[0]['flight_no'] for row in rows)
        raise ValueError(f'{identifier} 在 {str(rows[0][0].get("flight_date"))[:10]} 有多个航班（{candidates}等），请使用具体航班号和日期查询过站作业')
    flight, process = rows[0]
    payload = build_standard_turnaround(flight, process, identifier, bool(flight_date))
    if not any(has_operation_times(operation) for operation in payload['operations']):
        recorded = get_recorded_acdm_turnaround(identifier, flight_date)
        if recorded:
            if flight_date and (recorded['flight_pair'] != flight['flight_no'] or
                               recorded['craft_no'].replace('-', '').upper() != str(flight.get('aircraft_reg') or '').replace('-', '').upper()):
                return payload
            note = f'标准表最新匹配为 {payload["flight_pair"]}（{payload["flight_date"]}），未提供作业时刻；本图展示同库已同步的 ACDM 保障记录（{recorded["flight_date"]}），不是该最新航班的作业。'
            if not flight_date:
                recorded['selection_note'] = note
                recorded['dynamic_narrative'] = note + '\n\n' + recorded['dynamic_narrative']
                recorded['available_record'] = record_option(payload)
            return recorded
    elif payload['flight_date']:
        # Enrich only the exact same flight/day/aircraft; historical fallbacks stay separate.
        recorded = get_recorded_acdm_turnaround(flight['flight_no'], payload['flight_date'])
        if (recorded and recorded.get('craft_no') and payload.get('craft_no') and
                recorded['flight_date'] == payload['flight_date'] and
                recorded['flight_pair'] == payload['flight_pair'] and
                recorded['craft_no'].replace('-', '').upper() == payload['craft_no'].replace('-', '').upper()):
            by_key = {node['key']: node for node in recorded['flight_nodes']}
            for node in payload['flight_nodes']:
                extra = by_key.get(node['key'], {})
                for field in ('plan_time', 'estimated_time', 'actual_time', 'plan_at', 'estimated_at', 'actual_at'):
                    if not node.get(field) and extra.get(field):
                        node[field] = extra[field]
                if node.get('actual_at'):
                    node.update(status='completed', status_label='有实际记录')
                elif node.get('plan_time') or node.get('estimated_time'):
                    node.update(status='planned', status_label='目标时刻' if node['code'] in ('TOBT', 'TSAT') else '有计划 / 预计时刻')
                node['time_display'] = node.get('actual_time') or node.get('estimated_time') or node.get('plan_time') or ''
            for key in ('aibt', 'sobt', 'tobt', 'tsat'):
                if not payload['milestones'].get(key) and recorded['milestones'].get(key):
                    payload['milestones'][key] = recorded['milestones'][key]
            operations = {operation['id']: operation for operation in payload['operations']}
            for operation in recorded['operations']:
                if operation['id'] not in operations:
                    payload['operations'].append(operation)
                else:
                    current = operations[operation['id']]
                    for field in ('actual_start', 'actual_end', 'plan_start', 'plan_end',
                                  'actual_start_at', 'actual_end_at', 'plan_start_at', 'plan_end_at'):
                        if not current.get(field) and operation.get(field):
                            current[field] = operation[field]
                    current['status'] = 'completed' if current.get('actual_end') else 'in_progress' if current.get('actual_start') else 'planned'
            count = sum(has_operation_times(operation) for operation in payload['operations'])
            summary = f'返回 {count} 项有时刻记录的保障作业，完成记录以实际结束时刻为准。'
            payload['conclusion']['summary'] = summary
            payload['dynamic_narrative'] += '\n\n同日 ACDM 补充后：' + summary
            payload['flight_nodes_source'] = '标准航班 / 同日同航班同机号 ACDM 补充记录'
            payload['source_label'] = 'ops_ask 标准数据 / 同日 ACDM 补充记录'
            payload['conclusion']['meta']['evidence'] += '；补充同日同航班同机号 ACDM 已存时刻'
            payload['timeline_range'], payload['time_ticks'] = operation_timeline(payload['operations'], payload['flight_nodes'])
        if not flight_date:
            historical = recorded or get_recorded_acdm_turnaround(identifier)
            if historical and historical.get('flight_date') and historical['flight_date'] != payload['flight_date']:
                payload['available_record'] = record_option(historical)
    return payload


def get_recorded_acdm_turnaround(identifier, flight_date=None):
    """Read the synchronized ACDM table in the same active database, without demo defaults."""
    tail = bool(re.fullmatch(r'B-?[A-Z0-9]{4,5}', identifier))
    field = 'replace(upper(f."CRAFT_NO"),\'-\',\'\')' if tail else 'upper(f."FLIGHT_NO")'
    params = [identifier.replace('-', '') if tail else identifier]
    where = field + ' = %s'
    if flight_date:
        where += ' AND f."EXEC_DATE"::date = %s::date'
        params.append(flight_date)
    conn = connect_db()
    try:
        cursor = conn.cursor()
        try:
            columns = ','.join(f'f."{key}"' for key in ACDM_FIELDS)
            cursor.execute(f'SELECT {columns} FROM {ACDM_TABLE} f WHERE {where} ORDER BY f."EXEC_DATE" DESC NULLS LAST,f."FLIGHT_ID" DESC LIMIT 2', tuple(params))
            rows = [dict(zip(ACDM_FIELDS, row)) for row in cursor.fetchall()]
            associated = None
            if rows and rows[0].get('ASSOCIATED_FLIGHT_ID'):
                cursor.execute(f'SELECT {columns} FROM {ACDM_TABLE} f WHERE f."FLIGHT_ID"=%s LIMIT 1',
                               (rows[0]['ASSOCIATED_FLIGHT_ID'],))
                linked = cursor.fetchone()
                if linked:
                    candidate = dict(zip(ACDM_FIELDS, linked))
                    own = rows[0]
                    if (candidate.get('NORMAL_FLIGHT_STAT_CD') == 'ARR' and own.get('NORMAL_FLIGHT_STAT_CD') == 'DEP' and
                            candidate.get('CRAFT_NO') and own.get('CRAFT_NO') and
                            candidate['CRAFT_NO'].replace('-', '').upper() == own['CRAFT_NO'].replace('-', '').upper()):
                        associated = candidate
        finally:
            cursor.close()
    finally:
        conn.close()
    if not rows:
        return None
    if len(rows) > 1 and str(rows[0].get('EXEC_DATE'))[:10] == str(rows[1].get('EXEC_DATE'))[:10]:
        numbers = '、'.join(row['FLIGHT_NO'] for row in rows)
        raise ValueError(f'{identifier} 在 {str(rows[0]["EXEC_DATE"])[:10]} 有多个 ACDM 航班（{numbers}），请使用具体航班号和日期查询')
    raw = rows[0]
    flight = {'id': raw['FLIGHT_ID'], 'flight_no': raw['FLIGHT_NO'], 'aircraft_reg': raw.get('CRAFT_NO'),
              'flight_date': raw['EXEC_DATE'], 'direction': raw.get('NORMAL_FLIGHT_STAT_CD'),
              'stand_code': raw.get('CRAFT_SITE_CD'), 'gate_code': raw.get('GATE_CD')}
    process = {field: raw.get(source) for field, source in {
        'boarding_start': 'REAL_BEGIN_BOARD_TIME', 'boarding_end': 'REAL_END_BOARD_TIME',
        'actual_on_block': 'REAL_BLOCK_ON_TIME', 'scheduled_off_block': 'ALTERATE_BLOCK_OFF_TIME',
        'actual_off_block': 'REAL_BLOCK_OFF_TIME', 'tobt': 'TARGT_BLOCK_OFF_TIME', 'tsat': 'TARGT_DRIVE_TIME'}.items()}
    process['ext_json'] = raw
    if associated:
        for field, source in {'prev_scheduled_departure': 'SCHEME_TAKEOFF_TIME',
                              'prev_estimated_departure': 'ALTERATE_TAKEOFF_TIME',
                              'prev_actual_departure': 'REAL_TAKEOFF_TIME',
                              'inbound_scheduled_arrival': 'SCHEME_LANDIN_TIME',
                              'inbound_estimated_arrival': 'ALTERATE_LANDIN_TIME',
                              'inbound_actual_arrival': 'REAL_LANDIN_TIME'}.items():
            process[field] = associated.get(source)
        for field in ('REAL_BLOCK_ON_TIME', 'REAL_BY_BRIDGE_TIME', 'REAL_OPEN_CABIN_TIME'):
            if not raw.get(field):
                process['ext_json'] = {**process['ext_json'], field: associated.get(field)}
        if not process.get('actual_on_block'):
            process['actual_on_block'] = associated.get('REAL_BLOCK_ON_TIME')
    payload = build_standard_turnaround(flight, process, identifier, bool(flight_date))
    payload.update(source_table=ACDM_TABLE, id_column='FLIGHT_ID', source_row=raw, process_row={},
                   source_label='当前数据库 · ACDM保障表')
    payload['flight_nodes_source'] = (f'{ACDM_TABLE} · 已关联前序航班 {associated["FLIGHT_NO"]}' if associated else
                                      f'{ACDM_TABLE} · 未返回可确认的关联进港航班，前序节点保留未记录')
    payload['selection_note'] = (('指定业务日' if flight_date else '未指定日期，使用 ACDM 保障表最近有日期的记录') + '：' + payload['flight_date']
                                 if payload['flight_date'] else '本条 ACDM 记录未填写业务日期，无法确认所属运行日；以下仅展示已存时刻，不能据此确认当天运行状态。')
    payload['dynamic_narrative'] = turnaround_identity(payload['flight_pair'], payload['craft_no'], identifier) + f'，业务日 {payload["flight_date"] or "未提供"}。\n' + payload['selection_note'] + '。\n\n' + payload['conclusion']['summary']
    payload['conclusion']['meta']['evidence'] = f'{ACDM_TABLE} 已返回的实际作业与计划时刻，未补造缺失节点'
    return payload


def build_standard_turnaround(flight, process, identifier, explicit_date=False):
    ext = process.get('ext_json') or {}
    if isinstance(ext, str):
        ext = json.loads(ext)
    specs = [
        ('boarding', '旅客登机', process.get('boarding_start'), process.get('boarding_end'), 'ALTERATE_BEGIN_BOARD_TIME', 'ALTERATE_END_BOARD_TIME'),
        ('loading', '行李装卸', ext.get('LOAD_START_TIME'), ext.get('LOAD_END_TIME'), 'PLAN_LOAD_START_TIME', 'PLAN_LOAD_END_TIME'),
        ('cleaning', '保洁', ext.get('REAL_START_CLEAN_TIME'), ext.get('REAL_END_CLEAN_TIME'), 'ALTERATE_START_CLEAN_TIME', 'ALTERATE_END_CLEAN_TIME'),
        ('fueling', '加油', ext.get('REAL_START_SUPPLY_TIME'), ext.get('REAL_END_SUPPLY_TIME'), 'ALTERATE_START_SUPPLY_TIME', 'ALTERATE_END_SUPPLY_TIME'),
        ('catering', '配餐', ext.get('REAL_MEAL_START_TIME'), ext.get('REAL_MEAL_END_TIME'), 'ALTERATE_MEAL_START_TIME', 'ALTERATE_MEAL_END_TIME'),
    ]
    operations = []
    for key, name, start, end, plan_start, plan_end in specs:
        start, end, plan_start, plan_end = map(_dt, [start, end, ext.get(plan_start), ext.get(plan_end)])
        status = 'completed' if end else 'in_progress' if start else 'planned'
        operations.append({'id': key, 'name': name, 'status': status, 'icon': '',
                           'actual_start': _clock(start), 'actual_end': _clock(end),
                           'plan_start': _clock(plan_start), 'plan_end': _clock(plan_end),
                           'actual_start_at': start.isoformat() if start else None,
                           'actual_end_at': end.isoformat() if end else None,
                           'plan_start_at': plan_start.isoformat() if plan_start else None,
                           'plan_end_at': plan_end.isoformat() if plan_end else None})
    milestones = {key: _dt(process.get(field)) for key, field in
                  [('aibt', 'actual_on_block'), ('sobt', 'scheduled_off_block'), ('tobt', 'tobt'), ('tsat', 'tsat')]}
    day = str(flight.get('flight_date') or '')[:10]
    selection = ('指定业务日' if explicit_date else '未指定日期，取当前标准数据源最新业务日') + f'：{day}'
    count = sum(has_operation_times(operation) for operation in operations)
    summary = (f'{len(operations)} 类保障作业中，{count} 类有时刻记录；完成记录以实际结束时刻为准。' if count else
               '当前标准记录未提供保障作业时刻，暂无法生成作业时长与进度结论。')
    number, craft = flight.get('flight_no') or '', flight.get('aircraft_reg') or ''
    node_row = {**ext, **flight, **{key: value for key, value in process.items() if key != 'ext_json'}}
    flight_nodes = build_fishbone_meta(node_row)['nodes']
    timeline_range, ticks = operation_timeline(operations, flight_nodes)
    return {'flight_id': flight['id'], 'flight_pair': number, 'source_table': 'std_flight_core', 'id_column': 'id',
            'in_flight': number if flight.get('direction') == 'ARR' else '',
            'out_flight': number if flight.get('direction') == 'DEP' else '', 'craft_no': craft,
            'stand': flight.get('stand_code') or '', 'gate': flight.get('gate_code') or '', 'flight_date': day,
            'query_identifier': identifier, 'selection_note': selection, 'source_label': 'ops_ask 标准数据',
            'source_row': flight, 'process_row': process, 'operations': operations, 'pipeline': [],
            'flight_nodes': flight_nodes, 'flight_nodes_source': 'std_flight_core / std_flight_process 已返回字段；空节点保留未记录',
            'milestones': {**{key: _clock(value) for key, value in milestones.items()}, 'current_time': None},
            'timeline_range': timeline_range, 'time_ticks': ticks,
            'dynamic_narrative': turnaround_identity(number, craft, identifier) + f'，业务日 {day}。{selection}。\n\n{summary}',
            'conclusion': {'summary': summary, 'meta': {'has_delay': False,
                'evidence': 'std_flight_core / std_flight_process 已返回记录；未返回关联进出港连班',
                'actual': '', 'plan_end': '', 'delay_desc': ''}}}
