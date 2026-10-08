"""Form-based CRUD over the existing authoritative ontology, not a second copy."""
from copy import deepcopy
from hashlib import sha256

from app.admin.store import text


def entry_id(kind, table, key):
    return sha256((kind + ':' + table + ':' + key).encode()).hexdigest()[:24]


def entries(document, catalog):
    result = []
    for alias, code in document.get('enterprise_aliases', {}).items():
        result.append({'kind': 'company_alias', 'table': '', 'key': alias, 'value': code, 'description': ''})
    for table, spec in document.get('entities', {}).items():
        for alias in spec.get('aliases', []):
            result.append({'kind': 'entity_alias', 'table': table, 'key': alias, 'value': catalog.entities.get(table, {}).get('name', table), 'description': ''})
        for key, attr in spec.get('attributes', {}).items():
            result.append({'kind': 'field', 'table': table, 'key': key, 'value': attr.get('label', key),
                           'description': attr.get('description', ''), 'unit': attr.get('unit', '')})
        for key, metric in spec.get('metrics', {}).items():
            result.append({'kind': 'metric', 'table': table, 'key': key, 'value': metric.get('name', key),
                           'description': metric.get('description', ''), 'field': metric.get('field', ''),
                           'unit': metric.get('unit', ''), 'group_by': metric.get('group_by', [])})
    return [{**item, 'id': entry_id(item['kind'], item['table'], item['key'])} for item in result]


def apply(document, catalog, data, old_id=None, delete=False, enterprise_codes=None):
    draft = deepcopy(document)
    previous = next((item for item in entries(document, catalog) if item['id'] == old_id), None) if old_id else None
    if old_id and not previous:
        raise ValueError('词条已变化或不存在，请刷新')
    if previous:
        kind, table, key = previous['kind'], previous['table'], previous['key']
        if kind == 'company_alias':
            del draft['enterprise_aliases'][key]
        elif kind == 'entity_alias':
            draft['entities'][table]['aliases'].remove(key)
        else:
            del draft['entities'][table]['attributes' if kind == 'field' else 'metrics'][key]
    if delete:
        return draft
    kind = data.get('kind')
    key, value = text(data.get('key'), '词条', 160), text(data.get('value'), '名称/标准值', 200)
    table = data.get('table', '')
    if kind not in ('company_alias', 'entity_alias', 'field', 'metric'):
        raise ValueError('词条类别无效')
    if kind != 'company_alias' and table not in catalog.entities:
        raise ValueError('必须选择当前数据库中的业务对象')
    if any(item['id'] == entry_id(kind, table if kind != 'company_alias' else '', key) for item in entries(draft, catalog)):
        raise ValueError('词条已经存在，请编辑现有词条')
    if kind == 'company_alias':
        if enterprise_codes is not None and value not in enterprise_codes:
            raise ValueError('企业代码不在当前数据库的企业目录中，请核对规范代码')
        draft.setdefault('enterprise_aliases', {})[key] = value
    else:
        spec = draft.setdefault('entities', {}).setdefault(table, {})
        if kind == 'entity_alias':
            collisions = [name for name, entity in catalog.entities.items() if name != table and key in entity.get('aliases', [])]
            if collisions:
                raise ValueError('别名与其他业务对象冲突，请使用更明确的名称')
            spec.setdefault('aliases', []).append(key)
        else:
            description = text(data.get('description', ''), '业务解释', 2000, required=False)
            unit = text(data.get('unit', ''), '单位', 40, required=False)
            if kind == 'field':
                attr = catalog.attribute(table, key)
                if attr.get('column'):
                    raise ValueError('JSON 属性请在业务本体高级编辑中维护')
                prior = document.get('entities', {}).get(table, {}).get('attributes', {}).get(key, {})
                spec.setdefault('attributes', {})[key] = {**prior, 'label': value, 'description': description, 'unit': unit}
            else:
                field = text(data.get('field'), '指标字段', 160)
                catalog.attribute(table, field)
                groups = data.get('group_by', [])
                if not isinstance(groups, list) or any(not isinstance(group, str) for group in groups):
                    raise ValueError('分组字段无效')
                for group in groups:
                    catalog.attribute(table, group)
                prior = document.get('entities', {}).get(table, {}).get('metrics', {}).get(key, {})
                spec.setdefault('metrics', {})[key] = {**prior, 'name': value, 'field': field, 'unit': unit, 'description': description, 'group_by': groups}
    return draft
