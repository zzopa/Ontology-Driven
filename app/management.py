"""Versioned JSON ontology editing, optimistic concurrency and rollback."""
from datetime import datetime, timezone
import json
from threading import RLock

from app.metadata import fingerprint, save_snapshot
from app.ontology import Catalog


class OntologyStore:
    def __init__(self, path, settings):
        self.path, self.settings = path, settings
        self.history_dir = path.parent / 'ontology_history'
        self.lock = RLock()

    def load(self):
        with self.path.open(encoding='utf-8') as source:
            return json.load(source)

    def validate(self, document, schema):
        if not isinstance(document, dict) or not isinstance(document.get('entities'), dict):
            raise ValueError('本体必须包含 entities 对象')
        for table, spec in document['entities'].items():
            if not isinstance(spec, dict) or not isinstance(spec.get('aliases', []), list):
                raise ValueError('实体格式无效：' + table)
            if any(not isinstance(alias, str) or not alias.strip() for alias in spec.get('aliases', [])):
                raise ValueError('实体别名必须是非空字符串')
            for section in ('attributes','json_attributes','bindings','metrics'):
                if not isinstance(spec.get(section,{}),dict):
                    raise ValueError(table+'.'+section+' 必须是对象')
            for metric in spec.get('metrics',{}).values():
                if not isinstance(metric,dict) or not isinstance(metric.get('field'),str):
                    raise ValueError('指标必须指定字段')
                if not isinstance(metric.get('group_by',[]),list) or any(
                        not isinstance(key,str) for key in metric.get('group_by',[])):
                    raise ValueError('指标分组字段必须是字符串数组')
            for attr in spec.get('json_attributes', {}).values():
                if not isinstance(attr.get('path'), list) or not attr['path'] or any(
                        not isinstance(part, str) for part in attr['path']):
                    raise ValueError('JSON 属性必须配置非空字符串路径')
        for section in ('logical_links','relationship_hints'):
            if not isinstance(document.get(section, []), list):
                raise ValueError(section + ' 必须是数组')
            for edge in document.get(section, []):
                if not isinstance(edge,dict):
                    raise ValueError('关系定义必须是对象')
                if edge.get('status','configured') not in ('candidate','configured','confirmed','disabled','invalid'):
                    raise ValueError('关系状态无效')
                pairs = edge.get('pairs') or [[edge.get('source'), edge.get('target')]]
                if not pairs or any(len(pair) != 2 or any(
                        not isinstance(field, str) or '.' not in field for field in pair) for pair in pairs):
                    raise ValueError('关系必须填写 source/target 或完整 pairs')
        return Catalog(schema, document, self.settings)

    def save(self, document, expected, schema):
        with self.lock:
            old = self.load()
            if fingerprint(old) != expected:
                raise ValueError('本体已被其他编辑更新，请重新加载后保存')
            catalog = self.validate(document, schema)
            previous=Catalog(schema,old,self.settings)
            if any(item not in previous.warnings for item in catalog.warnings):
                raise ValueError('新增本体映射包含失效定义，请修正表、属性或身份绑定后保存')
            previous_skipped=previous.graph['stats']
            for key in ('skipped_relationship_hints','skipped_logical_links'):
                if any(item not in previous_skipped[key] for item in catalog.graph['stats'][key]):
                    raise ValueError('新关系包含不存在的字段，请修正后保存')
            for table,spec in catalog.entities.items():
                for role,field in spec['bindings'].items():
                    catalog.attribute(table,field)
                for metric in spec['metrics'].values():
                    catalog.attribute(table,metric['field'])
                    for field in metric.get('group_by',[]):
                        catalog.attribute(table,field)
            self.history_dir.mkdir(parents=True, exist_ok=True)
            revision = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + fingerprint(old)[:12]
            save_snapshot(self.history_dir / (revision + '.json'),
                          {'revision': revision, 'saved_at': datetime.now(timezone.utc).isoformat(),
                           'document': old})
            save_snapshot(self.path, document)
            return catalog

    def history(self):
        files = sorted(self.history_dir.glob('*.json'), reverse=True) if self.history_dir.exists() else []
        return [{'revision': file.stem} for file in files[:100]]

    def rollback(self, revision, expected, schema):
        known = {item['revision'] for item in self.history()}
        if revision not in known:
            raise ValueError('回滚版本不存在')
        with (self.history_dir / (revision + '.json')).open(encoding='utf-8') as source:
            document = json.load(source)['document']
        return self.save(document, expected, schema)
