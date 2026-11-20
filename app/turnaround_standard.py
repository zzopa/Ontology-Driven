"""Read turnaround identity and recorded milestones from the active standard model."""
from datetime import datetime, timedelta, timezone
import json
import re

from app.core import connect_db
from app.agent.fishbone import _sort_flight_nodes_chronologically, build_fishbone_meta

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
    times.extend(_dt(node.get(field)) for node in nodes for field in ('actual_at', 'estimated_at', 'plan_at'))
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


def refresh_turnaround_summary(payload):
    operations, nodes = payload['operations'], payload['flight_nodes']
    keys = {'inbound_landing', 'aibt', 'door_open', 'bridge_on', 'cargo_open', 'cargo_close',
            'door_close', 'bridge_off', 'push_request', 'pushback', 'aobt', 'atot'}
    if payload.get('in_flight') and not payload.get('out_flight'):
        keys.add('aldt')
    starts = [_dt(item.get('actual_start_at')) for item in operations if item.get('actual_start_at')]
    # An on-block time after local work belongs outside this ground-service window.
    ground = [node for node in nodes if node['key'] in keys and not (
        node['key'] in ('aibt', 'inbound_landing', 'aldt') and starts and
        _dt(node.get('actual_at')) and _dt(node['actual_at']) > min(starts))]
    payload['ground_nodes'] = ground
    count = sum(has_operation_times(item) for item in operations)
    recorded = [f'{node["name"]} {node["actual_time"]}' for node in ground if node.get('actual_time')]
    summary = (f'{len(operations)} 类保障作业中，{count} 类有时刻记录；完成记录以实际结束时刻为准。' if count else
               '当前记录未提供保障作业时刻，暂无法生成作业时长与进度结论。')
    if recorded:
        summary = '已记录保障节点：' + '、'.join(recorded) + '。\n' + summary
    tasks = [f'{item["name"]} {item.get("actual_start") or "开始未记录"}—{item.get("actual_end") or "结束未记录"}'
             for item in operations if item.get('actual_start') or item.get('actual_end')]
    if tasks:
        summary += '\n作业实绩：' + '；'.join(tasks) + '。'
    missing = [item['name'] for item in operations if not has_operation_times(item)]
    if missing:
        summary += '\n' + '、'.join(missing) + '未提供作业时刻；未记录不等于未发生。'
    if payload.get('association_note'):
        summary = payload['association_note'] + '\n' + summary
    payload['conclusion']['summary'] = summary
    payload['dynamic_narrative'] = (turnaround_identity(payload['flight_pair'], payload['craft_no'], payload['query_identifier']) +
        f'，业务日 {payload["flight_date"] or "未提供"}。\n{payload["selection_note"]}。\n\n' + summary)
    targets = [node for node in nodes if node['key'] in ('tobt', 'tsat')]
    payload['timeline_range'], payload['time_ticks'] = operation_timeline(operations, ground + targets)
    return payload


def record_option(payload):
    day, number = payload['flight_date'], payload['flight_pair']
    return {'flight_date': day, 'flight_no': number, 'source_label': payload.get('source_label', 'ACDM 保障记录'),
            'recorded_operations': sum(has_operation_times(operation) for operation in payload['operations']),
            'question': f'{number} {day} 过站保障情况'}


def turnaround_identity(number, craft, identifier):
    if craft and identifier.replace('-', '').upper() == craft.replace('-', '').upper():
        return f'飞机 {craft}，对应航班 {number}'
    return f'航班 {number}，执飞机号 {craft or "未提供"}'


def build_multi_flight_summary(records, identifier, flight_date):
    day_str = flight_date or str(records[0].get('flight_date') or '')[:10]
    flight_no = records[0].get('flight_pair') or identifier
    lines = [
        f'【航班 {flight_no} · {day_str} 运行保障分析】',
        f'当日共检索到 {len(records)} 架飞机执飞 {flight_no} 的保障记录：\n'
    ]
    for idx, rec in enumerate(records, 1):
        craft = rec.get('craft_no') or '未知机号'
        stand = rec.get('stand') or '未分配'
        gate = rec.get('gate') or '未分配'
        is_pair = bool(rec.get('in_flight') and rec.get('out_flight'))
        role = '进出港连班过站' if is_pair else ('出港航班' if rec.get('out_flight') else '进港航班')

        s_row = rec.get('source_row') or {}
        p_row = rec.get('process_row') or {}

        header = f'{idx}. 飞机 {craft}（机位 {stand} · 登机口 {gate} · {role}）'
        lines.append(header)

        if is_pair:
            in_sched = _clock(_dt(p_row.get('inbound_scheduled_arrival') or rec.get('inbound_scheduled_arrival')))
            in_act = _clock(_dt(p_row.get('inbound_actual_arrival') or rec.get('inbound_actual_arrival')))
            out_sched = _clock(_dt(s_row.get('scheduled_departure')))
            out_act = _clock(_dt(s_row.get('actual_departure')))
            lines.append(f'• 进出港起降：前序进港计划 {in_sched or "—"} / 实际落地 {in_act or "未记录"}；后续出港计划 {out_sched or "—"} / 实际起飞 {out_act or "未记录"}')
        elif rec.get('out_flight'):
            out_sched = _clock(_dt(s_row.get('scheduled_departure')))
            out_act = _clock(_dt(s_row.get('actual_departure')))
            delay_hint = '（原定凌晨计划，严重延误至夜间起飞）' if out_sched and out_act and out_sched < '06:00' and out_act > '20:00' else ''
            lines.append(f'• 出港起飞：计划起飞 {out_sched or "—"} / 实际起飞 {out_act or "未记录"}{delay_hint}')
        else:
            in_sched = _clock(_dt(s_row.get('scheduled_arrival')))
            in_act = _clock(_dt(s_row.get('actual_arrival')))
            lines.append(f'• 进港降落：计划落地 {in_sched or "—"} / 实际落地 {in_act or "未记录"}')

        ground = rec.get('ground_nodes') or []
        recorded_nodes = [f'{n["name"]} {n["actual_time"]}' for n in ground if n.get('actual_time')]
        if recorded_nodes:
            lines.append('• 关键保障节点：' + '、'.join(recorded_nodes))

        ops = rec.get('operations') or []
        op_tasks = [f'{o["name"]} {o["actual_start"]}—{o["actual_end"]}' for o in ops if o.get('actual_start') and o.get('actual_end')]
        if op_tasks:
            lines.append(f'• 作业实绩（{len(op_tasks)}类已完成）：' + '；'.join(op_tasks))
        missing_ops = [o['name'] for o in ops if not has_operation_times(o)]
        if missing_ops:
            lines.append(f'• 未记录作业：' + '、'.join(missing_ops) + '未提供时刻记录')
        lines.append('')

    lines.append('右侧保障视图已同步载入两架飞机的全部保障数据，可点击图表上方飞机标签切换查看各自的完整鱼骨图与时序图。')
    return '\n'.join(lines)


def get_standard_turnaround_data(identifier, flight_date=None, flight_id=None):
    identifier = identifier.strip().upper()
    tail = bool(re.fullmatch(r'B-?[A-Z0-9]{4,5}', identifier))
    if flight_id is not None:
        predicate = 'f.id = %s'
        params = [flight_id]
        if flight_date:
            flight_date = datetime.strptime(flight_date, '%Y-%m-%d').date().isoformat()
    else:
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
                           ' ORDER BY f.flight_date DESC NULLS LAST,f.id DESC LIMIT 10', tuple(params))
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
    
    distinct_flight_nos = {row[0].get('flight_no') for row in rows if row[0].get('flight_no')}
    if len(distinct_flight_nos) > 1 and str(rows[0][0].get('flight_date'))[:10] == str(rows[1][0].get('flight_date'))[:10]:
        candidates = '、'.join(list(distinct_flight_nos)[:5])
        raise ValueError(f'{identifier} 在 {str(rows[0][0].get("flight_date"))[:10]} 有多个航班（{candidates}等），请使用具体航班号和日期查询过站作业')

    def _rank_flight_row(r):
        f_row, p_row = r
        has_proc = 1 if any(p_row.get(k) for k in ('boarding_start', 'boarding_end', 'actual_on_block', 'actual_off_block', 'real_start_clean_time')) else 0
        is_dep = 1 if f_row.get('direction') == 'DEP' else 0
        return (has_proc, is_dep, f_row.get('id', 0))

    # 按飞机机号分组 (aircraft_reg)，避免不同飞机航段错误杂交
    by_craft = {}
    for f_row, p_row in rows:
        c = (f_row.get('aircraft_reg') or '').replace('-', '').upper()
        by_craft.setdefault(c, []).append((f_row, p_row))

    records = []
    for craft, craft_rows in by_craft.items():
        dep_rows = [r for r in craft_rows if r[0].get('direction') == 'DEP']
        arr_rows = [r for r in craft_rows if r[0].get('direction') == 'ARR']
        if dep_rows and arr_rows:
            dep_flight, dep_proc = max(dep_rows, key=_rank_flight_row)
            in_flight, in_proc = max(arr_rows, key=_rank_flight_row)
            merged_proc = dict(dep_proc)
            in_act_arr = in_flight.get('actual_arrival') or in_proc.get('inbound_actual_arrival')
            in_plan_arr = in_flight.get('scheduled_arrival') or in_proc.get('inbound_scheduled_arrival')
            if in_act_arr:
                merged_proc['inbound_actual_arrival'] = in_act_arr
            if in_plan_arr:
                merged_proc['inbound_scheduled_arrival'] = in_plan_arr
            if in_proc.get('actual_on_block') and not merged_proc.get('actual_on_block'):
                merged_proc['actual_on_block'] = in_proc.get('actual_on_block')
            if in_proc.get('real_open_cabin_time') and not merged_proc.get('real_open_cabin_time'):
                merged_proc['real_open_cabin_time'] = in_proc.get('real_open_cabin_time')
            if in_proc.get('real_by_bridge_time') and not merged_proc.get('real_by_bridge_time'):
                merged_proc['real_by_bridge_time'] = in_proc.get('real_by_bridge_time')
            payload = build_standard_turnaround(dep_flight, merged_proc, identifier, bool(flight_date))
            payload['in_flight'] = in_flight.get('flight_no') or dep_flight.get('flight_no')
            payload['out_flight'] = dep_flight.get('flight_no')
            payload['stand'] = dep_flight.get('stand_code') or in_flight.get('stand_code') or ''
            payload['gate'] = dep_flight.get('gate_code') or in_flight.get('gate_code') or ''
            payload['association_note'] = f'飞机 {payload["craft_no"]} · 进出港连班过站（机位 {payload["stand"] or "未分配"} · 登机口 {payload["gate"] or "未分配"}）'
            refresh_turnaround_summary(payload)
            records.append(payload)
        elif dep_rows:
            dep_flight, dep_proc = max(dep_rows, key=_rank_flight_row)
            payload = build_standard_turnaround(dep_flight, dep_proc, identifier, bool(flight_date))
            payload['in_flight'] = ''
            payload['out_flight'] = dep_flight.get('flight_no')
            payload['stand'] = dep_flight.get('stand_code') or ''
            payload['gate'] = dep_flight.get('gate_code') or ''
            payload['association_note'] = f'飞机 {payload["craft_no"]} · 出港航班（机位 {payload["stand"] or "未分配"} · 登机口 {payload["gate"] or "未分配"}）'
            refresh_turnaround_summary(payload)
            records.append(payload)
        elif arr_rows:
            arr_flight, arr_proc = max(arr_rows, key=_rank_flight_row)
            payload = build_standard_turnaround(arr_flight, arr_proc, identifier, bool(flight_date))
            payload['in_flight'] = arr_flight.get('flight_no')
            payload['out_flight'] = ''
            payload['stand'] = arr_flight.get('stand_code') or ''
            payload['gate'] = arr_flight.get('gate_code') or ''
            payload['association_note'] = f'飞机 {payload["craft_no"]} · 进港航班（机位 {payload["stand"] or "未分配"}）'
            refresh_turnaround_summary(payload)
            records.append(payload)

    def _score_record(r):
        is_pair = 2 if r.get('in_flight') and r.get('out_flight') else 1
        ended_ops = sum(1 for o in r.get('operations', []) if o.get('actual_end'))
        recorded_nodes = sum(1 for n in r.get('ground_nodes', []) if n.get('actual_time'))
        return (is_pair, ended_ops, recorded_nodes, r.get('flight_id', 0))

    records.sort(key=_score_record, reverse=True)
    payload = records[0]
    records_clean = [{k: v for k, v in r.items() if k != 'flight_records'} for r in records]
    payload['flight_records'] = records_clean
    payload['matched_total'] = len(records)

    if len(records) > 1:
        multi_summary = build_multi_flight_summary(records, identifier, flight_date)
        payload['conclusion']['summary'] = multi_summary
        payload['dynamic_narrative'] = multi_summary

    if not any(has_operation_times(operation) for operation in payload['operations']):
        acdm_date = flight_date or (str(payload['source_row'].get('flight_date') or '')[:10] if flight_id is not None else None)
        recorded = get_recorded_acdm_turnaround(identifier, acdm_date)
        if recorded:
            if acdm_date and (recorded['flight_pair'] != payload['flight_pair'] or
                              recorded['craft_no'].replace('-', '').upper() != str(payload['craft_no'] or '').replace('-', '').upper()):
                return payload
            note = f'标准表最新匹配为 {payload["flight_pair"]}（{payload["flight_date"]}），未提供作业时刻；本图展示同库已同步的 ACDM 保障记录（{recorded["flight_date"]}），不是该最新航班的作业。'
            if not flight_date:
                recorded['selection_note'] = note
                recorded['dynamic_narrative'] = note + '\n\n' + recorded['dynamic_narrative']
                recorded['available_record'] = record_option(payload)
            return recorded
    elif payload['flight_date'] and len(records) == 1:
        # Enrich only the exact same flight/day/aircraft; historical fallbacks stay separate.
        recorded = get_recorded_acdm_turnaround(payload['flight_pair'], payload['flight_date'])
        if (recorded and recorded.get('craft_no') and payload.get('craft_no') and
                recorded['flight_date'] == payload['flight_date'] and
                recorded['flight_pair'] == payload['flight_pair'] and
                recorded['craft_no'].replace('-', '').upper() == payload['craft_no'].replace('-', '').upper()):
            if payload.get('source_row', {}).get('direction') == 'ARR' and recorded.get('out_flight'):
                for node in payload['flight_nodes']:
                    if node['key'] == 'aldt':
                        node.update(key='inbound_landing', name='落地', code='IN_ALDT',
                                    note='关联过站的进港航班在本场落地')
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
            existing_keys = {node['key'] for node in payload['flight_nodes']}
            payload['flight_nodes'].extend(node for node in recorded['flight_nodes'] if node['key'] not in existing_keys)
            payload['flight_nodes'] = _sort_flight_nodes_chronologically(payload['flight_nodes'])
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
            payload['flight_nodes_source'] = '标准航班 / 同日同航班同机号 ACDM 补充记录'
            payload['source_label'] = 'ops_ask 标准数据 / 同日 ACDM 补充记录'
            payload['conclusion']['meta']['evidence'] += '；补充同日同航班同机号 ACDM 已存时刻'
            for key in ('in_flight', 'out_flight', 'association_note', 'acdm_rows'):
                if key in recorded:
                    payload[key] = recorded[key]
            payload['flight_nodes_source'] += '；' + recorded.get('flight_nodes_source', '')
            refresh_turnaround_summary(payload)
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
                    if ({candidate.get('NORMAL_FLIGHT_STAT_CD'), own.get('NORMAL_FLIGHT_STAT_CD')} == {'ARR', 'DEP'} and
                            candidate.get('CRAFT_NO') and own.get('CRAFT_NO') and
                            candidate['CRAFT_NO'].replace('-', '').upper() == own['CRAFT_NO'].replace('-', '').upper()):
                        inbound, outbound = (own, candidate) if own['NORMAL_FLIGHT_STAT_CD'] == 'ARR' else (candidate, own)
                        arrival = _dt(inbound.get('REAL_BLOCK_ON_TIME') or inbound.get('REAL_LANDIN_TIME'))
                        departure = _dt(outbound.get('REAL_BLOCK_OFF_TIME') or outbound.get('REAL_TAKEOFF_TIME'))
                        if not (arrival and departure and arrival > departure):
                            associated = candidate
        finally:
            cursor.close()
    finally:
        conn.close()
    if not rows:
        return None
    distinct_flight_nos = {row['FLIGHT_NO'] for row in rows if row.get('FLIGHT_NO')}
    if len(distinct_flight_nos) > 1 and str(rows[0].get('EXEC_DATE'))[:10] == str(rows[1].get('EXEC_DATE'))[:10]:
        numbers = '、'.join(list(distinct_flight_nos)[:5])
        raise ValueError(f'{identifier} 在 {str(rows[0]["EXEC_DATE"])[:10]} 有多个 ACDM 航班（{numbers}），请使用具体航班号和日期查询')
    raw = max(rows, key=lambda r: (1 if r.get('NORMAL_FLIGHT_STAT_CD') == 'DEP' else 0, r.get('FLIGHT_ID', 0)))
    inbound = (raw if raw.get('NORMAL_FLIGHT_STAT_CD') == 'ARR' else associated) if associated else None
    outbound = (raw if raw.get('NORMAL_FLIGHT_STAT_CD') == 'DEP' else associated) if associated else None
    service = outbound or raw
    flight = {'id': raw['FLIGHT_ID'], 'flight_no': raw['FLIGHT_NO'], 'aircraft_reg': raw.get('CRAFT_NO'),
              'flight_date': raw['EXEC_DATE'], 'direction': 'DEP' if associated else raw.get('NORMAL_FLIGHT_STAT_CD'),
              'stand_code': raw.get('CRAFT_SITE_CD'), 'gate_code': raw.get('GATE_CD')}
    process = {field: service.get(source) for field, source in {
        'boarding_start': 'REAL_BEGIN_BOARD_TIME', 'boarding_end': 'REAL_END_BOARD_TIME',
        'actual_on_block': 'REAL_BLOCK_ON_TIME', 'scheduled_off_block': 'ALTERATE_BLOCK_OFF_TIME',
        'actual_off_block': 'REAL_BLOCK_OFF_TIME', 'tobt': 'TARGT_BLOCK_OFF_TIME', 'tsat': 'TARGT_DRIVE_TIME'}.items()}
    process['ext_json'] = dict(service)
    if associated:
        for field, source in {'prev_scheduled_departure': 'SCHEME_TAKEOFF_TIME',
                              'prev_estimated_departure': 'ALTERATE_TAKEOFF_TIME',
                              'prev_actual_departure': 'REAL_TAKEOFF_TIME',
                              'inbound_scheduled_arrival': 'SCHEME_LANDIN_TIME',
                              'inbound_estimated_arrival': 'ALTERATE_LANDIN_TIME',
                              'inbound_actual_arrival': 'REAL_LANDIN_TIME'}.items():
            process[field] = inbound.get(source)
        for field in ('REAL_BLOCK_ON_TIME', 'REAL_BY_BRIDGE_TIME', 'REAL_OPEN_CABIN_TIME'):
            process['ext_json'][field] = inbound.get(field)
        process['actual_on_block'] = inbound.get('REAL_BLOCK_ON_TIME')
    payload = build_standard_turnaround(flight, process, identifier, bool(flight_date))
    payload.update(source_table=ACDM_TABLE, id_column='FLIGHT_ID', source_row=raw, process_row={},
                   source_label='当前数据库 · ACDM保障表')
    payload['acdm_rows'] = [raw] + ([associated] if associated else [])
    if associated:
        payload.update(in_flight=inbound['FLIGHT_NO'], out_flight=outbound['FLIGHT_NO'])
        payload['association_note'] = (f'同机号 {payload["craft_no"]} 的关联过站：进港 {inbound["FLIGHT_NO"]}'
            f'（{str(inbound.get("EXEC_DATE") or "未提供")[:10]}）→ 出港 {outbound["FLIGHT_NO"]}'
            f'（{str(outbound.get("EXEC_DATE") or "未提供")[:10]}）；按明确关联航班记录展示。')
    payload['flight_nodes_source'] = (f'{ACDM_TABLE} · {payload["association_note"]}' if associated else
                                      f'{ACDM_TABLE} · 未返回可确认的关联航班，仅展示本航班已存节点')
    payload['selection_note'] = (('指定业务日' if flight_date else '未指定日期，使用 ACDM 保障表最近有日期的记录') + '：' + payload['flight_date']
                                 if payload['flight_date'] else '本条 ACDM 记录未填写业务日期，无法确认所属运行日；以下仅展示已存时刻，不能据此确认当天运行状态。')
    payload['conclusion']['meta']['evidence'] = f'{ACDM_TABLE} 已返回的实际作业与计划时刻，未补造缺失节点'
    return refresh_turnaround_summary(payload)


def build_standard_turnaround(flight, process, identifier, explicit_date=False):
    ext = process.get('ext_json') or {}
    if isinstance(ext, str):
        ext = json.loads(ext)
    specs = [
        ('boarding', '旅客登机',
         process.get('boarding_start') or ext.get('REAL_BEGIN_BOARD_TIME'),
         process.get('boarding_end') or ext.get('REAL_END_BOARD_TIME'),
         process.get('alterate_begin_board_time') or ext.get('ALTERATE_BEGIN_BOARD_TIME'),
         process.get('alterate_end_board_time') or ext.get('ALTERATE_END_BOARD_TIME')),
        ('loading', '行李装卸',
         process.get('first_baggage') or ext.get('LOAD_START_TIME'),
         process.get('last_baggage') or ext.get('LOAD_END_TIME'),
         process.get('plan_load_start_time') or ext.get('PLAN_LOAD_START_TIME'),
         process.get('plan_load_end_time') or ext.get('PLAN_LOAD_END_TIME')),
        ('cleaning', '保洁',
         process.get('real_start_clean_time') or ext.get('REAL_START_CLEAN_TIME'),
         process.get('real_end_clean_time') or ext.get('REAL_END_CLEAN_TIME'),
         process.get('alterate_start_clean_time') or ext.get('ALTERATE_START_CLEAN_TIME'),
         process.get('alterate_end_clean_time') or ext.get('ALTERATE_END_CLEAN_TIME')),
        ('fueling', '加油',
         process.get('real_start_supply_time') or ext.get('REAL_START_SUPPLY_TIME'),
         process.get('real_end_supply_time') or ext.get('REAL_END_SUPPLY_TIME'),
         process.get('alterate_start_supply_time') or ext.get('ALTERATE_START_SUPPLY_TIME'),
         process.get('alterate_end_supply_time') or ext.get('ALTERATE_END_SUPPLY_TIME')),
        ('catering', '配餐',
         process.get('real_meal_start_time') or ext.get('REAL_MEAL_START_TIME'),
         process.get('real_meal_end_time') or ext.get('REAL_MEAL_END_TIME'),
         process.get('alterate_meal_start_time') or ext.get('ALTERATE_MEAL_START_TIME'),
         process.get('alterate_meal_end_time') or ext.get('ALTERATE_MEAL_END_TIME')),
    ]
    operations = []
    for key, name, start, end, plan_start, plan_end in specs:
        start_dt, end_dt, p_start_dt, p_end_dt = map(_dt, [start, end, plan_start, plan_end])
        status = 'completed' if end_dt else 'in_progress' if start_dt else 'planned'
        operations.append({'id': key, 'name': name, 'status': status, 'icon': '',
                           'actual_start': _clock(start_dt), 'actual_end': _clock(end_dt),
                           'plan_start': _clock(p_start_dt), 'plan_end': _clock(p_end_dt),
                           'actual_start_at': start_dt.isoformat() if start_dt else None,
                           'actual_end_at': end_dt.isoformat() if end_dt else None,
                           'plan_start_at': p_start_dt.isoformat() if p_start_dt else None,
                           'plan_end_at': p_end_dt.isoformat() if p_end_dt else None})
    milestones = {key: _dt(process.get(field) or ext.get(ext_field)) for key, field, ext_field in
                  [('aibt', 'actual_on_block', 'REAL_BLOCK_ON_TIME'),
                   ('sobt', 'scheduled_off_block', 'ALTERATE_BLOCK_OFF_TIME'),
                   ('tobt', 'tobt', 'TARGT_BLOCK_OFF_TIME'),
                   ('tsat', 'tsat', 'TARGT_DRIVE_TIME')]}
    day = str(flight.get('flight_date') or '')[:10]
    selection = ('指定业务日' if explicit_date else '未指定日期，取当前标准数据源最新业务日') + f'：{day}'
    number, craft = flight.get('flight_no') or '', flight.get('aircraft_reg') or ''
    node_row = {**ext, **flight, **{key: value for key, value in process.items() if key != 'ext_json'}}
    flight_nodes = build_fishbone_meta(node_row)['nodes']
    payload = {'flight_id': flight['id'], 'flight_pair': number, 'source_table': 'std_flight_core', 'id_column': 'id',
            'in_flight': number if flight.get('direction') == 'ARR' else '',
            'out_flight': number if flight.get('direction') == 'DEP' else '', 'craft_no': craft,
            'stand': flight.get('stand_code') or '', 'gate': flight.get('gate_code') or '', 'flight_date': day,
            'query_identifier': identifier, 'selection_note': selection, 'source_label': 'ops_ask 标准数据',
            'source_row': flight, 'process_row': process, 'operations': operations, 'pipeline': [],
            'flight_nodes': flight_nodes, 'flight_nodes_source': 'std_flight_core / std_flight_process 已返回字段；空节点保留未记录',
            'milestones': {**{key: _clock(value) for key, value in milestones.items()}, 'current_time': None},
            'timeline_range': '', 'time_ticks': [],
            'conclusion': {'summary': '', 'meta': {'has_delay': False,
                'evidence': 'std_flight_core / std_flight_process 已返回记录；未返回关联进出港连班',
                'actual': '', 'plan_end': '', 'delay_desc': ''}}}
    return refresh_turnaround_summary(payload)
