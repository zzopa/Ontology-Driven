"""Create traceable record facts and validate model claims against supplied evidence."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Tuple

from app.query import json_safe


def sanitize(value, hidden):
    if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return json.dumps(sanitize(parsed, hidden), ensure_ascii=False)
        except (ValueError, RecursionError):
            pass
    if isinstance(value, dict):
        return {k: sanitize(v, hidden) for k, v in value.items()
                if k.lower() not in hidden and not any(x in k for x in ('身份证','证件号码','手机号码','联系电话'))}
    if isinstance(value, list):
        return [sanitize(v, hidden) for v in value]
    return json_safe(value)


def enrich(row, spec):
    result = dict(row)
    for key, attr in spec['attributes'].items():
        if 'column' not in attr or 'path' not in attr:
            continue
        value = row.get(attr['column'])
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = None
        for part in attr['path']:
            value = value.get(part) if isinstance(value, dict) else None
        result[key] = value
    return result


def build_evidence(catalog, plan, main_rows, main_total, related, links, metric=None):
    now = datetime.now(timezone.utc).isoformat()
    records, counters, by_table = [], [], {}
    hidden = set(catalog.overrides.get('sensitive_fields', []))
    tables = [(plan['subject'], main_rows, main_total)] + [
        (r['table'], r['rows'], r['count']) for r in related]
    if plan['intent'] in ('count', 'sum', 'metric', 'acdm_report'):
        target = plan.get('aggregate_target') or plan['subject']
        facts = [{'id': 'A%d' % (i+1),
                  'kind': 'report' if plan['intent'] == 'acdm_report' else 'aggregate',
                  'table': target,
                  'name': catalog.entities[target]['name'], 'values': json_safe(row),
                  'metric': metric, 'source': {'database': catalog.schema['database'],
                    'table': target, 'queried_at': now, 'scope': '全部匹配记录'}}
                 for i, row in enumerate(main_rows)]
        cov = [{'table': r['table'], 'name': r.get('name', r['table']), 'total': r['count'],
                'provided': len(r.get('rows') or []), 'complete': len(r.get('rows') or []) == r['count']}
               for r in related] if plan['intent'] == 'acdm_report' else []
        return {'version': catalog.version, 'queried_at': now, 'facts': facts,
                'relationships': [], 'scope': {'complete_aggregate': True}, 'coverage': cov}
    for table, rows, total in tables:
        spec = catalog.entities[table]
        by_table[table] = []
        counters.append({'table': table, 'name': spec['name'], 'total': total,
                         'provided': len(rows), 'complete': len(rows) == total})
        for row in rows:
            clean = sanitize(enrich(row, spec), hidden)
            pk = catalog.tables[table].get('primary_key', [])
            identity = {key: clean.get(key) for key in pk}
            digest = hashlib.sha256(json.dumps(identity or clean, sort_keys=True,
                                    ensure_ascii=False, default=str).encode()).hexdigest()[:20]
            properties = {}
            for field, value in clean.items():
                # NULL/empty is also a real query value (e.g. an unset end date),
                # not a licence for the model to invent a value or hide a field.
                attr = spec['attributes'].get(field, {})
                properties[field] = {'value': value, 'label': attr.get('label', field),
                    'description': attr.get('description', ''), 'unit': attr.get('unit', ''),
                    'enum_label': attr.get('enums', {}).get(str(value))}
            record = {'id': 'F%d' % (len(records)+1), 'kind': 'record',
                'entity': catalog.schema['database'] + '.' + table + '.' + digest,
                'table': table, 'name': spec['name'], 'properties': properties,
                'business_time': clean.get(spec['bindings'].get('time')),
                'source': {'database': catalog.schema['database'], 'table': table,
                           'record_key': identity, 'queried_at': now}}
            records.append(record)
            by_table[table].append((record, clean))
    relationships = []
    for edge in links:
        for left, lrow in by_table.get(edge['from_tab'], []):
            for right, rrow in by_table.get(edge['to_tab'], []):
                pairs = edge.get('pairs', [[edge['fcol'],edge['tcol']]])
                if all(lrow.get(a) is not None and rrow.get(b) is not None and
                       str(lrow[a]) == str(rrow[b]) for a, b in pairs):
                    relationships.append({'source': left['id'], 'target': right['id'],
                        'predicate': edge.get('note') or edge.get('role') or '字段匹配关联',
                        'pairs': pairs, 'validation': edge.get('validation','schema_only')})
    return {'version': catalog.version, 'queried_at': now, 'facts': records,
            'relationships': relationships, 'coverage': counters,
            'scope': {'main_total': main_total, 'relationship_scope': '全部匹配主记录',
                      'main_table': plan['subject'], 'main_name': catalog.entities[plan['subject']]['name'],
                      'name_field': catalog.entities[plan['subject']]['bindings'].get('name'),
                      'person_records': bool(catalog.entities[plan['subject']].get('name_lookup')),
                      'complete_records': all(c['complete'] for c in counters)}}


def model_evidence(evidence, budget):
    """Single-payload compatibility helper; never silently return a subset."""
    from app.evidence_transport import evidence_batches
    batches = evidence_batches(evidence, budget)
    if len(batches) > 1:
        raise ValueError('完整查询证据需要 %d 批，请使用 evidence_batches，不能只发送第一批' % len(batches))
    return batches[0].payload if batches else {'facts': [], 'relationships': []}


def validate_claims(claims, evidence):
    index = {fact['id']: fact for fact in evidence['facts']}
    accepted = []
    if not isinstance(claims, list) or not claims:
        raise ValueError('模型没有返回可核验结论')
    for claim in claims[:8]:
        if not isinstance(claim, dict):
            raise ValueError('结论格式无效')
        text, ids = claim.get('text'), claim.get('fact_ids')
        if not isinstance(text, str) or not text.strip() or not isinstance(ids, list) or not ids:
            raise ValueError('结论缺少证据引用')
        if any(not isinstance(key,str) or key not in index for key in ids):
            raise ValueError('结论引用了不存在的事实')
        referenced = [index[key] for key in ids]
        values = [fact['values'] if fact['kind']=='aggregate' else
                  {key:prop['value'] for key,prop in fact['properties'].items()} for fact in referenced]
        pattern = r'[+-]?\d+(?:,\d{3})*(?:\.\d+)?'
        def atom(number):
            try:
                return Decimal(number.replace(',',''))
            except InvalidOperation:
                return number
        # Query timestamps, source IDs and catalog versions are not business evidence.
        supported = {atom(n) for n in re.findall(pattern,json.dumps(values,ensure_ascii=False,default=str))}
        scope = evidence.get('scope', {})
        root = scope.get('main_table')
        if root and any(f.get('table') == root for f in referenced):
            for match in re.finditer(r'(?:共有|共|总计|合计|命中|匹配|找到|查询到|有)\s*(' + pattern + r')\s*(名|位|人|条)([^。；\n，]*)', text):
                number, unit, tail = match.groups()
                is_main_count = (unit in ('名', '位', '人') and scope.get('person_records')) or (
                    unit == '条' and (scope.get('main_name', '') in tail or not tail.strip()))
                if is_main_count:
                    if atom(number) != atom(str(scope.get('main_total'))):
                        raise ValueError('结论中的主体命中数与数据库实际总数不一致')
                    supported.add(atom(number))
        for number in re.findall(pattern, text):
            if atom(number) not in supported:
                raise ValueError('结论包含证据未支持的数值：' + number)
        for number, unit in re.findall('(' + pattern + r')\s*(亿元|万元|元)',text):
            supported_unit = False
            for fact in referenced:
                properties = (fact.get('properties') or {}).values()
                pairs = [(prop['value'],prop.get('unit')) for prop in properties]
                if fact.get('metric'):
                    pairs.append((fact['values'].get('total'),fact['metric'].get('unit')))
                if any(u == unit and atom(str(v)) == atom(number) for v,u in pairs):
                    supported_unit = True
            if not supported_unit:
                raise ValueError('结论金额的数值与单位未共同得到证据支持')
        accepted.append({'text': text.strip(), 'fact_ids': list(dict.fromkeys(ids))})
        if 'kind' in claim:
            if claim['kind'] not in ('findings', 'interpretation', 'limitations', 'recommendations'):
                raise ValueError('模型结论分类无效，不能把分析或建议伪装成查询事实')
            accepted[-1]['kind'] = claim['kind']
            # The live stream is plain text before the final document arrives.
            # Keep interpretations/suggestions visibly distinct there as well.
            marker = {'interpretation':'分析解释：', 'recommendations':'核对建议（非已证实事实）：'}.get(claim['kind'])
            if marker:
                accepted[-1]['text'] = marker + accepted[-1]['text']
    return accepted


def _prop_val(props, *keys):
    for key in keys:
        item = props.get(key)
        if isinstance(item, dict) and item.get('value') not in (None, ''):
            return item['value']
    return None


def _fmt_local_time(raw):
    if raw in (None, ''):
        return ''
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        bj = timezone(timedelta(hours=8))
        dt = dt.replace(tzinfo=bj) if dt.tzinfo is None else dt.astimezone(bj)
        return f'{dt:%H:%M}'
    except ValueError:
        text = str(raw).strip()
        return text[11:16] if len(text) >= 16 and text[10] in ('T', ' ') else text


def _single_flight_summary(props):
    fno = _prop_val(props, 'flight_no', 'FLIGHT_NO')
    if not fno:
        return None
    direction = str(_prop_val(props, 'direction', 'NORMAL_FLIGHT_STAT_CD', 'IS_OFF_IN') or '').upper()
    dir_lbl = {'DEP': '出港', 'D': '出港', 'ARR': '进港', 'A': '进港'}.get(direction, '')
    fdate = str(_prop_val(props, 'flight_date', 'EXEC_DATE') or '')[:10]
    origin_raw = _prop_val(props, 'origin_city', 'origin_airport', 'TAKEOFF_AIRPORT_IATA')
    dest_raw = _prop_val(props, 'dest_city', 'dest_airport', 'ARRIVAL_AIRPORT_IATA')
    from app.agent.visual import get_airport_name, format_flight_status
    origin = get_airport_name(origin_raw) or origin_raw
    dest = get_airport_name(dest_raw) or dest_raw
    route = f'{origin} → {dest}' if origin and dest else ''
    if direction in ('ARR', 'A'):
        plan_t = _fmt_local_time(_prop_val(props, 'scheduled_arrival', 'SCHEME_LANDIN_TIME'))
        act_t = _fmt_local_time(_prop_val(props, 'actual_arrival', 'REAL_LANDIN_TIME', 'execution_time'))
    else:
        plan_t = _fmt_local_time(_prop_val(props, 'scheduled_departure', 'SCHEME_TAKEOFF_TIME'))
        act_t = _fmt_local_time(_prop_val(props, 'actual_departure', 'REAL_TAKEOFF_TIME', 'execution_time'))
    stand = _prop_val(props, 'stand_code', 'CRAFT_SITE_CD')
    gate = _prop_val(props, 'gate_code', 'GATE_CD')
    raw_status = _prop_val(props, 'flight_status', 'operation_status')
    status = format_flight_status(raw_status) if raw_status else None
    parts = [f'{fno}' + (f'（{dir_lbl}）' if dir_lbl else '')]
    if fdate:
        parts.append(f'业务日 {fdate}')
    if route:
        parts.append(f'航线 {route}')
    if plan_t:
        parts.append(f'计划 {plan_t}')
    if act_t:
        parts.append(f'实际 {act_t}')
    if stand:
        parts.append(f'机位 {stand}')
    if gate:
        parts.append(f'登机口 {gate}')
    if status:
        parts.append(f'状态 {status}')
    delay_mins = _prop_val(props, 'flight_delay_time', 'clearance_delay_time', 'dep_delay_time', 'arr_delay_time')
    if delay_mins is not None:
        try:
            d_val = int(delay_mins)
            if d_val > 0:
                parts.append(f'延误 {d_val} 分钟')
        except (ValueError, TypeError):
            pass
    return '；'.join(parts)


def _format_dim_value(k: str, dim_lbl: str, val_raw: Any) -> tuple[str, str]:
    if val_raw is None:
        return dim_lbl, '未标注'
    val_str = str(val_raw)
    norm_lbl = dim_lbl
    if k in ('airline', 'airline_code', 'airline_two_char_cd') or dim_lbl in ('航司代码', '航司', '航空公司'):
        norm_lbl = '航司'
        from app.agent.visual import get_airline_name
        al_name = get_airline_name(val_raw)
        if al_name and al_name != str(val_raw):
            val_str = f"{al_name}（{val_raw}）"
    elif k in ('dest_city', 'origin_city', 'dest_airport', 'origin_airport', 'airport', 'airport_code',
              'takeoff_airport_iata', 'arrival_airport_iata', 'TAKEOFF_AIRPORT_IATA', 'ARRIVAL_AIRPORT_IATA') or dim_lbl in ('机场代码', '起飞机场', '到达机场', '目的地'):
        from app.agent.visual import get_airport_name
        ap_name = get_airport_name(val_raw)
        if ap_name and ap_name != str(val_raw):
            val_str = f"{ap_name}（{val_raw}）"
    return norm_lbl, val_str


def fallback_claims(catalog, plan, evidence, main_total):
    subject = catalog.entities[plan['subject']]['name']
    if plan.get('answer_field') and main_total == 1:
        record = next((f for f in evidence['facts'] if f['table'] == plan['subject']), None)
        if record:
            props = record['properties']
            field = plan['answer_field']
            label = catalog.attribute(plan['subject'], field)['label']
            number = _prop_val(props, 'flight_no', 'FLIGHT_NO') or '该航班'
            value = _prop_val(props, field)
            text = (f'{number} 的{label}是 {value}。' if value is not None
                    else f'{number} 的{label}在当前记录中未填写，无法确定。')
            return [{'text': text, 'fact_ids': [record['id']]}], text
    if plan.get('flight_sequence'):
        op_w = plan.get('operational_window')
        day = (op_w['start'][:10] if op_w and op_w.get('start')
               else next((str(c['value'])[:10] for c in plan['conditions'] if c['op'] == '>='), ''))
        mode_label = f"运营日（{int(op_w.get('startHour', 5)):02d}:00起）" if op_w else '自然日'
        direction = next((c['value'] for c in plan['conditions'] if c['col'] == 'direction'), None)
        scope = {'DEP': '出港', 'ARR': '进港'}.get(direction, '进出港')
        label = '最后一班' if plan['flight_sequence'] == 'last' else '第一班'
        if not evidence['facts']:
            return [], f'按北京时间{mode_label}及本场实际起降时间，未查到 {day} {scope}航班的实际执行记录，无法确定{label}。这只反映当前已同步数据，不表示当天没有航班。'
        record = next(f for f in evidence['facts'] if f['table'] == plan['subject'])
        props = record['properties']
        number = props['flight_no']['value']
        event = '实际起飞' if props.get('direction', {}).get('value') == 'DEP' else '实际落地'
        value = datetime.fromisoformat(str(props['execution_time']['value']).replace('Z', '+00:00'))
        value = value.replace(tzinfo=timezone(timedelta(hours=8))) if value.tzinfo is None else value.astimezone(timezone(timedelta(hours=8)))
        text = f'{day} {scope}{label}航班是 {number}，{event}时间为 {value:%Y-%m-%d %H:%M:%S}（北京时间{mode_label}）。按本场实际起降时间排序，仅比较当前数据库已同步且有实际时刻的记录。'
        return [{'text': text, 'fact_ids': [record['id']]}], text
    if not evidence['facts']:
        fno = next((c['value'] for c in plan.get('conditions', []) if c.get('col') == 'flight_no'), None)
        if fno:
            return [], f'按当前数据库与查询条件，未检索到航班 {fno} 的相关执行记录。'
        return [], '按当前数据库与查询条件，未命中%s记录。' % subject
    facts = evidence['facts']
    if plan.get('safeguard_node_breakdown'):
        summary = f'保障节点实测录入共 {main_total:,} 条，涉及 {len(facts)} 类节点。'
        claims = [{'text': summary, 'fact_ids': [f['id'] for f in facts]}]
        for fact in facts:
            row = fact['values']
            pct = row['entry_rate_pct']
            coverage = f"{pct}%（{row['recorded_flights']:,}/{row['eligible_flights']:,}）" if pct is not None else '无适用航班'
            text = (f"{row['node_name']}：{row['n']:,} 条，去重航班 {row['distinct_flights']:,} 班，"
                    f"适用航班覆盖率 {coverage}。")
            claims.append({'text': text, 'fact_ids': [fact['id']]})
        return claims, '\n'.join(c['text'] for c in claims)
    if plan['intent'] == 'acdm_report':
        title_txt = plan.get('disp') or subject
        return [{'text': title_txt, 'fact_ids': [f['id'] for f in facts]}], title_txt
    if plan.get('plan_template'):
        from app.agent.governance_runtime import render_template_summary
        rendered_tpl = render_template_summary(catalog, plan, evidence, main_total)
        if rendered_tpl:
            if plan['intent'] in ('count', 'sum', 'metric'):
                return [{'text': rendered_tpl, 'fact_ids': [f['id'] for f in facts]}], rendered_tpl
            primary_ids = [f['id'] for f in facts if f['table'] == plan['subject']] or [facts[0]['id']]
            return [{'text': rendered_tpl, 'fact_ids': primary_ids}], rendered_tpl
    if plan['intent'] in ('count','sum','metric'):
        if len(facts)>8:
            text='%s匹配 %d 条记录，共 %d 个统计分组。' % (subject,main_total,len(facts))
            top_fact = facts[0]
            top_vals = top_fact.get('values') or {}
            metric_meta = top_fact.get('metric') or {}
            val_alias = metric_meta.get('value_alias') or metric_meta.get('field') or ('n' if plan['intent'] == 'count' else 'total')
            def _fact_num(f):
                v = (f.get('values') or {}).get(val_alias)
                if v is None:
                    v = (f.get('values') or {}).get('total', (f.get('values') or {}).get('n'))
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return float('-inf')
            best_fact = max(facts, key=_fact_num)
            best_vals = best_fact.get('values') or {}
            best_val = best_vals.get(val_alias)
            if best_val is None:
                best_val = best_vals.get('total', best_vals.get('n'))
            ignore_keys = {'total', 'mx', 'n', 'missing', 'invalid', val_alias, '__num', '__den',
                           metric_meta.get('value_alias'), metric_meta.get('field'), metric_meta.get('code'), metric_meta.get('key')}
            best_groups = []
            for k in best_vals:
                if k in ignore_keys or (isinstance(k, str) and k.startswith('__') and k != '__period'):
                    continue
                if k == '__period':
                    dim_lbl = '统计期间'
                elif hasattr(catalog, 'dimensions') and k in catalog.dimensions:
                    dim_lbl = catalog.dimensions[k].get('name', k)
                elif best_fact['table'] in catalog.entities and k in catalog.entities[best_fact['table']]['attributes']:
                    dim_lbl = catalog.attribute(best_fact['table'], k)['label']
                else:
                    dim_lbl = k
                norm_lbl, val_display = _format_dim_value(k, dim_lbl, best_vals[k])
                best_groups.append(f"{norm_lbl}={val_display}")
            if best_groups and best_val is not None:
                unit_str = (' ' + metric_meta.get('unit')) if metric_meta.get('unit') else (' 条' if plan['intent'] == 'count' else '')
                text += '其中最高为 %s（%s%s）。' % ('、'.join(best_groups), best_val, unit_str)
            text += '完整分组结果及口径见下方表格。'
            if plan['intent']=='sum':
                text+='各组金额独立展示，不混合不同币种或业务类型。'
            shown_ids = [f['id'] for f in facts] if len(facts) <= 15 else [best_fact['id']]
            return [{'text':text,'fact_ids':shown_ids}],text
        claims = []
        for fact in facts:
            values = fact['values']
            if plan['intent'] == 'count':
                groups = [key for key in values if key != 'n']
                group_parts = []
                for k in groups:
                    if k == '__period':
                        dim_lbl = '统计期间'
                    elif hasattr(catalog, 'dimensions') and k in catalog.dimensions:
                        dim_lbl = catalog.dimensions[k].get('name', k)
                    elif fact['table'] in catalog.entities and k in catalog.entities[fact['table']]['attributes']:
                        dim_lbl = catalog.attribute(fact['table'], k)['label']
                    else:
                        dim_lbl = k
                    norm_lbl, val_display = _format_dim_value(k, dim_lbl, values[k])
                    group_parts.append(f"{norm_lbl}={val_display}")
                group_text = '、'.join(group_parts)
                text = '%s%s命中 %s 条记录。' % ((group_text+'：') if group_text else '', fact['name'], values['n'])
            else:
                metric = fact.get('metric') or {}
                val_alias = metric.get('value_alias') or metric.get('field') or 'total'
                val = values.get(val_alias)
                if val is None:
                    for candidate in (metric.get('code'), metric.get('key'), 'total', 'mx'):
                        if candidate and candidate in values and values[candidate] is not None:
                            val = values[candidate]
                            val_alias = candidate
                            break
                val_str = str(val) if val is not None else '未能计算（缺少有效值或分母为零）'
                ignore_keys = {'total', 'mx', 'n', 'missing', 'invalid', val_alias, '__num', '__den',
                               metric.get('value_alias'), metric.get('field'), metric.get('code'), metric.get('key')}
                groups = [key for key in values if key not in ignore_keys and not (isinstance(key, str) and key.startswith('__') and key != '__period')]
                group_parts = []
                for k in groups:
                    if k == '__period':
                        dim_lbl = '统计期间'
                    elif hasattr(catalog, 'dimensions') and k in catalog.dimensions:
                        dim_lbl = catalog.dimensions[k].get('name', k)
                    elif fact['table'] in catalog.entities and k in catalog.entities[fact['table']]['attributes']:
                        dim_lbl = catalog.attribute(fact['table'], k)['label']
                    else:
                        dim_lbl = k
                    norm_lbl, val_display = _format_dim_value(k, dim_lbl, values[k])
                    group_parts.append(f"{norm_lbl}={val_display}")
                group_text = '、'.join(group_parts)
                m_name = metric.get('name') or metric.get('code') or fact['name']
                unit = (' ' + metric.get('unit')) if metric.get('unit') and val is not None else ''
                num_v = values.get('__num')
                den_v = values.get('__den')
                parts_note = []
                if num_v is not None and den_v is not None:
                    m_key = metric.get('key') or metric.get('code') or ''
                    if 'release' in m_key or '放行' in m_name:
                        parts_note.append(f'正常架次 {num_v}，出港架次 {den_v}')
                    elif 'takeoff' in m_key or '起飞' in m_name:
                        parts_note.append(f'正常架次 {num_v}，执飞架次 {den_v}')
                    else:
                        parts_note.append(f'分子 {num_v}，分母 {den_v}')
                if 'n' in values:
                    parts_note.append(f'匹配 {values["n"]} 条记录')
                note_str = f"（{'，'.join(parts_note)}）" if parts_note else ''
                text = '%s%s为 %s%s%s。' % (
                    (group_text + '：') if group_text else '', m_name,
                    val_str, unit, (' ' + note_str) if note_str else '')
                if values.get('missing'):
                    text += '其中 %s 条缺少有效值。' % values['missing']
            claims.append({'text': text, 'fact_ids': [fact['id']]})
        return claims, '\n'.join(c['text'] for c in claims)
    primary = [f for f in facts if f['table'] == plan['subject']]
    record = primary[0] if primary else facts[0]
    fields = catalog.entities[record['table']]['identity_fields']
    selected = [(record['properties'][field]['label'], record['properties'][field]['value'])
                for field in fields if field in record['properties']]
    text = '%s命中 %d 条记录。' % (subject, main_total)
    binding = catalog.entities[plan['subject']]['bindings']
    identity_field = binding.get('name') or binding.get('code')
    if not identity_field:
        identity_field = next(iter(catalog.entities[plan['subject']]['identity_fields']), None)
    identities = [str(f['properties'][identity_field]['value']) for f in primary
                  if identity_field in f['properties']]
    if len(primary) > 1 and identities:
        text += '本次已查询到的%s：%s。' % (catalog.attribute(plan['subject'], identity_field)['label'],
                                             '、'.join(identities))
    elif len(primary) == 1 and _single_flight_summary(record['properties']):
        text += '当前航班：' + _single_flight_summary(record['properties']) + '。'
    elif selected:
        text += '当前提供记录：' + '；'.join('%s：%s' % item for item in selected[:5]) + '。'
    if len(primary) < main_total:
        text += '本次读取 %d / %d 条主记录，未读取部分请通过命中记录明细分页查看。' % (len(primary), main_total)
    partial = [c for c in evidence.get('coverage', []) if not c['complete'] and c['table'] != plan['subject']]
    if partial:
        text += '关联数据仅读取部分记录（%s），不能据此判断未读取记录或计算全量合计。' % '；'.join(
            '%s %d/%d 条' % (c['name'], c['provided'], c['total']) for c in partial)
    if len(primary) > 1 and identity_field in ('flight_no', 'FLIGHT_NO'):
        text += '明细列表已按航班展示时刻、航线、机位与状态；点击单条航班可查看节点详情。'
    else:
        text += '详细字段及关联记录可在下方查看；所示记录不能代表未展示记录的全部状态。'
    return [{'text': text, 'fact_ids': [f['id'] for f in primary] or [record['id']]}], text
