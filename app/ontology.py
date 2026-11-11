"""Compile database annotations and business overrides into a query catalog."""
from collections import defaultdict, deque
from copy import deepcopy
import re

from app.metadata import build_graph, fingerprint


def label(comment, fallback):
    value = re.split(r'[。；;\n]', comment or '', maxsplit=1)[0].strip()
    return value[:80] if value else fallback


def unit_from_comment(comment):
    match = re.search(r'[（(]\s*(万元|亿元|元|%|天|人|笔|条)\s*[）)]', comment or '')
    return match.group(1) if match else ''


class Catalog:
    def __init__(self, schema, overrides, settings):
        self.schema, self.overrides, self.settings = schema, deepcopy(overrides), settings
        self.tables = {t['table']: t for t in schema['tables']}
        self.entities, self.warnings = {}, []
        for name, table in self.tables.items():
            bare = table.get('name', name.split('.')[-1])
            spec = deepcopy(overrides.get('entities', {}).get(name, {}))
            excluded = bare.startswith(settings.excluded_prefixes) or bare.endswith(settings.excluded_suffixes)
            if spec.get('queryable') is False or (excluded and not spec.get('queryable')):
                continue
            attrs = {}
            for col in table['columns']:
                configured = spec.get('attributes', {}).get(col['name'], {})
                attrs[col['name']] = {
                    **col, 'label': configured.get('label', label(col.get('comment', ''), col['name'])),
                    'description': configured.get('description', col.get('comment', '')),
                    'unit': configured.get('unit', unit_from_comment(col.get('comment', ''))),
                    'enums': configured.get('enums', {}), 'source': 'database_comment',
                    **configured}
            for key, attr in spec.get('json_attributes', {}).items():
                if attr.get('column') not in attrs or not isinstance(attr.get('path'), list):
                    self.warnings.append({'table': name, 'field': key, 'reason': 'JSON 属性映射无效'})
                    continue
                attrs[key] = {'name': key, 'type': 'text', 'comment': '',
                              'label': key, 'description': '', 'unit': '', 'enums': {},
                              'source': 'configured_json_mapping', **attr}
            aliases = list(dict.fromkeys(spec.get('aliases', []) + [spec.get('name', ''),
                       table.get('comment', ''), bare]))
            self.entities[name] = {
                'name': spec.get('name', label(table.get('comment', ''), bare)),
                'aliases': [a for a in aliases if a], 'attributes': attrs,
                'identity_fields': spec.get('identity_fields', table.get('primary_key', [])),
                'bindings': spec.get('bindings', {}), 'metrics': spec.get('metrics', {}),
                'default_depth': spec.get('default_depth', 1), **spec}
            self.entities[name]['attributes'] = attrs
            self.entities[name]['aliases'] = [a for a in aliases if a]
            for role,field in self.entities[name]['bindings'].items():
                if field not in attrs:
                    self.warnings.append({'table':name,'binding':role,'field':field,'reason':'绑定字段不存在'})
            for field in self.entities[name]['identity_fields']:
                if field not in attrs:
                    self.warnings.append({'table':name,'field':field,'reason':'身份显示字段不存在'})
            for metric, cfg in self.entities[name]['metrics'].items():
                if 'field' in cfg and cfg.get('field') not in attrs:
                    self.warnings.append({'table': name, 'metric': metric, 'reason': '指标字段不存在'})
        self.metrics = deepcopy(overrides.get('metrics', {}))
        self.dimensions = deepcopy(overrides.get('dimensions', {}))
        self.scenarios = deepcopy(overrides.get('scenarios', {}))
        self.filters = deepcopy(overrides.get('filters', {}))
        self.concepts = deepcopy(overrides.get('concepts', {}))
        self.concept_metrics = deepcopy(overrides.get('concept_metrics', []))
        self.synonyms = deepcopy(overrides.get('synonyms', []))
        self.business_rules = deepcopy(overrides.get('business_rules', []))
        self.capabilities = deepcopy(overrides.get('capabilities', []))
        self.plan_templates = deepcopy(overrides.get('plan_templates', []))
        self.home_airport = deepcopy(overrides.get('home_airport', {}))
        self.operational_day = deepcopy(overrides.get('operational_day', {}))
        for name in overrides.get('entities', {}):
            if name not in self.tables:
                self.warnings.append({'table': name, 'reason': '人工定义的表不存在'})
        self.graph = build_graph(schema, overrides.get('logical_links', []),
                                 overrides.get('relationship_hints', []))
        self.adjacency = defaultdict(list)
        for edge in self.graph['links_all']:
            ft, tt = edge['from_table'], edge['to_table']
            if ft in self.entities and tt in self.entities and ft != tt:
                self.adjacency[ft].append((tt, edge))
                self.adjacency[tt].append((ft, edge))
        self.version = fingerprint({'schema': schema.get('fingerprint', schema),
                                    'ontology': overrides})

    def refresh_version(self):
        """Fingerprint the effective published catalog, including startup overlays."""
        document = self.public()
        document.pop('version', None)
        document.pop('refreshed_at', None)
        document.pop('graph', None)
        document['concept_metrics'] = self.concept_metrics
        document['plan_templates'] = self.plan_templates
        self.version = fingerprint({'schema': self.schema.get('fingerprint'), 'catalog': document})
        return self.version

    def query_capabilities(self):
        hidden = set(self.overrides.get('sensitive_fields', []))
        return {table: {
            'name': spec['name'], 'aliases': spec['aliases'],
            'description': spec.get('description') or self.tables[table].get('comment', ''),
            'grain': spec.get('grain', '一条数据库记录'),
            'identity_fields': spec.get('identity_fields', []),
            'bindings': spec['bindings'], 'semantics': spec.get('query_semantics', {}),
            'operations': ['detail', 'count', 'filter', 'order', 'top_n'],
            'metrics': [key for key, metric in self.metrics.items()
                        if metric.get('base_table') == table and metric.get('availability') not in ('PLANNED', 'UNAVAILABLE')],
            'fields': {key: attr['label'] for key, attr in spec['attributes'].items() if key not in hidden},
        } for table, spec in self.entities.items()}

    def attribute(self, table, field):
        if table in self.entities and field in self.entities[table]['attributes']:
            return self.entities[table]['attributes'][field]
        flt = getattr(self, 'filters', {}).get(field)
        if flt is not None and table in self.entities:
            vtype = flt.get('value_type', 'string')
            return {
                'name': field,
                'label': flt.get('name', field),
                'description': flt.get('name', field),
                'type': 'boolean' if vtype == 'boolean' else ('numeric' if vtype == 'number' else 'text'),
                'enums': {},
                'unit': '',
            }
        raise ValueError('字段映射不存在：%s.%s，请补充或修正本体规则' % (table, field))

    def retrieve(self, question, limit=10):
        scored = []
        for table, spec in self.entities.items():
            aliases = [a for a in spec['aliases'] if a.lower() in question.lower()]
            score = max((min(len(a), 24) * 10 for a in aliases), default=0)
            for attr in spec['attributes'].values():
                term = attr['label']
                if 2 <= len(term) <= 20 and term in question:
                    score += min(len(term), 6)
            if score:
                position = min((question.lower().find(a.lower()) for a in aliases), default=len(question))
                scored.append((score, position, table))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        return [table for _, _, table in scored[:limit]]

    def relation_targets(self, question, subject):
        return [table for table, spec in self.overrides.get('relations', {}).get(subject, {}).items()
                if table in self.entities and spec.get('status') != 'disabled'
                and any(alias in question for alias in spec.get('aliases', []))]

    def path(self, subject, target, via=None):
        relation = self.overrides.get('relations', {}).get(subject, {}).get(target, {})
        if via is None:
            via = relation.get('via')
        if subject == target:
            return []
        queue = deque([(subject, [])])
        found, shortest = [], None
        while queue:
            node, path = queue.popleft()
            if len(path) >= self.settings.max_depth or (shortest is not None and len(path) >= shortest):
                continue
            visited = {subject} | {e['table'] for e in path}
            for nxt, edge in self.adjacency.get(node, []):
                if nxt in visited:
                    continue
                route = path + [{'table': nxt, 'parent': node,
                                 'from_tab': edge['from_table'], 'to_tab': edge['to_table'],
                                 'fcol': edge['pairs'][0][0], 'tcol': edge['pairs'][0][1],
                                 'pairs': edge['pairs'], 'type': edge['type'],
                                 'note': edge['note'], 'role': edge.get('role', ''),
                                 'validation': edge['validation'],
                                 'cardinality': edge['cardinality'], 'level': len(path) + 1}]
                if nxt == target:
                    if relation.get('pairs'):
                        pairs = {(edge['from_table'] + '.' + a, edge['to_table'] + '.' + b)
                                 for a, b in edge['pairs']}
                        expected = {tuple(pair) for pair in relation['pairs']}
                        if expected != pairs and expected != {(b, a) for a, b in pairs}:
                            continue
                    if via and [e['table'] for e in route[:-1]] != via:
                        continue
                    found.append(route)
                    shortest = len(route)
                else:
                    queue.append((nxt, route))
        if not found:
            raise ValueError('缺少 %s 到 %s 的有效关系，请在本体管理中配置' % (subject, target))
        if len(found) > 1:
            raise ValueError('%s 到 %s 有多个关联路径，请指定业务角色或配置 via 路径' % (subject, target))
        return found[0]

    def links(self, subject, targets):
        if not targets:
            targets = [t for t, spec in self.overrides.get('relations', {}).get(subject, {}).items()
                       if t in self.entities and spec.get('status') != 'disabled']
            if not targets:
                targets = [table for table, _ in self.adjacency.get(subject, [])]
        if len(set(targets)) > self.settings.max_related_tables:
            raise ValueError('关联目标过多，请缩小查询范围')
        result, seen = [], set()
        for target in dict.fromkeys(targets):
            spec = self.overrides.get('relations', {}).get(subject, {}).get(target, {})
            for edge in self.path(subject, target, spec.get('via')):
                key = (edge['parent'], edge['table'], tuple(map(tuple, edge['pairs'])))
                if key not in seen:
                    seen.add(key)
                    result.append(edge)
        return result

    def context(self, question, extra=()):
        selected = list(dict.fromkeys(self.retrieve(question) + list(extra)))
        expanded = list(selected)
        for table in selected:
            expanded.extend(t for t, _ in self.adjacency.get(table, []))
        return {table: {
            'name': self.entities[table]['name'], 'description': self.entities[table].get('description', self.tables[table].get('comment', '')),
            'bindings': self.entities[table]['bindings'],
            'fields': {key: {k: val for k, val in attr.items()
                             if k in ('label', 'description', 'type', 'unit', 'enums')}
                       for key, attr in self.entities[table]['attributes'].items()},
            'metrics': self.entities[table]['metrics'],
            'relations': self.overrides.get('relations', {}).get(table, {}),
            'connections': [{ 'table': nxt, 'pairs': edge['pairs'], 'from': edge['from_table'],
                              'to': edge['to_table'], 'cardinality': edge['cardinality']}
                            for nxt, edge in self.adjacency.get(table, [])]}
                for table in dict.fromkeys(expanded) if table in self.entities}

    def resolve_ontology_hints(self, question):
        q = str(question or '').strip()
        q_low = q.lower()
        matched_synonyms = []
        concept_scores = {}
        for syn in getattr(self, 'synonyms', []) or []:
            term = str(syn.get('term') or '').strip()
            if not term or term.lower() not in q_low:
                continue
            ccode = syn.get('concept_code') or syn.get('conceptCode') or ''
            if not ccode:
                continue
            weight = float(syn.get('weight') or 1.0)
            matched_synonyms.append({'term': term, 'concept_code': ccode, 'weight': weight})
            concept_scores[ccode] = concept_scores.get(ccode, 0.0) + len(term) * weight

        matched_concepts = []
        concepts_map = getattr(self, 'concepts', {}) or {}
        for ccode, cfg in concepts_map.items():
            cname = str(cfg.get('name') or '')
            if cname and cname.lower() in q_low:
                concept_scores[ccode] = concept_scores.get(ccode, 0.0) + len(cname) * 1.2
        for ccode, score in sorted(concept_scores.items(), key=lambda item: -item[1]):
            cfg = concepts_map.get(ccode, {})
            matched_concepts.append({
                'code': ccode,
                'name': cfg.get('name', ccode),
                'concept_type': cfg.get('concept_type', ''),
                'domain': cfg.get('domain', ''),
                'score': round(score, 2),
            })

        matched_scenarios = []
        for scode, scfg in (getattr(self, 'scenarios', {}) or {}).items():
            if scfg.get('status') not in (None, '', 'PUBLISHED'):
                continue
            hits = [kw for kw in (scfg.get('trigger_keywords') or []) if kw and str(kw).lower() in q_low]
            if hits:
                best_kw = max(len(str(k)) for k in hits)
                matched_scenarios.append({
                    'code': scode,
                    'name': scfg.get('name', scode),
                    'query_type': scfg.get('query_type', 'COUNT'),
                    'metric_code': scfg.get('metric_code'),
                    'dimensions': list(scfg.get('dimensions') or []),
                    'default_filters': dict(scfg.get('default_filters') or {}),
                    'priority': int(scfg.get('priority') or 50),
                    'matched_keywords': hits,
                    '_score': (best_kw, int(scfg.get('priority') or 50)),
                })
        matched_scenarios.sort(key=lambda x: (-x['_score'][0], -x['_score'][1], x['code']))
        for item in matched_scenarios:
            item.pop('_score', None)

        return {
            'synonyms': matched_synonyms,
            'concepts': matched_concepts,
            'scenarios': matched_scenarios,
        }

    def public(self):
        return {'version': self.version, 'database': self.schema.get('database'),
                'refreshed_at': self.schema.get('refreshed_at'),
                'entities': self.entities, 'graph': self.graph, 'warnings': self.warnings,
                'metrics': getattr(self, 'metrics', {}),
                'dimensions': getattr(self, 'dimensions', {}),
                'scenarios': getattr(self, 'scenarios', {}),
                'filters': getattr(self, 'filters', {}),
                'concepts': getattr(self, 'concepts', {}),
                'synonyms': getattr(self, 'synonyms', []),
                'business_rules': getattr(self, 'business_rules', []),
                'capabilities': getattr(self, 'capabilities', []),
                'home_airport': getattr(self, 'home_airport', {}),
                'operational_day': getattr(self, 'operational_day', {}),
                'query_capabilities': self.query_capabilities(),
                'pending_business_objects': [
                    {'table':name,'description':table.get('comment',''),'reason':'尚未显式开放'}
                    for name,table in self.tables.items() if name not in self.entities
                    and (table.get('name',name).startswith(('std_','dim_')) or table.get('schema') in ('acdm','ops_ask_views'))
                    and self.overrides.get('entities',{}).get(name,{}).get('queryable') is not False]}
