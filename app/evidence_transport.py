"""Lossless, bounded model payloads; never discard queried facts to fit context.

Field definitions and source metadata are interned, not values. Oversized records
are explicitly serialized into numbered fragments (not usable as whole-record
claim evidence). UI evidence remains untouched. The budget is JSON characters.
"""
from copy import deepcopy
from dataclasses import dataclass
import json


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(',', ':'))


@dataclass
class EvidenceBatch:
    payload: dict
    evidence: dict


def evidence_batches(evidence, budget):
    if budget < 2048:
        raise ValueError('证据单批预算至少为 2048 字符；不能通过丢弃查询记录来满足预算')
    originals = {fact['id']: fact for fact in evidence['facts']}
    definitions, sources, meta_keys, source_keys = {}, {}, {}, {}
    relation_defs, relation_keys = {}, {}

    def intern(value, index, values, prefix):
        key = dumps(value)
        if key not in index:
            token = prefix + str(len(index) + 1)
            index[key] = token
            values[token] = deepcopy(value)
        return index[key]

    def encode(fact):
        item = deepcopy(fact)
        if fact['kind'] == 'record':
            item['properties'] = []
            for field, prop in fact['properties'].items():
                meta = {k: v for k, v in prop.items() if k != 'value'}
                token = intern({'field': field, 'attributes': meta}, meta_keys, definitions, 'D')
                item['properties'].append([token, deepcopy(prop['value'])])
        if 'source' in fact:
            common = {k: v for k, v in fact['source'].items() if k != 'record_key'}
            item['source'] = {'definition': intern(common, source_keys, sources, 'S')}
            if 'record_key' in fact['source']:
                item['source']['record_key'] = deepcopy(fact['source']['record_key'])
        return item

    encoded = [encode(fact) for fact in evidence['facts']]
    main_table = evidence.get('scope', {}).get('main_table')
    if not main_table and evidence.get('coverage'):
        main_table = evidence['coverage'][0]['table']
    # Main records always precede ancillary records, irrespective of input order.
    encoded.sort(key=lambda f: f.get('table') != main_table)
    encoded_edges = [{key: edge[key] for key in ('source', 'target')} | {
        'definition': intern({k: v for k, v in edge.items() if k not in ('source', 'target')},
                             relation_keys, relation_defs, 'L')}
        for edge in evidence.get('relationships', [])]

    def reference(fid):
        original = originals[fid]
        # Endpoint identities help interpret a relation without repeating the
        # complete payload. They are not counted as fully delivered records.
        props = {field: prop for field, prop in original.get('properties', {}).items()
                 if field == evidence.get('scope', {}).get('name_field') or
                    prop.get('label') in ('姓名', '名称', '合同编号', '资产名称', '会议名称')}
        return encode({'id': fid, 'kind': original['kind'], 'table': original['table'],
                       'name': original.get('name', original['table']), 'properties': props})
    base = {k: deepcopy(v) for k, v in evidence.items()
            if k not in ('facts', 'relationships', 'coverage', 'scope')}

    def make(facts=(), relationships=(), fragments=()):
        ids = {f['id'] for f in facts}
        endpoint_ids = {e[key] for e in relationships for key in ('source', 'target')} - ids
        references = [reference(fid) for fid in sorted(endpoint_ids)]
        all_records = list(facts) + references
        meta_ids = {p[0] for f in all_records for p in f.get('properties', [])}
        source_ids = {f['source']['definition'] for f in facts if 'source' in f}
        link_ids = {e['definition'] for e in relationships}
        counts = {}
        for f in facts:
            counts[f['table']] = counts.get(f['table'], 0) + 1
        coverage = [{**c, 'query_provided': c['provided'],
                     'provided': counts.get(c['table'], 0),
                     'complete': counts.get(c['table'], 0) == c['total']}
                    for c in evidence.get('coverage', [])]
        scope = {**deepcopy(evidence.get('scope', {})), 'main_table': main_table,
                 'complete_records': all(c['complete'] for c in coverage),
                 'delivery_scope': '当前批次；不能把本批记录数当作查询总数',
                 'query_complete_records': evidence.get('scope', {}).get('complete_records', False)}
        payload = {**deepcopy(base), 'format': 'lossless-evidence-v1', 'scope': scope,
                   'coverage': coverage, 'facts': list(facts),
                   'field_definitions': {k: definitions[k] for k in sorted(meta_ids)},
                   'sources': {k: sources[k] for k in sorted(source_ids)},
                   'relation_definitions': {k: relation_defs[k] for k in sorted(link_ids)},
                   'fact_references': references,
                   'relationships': list(relationships), 'record_fragments': list(fragments),
                   'provided_fact_count': len(ids), 'total_fact_count': len(originals)}
        supplied = {**deepcopy(base), 'scope': deepcopy(scope), 'coverage': deepcopy(coverage),
                    'facts': [deepcopy(originals[f['id']]) for f in facts],
                    'relationships': [{**{key: edge[key] for key in ('source','target')},
                                       **deepcopy(relation_defs[edge['definition']])} for edge in relationships]}
        for ref in references:
            supplied['facts'].append({'id': ref['id'], 'kind': ref['kind'], 'table': ref['table'],
                'name': ref['name'], 'properties': {definitions[p[0]]['field']:
                    {**deepcopy(definitions[p[0]]['attributes']), 'value': deepcopy(p[1])}
                    for p in ref.get('properties', [])}})
        return EvidenceBatch(payload, supplied)

    # Reserve enough room for batch numbering even when there are many fragments.
    def fits(batch, ceiling=None):
        return len(dumps({**batch.payload, 'batch': {'index': 99999999, 'count': 99999999}})) <= (
            budget if ceiling is None else ceiling)

    if not fits(make()):
        raise ValueError('查询范围说明超过单批证据预算；请提高 evidence_budget，数据未被静默丢弃')
    batches, current = [], make()
    # A small bounded margin for THIS batch's joins/endpoint identities, not an
    # upfront reservation for the entire graph. Records still spill losslessly.
    record_ceiling = budget - min(6000,budget//8) if encoded_edges else budget
    for fact in encoded:
        candidate = make(current.payload['facts'] + [fact])
        if fits(candidate,record_ceiling):
            current = candidate
            continue
        if current.payload['facts']:
            batches.append(current)
            current = make()
        single = make([fact])
        if fits(single):
            current = single
            continue
        # Do not slice a value into an unmarked fake value, or skip the record.
        # Send the entire original JSON in contiguous, numbered string fragments.
        serialized, offset, parts = dumps(originals[fact['id']]), 0, []
        while offset < len(serialized):
            low, high, best = 1, len(serialized) - offset, 0
            while low <= high:
                size = (low + high) // 2
                fragment = {'fact_id': fact['id'], 'table': fact['table'],
                            'encoding': 'original_fact_json', 'part': 99999999,
                            'parts': 99999999, 'text': serialized[offset:offset + size]}
                if fits(make(fragments=[fragment])):
                    best, low = size, size + 1
                else:
                    high = size - 1
            if not best:
                raise ValueError('证据预算不足以传递分片；原始数据仍完整保留在查询明细中')
            parts.append(serialized[offset:offset + best])
            offset += best
        for i, part in enumerate(parts, 1):
            batches.append(make(fragments=[{'fact_id': fact['id'], 'table': fact['table'],
                'encoding': 'original_fact_json', 'part': i, 'parts': len(parts), 'text': part}]))
    if current.payload['facts']:
        batches.append(current)

    # Add relationships AFTER records, without reserving all edges up front.
    # Cross-batch endpoints use explicitly labelled identity references; their
    # complete values have already been included in the record batches above.
    for edge in encoded_edges:
        if edge['source'] not in originals or edge['target'] not in originals:
            raise ValueError('真实关联关系的端点缺少来源记录，不能静默丢弃该关联')
        placed = False
        for i, batch in enumerate(batches):
            ids = {f['id'] for f in batch.payload['facts']}
            if edge['source'] in ids or edge['target'] in ids:
                candidate = make(batch.payload['facts'], batch.payload['relationships'] + [edge])
                if fits(candidate):
                    batches[i], placed = candidate, True
                    break
        if placed:
            continue
        # Combine overflow edges to avoid one provider request per edge.
        if batches and batches[-1].payload['relationships'] and not batches[-1].payload['record_fragments']:
            batch = batches[-1]
            candidate = make(batch.payload['facts'], batch.payload['relationships'] + [edge])
            if fits(candidate):
                batches[-1] = candidate
                continue
        candidate = make(relationships=[edge])
        if fits(candidate):
            batches.append(candidate)
        else:
            raise ValueError('关联描述超过证据预算，请提高 evidence_budget；不返回假完整证据')
    for i, batch in enumerate(batches, 1):
        batch.payload['batch'] = {'index': i, 'count': len(batches)}
        assert len(dumps(batch.payload)) <= budget
    return batches


def restore_batches(batches):
    """Verification helper: reconstruct original facts/edges, including long values."""
    facts, fragments, relationships = {}, {}, []
    for batch in batches:
        payload = batch.payload if isinstance(batch, EvidenceBatch) else batch
        for encoded in payload['facts']:
            fact = deepcopy(encoded)
            if fact['kind'] == 'record':
                fact['properties'] = {payload['field_definitions'][prop[0]]['field']:
                    {**deepcopy(payload['field_definitions'][prop[0]]['attributes']), 'value': deepcopy(prop[1])}
                    for prop in fact['properties']}
            if 'source' in fact:
                source = fact['source']
                fact['source'] = deepcopy(payload['sources'][source['definition']])
                if 'record_key' in source:
                    fact['source']['record_key'] = deepcopy(source['record_key'])
            if fact['id'] in facts and facts[fact['id']] != fact:
                raise ValueError('同一事实的跨批数据不一致')
            facts[fact['id']] = fact
        for part in payload['record_fragments']:
            fragments.setdefault(part['fact_id'], {})[part['part']] = part
        for edge in payload['relationships']:
            relationships.append({**{key: edge[key] for key in ('source','target')},
                                  **deepcopy(payload['relation_definitions'][edge['definition']])})
    for fid, parts in fragments.items():
        count = next(iter(parts.values()))['parts']
        if set(parts) != set(range(1, count + 1)):
            raise ValueError('长记录分片不完整')
        fact = json.loads(''.join(parts[i]['text'] for i in range(1, count + 1)))
        if fid in facts and facts[fid] != fact:
            raise ValueError('分片与完整事实不一致')
        facts[fid] = fact
    return list(facts.values()), relationships
