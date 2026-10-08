"""Create traceable record facts and validate model claims against supplied evidence."""
from datetime import datetime, timezone
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

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
    if plan['intent'] in ('count','sum'):
        target = plan.get('aggregate_target') or plan['subject']
        facts = [{'id': 'A%d' % (i+1), 'kind': 'aggregate', 'table': target,
                  'name': catalog.entities[target]['name'], 'values': json_safe(row),
                  'metric': metric, 'source': {'database': catalog.schema['database'],
                    'table': target, 'queried_at': now, 'scope': '全部匹配记录'}}
                 for i, row in enumerate(main_rows)]
        return {'version': catalog.version, 'queried_at': now, 'facts': facts,
                'relationships': [], 'scope': {'complete_aggregate': True}, 'coverage': []}
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


def fallback_claims(catalog, plan, evidence, main_total):
    subject = catalog.entities[plan['subject']]['name']
    if not evidence['facts']:
        return [], '按当前数据库与查询条件，未命中%s记录。' % subject
    facts = evidence['facts']
    if plan['intent'] in ('count','sum'):
        if len(facts)>8:
            text='%s匹配 %d 条记录，共 %d 个统计分组。完整分组结果及口径见下方表格。' % (subject,main_total,len(facts))
            if plan['intent']=='sum':
                text+='各组金额独立展示，不混合不同币种或业务类型。'
            return [{'text':text,'fact_ids':[f['id'] for f in facts]}],text
        claims = []
        for fact in facts:
            values = fact['values']
            if plan['intent'] == 'count':
                groups = [key for key in values if key != 'n']
                group_text = '、'.join('%s=%s' % ('统计期间' if k == '__period' else catalog.attribute(fact['table'],k)['label'],
                                      values[k] if values[k] is not None else '未标注') for k in groups)
                text = '%s%s命中 %s 条记录。' % ((group_text+'：') if group_text else '', fact['name'], values['n'])
            else:
                metric = fact['metric']
                groups = [key for key in values if key not in ('total','mx','n','missing','invalid')]
                group_text = '、'.join('%s=%s' % ('统计期间' if k == '__period' else catalog.attribute(fact['table'], k)['label'],
                                      values[k] if values[k] is not None else '未标注') for k in groups)
                text = '%s%s为 %s %s，参与 %s 条记录。' % (
                    (group_text + '：') if group_text else '', metric['name'],
                    values['total'], metric.get('unit',''), values['n'])
                if values.get('missing'):
                    text += '其中 %s 条缺少金额值，合计仅包含有效非空金额。' % values['missing']
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
    elif selected:
        text += '当前提供记录：' + '；'.join('%s：%s' % item for item in selected[:5]) + '。'
    if len(primary) < main_total:
        text += '本次读取 %d / %d 条主记录，未读取部分请通过命中记录明细分页查看。' % (len(primary), main_total)
    partial = [c for c in evidence.get('coverage', []) if not c['complete'] and c['table'] != plan['subject']]
    if partial:
        text += '关联数据仅读取部分记录（%s），不能据此判断未读取记录或计算全量合计。' % '；'.join(
            '%s %d/%d 条' % (c['name'], c['provided'], c['total']) for c in partial)
    text += '详细字段及关联记录可在下方查看；所示记录不能代表未展示记录的全部状态。'
    return [{'text': text, 'fact_ids': [f['id'] for f in primary] or [record['id']]}], text
