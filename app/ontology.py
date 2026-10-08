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
            for role,field in self.entities[name]['bindings'].items():
                if field not in attrs:
                    self.warnings.append({'table':name,'binding':role,'field':field,'reason':'绑定字段不存在'})
            for field in self.entities[name]['identity_fields']:
                if field not in attrs:
                    self.warnings.append({'table':name,'field':field,'reason':'身份显示字段不存在'})
            for metric, cfg in self.entities[name]['metrics'].items():
                if cfg.get('field') not in attrs:
                    self.warnings.append({'table': name, 'metric': metric, 'reason': '指标字段不存在'})
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

    def attribute(self, table, field):
        if table not in self.entities or field not in self.entities[table]['attributes']:
            raise ValueError('字段映射不存在：%s.%s，请补充或修正本体规则' % (table, field))
        return self.entities[table]['attributes'][field]

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
        if via is None:
            via = self.overrides.get('relations', {}).get(subject, {}).get(target, {}).get('via')
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
            'name': self.entities[table]['name'], 'description': self.tables[table].get('comment', ''),
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

    def public(self):
        return {'version': self.version, 'database': self.schema.get('database'),
                'refreshed_at': self.schema.get('refreshed_at'),
                'entities': self.entities, 'graph': self.graph, 'warnings': self.warnings}
