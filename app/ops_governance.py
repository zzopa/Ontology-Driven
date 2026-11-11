"""Governance store and runtime ontology bridge modeled after ops-ask."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from app import core
from app.agent.operational_day import get_operational_window
from app.config import settings
from app.sync import pg_sink

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]{0,79}$')
_SQL_FORBIDDEN = re.compile(
    r';|--|/\*|\*/|\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|EXECUTE|CALL|VACUUM|DO|PG_SLEEP|PG_READ_FILE|LO_IMPORT|LO_EXPORT)\b',
    re.IGNORECASE,
)

DEFAULT_HOME_AIRPORT = {
    'iataCode': 'WUH',
    'cityName': '武汉',
    'displayName': '武汉天河',
    'aliases': ['天河', '武汉机场', '武汉天河机场'],
}

DEFAULT_OPERATIONAL_DAY = {
    'enabled': True,
    'startHour': 5,
    'timezone': 'Asia/Shanghai',
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _current_doc() -> dict[str, Any]:
    if core.CATALOG is not None:
        return core.ontology_document()['document']
    try:
        return core.STORE.load()
    except Exception:
        return {}


def _as_json(val, fallback):
    if val is None:
        return deepcopy(fallback)
    if isinstance(val, str):
        try:
            return json.loads(val)
        except ValueError:
            return deepcopy(fallback)
    return deepcopy(val)


def _as_iso(val):
    if val is None:
        return None
    if hasattr(val, 'isoformat'):
        return val.isoformat()
    return str(val)


def _validate_code(value: Any, label: str = '编码') -> str:
    text = str(value or '').strip()
    if not _CODE_RE.fullmatch(text):
        raise ValueError(f'{label}仅允许字母/数字/下划线/点/短横线，且以字母或下划线开头')
    return text


def _validate_sql_expr(value: Any, label: str = 'SQL 表达式', required: bool = True) -> str:
    text = str(value or '').strip()
    if not text:
        if required:
            raise ValueError(f'{label}不能为空')
        return ''
    if len(text) > 2000 or _SQL_FORBIDDEN.search(text):
        raise ValueError(f'{label}包含不安全或禁止的 SQL 语法')
    return text


def boundary_label_for(cfg: dict[str, Any]) -> str:
    if not cfg.get('enabled', True):
        return '已停用（按自然日 00:00–23:59 统计）'
    start = int(cfg.get('startHour', 4)) % 24
    end = (start - 1) % 24
    return f'{start:02d}:00 至次日 {end:02d}:59'


def _has_pg_governance() -> bool:
    return settings.data_source == 'ops_ask'


@contextmanager
def _pg_read():
    conn = core.connect_db()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _pg_write():
    conn = pg_sink.connect_pg_write()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sync_seq(cur, table: str):
    if not re.fullmatch(r'[a-z_]+', table):
        raise ValueError('无效的表名')
    cur.execute(
        f"SELECT setval(pg_get_serial_sequence('public.{table}', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM public.{table}), 1), 1), true)"
    )


def _fetch_dicts(cur, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _live_data_coverage(cur=None) -> dict[str, Any]:
    if not _has_pg_governance():
        cov = _current_doc().get('coverage') or {}
        return {
            'start': cov.get('start') or cov.get('start_date') or '',
            'end': cov.get('end') or cov.get('end_date') or '',
            'totalRecords': 0,
            'sourceTable': '',
            'empty': True,
        }

    def _run(c):
        rows = _fetch_dicts(
            c,
            'SELECT MIN(flight_date)::date::text AS min_d, MAX(flight_date)::date::text AS max_d, COUNT(*) AS total '
            'FROM public.std_flight_core',
        )
        row = rows[0] if rows else {}
        total = int(row.get('total') or 0)
        return {
            'start': row.get('min_d') or '',
            'end': row.get('max_d') or '',
            'totalRecords': total,
            'sourceTable': 'std_flight_core',
            'empty': total == 0,
        }

    try:
        if cur is not None:
            return _run(cur)
        with _pg_read() as conn:
            c = conn.cursor()
            try:
                return _run(c)
            finally:
                c.close()
    except Exception:
        logger.debug('读取 std_flight_core 覆盖窗口失败', exc_info=True)
        return {'start': '', 'end': '', 'totalRecords': 0, 'sourceTable': 'std_flight_core', 'empty': True}


# ---------------------------------------------------------------------------
# Metrics, Dimensions, Filters
# ---------------------------------------------------------------------------

def list_metrics() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT id, code, name, domain, description, formula_desc, aggregation, unit, rule_code, '
                        'availability, phase, status, base_table, joins_json, aggregate_expression, value_alias, '
                        'required_fields, default_filters, date_field, created_at, updated_at '
                        'FROM public.metrics_metric ORDER BY phase ASC, id ASC',
                    )
                    return [
                        {
                            'id': r['id'],
                            'code': r['code'],
                            'name': r['name'],
                            'domain': r['domain'] or 'FIMS',
                            'description': r.get('description') or '',
                            'formulaDesc': r.get('formula_desc') or '',
                            'aggregation': r.get('aggregation') or 'count',
                            'unit': r.get('unit') or '',
                            'ruleCode': r.get('rule_code') or '',
                            'availability': r.get('availability') or 'AVAILABLE',
                            'phase': int(r.get('phase') or 1),
                            'status': r.get('status') or 'PUBLISHED',
                            'baseTable': r.get('base_table') or 'std_flight_core',
                            'joinsJson': _as_json(r.get('joins_json'), []),
                            'aggregateExpression': r.get('aggregate_expression') or 'COUNT(*)',
                            'valueAlias': r.get('value_alias') or r['code'],
                            'requiredFields': _as_json(r.get('required_fields'), []),
                            'defaultFilters': _as_json(r.get('default_filters'), {}),
                            'dateField': r.get('date_field') or 'flight_date',
                            'aliases': _as_json(_current_doc().get('metrics', {}).get(r['code'], {}).get('aliases'), []),
                            'createdAt': _as_iso(r.get('created_at')),
                            'updatedAt': _as_iso(r.get('updated_at')),
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG metrics_metric 查询降级到 ontology.json', exc_info=True)

    doc_metrics = _current_doc().get('metrics', {})
    return [
        {
            'id': idx,
            'code': code,
            'name': m.get('name', code),
            'domain': m.get('domain', 'FIMS'),
            'description': m.get('description', ''),
            'formulaDesc': m.get('formula_desc', ''),
            'aggregation': m.get('aggregation', 'count'),
            'unit': m.get('unit', ''),
            'ruleCode': m.get('rule_code', ''),
            'availability': m.get('availability', 'AVAILABLE'),
            'phase': int(m.get('phase', 1)),
            'status': m.get('status', 'PUBLISHED'),
            'baseTable': m.get('base_table', 'std_flight_core'),
            'joinsJson': _as_json(m.get('joins_json'), []),
            'aggregateExpression': m.get('aggregate_expression', 'COUNT(*)'),
            'valueAlias': m.get('value_alias', code),
            'requiredFields': _as_json(m.get('required_fields'), []),
            'defaultFilters': _as_json(m.get('default_filters'), {}),
            'dateField': m.get('date_field', 'flight_date'),
            'aliases': m.get('aliases', []),
        }
        for idx, (code, m) in enumerate(doc_metrics.items(), start=1)
    ]


def save_metric(payload: dict[str, Any]) -> dict[str, Any]:
    code = _validate_code(payload.get('code'), '指标编码')
    name = str(payload.get('name') or '').strip()
    if not name or len(name) > 100:
        raise ValueError('指标名称长度需在 1–100 之间')
    domain = str(payload.get('domain') or 'FIMS').strip().upper()[:30]
    formula_desc = str(payload.get('formulaDesc') or payload.get('formula_desc') or '').strip()[:1000]
    description = str(payload.get('description') or formula_desc).strip()[:2000]
    aggregation = str(payload.get('aggregation') or 'count').strip().lower()[:30]
    unit = str(payload.get('unit') or '').strip()[:30]
    rule_code = str(payload.get('ruleCode') or payload.get('rule_code') or '').strip()[:64] or None
    availability = str(payload.get('availability') or 'AVAILABLE').strip().upper()
    if availability not in ('AVAILABLE', 'PARTIAL', 'PLANNED', 'UNAVAILABLE'):
        raise ValueError('可用性状态无效')
    status = str(payload.get('status') or 'PUBLISHED').strip().upper()
    if status not in ('PUBLISHED', 'DRAFT', 'ARCHIVED'):
        raise ValueError('指标发布状态无效')
    phase = max(1, min(9, int(payload.get('phase') or 1)))
    base_table = _validate_code(payload.get('baseTable') or payload.get('base_table') or 'std_flight_core', '基准表')
    agg_expr = _validate_sql_expr(payload.get('aggregateExpression') or payload.get('aggregate_expression') or 'COUNT(*)', '聚合表达式')
    value_alias = _validate_code(payload.get('valueAlias') or payload.get('value_alias') or code, '结果别名')
    date_field = _validate_code(payload.get('dateField') or payload.get('date_field') or 'flight_date', '日期字段')
    default_filters = payload.get('defaultFilters') if isinstance(payload.get('defaultFilters'), dict) else _as_json(payload.get('default_filters'), {})
    joins_json = payload.get('joinsJson') if isinstance(payload.get('joinsJson'), list) else _as_json(payload.get('joins_json'), [])
    required_fields = payload.get('requiredFields') if isinstance(payload.get('requiredFields'), list) else _as_json(payload.get('required_fields'), [date_field])

    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                _sync_seq(cur, 'metrics_metric')
                cur.execute(
                    'INSERT INTO public.metrics_metric '
                    '(code, name, domain, description, formula_desc, aggregation, unit, rule_code, availability, phase, status, '
                    'base_table, joins_json, aggregate_expression, value_alias, required_fields, default_filters, date_field, created_at, updated_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, %s, now(), now()) '
                    'ON CONFLICT (code) DO UPDATE SET '
                    'name=EXCLUDED.name, domain=EXCLUDED.domain, description=EXCLUDED.description, formula_desc=EXCLUDED.formula_desc, '
                    'aggregation=EXCLUDED.aggregation, unit=EXCLUDED.unit, rule_code=EXCLUDED.rule_code, availability=EXCLUDED.availability, '
                    'phase=EXCLUDED.phase, status=EXCLUDED.status, base_table=EXCLUDED.base_table, joins_json=EXCLUDED.joins_json, '
                    'aggregate_expression=EXCLUDED.aggregate_expression, value_alias=EXCLUDED.value_alias, '
                    'required_fields=EXCLUDED.required_fields, default_filters=EXCLUDED.default_filters, date_field=EXCLUDED.date_field, updated_at=now()',
                    (
                        code, name, domain, description, formula_desc, aggregation, unit, rule_code,
                        availability, phase, status, base_table,
                        json.dumps(joins_json, ensure_ascii=False),
                        agg_expr, value_alias,
                        json.dumps(required_fields, ensure_ascii=False),
                        json.dumps(default_filters, ensure_ascii=False),
                        date_field,
                    ),
                )
            finally:
                cur.close()
        return next((m for m in list_metrics() if m['code'] == code), {'code': code, 'name': name})

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    doc.setdefault('metrics', {})[code] = {
        'name': name, 'domain': domain, 'description': description, 'formula_desc': formula_desc,
        'aggregation': aggregation, 'unit': unit, 'rule_code': rule_code, 'availability': availability,
        'phase': phase, 'status': status, 'base_table': base_table, 'joins_json': joins_json,
        'aggregate_expression': agg_expr, 'value_alias': value_alias, 'required_fields': required_fields,
        'default_filters': default_filters, 'date_field': date_field,
        'aliases': payload.get('aliases') if isinstance(payload.get('aliases'), list) else [name],
    }
    core.update_ontology(doc, state['revision'])
    return next((m for m in list_metrics() if m['code'] == code), {'code': code, 'name': name})


def delete_metric(code: str) -> None:
    target = _validate_code(code, '指标编码')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM public.metrics_metric WHERE code = %s', (target,))
                if cur.rowcount == 0:
                    raise ValueError('指标不存在')
            finally:
                cur.close()
        return
    state = core.ontology_document()
    doc = deepcopy(state['document'])
    if target not in doc.get('metrics', {}):
        raise ValueError('指标不存在')
    del doc['metrics'][target]
    core.update_ontology(doc, state['revision'])


def list_dimensions() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT id, code, name, expression, alias, required_field, status '
                        'FROM public.metrics_dimension ORDER BY id ASC',
                    )
                    return [
                        {
                            'id': r['id'],
                            'code': r['code'],
                            'name': r['name'],
                            'expression': r['expression'],
                            'alias': r['alias'] or r['code'],
                            'requiredField': r.get('required_field') or '',
                            'status': r.get('status') or 'PUBLISHED',
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG metrics_dimension 查询降级到 ontology.json', exc_info=True)

    dims = _current_doc().get('dimensions', {})
    return [
        {
            'id': idx,
            'code': code,
            'name': d.get('name', code),
            'expression': d.get('expression', ''),
            'alias': d.get('alias', code),
            'requiredField': d.get('required_field', ''),
            'status': d.get('status', 'PUBLISHED'),
        }
        for idx, (code, d) in enumerate(dims.items(), start=1)
    ]


def save_dimension(payload: dict[str, Any]) -> dict[str, Any]:
    code = _validate_code(payload.get('code'), '维度编码')
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('维度名称不能为空')
    expression = _validate_sql_expr(payload.get('expression'), '维度 SQL 表达式')
    alias = _validate_code(payload.get('alias') or code, '输出别名')
    required_field = str(payload.get('requiredField') or payload.get('required_field') or '').strip()[:80]
    status = str(payload.get('status') or 'PUBLISHED').strip().upper()
    if status not in ('PUBLISHED', 'DRAFT', 'ARCHIVED'):
        raise ValueError('状态无效')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                _sync_seq(cur, 'metrics_dimension')
                cur.execute(
                    'INSERT INTO public.metrics_dimension (code, name, expression, alias, required_field, status) '
                    'VALUES (%s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, expression=EXCLUDED.expression, '
                    'alias=EXCLUDED.alias, required_field=EXCLUDED.required_field, status=EXCLUDED.status',
                    (code, name, expression, alias, required_field, status),
                )
            finally:
                cur.close()
        return next((d for d in list_dimensions() if d['code'] == code), {'code': code})

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    doc.setdefault('dimensions', {})[code] = {
        'name': name, 'expression': expression, 'alias': alias,
        'required_field': required_field, 'status': status,
    }
    core.update_ontology(doc, state['revision'])
    return next((d for d in list_dimensions() if d['code'] == code), {'code': code})


def delete_dimension(code: str) -> None:
    target = _validate_code(code, '维度编码')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM public.metrics_dimension WHERE code = %s', (target,))
                if cur.rowcount == 0:
                    raise ValueError('维度不存在')
            finally:
                cur.close()
        return
    state = core.ontology_document()
    doc = deepcopy(state['document'])
    if target not in doc.get('dimensions', {}):
        raise ValueError('维度不存在')
    del doc['dimensions'][target]
    core.update_ontology(doc, state['revision'])


def list_filters() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT id, code, name, column_expr, operator, join_indicator, value_type, status '
                        'FROM public.metrics_filter ORDER BY id ASC',
                    )
                    return [
                        {
                            'id': r['id'],
                            'code': r['code'],
                            'name': r['name'],
                            'columnExpr': r['column_expr'],
                            'operator': r['operator'] or 'eq',
                            'joinIndicator': bool(r.get('join_indicator')),
                            'valueType': r.get('value_type') or 'string',
                            'status': r.get('status') or 'PUBLISHED',
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG metrics_filter 查询降级', exc_info=True)

    flts = _current_doc().get('filters', {})
    return [
        {
            'id': idx,
            'code': code,
            'name': f.get('name', code),
            'columnExpr': f.get('column_expr', ''),
            'operator': f.get('operator', 'eq'),
            'joinIndicator': bool(f.get('join_indicator')),
            'valueType': f.get('value_type', 'string'),
            'status': f.get('status', 'PUBLISHED'),
        }
        for idx, (code, f) in enumerate(flts.items(), start=1)
    ]


def save_filter(payload: dict[str, Any]) -> dict[str, Any]:
    code = _validate_code(payload.get('code'), '筛选编码')
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('筛选名称不能为空')
    column_expr = _validate_sql_expr(payload.get('columnExpr') or payload.get('column_expr'), '筛选列表达式')
    operator = str(payload.get('operator') or 'eq').strip().lower()
    if operator not in ('eq', 'ne', 'like', 'not_like', 'exists', 'in', 'gt', 'gte', 'lt', 'lte'):
        raise ValueError('操作符仅允许 eq/ne/like/not_like/exists/in/gt/gte/lt/lte')
    value_type = str(payload.get('valueType') or payload.get('value_type') or 'string').strip().lower()
    if value_type not in ('string', 'boolean', 'number', 'date'):
        raise ValueError('值类型无效')
    join_indicator = bool(payload.get('joinIndicator') if 'joinIndicator' in payload else payload.get('join_indicator', False))
    status = str(payload.get('status') or 'PUBLISHED').strip().upper()
    if status not in ('PUBLISHED', 'DRAFT', 'ARCHIVED'):
        raise ValueError('状态无效')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                _sync_seq(cur, 'metrics_filter')
                cur.execute(
                    'INSERT INTO public.metrics_filter (code, name, column_expr, operator, join_indicator, value_type, status) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, column_expr=EXCLUDED.column_expr, '
                    'operator=EXCLUDED.operator, join_indicator=EXCLUDED.join_indicator, value_type=EXCLUDED.value_type, status=EXCLUDED.status',
                    (code, name, column_expr, operator, join_indicator, value_type, status),
                )
            finally:
                cur.close()
        return next((f for f in list_filters() if f['code'] == code), {'code': code})

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    doc.setdefault('filters', {})[code] = {
        'name': name, 'column_expr': column_expr, 'operator': operator,
        'join_indicator': join_indicator, 'value_type': value_type, 'status': status,
    }
    core.update_ontology(doc, state['revision'])
    return next((f for f in list_filters() if f['code'] == code), {'code': code})


def delete_filter(code: str) -> None:
    target = _validate_code(code, '筛选编码')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM public.metrics_filter WHERE code = %s', (target,))
                if cur.rowcount == 0:
                    raise ValueError('筛选条件不存在')
            finally:
                cur.close()
        return
    state = core.ontology_document()
    doc = deepcopy(state['document'])
    if target not in doc.get('filters', {}):
        raise ValueError('筛选条件不存在')
    del doc['filters'][target]
    core.update_ontology(doc, state['revision'])


# ---------------------------------------------------------------------------
# Ontology Concepts, Synonyms, Attributes, Scenarios, Versions
# ---------------------------------------------------------------------------

def list_concepts() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT c.id, c.code, c.name, c.parent_id, c.domain, c.concept_type, c.description, c.status, c.version, '
                        'c.created_at, c.updated_at, COALESCE(s.syn_cnt, 0) AS synonym_count '
                        'FROM public.ont_concept c '
                        'LEFT JOIN (SELECT concept_id, COUNT(*) AS syn_cnt FROM public.ont_synonym GROUP BY concept_id) s ON s.concept_id = c.id '
                        'ORDER BY c.id ASC',
                    )
                    return [
                        {
                            'id': r['id'],
                            'code': r['code'],
                            'name': r['name'],
                            'parentId': r.get('parent_id'),
                            'domain': r.get('domain') or 'FIMS',
                            'conceptType': r.get('concept_type') or 'METRIC',
                            'description': r.get('description') or '',
                            'status': r.get('status') or 'PUBLISHED',
                            'version': int(r.get('version') or 1),
                            'synonymCount': int(r.get('synonym_count') or 0),
                            'createdAt': _as_iso(r.get('created_at')),
                            'updatedAt': _as_iso(r.get('updated_at')),
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG ont_concept 查询降级', exc_info=True)

    concepts = _current_doc().get('concepts', {})
    return [
        {
            'id': idx, 'code': code, 'name': c.get('name', code), 'domain': c.get('domain', 'FIMS'),
            'conceptType': c.get('concept_type', 'METRIC'), 'description': c.get('description', ''),
            'status': c.get('status', 'PUBLISHED'), 'version': int(c.get('version', 1)), 'synonymCount': 0,
        }
        for idx, (code, c) in enumerate(concepts.items(), start=1)
    ]


def save_concept(payload: dict[str, Any]) -> dict[str, Any]:
    code = _validate_code(payload.get('code'), '概念编码')
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('概念名称不能为空')
    domain = str(payload.get('domain') or 'FIMS').strip().upper()[:30]
    concept_type = str(payload.get('conceptType') or payload.get('concept_type') or 'METRIC').strip().upper()[:30]
    description = str(payload.get('description') or '').strip()[:1000]
    status = str(payload.get('status') or 'PUBLISHED').strip().upper()
    if status not in ('PUBLISHED', 'DRAFT', 'ARCHIVED'):
        raise ValueError('概念状态无效')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                _sync_seq(cur, 'ont_concept')
                cur.execute(
                    'INSERT INTO public.ont_concept (code, name, domain, concept_type, description, status, version, created_at, updated_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s, 1, now(), now()) '
                    'ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, domain=EXCLUDED.domain, '
                    'concept_type=EXCLUDED.concept_type, description=EXCLUDED.description, status=EXCLUDED.status, '
                    'version=public.ont_concept.version + 1, updated_at=now()',
                    (code, name, domain, concept_type, description, status),
                )
            finally:
                cur.close()
        return next((c for c in list_concepts() if c['code'] == code), {'code': code})

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    doc.setdefault('concepts', {})[code] = {
        'name': name, 'domain': domain, 'concept_type': concept_type,
        'description': description, 'status': status, 'version': 1,
    }
    core.update_ontology(doc, state['revision'])
    return next((c for c in list_concepts() if c['code'] == code), {'code': code})


def delete_concept(concept_id: str) -> None:
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                if str(concept_id).isdigit():
                    cur.execute('DELETE FROM public.ont_synonym WHERE concept_id = %s', (int(concept_id),))
                    cur.execute('DELETE FROM public.ont_concept_metric WHERE concept_id = %s', (int(concept_id),))
                    cur.execute('DELETE FROM public.ont_concept WHERE id = %s', (int(concept_id),))
                else:
                    cur.execute('DELETE FROM public.ont_concept WHERE code = %s', (str(concept_id),))
                if cur.rowcount == 0:
                    raise ValueError('概念不存在')
            finally:
                cur.close()
        return
    state = core.ontology_document()
    doc = deepcopy(state['document'])
    if str(concept_id) not in doc.get('concepts', {}):
        raise ValueError('概念不存在')
    del doc['concepts'][str(concept_id)]
    core.update_ontology(doc, state['revision'])


def list_synonyms() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT s.id, s.concept_id, c.code AS concept_code, c.name AS concept_name, '
                        'c.concept_type, s.term, s.term_type, s.weight '
                        'FROM public.ont_synonym s '
                        'JOIN public.ont_concept c ON c.id = s.concept_id '
                        'ORDER BY s.id DESC',
                    )
                    return [
                        {
                            'id': r['id'],
                            'conceptId': r['concept_id'],
                            'conceptCode': r['concept_code'],
                            'conceptName': r['concept_name'],
                            'conceptType': r.get('concept_type') or '',
                            'term': r['term'],
                            'termType': r.get('term_type') or 'alias',
                            'weight': float(r.get('weight') or 1.0),
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG ont_synonym 查询降级', exc_info=True)

    syns = _current_doc().get('synonyms', [])
    return [
        {
            'id': idx,
            'conceptCode': s.get('concept_code', ''),
            'conceptName': s.get('concept_name', s.get('concept_code', '')),
            'term': s.get('term', ''),
            'termType': s.get('term_type', 'alias'),
            'weight': float(s.get('weight', 1.0)),
        }
        for idx, s in enumerate(syns, start=1)
    ]


def save_synonym(payload: dict[str, Any]) -> dict[str, Any]:
    concept_code = _validate_code(payload.get('conceptCode') or payload.get('concept_code'), '关联概念编码')
    term = str(payload.get('term') or '').strip()[:120]
    if not term:
        raise ValueError('同义词/触发词不能为空')
    term_type = str(payload.get('termType') or payload.get('term_type') or 'alias').strip()[:30]
    weight = max(0.1, min(10.0, float(payload.get('weight') or 1.0)))

    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute('SELECT id FROM public.ont_concept WHERE code = %s', (concept_code,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f'未找到概念：{concept_code}')
                concept_id = row[0]
                _sync_seq(cur, 'ont_synonym')
                cur.execute(
                    'INSERT INTO public.ont_synonym (concept_id, term, term_type, weight) '
                    'VALUES (%s, %s, %s, %s) '
                    'ON CONFLICT (concept_id, term) DO UPDATE SET term_type=EXCLUDED.term_type, weight=EXCLUDED.weight '
                    'RETURNING id',
                    (concept_id, term, term_type, weight),
                )
                sid = cur.fetchone()[0]
            finally:
                cur.close()
        return next((s for s in list_synonyms() if s['id'] == sid), {'id': sid, 'term': term, 'conceptCode': concept_code})

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    syns = [s for s in doc.get('synonyms', []) if not (s.get('concept_code') == concept_code and s.get('term') == term)]
    syns.append({'concept_code': concept_code, 'term': term, 'term_type': term_type, 'weight': weight})
    doc['synonyms'] = syns
    core.update_ontology(doc, state['revision'])
    return {'conceptCode': concept_code, 'term': term, 'termType': term_type, 'weight': weight}


def delete_synonym(synonym_id: str) -> None:
    if _has_pg_governance():
        if not str(synonym_id).isdigit():
            raise ValueError('同义词 ID 无效')
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM public.ont_synonym WHERE id = %s', (int(synonym_id),))
                if cur.rowcount == 0:
                    raise ValueError('同义词不存在')
            finally:
                cur.close()
        return
    state = core.ontology_document()
    doc = deepcopy(state['document'])
    idx = int(synonym_id) - 1 if str(synonym_id).isdigit() else -1
    syns = doc.get('synonyms', [])
    if not (0 <= idx < len(syns)):
        raise ValueError('同义词不存在')
    syns.pop(idx)
    doc['synonyms'] = syns
    core.update_ontology(doc, state['revision'])


def list_attributes() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT id, code, name, column_name, table_name, value_type, description '
                        'FROM public.ont_attribute ORDER BY id ASC',
                    )
                    return [
                        {
                            'id': r['id'],
                            'code': r['code'],
                            'name': r['name'],
                            'columnName': r['column_name'],
                            'tableName': r['table_name'],
                            'valueType': r.get('value_type') or 'string',
                            'description': r.get('description') or '',
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG ont_attribute 查询降级', exc_info=True)
    return []


def save_attribute(payload: dict[str, Any]) -> dict[str, Any]:
    code = _validate_code(payload.get('code'), '属性编码')
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('属性名称不能为空')
    column_name = _validate_sql_expr(payload.get('columnName') or payload.get('column_name'), '列/表达式')
    table_name = _validate_code(payload.get('tableName') or payload.get('table_name') or 'std_flight_core', '所属表/模式')
    value_type = str(payload.get('valueType') or payload.get('value_type') or 'string').strip()[:30]
    description = str(payload.get('description') or '').strip()[:500]
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                _sync_seq(cur, 'ont_attribute')
                cur.execute(
                    'INSERT INTO public.ont_attribute (code, name, column_name, table_name, value_type, description) '
                    'VALUES (%s, %s, %s, %s, %s, %s) '
                    'ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, column_name=EXCLUDED.column_name, '
                    'table_name=EXCLUDED.table_name, value_type=EXCLUDED.value_type, description=EXCLUDED.description',
                    (code, name, column_name, table_name, value_type, description),
                )
            finally:
                cur.close()
        return next((a for a in list_attributes() if a['code'] == code), {'code': code})
    raise ValueError('当前数据源不支持写入 ont_attribute')


def delete_attribute(attr_id: str) -> None:
    if not _has_pg_governance():
        raise ValueError('当前数据源不支持删除 ont_attribute')
    with _pg_write() as conn:
        cur = conn.cursor()
        try:
            if str(attr_id).isdigit():
                cur.execute('DELETE FROM public.ont_attribute WHERE id = %s', (int(attr_id),))
            else:
                cur.execute('DELETE FROM public.ont_attribute WHERE code = %s', (str(attr_id),))
            if cur.rowcount == 0:
                raise ValueError('属性不存在')
        finally:
            cur.close()


def list_scenarios() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT code, name, query_type, metric_code, dimensions, default_filters, '
                        'select_fields, trigger_keywords, priority, status '
                        'FROM public.ont_scenario ORDER BY priority DESC, code ASC',
                    )
                    return [
                        {
                            'code': r['code'],
                            'name': r['name'],
                            'queryType': r['query_type'],
                            'metricCode': r.get('metric_code') or '',
                            'dimensions': _as_json(r.get('dimensions'), []),
                            'defaultFilters': _as_json(r.get('default_filters'), {}),
                            'selectFields': _as_json(r.get('select_fields'), []),
                            'triggerKeywords': _as_json(r.get('trigger_keywords'), []),
                            'priority': int(r.get('priority') or 50),
                            'status': r.get('status') or 'PUBLISHED',
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG ont_scenario 查询降级', exc_info=True)

    scenarios = _current_doc().get('scenarios', {})
    return [
        {
            'code': code,
            'name': s.get('name', code),
            'queryType': s.get('query_type', 'COUNT'),
            'metricCode': s.get('metric_code', ''),
            'dimensions': _as_json(s.get('dimensions'), []),
            'defaultFilters': _as_json(s.get('default_filters'), {}),
            'selectFields': _as_json(s.get('select_fields'), []),
            'triggerKeywords': _as_json(s.get('trigger_keywords'), []),
            'priority': int(s.get('priority', 50)),
            'status': s.get('status', 'PUBLISHED'),
        }
        for code, s in scenarios.items()
    ]


def save_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    code = _validate_code(payload.get('code'), '场景编码')
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('场景名称不能为空')
    query_type = str(payload.get('queryType') or payload.get('query_type') or 'COUNT').strip().upper()
    if query_type not in ('DETAIL', 'LIST', 'COUNT', 'TREND', 'RANK', 'OVERVIEW', 'COMPARE'):
        raise ValueError('查询类型无效')
    metric_code = str(payload.get('metricCode') or payload.get('metric_code') or '').strip() or None
    dimensions = payload.get('dimensions') if isinstance(payload.get('dimensions'), list) else _as_json(payload.get('dimensions'), [])
    default_filters = payload.get('defaultFilters') if isinstance(payload.get('defaultFilters'), dict) else _as_json(payload.get('default_filters'), {})
    select_fields = payload.get('selectFields') if isinstance(payload.get('selectFields'), list) else _as_json(payload.get('select_fields'), [])
    keywords = payload.get('triggerKeywords') if isinstance(payload.get('triggerKeywords'), list) else [
        s.strip() for s in str(payload.get('triggerKeywords') or payload.get('trigger_keywords') or '').replace('，', ',').split(',') if s.strip()
    ]
    priority = max(1, min(999, int(payload.get('priority') or 70)))
    status = str(payload.get('status') or 'PUBLISHED').strip().upper()
    if status not in ('PUBLISHED', 'DRAFT', 'ARCHIVED'):
        raise ValueError('状态无效')

    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    'INSERT INTO public.ont_scenario '
                    '(code, name, query_type, metric_code, dimensions, default_filters, select_fields, trigger_keywords, priority, status) '
                    'VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s) '
                    'ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, query_type=EXCLUDED.query_type, '
                    'metric_code=EXCLUDED.metric_code, dimensions=EXCLUDED.dimensions, default_filters=EXCLUDED.default_filters, '
                    'select_fields=EXCLUDED.select_fields, trigger_keywords=EXCLUDED.trigger_keywords, priority=EXCLUDED.priority, status=EXCLUDED.status',
                    (
                        code, name, query_type, metric_code,
                        json.dumps(dimensions, ensure_ascii=False),
                        json.dumps(default_filters, ensure_ascii=False),
                        json.dumps(select_fields, ensure_ascii=False),
                        json.dumps(keywords, ensure_ascii=False),
                        priority, status,
                    ),
                )
            finally:
                cur.close()
        return next((s for s in list_scenarios() if s['code'] == code), {'code': code})

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    doc.setdefault('scenarios', {})[code] = {
        'name': name, 'query_type': query_type, 'metric_code': metric_code,
        'dimensions': dimensions, 'default_filters': default_filters,
        'select_fields': select_fields, 'trigger_keywords': keywords,
        'priority': priority, 'status': status,
    }
    core.update_ontology(doc, state['revision'])
    return next((s for s in list_scenarios() if s['code'] == code), {'code': code})


def delete_scenario(code: str) -> None:
    target = _validate_code(code, '场景编码')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM public.ont_scenario WHERE code = %s', (target,))
                if cur.rowcount == 0:
                    raise ValueError('场景不存在')
            finally:
                cur.close()
        return
    state = core.ontology_document()
    doc = deepcopy(state['document'])
    if target not in doc.get('scenarios', {}):
        raise ValueError('场景不存在')
    del doc['scenarios'][target]
    core.update_ontology(doc, state['revision'])


def list_catalog_versions() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT id, version_no, change_summary, triggered_by, published_at '
                        'FROM public.ont_catalog_version ORDER BY version_no DESC LIMIT 50',
                    )
                    return [
                        {
                            'id': r['id'],
                            'versionNo': int(r['version_no']),
                            'changeSummary': r.get('change_summary') or '',
                            'triggeredBy': r.get('triggered_by') or 'system',
                            'publishedAt': _as_iso(r.get('published_at')),
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG ont_catalog_version 查询失败', exc_info=True)
    return [{'versionNo': i + 1, 'changeSummary': h['revision'], 'triggeredBy': 'ontology_history', 'publishedAt': ''} for i, h in enumerate(core.STORE.history()[:20])]


# ---------------------------------------------------------------------------
# Rules, Capabilities, Planner Templates, Ask Settings
# ---------------------------------------------------------------------------

def list_rules() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT r.id, r.code, r.name, r.rule_type, r.domain, r.description, r.status, r.created_at, '
                        'v.version_no, v.scope_type, v.expression, v.output_mapping, v.priority, v.published '
                        'FROM public.rule_definition r '
                        'LEFT JOIN LATERAL ('
                        '  SELECT version_no, scope_type, expression, output_mapping, priority, published '
                        '  FROM public.rule_version rv WHERE rv.rule_id = r.id ORDER BY rv.version_no DESC LIMIT 1'
                        ') v ON true '
                        'ORDER BY r.id ASC',
                    )
                    return [
                        {
                            'id': r['id'],
                            'code': r['code'],
                            'name': r['name'],
                            'ruleType': r.get('rule_type') or 'derived_field',
                            'domain': r.get('domain') or 'FIMS',
                            'description': r.get('description') or '',
                            'status': r.get('status') or 'PUBLISHED',
                            'versionNo': int(r.get('version_no') or 1),
                            'scopeType': r.get('scope_type') or 'global',
                            'expression': r.get('expression') or '',
                            'outputMapping': _as_json(r.get('output_mapping'), {}),
                            'priority': int(r.get('priority') or 100),
                            'published': bool(r.get('published', True)),
                            'createdAt': _as_iso(r.get('created_at')),
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG rule_definition 查询降级', exc_info=True)

    return _as_json(_current_doc().get('business_rules'), [])


def save_rule(payload: dict[str, Any]) -> dict[str, Any]:
    code = _validate_code(payload.get('code'), '规则编码')
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('规则名称不能为空')
    rule_type = str(payload.get('ruleType') or payload.get('rule_type') or 'derived_field').strip()[:40]
    domain = str(payload.get('domain') or 'FIMS').strip().upper()[:30]
    description = str(payload.get('description') or '').strip()[:1000]
    status = str(payload.get('status') or 'PUBLISHED').strip().upper()
    if status not in ('PUBLISHED', 'DRAFT', 'ARCHIVED'):
        raise ValueError('状态无效')
    expression = str(payload.get('expression') or '').strip()[:1000]
    scope_type = str(payload.get('scopeType') or payload.get('scope_type') or 'global').strip()[:30]
    priority = max(1, min(999, int(payload.get('priority') or 100)))
    output_mapping = payload.get('outputMapping') if isinstance(payload.get('outputMapping'), dict) else _as_json(payload.get('output_mapping'), {})

    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                _sync_seq(cur, 'rule_definition')
                _sync_seq(cur, 'rule_version')
                cur.execute('SELECT id FROM public.rule_definition WHERE code = %s', (code,))
                existing = cur.fetchone()
                if existing:
                    rid = existing[0]
                    cur.execute(
                        'UPDATE public.rule_definition SET name=%s, rule_type=%s, domain=%s, description=%s, status=%s WHERE id=%s',
                        (name, rule_type, domain, description, status, rid),
                    )
                    cur.execute('SELECT COALESCE(MAX(version_no), 0) FROM public.rule_version WHERE rule_id = %s', (rid,))
                    next_ver = int(cur.fetchone()[0]) + 1
                else:
                    cur.execute(
                        'INSERT INTO public.rule_definition (code, name, rule_type, domain, description, status, created_at) '
                        'VALUES (%s, %s, %s, %s, %s, %s, now()) RETURNING id',
                        (code, name, rule_type, domain, description, status),
                    )
                    rid = cur.fetchone()[0]
                    next_ver = 1
                if expression or output_mapping or not existing:
                    cur.execute(
                        'INSERT INTO public.rule_version '
                        '(rule_id, version_no, scope_type, condition_json, expression, output_mapping, priority, effective_from, published) '
                        'VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, now(), %s)',
                        (rid, next_ver, scope_type, '{}', expression or '', json.dumps(output_mapping, ensure_ascii=False), priority, status == 'PUBLISHED'),
                    )
            finally:
                cur.close()
        return next((r for r in list_rules() if r['code'] == code), {'code': code})

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    rules = [r for r in _as_json(doc.get('business_rules'), []) if r.get('code') != code]
    rules.append({
        'code': code, 'name': name, 'ruleType': rule_type, 'domain': domain, 'description': description,
        'status': status, 'versionNo': 1, 'scopeType': scope_type, 'expression': expression,
        'outputMapping': output_mapping, 'priority': priority, 'published': status == 'PUBLISHED',
    })
    doc['business_rules'] = rules
    core.update_ontology(doc, state['revision'])
    return next((r for r in list_rules() if r['code'] == code), {'code': code})


def delete_rule(code: str) -> None:
    target = _validate_code(code, '规则编码')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute('SELECT id FROM public.rule_definition WHERE code = %s', (target,))
                row = cur.fetchone()
                if not row:
                    raise ValueError('规则不存在')
                cur.execute('DELETE FROM public.rule_version WHERE rule_id = %s', (row[0],))
                cur.execute('DELETE FROM public.rule_definition WHERE id = %s', (row[0],))
            finally:
                cur.close()
        return
    state = core.ontology_document()
    doc = deepcopy(state['document'])
    rules = _as_json(doc.get('business_rules'), [])
    filtered = [r for r in rules if r.get('code') != target]
    if len(filtered) == len(rules):
        raise ValueError('规则不存在')
    doc['business_rules'] = filtered
    core.update_ontology(doc, state['revision'])


def list_capabilities() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    rows = _fetch_dicts(
                        cur,
                        'SELECT id, code, name, domain, phase, status, coverage_json, fallback_message, alternatives, effective_from '
                        'FROM public.cap_capability ORDER BY phase ASC, id ASC',
                    )
                    return [
                        {
                            'id': r['id'],
                            'code': r['code'],
                            'name': r['name'],
                            'domain': r.get('domain') or 'FIMS',
                            'phase': int(r.get('phase') or 1),
                            'status': r.get('status') or 'AVAILABLE',
                            'coverageJson': _as_json(r.get('coverage_json'), {}),
                            'fallbackMessage': r.get('fallback_message') or '',
                            'alternatives': _as_json(r.get('alternatives'), []),
                            'effectiveFrom': _as_iso(r.get('effective_from')),
                        }
                        for r in rows
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG cap_capability 查询降级', exc_info=True)
    return _as_json(_current_doc().get('capabilities'), [])


def save_capability(payload: dict[str, Any]) -> dict[str, Any]:
    code = _validate_code(payload.get('code'), '能力编码')
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('能力名称不能为空')
    domain = str(payload.get('domain') or 'FIMS').strip().upper()[:30]
    phase = max(1, min(9, int(payload.get('phase') or 1)))
    status = str(payload.get('status') or 'AVAILABLE').strip().upper()
    if status not in ('AVAILABLE', 'PLANNED', 'UNAVAILABLE'):
        raise ValueError('能力状态无效')
    fallback_msg = str(payload.get('fallbackMessage') or payload.get('fallback_message') or '').strip()[:1000]
    coverage_json = payload.get('coverageJson') if isinstance(payload.get('coverageJson'), dict) else _as_json(payload.get('coverage_json'), {})
    alts_raw = payload.get('alternatives')
    alternatives = alts_raw if isinstance(alts_raw, list) else [
        s.strip() for s in str(alts_raw or '').splitlines() if s.strip()
    ]

    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                _sync_seq(cur, 'cap_capability')
                cur.execute(
                    'INSERT INTO public.cap_capability (code, name, domain, phase, status, coverage_json, fallback_message, alternatives, effective_from) '
                    'VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, now()) '
                    'ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, domain=EXCLUDED.domain, phase=EXCLUDED.phase, '
                    'status=EXCLUDED.status, coverage_json=EXCLUDED.coverage_json, fallback_message=EXCLUDED.fallback_message, '
                    'alternatives=EXCLUDED.alternatives',
                    (code, name, domain, phase, status, json.dumps(coverage_json, ensure_ascii=False), fallback_msg, json.dumps(alternatives, ensure_ascii=False)),
                )
            finally:
                cur.close()
        return next((c for c in list_capabilities() if c['code'] == code), {'code': code})

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    caps = [c for c in _as_json(doc.get('capabilities'), []) if c.get('code') != code]
    caps.append({
        'code': code, 'name': name, 'domain': domain, 'phase': phase, 'status': status,
        'coverageJson': coverage_json, 'fallbackMessage': fallback_msg, 'alternatives': alternatives,
    })
    doc['capabilities'] = caps
    core.update_ontology(doc, state['revision'])
    return next((c for c in list_capabilities() if c['code'] == code), {'code': code})


def delete_capability(code: str) -> None:
    target = _validate_code(code, '能力编码')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM public.cap_capability WHERE code = %s', (target,))
                if cur.rowcount == 0:
                    raise ValueError('能力记录不存在')
            finally:
                cur.close()
        return
    state = core.ontology_document()
    doc = deepcopy(state['document'])
    caps = _as_json(doc.get('capabilities'), [])
    filtered = [c for c in caps if c.get('code') != target]
    if len(filtered) == len(caps):
        raise ValueError('能力记录不存在')
    doc['capabilities'] = filtered
    core.update_ontology(doc, state['revision'])


def list_planner_templates() -> list[dict[str, Any]]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    templates = _fetch_dicts(
                        cur,
                        'SELECT id, code, name, plan_type, trigger_concepts, required_caps, response_template, status '
                        'FROM public.plan_template ORDER BY id ASC',
                    )
                    steps = _fetch_dicts(
                        cur,
                        'SELECT id, plan_id, step_order, step_code, step_type, config_json, on_fail '
                        'FROM public.plan_step ORDER BY plan_id ASC, step_order ASC',
                    )
                    by_plan = {}
                    for st in steps:
                        by_plan.setdefault(st['plan_id'], []).append({
                            'id': st['id'],
                            'stepOrder': int(st['step_order']),
                            'stepCode': st['step_code'],
                            'stepType': st['step_type'],
                            'configJson': _as_json(st.get('config_json'), {}),
                            'onFail': st.get('on_fail') or 'abort',
                        })
                    return [
                        {
                            'id': t['id'],
                            'code': t['code'],
                            'name': t['name'],
                            'planType': t['plan_type'],
                            'triggerConcepts': _as_json(t.get('trigger_concepts'), []),
                            'requiredCaps': _as_json(t.get('required_caps'), []),
                            'responseTemplate': t.get('response_template') or '',
                            'status': t.get('status') or 'PUBLISHED',
                            'steps': by_plan.get(t['id'], []),
                        }
                        for t in templates
                    ]
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG plan_template 查询降级', exc_info=True)
    return _as_json(_current_doc().get('plan_templates'), [])


def save_planner_template(payload: dict[str, Any]) -> dict[str, Any]:
    code = _validate_code(payload.get('code'), '模板编码')
    name = str(payload.get('name') or '').strip()
    if not name:
        raise ValueError('模板名称不能为空')
    plan_type = str(payload.get('planType') or payload.get('plan_type') or 'single_metric').strip()[:40]
    triggers = payload.get('triggerConcepts') if isinstance(payload.get('triggerConcepts'), list) else [
        s.strip() for s in str(payload.get('triggerConcepts') or '').replace('，', ',').split(',') if s.strip()
    ]
    req_caps = payload.get('requiredCaps') if isinstance(payload.get('requiredCaps'), list) else [
        s.strip() for s in str(payload.get('requiredCaps') or '').replace('，', ',').split(',') if s.strip()
    ]
    response_tpl = str(payload.get('responseTemplate') or payload.get('response_template') or '').strip()[:1000]
    status = str(payload.get('status') or 'PUBLISHED').strip().upper()
    if status not in ('PUBLISHED', 'DRAFT', 'ARCHIVED'):
        raise ValueError('状态无效')

    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                _sync_seq(cur, 'plan_template')
                cur.execute(
                    'INSERT INTO public.plan_template (code, name, plan_type, trigger_concepts, required_caps, response_template, status) '
                    'VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s) '
                    'ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, plan_type=EXCLUDED.plan_type, '
                    'trigger_concepts=EXCLUDED.trigger_concepts, required_caps=EXCLUDED.required_caps, '
                    'response_template=EXCLUDED.response_template, status=EXCLUDED.status',
                    (code, name, plan_type, json.dumps(triggers, ensure_ascii=False), json.dumps(req_caps, ensure_ascii=False), response_tpl, status),
                )
            finally:
                cur.close()
        return next((t for t in list_planner_templates() if t['code'] == code), {'code': code})

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    templates = [t for t in _as_json(doc.get('plan_templates'), []) if t.get('code') != code]
    templates.append({
        'code': code, 'name': name, 'planType': plan_type,
        'triggerConcepts': triggers, 'requiredCaps': req_caps,
        'responseTemplate': response_tpl, 'status': status, 'steps': [],
    })
    doc['plan_templates'] = templates
    core.update_ontology(doc, state['revision'])
    return next((t for t in list_planner_templates() if t['code'] == code), {'code': code})


def delete_planner_template(code: str) -> None:
    target = _validate_code(code, '模板编码')
    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM public.plan_template WHERE code = %s', (target,))
                if cur.rowcount == 0:
                    raise ValueError('规划模板不存在')
            finally:
                cur.close()
        return
    state = core.ontology_document()
    doc = deepcopy(state['document'])
    templates = _as_json(doc.get('plan_templates'), [])
    filtered = [t for t in templates if t.get('code') != target]
    if len(filtered) == len(templates):
        raise ValueError('规划模板不存在')
    doc['plan_templates'] = filtered
    core.update_ontology(doc, state['revision'])


# ---------------------------------------------------------------------------
# Ask Settings: home_airport & operational_day
# ---------------------------------------------------------------------------

def get_home_airport_config() -> dict[str, Any]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    cur.execute("SELECT value_json FROM public.sys_ask_setting WHERE code = 'home_airport'")
                    row = cur.fetchone()
                    if row:
                        cfg = _as_json(row[0], DEFAULT_HOME_AIRPORT)
                        return {
                            'iataCode': str(cfg.get('iataCode') or 'WUH').upper(),
                            'cityName': cfg.get('cityName') or '武汉',
                            'displayName': cfg.get('displayName') or '武汉天河',
                            'aliases': list(cfg.get('aliases') or []),
                        }
                finally:
                    cur.close()
        except Exception:
            logger.debug('读取 sys_ask_setting.home_airport 失败', exc_info=True)
    doc_home = _current_doc().get('home_airport') or DEFAULT_HOME_AIRPORT
    return {
        'iataCode': str(doc_home.get('iataCode') or 'WUH').upper(),
        'cityName': doc_home.get('cityName') or '武汉',
        'displayName': doc_home.get('displayName') or '武汉天河',
        'aliases': list(doc_home.get('aliases') or []),
    }


def save_home_airport_config(payload: dict[str, Any]) -> dict[str, Any]:
    iata = str(payload.get('iataCode') or '').strip().upper()
    if not re.fullmatch(r'[A-Z]{3}', iata):
        raise ValueError('本场三字码必须为 3 位大写英文字母（例如 WUH）')
    city_name = str(payload.get('cityName') or '').strip()[:40]
    display_name = str(payload.get('displayName') or '').strip()[:60]
    if not city_name or not display_name:
        raise ValueError('城市名与展示名不能为空')
    raw_aliases = payload.get('aliases')
    if isinstance(raw_aliases, list):
        aliases = [str(x).strip() for x in raw_aliases if str(x).strip()]
    else:
        aliases = [s.strip() for s in str(raw_aliases or '').replace('，', ',').split(',') if s.strip()]
    cfg = {'iataCode': iata, 'cityName': city_name, 'displayName': display_name, 'aliases': list(dict.fromkeys(aliases))}

    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO public.sys_ask_setting (code, value_json, updated_at) VALUES ('home_airport', %s::jsonb, now()) "
                    'ON CONFLICT (code) DO UPDATE SET value_json = EXCLUDED.value_json, updated_at = now()',
                    (json.dumps(cfg, ensure_ascii=False),),
                )
            finally:
                cur.close()

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    doc['home_airport'] = cfg
    core.update_ontology(doc, state['revision'])
    return cfg


def get_operational_day_config() -> dict[str, Any]:
    cfg = None
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    cur.execute("SELECT value_json FROM public.sys_ask_setting WHERE code = 'operational_day'")
                    row = cur.fetchone()
                    if row:
                        cfg = _as_json(row[0], DEFAULT_OPERATIONAL_DAY)
                finally:
                    cur.close()
        except Exception:
            logger.debug('读取 sys_ask_setting.operational_day 失败', exc_info=True)
    if cfg is None:
        cfg = _current_doc().get('operational_day') or DEFAULT_OPERATIONAL_DAY
    normalized = {
        'enabled': bool(cfg.get('enabled', True)),
        'startHour': max(0, min(23, int(cfg.get('startHour', 4)))),
        'timezone': str(cfg.get('timezone') or 'Asia/Shanghai'),
    }
    normalized['boundaryLabel'] = boundary_label_for(normalized)
    return normalized


def save_operational_day_config(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(payload.get('enabled', True))
    try:
        start_hour = int(payload.get('startHour', 4))
    except (TypeError, ValueError) as exc:
        raise ValueError('日界时刻必须为 0–23 的整数') from exc
    if not (0 <= start_hour <= 23):
        raise ValueError('日界时刻必须在 0 到 23 小时之间')
    tz = str(payload.get('timezone') or 'Asia/Shanghai').strip()[:40] or 'Asia/Shanghai'
    stored = {'enabled': enabled, 'startHour': start_hour, 'timezone': tz}

    if _has_pg_governance():
        with _pg_write() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO public.sys_ask_setting (code, value_json, updated_at) VALUES ('operational_day', %s::jsonb, now()) "
                    'ON CONFLICT (code) DO UPDATE SET value_json = EXCLUDED.value_json, updated_at = now()',
                    (json.dumps(stored, ensure_ascii=False),),
                )
            finally:
                cur.close()

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    doc['operational_day'] = stored
    core.update_ontology(doc, state['revision'])
    return {**stored, 'boundaryLabel': boundary_label_for(stored)}


# ---------------------------------------------------------------------------
# Data Models (meta_biz_* + meta_model_*)
# ---------------------------------------------------------------------------

def get_model_overview() -> dict[str, Any]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    objs = _fetch_dicts(
                        cur,
                        'SELECT id, code, name, summary, description, sort_order, status '
                        "FROM public.meta_biz_object WHERE status = 'ACTIVE' ORDER BY sort_order ASC, id ASC",
                    )
                    attrs = _fetch_dicts(cur, "SELECT object_code, COUNT(*) AS n FROM public.meta_biz_attribute WHERE status='ACTIVE' GROUP BY object_code")
                    rels = _fetch_dicts(cur, "SELECT id, code, name, from_object, to_object, cardinality, description FROM public.meta_biz_relation WHERE status='ACTIVE' ORDER BY sort_order ASC, id ASC")
                    attr_map = {r['object_code']: int(r['n']) for r in attrs}
                    rel_counts = {}
                    for r in rels:
                        rel_counts[r['from_object']] = rel_counts.get(r['from_object'], 0) + 1
                        if r['to_object'] != r['from_object']:
                            rel_counts[r['to_object']] = rel_counts.get(r['to_object'], 0) + 1
                    return {
                        'ok': True,
                        'objectCount': len(objs),
                        'attributeCount': sum(attr_map.values()),
                        'relationCount': len(rels),
                        'objects': [
                            {
                                'code': o['code'],
                                'name': o['name'],
                                'summary': o.get('summary') or '',
                                'description': o.get('description') or '',
                                'attributeCount': attr_map.get(o['code'], 0),
                                'relationCount': rel_counts.get(o['code'], 0),
                            }
                            for o in objs
                        ],
                        'relations': [
                            {
                                'code': r['code'],
                                'name': r['name'],
                                'fromObject': r['from_object'],
                                'toObject': r['to_object'],
                                'cardinality': r['cardinality'],
                                'description': r.get('description') or '',
                            }
                            for r in rels
                        ],
                    }
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG meta_biz_* 查询降级到 Catalog entities', exc_info=True)

    cat = core.current_catalog()
    objects = [
        {
            'code': name,
            'name': spec['name'],
            'summary': spec.get('description', ''),
            'description': spec.get('description', ''),
            'attributeCount': len(spec.get('attributes', {})),
            'relationCount': len(cat.adjacency.get(name, [])),
        }
        for name, spec in cat.entities.items()
    ]
    return {
        'ok': True,
        'objectCount': len(objects),
        'attributeCount': sum(o['attributeCount'] for o in objects),
        'relationCount': len(cat.graph.get('links_all', [])),
        'objects': objects,
        'relations': [],
    }


def get_model_object_detail(code: str) -> dict[str, Any]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    objs = _fetch_dicts(cur, 'SELECT code, name, summary, description FROM public.meta_biz_object WHERE code = %s', (code,))
                    if objs:
                        obj = objs[0]
                        all_objs = {r['code']: r['name'] for r in _fetch_dicts(cur, 'SELECT code, name FROM public.meta_biz_object')}
                        attrs = _fetch_dicts(
                            cur,
                            'SELECT id, object_code, code, name, attr_group, value_type, description, example_value, std_entity, std_field, ask_attr_code '
                            "FROM public.meta_biz_attribute WHERE object_code = %s AND status = 'ACTIVE' ORDER BY sort_order ASC, id ASC",
                            (code,),
                        )
                        attr_list = []
                        by_group = {}
                        for a in attrs:
                            item = {
                                'id': a['id'],
                                'objectCode': a['object_code'],
                                'code': a['code'],
                                'name': a['name'],
                                'attrGroup': a.get('attr_group') or '基本信息',
                                'valueType': a.get('value_type') or 'string',
                                'description': a.get('description') or '',
                                'exampleValue': a.get('example_value') or '',
                                'stdEntity': a.get('std_entity') or '',
                                'stdField': a.get('std_field') or '',
                                'askAttrCode': a.get('ask_attr_code') or '',
                            }
                            attr_list.append(item)
                            by_group.setdefault(item['attrGroup'], []).append(item)
                        rels = _fetch_dicts(
                            cur,
                            "SELECT code, name, from_object, to_object, cardinality, description FROM public.meta_biz_relation "
                            "WHERE status = 'ACTIVE' AND (from_object = %s OR to_object = %s) ORDER BY sort_order ASC, id ASC",
                            (code, code),
                        )
                        rel_views = []
                        for r in rels:
                            is_out = r['from_object'] == code
                            peer = r['to_object'] if is_out else r['from_object']
                            rel_views.append({
                                'code': r['code'],
                                'name': r['name'],
                                'cardinality': r['cardinality'],
                                'description': r.get('description') or '',
                                'fromObject': r['from_object'],
                                'toObject': r['to_object'],
                                'fromObjectName': all_objs.get(r['from_object'], r['from_object']),
                                'toObjectName': all_objs.get(r['to_object'], r['to_object']),
                                'peerObject': peer,
                                'peerObjectName': all_objs.get(peer, peer),
                                'direction': 'OUT' if is_out else 'IN',
                            })
                        return {'ok': True, 'object': obj, 'attributes': attr_list, 'attributesByGroup': by_group, 'relations': rel_views}
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG meta_biz_object 详情查询失败', exc_info=True)

    cat = core.current_catalog()
    if code not in cat.entities:
        raise ValueError('业务对象不存在')
    spec = cat.entities[code]
    attrs = [
        {
            'objectCode': code, 'code': k, 'name': v.get('label', k), 'attrGroup': '表字段属性',
            'valueType': v.get('type', 'text'), 'description': v.get('description', ''),
            'exampleValue': '', 'stdEntity': code, 'stdField': k, 'askAttrCode': k,
        }
        for k, v in spec.get('attributes', {}).items()
    ]
    rels = [
        {
            'code': edge.get('role') or edge['table'], 'name': edge.get('note', edge['table']),
            'cardinality': edge.get('cardinality', '1:N'), 'description': edge.get('note', ''),
            'fromObject': edge['from_table'], 'toObject': edge['to_table'],
            'fromObjectName': cat.entities.get(edge['from_table'], {}).get('name', edge['from_table']),
            'toObjectName': cat.entities.get(edge['to_table'], {}).get('name', edge['to_table']),
            'peerObject': nxt, 'peerObjectName': cat.entities.get(nxt, {}).get('name', nxt),
            'direction': 'OUT' if edge['from_table'] == code else 'IN',
        }
        for nxt, edge in cat.adjacency.get(code, [])
    ]
    return {
        'ok': True,
        'object': {'code': code, 'name': spec['name'], 'summary': spec.get('description', ''), 'description': spec.get('description', '')},
        'attributes': attrs,
        'attributesByGroup': {'表字段属性': attrs},
        'relations': rels,
    }


def get_physical_model() -> dict[str, Any]:
    if _has_pg_governance():
        try:
            with _pg_read() as conn:
                cur = conn.cursor()
                try:
                    entities = _fetch_dicts(
                        cur,
                        "SELECT code, name, entity_kind, grain_desc, description, sort_order FROM public.meta_model_entity WHERE status='ACTIVE' ORDER BY sort_order ASC, id ASC",
                    )
                    fields = _fetch_dicts(
                        cur,
                        "SELECT entity_code, field_code, name_zh, data_type, field_group, is_biz_key_part, is_required, natural_ref_entity, natural_ref_field, ask_attr_code, description FROM public.meta_model_field WHERE status='ACTIVE' ORDER BY sort_order ASC, id ASC",
                    )
                    relations = _fetch_dicts(
                        cur,
                        "SELECT code, name, from_entity, to_entity, cardinality, join_type, from_fields, to_fields, description FROM public.meta_model_relation WHERE status='ACTIVE' ORDER BY sort_order ASC, id ASC",
                    )
                    fields_by_entity = {}
                    for f in fields:
                        fields_by_entity.setdefault(f['entity_code'], []).append({
                            'code': f['field_code'],
                            'name': f['name_zh'],
                            'dataType': f.get('data_type') or '',
                            'fieldGroup': f.get('field_group') or 'other',
                            'isBizKeyPart': bool(f.get('is_biz_key_part')),
                            'isRequired': bool(f.get('is_required')),
                            'naturalRef': f"{f['natural_ref_entity']}.{f['natural_ref_field']}" if f.get('natural_ref_entity') else '',
                            'askAttrCode': f.get('ask_attr_code') or '',
                            'description': f.get('description') or '',
                        })
                    return {
                        'ok': True,
                        'entities': [
                            {
                                'code': e['code'],
                                'name': e['name'],
                                'entityKind': e['entity_kind'],
                                'grainDesc': e.get('grain_desc') or '',
                                'description': e.get('description') or '',
                                'fields': fields_by_entity.get(e['code'], []),
                            }
                            for e in entities
                        ],
                        'relations': [
                            {
                                'code': r['code'],
                                'name': r['name'],
                                'fromEntity': r['from_entity'],
                                'toEntity': r['to_entity'],
                                'cardinality': r['cardinality'],
                                'joinType': r['join_type'],
                                'fromFields': r['from_fields'],
                                'toFields': r['to_fields'],
                                'description': r.get('description') or '',
                            }
                            for r in relations
                        ],
                    }
                finally:
                    cur.close()
        except Exception:
            logger.debug('PG meta_model_* 查询失败', exc_info=True)
    return {'ok': True, 'entities': [], 'relations': []}


# ---------------------------------------------------------------------------
# Catalog Overview, Capability Profile & Publish Bridge
# ---------------------------------------------------------------------------

def get_ontology_catalog() -> dict[str, Any]:
    metrics = [m for m in list_metrics() if m.get('status') == 'PUBLISHED']
    dimensions = [d for d in list_dimensions() if d.get('status') == 'PUBLISHED']
    filters = [f for f in list_filters() if f.get('status') == 'PUBLISHED']
    scenarios = [s for s in list_scenarios() if s.get('status') == 'PUBLISHED']
    attributes = list_attributes()
    synonyms = list_synonyms()
    concepts = [c for c in list_concepts() if c.get('status') == 'PUBLISHED']
    coverage = _live_data_coverage()
    return {
        'ok': True,
        'queryTypes': ['DETAIL', 'LIST', 'COUNT', 'TREND', 'RANK', 'OVERVIEW'],
        'dataCoverage': coverage,
        'metrics': metrics,
        'dimensions': dimensions,
        'filters': filters,
        'scenarios': scenarios,
        'attributes': attributes,
        'synonyms': synonyms,
        'concepts': concepts,
    }


def get_capability_profile() -> dict[str, Any]:
    catalog = get_ontology_catalog()
    home = get_home_airport_config()
    op_day = get_operational_day_config()
    metric_names = [m['name'] for m in catalog['metrics']]
    filter_names = [f['name'] for f in catalog['filters']]
    dim_names = [d['name'] for d in catalog['dimensions']]
    query_types = [
        {
            'queryType': 'DETAIL',
            'name': '航班/保障明细查询',
            'supportLevel': 'FULL',
            'description': '按航班号、共享航班号或机号精确检索单班运行动态、保障节点时刻与放行延误状态',
            'metrics': [],
            'filters': filter_names[:6],
            'dimensions': [],
            'exampleQuestions': ['MU2472 过站保障情况如何', '查一下共享航班 CZ3121 的运行动态'],
            'unsupportedPatterns': ['无航班号或无筛选条件时的全表随机抽样'],
        },
        {
            'queryType': 'LIST',
            'name': '航班条件清单查询',
            'supportLevel': 'FULL',
            'description': '按日期、进出港、航司、延误状态、起降城市与航站楼列出航班清单明细',
            'metrics': [],
            'filters': filter_names,
            'dimensions': [],
            'exampleQuestions': ['昨天从上海来本场的延误进港航班有哪些', '今天南航出港航班列表'],
            'unsupportedPatterns': ['单次返回超过配置上限的全量明细下载'],
        },
        {
            'queryType': 'COUNT',
            'name': '运营指标汇总统计',
            'supportLevel': 'FULL',
            'description': '依据已发布的指标公式与默认过滤器执行确定性 SQL 聚合统计',
            'metrics': metric_names,
            'filters': filter_names,
            'dimensions': [],
            'exampleQuestions': ['昨天出港延误有多少架次', '昨天靠桥率是多少', '昨天旅客人次和货邮行重量是多少'],
            'unsupportedPatterns': ['未注册口径的自定义比率四则混合推导'],
        },
        {
            'queryType': 'TREND',
            'name': '分时段/周期趋势统计',
            'supportLevel': 'FULL',
            'description': '按小时、日期、周或月份展开航班量或延误架次分布趋势',
            'metrics': ['航班量', '出港延误架次', '进港延误架次'],
            'filters': filter_names,
            'dimensions': ['小时', '日期', '周', '月份'],
            'exampleQuestions': ['今天各小时出港航班趋势', '近7天每天航班量变化'],
            'unsupportedPatterns': ['跨多张无直接关联事实表的联合回归预测'],
        },
        {
            'queryType': 'RANK',
            'name': '维度排名统计',
            'supportLevel': 'FULL',
            'description': '按航司、起站城市、目的站城市、小时或延误原因排序统计 Top-N',
            'metrics': ['航班量', '出港延误架次', '旅客人次'],
            'filters': filter_names,
            'dimensions': dim_names,
            'exampleQuestions': ['昨天哪个航司出港航班最多', '昨天进港来源城市排名前5'],
            'unsupportedPatterns': ['多跳模糊外部经济指标排名'],
        },
        {
            'queryType': 'OVERVIEW',
            'name': '运行总览概览',
            'supportLevel': 'FULL',
            'description': '聚合展示进出港总量、延误/取消情况与重点维度分布',
            'metrics': ['航班量', '出港延误架次', '进港延误架次', '取消架次'],
            'filters': ['日期', '本场三字码'],
            'dimensions': ['进出港', '航司'],
            'exampleQuestions': ['今日运行总览'],
            'unsupportedPatterns': [],
        },
    ]
    cov = catalog['dataCoverage']
    range_note = f"{cov.get('start') or '—'} 至 {cov.get('end') or '—'}"
    return {
        'ok': True,
        'dataCoverage': cov,
        'summary': {
            'metricCount': len(catalog['metrics']),
            'filterCount': len(catalog['filters']),
            'dimensionCount': len(catalog['dimensions']),
            'scenarioCount': len(catalog['scenarios']),
            'attributeCount': len(catalog['attributes']),
            'conceptCount': len(catalog['concepts']),
            'synonymCount': len(catalog['synonyms']),
        },
        'queryTypes': query_types,
        'timeSemantics': [
            {
                'slot': 'operational_day',
                'name': '业务运营日边界',
                'values': [
                    f"状态：{'已启用' if op_day['enabled'] else '已停用'}",
                    f"边界：{op_day['boundaryLabel']}（{op_day['timezone']}）",
                ],
                'defaultPolicy': '涉及次日凌晨保障或明确运营日统计时自动按日界时刻切日，默认航班日期使用 flight_date。',
            },
            {
                'slot': 'home_airport',
                'name': '本场机场语义锚点',
                'values': [
                    f"{home['displayName']}（{home['iataCode']} / {home['cityName']}）",
                    f"别名：{'、'.join(home['aliases']) or '无'}",
                ],
                'defaultPolicy': '提问中出现“来/到本场”优先映射为进港（ARR），出现“从本场出港”优先映射为出港（DEP），避免错当起终点城市过滤。',
            },
        ],
        'limitations': [
            f'当前已同步入库的航班核心覆盖区间为 {range_note}，范围外日期将由边界服务明确提示而不伪造零值。',
            '生产量（pax_total_sum）涉及多航段，仅汇总 is_local_leg=true 防止重复累计。',
            '所有管理端新建的指标、同义词与场景可在“发布并同步本体”后即时参与线上规划与只读 SQL 编译。',
        ],
        'examples': [
            {'question': '昨天靠桥率是多少？', 'queryType': 'COUNT', 'scenarioCode': 'BridgeStandRateQuery', 'note': '全局指标 + dim_stand 左连接'},
            {'question': '昨天出港延误有多少架次？', 'queryType': 'COUNT', 'scenarioCode': 'MetricQuery', 'note': '同义词 FlightDelay + direction=DEP → dep_delay_count'},
            {'question': '昨天旅客吞吐量和货邮行重量是多少？', 'queryType': 'COUNT', 'scenarioCode': 'ProductionVolume', 'note': 'PRODUCTION 域指标自动路由 std_flight_production'},
            {'question': '昨天从上海来本场的进港延误航班有哪些？', 'queryType': 'LIST', 'scenarioCode': 'FlightListQuery', 'note': '本场语义消歧 + originCity EXISTS 过滤'},
        ],
    }


def _load_concept_metrics_from_pg() -> list[dict[str, Any]]:
    if not _has_pg_governance():
        return []
    try:
        with _pg_read() as conn:
            cur = conn.cursor()
            try:
                rows = _fetch_dicts(
                    cur,
                    'SELECT c.code AS concept_code, cm.metric_code, cm.condition_expr, cm.priority '
                    'FROM public.ont_concept_metric cm '
                    'JOIN public.ont_concept c ON c.id = cm.concept_id '
                    'ORDER BY cm.priority DESC, cm.id ASC',
                )
                return [
                    {
                        'concept_code': r['concept_code'],
                        'metric_code': r['metric_code'],
                        'condition_expr': _as_json(r.get('condition_expr'), {}),
                        'priority': int(r.get('priority') or 100),
                    }
                    for r in rows
                ]
            finally:
                cur.close()
    except Exception:
        return []


def build_runtime_semantic_overlay() -> dict[str, Any]:
    """Assemble PUBLISHED governance artifacts from PG (when ops_ask) into runtime Catalog structures."""
    metrics_map = {}
    for m in list_metrics():
        if m.get('status') != 'PUBLISHED':
            continue
        metrics_map[m['code']] = {
            'name': m['name'],
            'domain': m['domain'],
            'formula_desc': m['formulaDesc'],
            'aggregation': m['aggregation'],
            'unit': m['unit'],
            'rule_code': m['ruleCode'] or None,
            'availability': m['availability'],
            'phase': m['phase'],
            'status': m['status'],
            'base_table': m['baseTable'],
            'aggregate_expression': m['aggregateExpression'],
            'value_alias': m['valueAlias'],
            'default_filters': m['defaultFilters'],
            'required_fields': m['requiredFields'],
            'date_field': m['dateField'],
            'joins_json': m['joinsJson'],
            'aliases': list(dict.fromkeys([m['name'], m['code'], *(m.get('aliases') or [])])),
        }

    dims_map = {}
    for d in list_dimensions():
        if d.get('status') != 'PUBLISHED':
            continue
        dims_map[d['code']] = {
            'name': d['name'],
            'expression': d['expression'],
            'alias': d['alias'],
            'required_field': d['requiredField'],
            'status': d['status'],
            'aliases': [d['name'], d['code']],
        }

    filters_map = {}
    for f in list_filters():
        if f.get('status') != 'PUBLISHED':
            continue
        filters_map[f['code']] = {
            'name': f['name'],
            'column_expr': f['columnExpr'],
            'operator': f['operator'],
            'join_indicator': f['joinIndicator'],
            'value_type': f['valueType'],
            'status': f['status'],
        }

    scenarios_map = {}
    for s in list_scenarios():
        if s.get('status') != 'PUBLISHED':
            continue
        scenarios_map[s['code']] = {
            'name': s['name'],
            'query_type': s['queryType'],
            'metric_code': s['metricCode'] or None,
            'dimensions': s['dimensions'],
            'default_filters': s['defaultFilters'],
            'select_fields': s['selectFields'],
            'trigger_keywords': s['triggerKeywords'],
            'priority': s['priority'],
            'status': s['status'],
        }

    concepts_map = {
        c['code']: {
            'name': c['name'], 'domain': c['domain'], 'concept_type': c['conceptType'],
            'description': c['description'], 'status': c['status'], 'version': c['version'],
        }
        for c in list_concepts() if c.get('status') == 'PUBLISHED'
    }

    return {
        'metrics': metrics_map,
        'dimensions': dims_map,
        'filters': filters_map,
        'scenarios': scenarios_map,
        'concepts': concepts_map,
        'concept_metrics': _load_concept_metrics_from_pg(),
        'synonyms': list_synonyms(),
        'business_rules': [r for r in list_rules() if r.get('status') == 'PUBLISHED'],
        'capabilities': list_capabilities(),
        'plan_templates': [t for t in list_planner_templates() if t.get('status') == 'PUBLISHED'],
        'home_airport': get_home_airport_config(),
        'operational_day': get_operational_day_config(),
    }


def publish_catalog_to_ontology(actor_name: str, change_summary: str = '') -> dict[str, Any]:
    """Compile PG governance models/metrics/concepts/rules/settings into runtime ontology & record version."""
    overlay = build_runtime_semantic_overlay()
    cov = _live_data_coverage()
    if cov.get('start') and cov.get('end'):
        overlay['coverage'] = {'start': cov['start'], 'end': cov['end']}

    state = core.ontology_document()
    doc = deepcopy(state['document'])
    for key, val in overlay.items():
        if key in ('metrics', 'dimensions', 'filters', 'scenarios') and isinstance(doc.get(key), dict) and isinstance(val, dict):
            # Merge keeping custom aliases on existing metrics/dimensions
            merged = deepcopy(val)
            for k, old_item in doc[key].items():
                if k in merged and isinstance(old_item, dict):
                    old_aliases = old_item.get('aliases') or []
                    merged[k]['aliases'] = list(dict.fromkeys([*(merged[k].get('aliases') or []), *old_aliases]))
                elif k not in merged and not _has_pg_governance():
                    merged[k] = old_item
            doc[key] = merged
        else:
            doc[key] = val

    result = core.update_ontology(doc, state['revision'])
    summary = (change_summary or '').strip()[:300] or f"发布 {len(overlay['metrics'])} 个指标 · {len(overlay['scenarios'])} 个场景 · {len(overlay['synonyms'])} 条同义词"

    version_no = len(core.STORE.history()) + 1
    if _has_pg_governance():
        try:
            with _pg_write() as conn:
                cur = conn.cursor()
                try:
                    _sync_seq(cur, 'ont_catalog_version')
                    cur.execute('SELECT COALESCE(MAX(version_no), 0) + 1 FROM public.ont_catalog_version')
                    version_no = int(cur.fetchone()[0])
                    cur.execute(
                        'INSERT INTO public.ont_catalog_version (version_no, change_summary, triggered_by, published_at) '
                        'VALUES (%s, %s, %s, now())',
                        (version_no, summary, str(actor_name or 'admin')[:80]),
                    )
                finally:
                    cur.close()
        except Exception:
            logger.warning('记录 PG ont_catalog_version 失败（本体已更新成功）', exc_info=True)

    return {
        'ok': True,
        'revision': result['revision'],
        'versionNo': version_no,
        'changeSummary': summary,
        'counts': {
            'metrics': len(overlay['metrics']),
            'dimensions': len(overlay['dimensions']),
            'filters': len(overlay['filters']),
            'scenarios': len(overlay['scenarios']),
            'concepts': len(overlay['concepts']),
            'synonyms': len(overlay['synonyms']),
            'rules': len(overlay['business_rules']),
        },
    }


def hydrate_catalog_in_memory(catalog) -> None:
    """Enrich an in-memory Catalog with live PG governance definitions on startup without disk side-effects."""
    if not _has_pg_governance():
        return
    try:
        overlay = build_runtime_semantic_overlay()
        cov = _live_data_coverage()
        if overlay['metrics']:
            for k, v in overlay['metrics'].items():
                existing_aliases = catalog.metrics.get(k, {}).get('aliases', [])
                v['aliases'] = list(dict.fromkeys([*(v.get('aliases') or []), *existing_aliases]))
            catalog.metrics.update(overlay['metrics'])
        if overlay['dimensions']:
            for k, v in overlay['dimensions'].items():
                existing_aliases = catalog.dimensions.get(k, {}).get('aliases', [])
                v['aliases'] = list(dict.fromkeys([*(v.get('aliases') or []), *existing_aliases]))
            catalog.dimensions.update(overlay['dimensions'])
        if overlay['scenarios']:
            catalog.scenarios.update(overlay['scenarios'])
        for field in ('filters', 'concepts', 'concept_metrics', 'synonyms', 'business_rules', 'capabilities', 'plan_templates', 'home_airport', 'operational_day'):
            setattr(catalog, field, overlay[field])
            catalog.overrides[field] = overlay[field]
        if cov.get('start') and cov.get('end'):
            catalog.overrides['coverage'] = {'start': cov['start'], 'end': cov['end']}
        catalog.refresh_version()
    except Exception:
        logger.debug('启动时从 PG 加载本体扩展目录失败，保留本地文件定义', exc_info=True)


# ---------------------------------------------------------------------------
# Planner Dry-Run Simulator & Feedback Promotion
# ---------------------------------------------------------------------------

def dry_run_plan(question: str, day_mode: str = 'AUTO') -> dict[str, Any]:
    """Run ontology resolution, planner, rule/capability checks, and SQL compilation without querying rows."""
    q = str(question or '').strip()
    if not q:
        raise ValueError('请输入待演练的自然语言问题')
    catalog = core.current_catalog()
    from app.agent.governance_runtime import check_capability_gate
    from app.coverage import DataCoverageService
    from app.execution import ExecutionTrace
    from app.query import Compiler

    trace = ExecutionTrace()
    hints = catalog.resolve_ontology_hints(q) if hasattr(catalog, 'resolve_ontology_hints') else {}

    try:
        coordinated = settings.data_source == 'ops_ask' and getattr(settings, 'coordinator_enabled', False)
        plan, source = core.parse_question(q, catalog, use_model=False, use_cache=False, trace=trace, day_mode=day_mode)
        if plan.get('outcome'):
            gate = plan.get('capability_gate')
            return {
                'ok': True,
                'resolved': False,
                'question': q,
                'hints': hints,
                'capabilityStatus': (gate or {}).get('status', 'UNAVAILABLE' if plan['outcome'] == 'unsupported' else 'AVAILABLE'),
                'capabilityGate': gate,
                'trace': trace.snapshot(),
                **plan,
            }
        compiler = Compiler(catalog)
        compiler.validate(plan)
        gate = check_capability_gate(catalog, plan, template=plan.get('plan_template'))
        if not gate['allowed']:
            return {
                'ok': True,
                'resolved': False,
                'outcome': 'unsupported',
                'answer': gate['message'],
                'error': gate['message'],
                'question': q,
                'source': source,
                'hints': hints,
                'plan': core.json_safe(plan),
                'capabilityStatus': gate['status'],
                'capabilityGate': gate,
                'matchedRules': plan.get('applied_rules') or [],
                'matchedTemplate': plan.get('plan_template'),
                'trace': trace.snapshot(),
            }
        intent = plan.get('intent', 'detail')
        subject = plan['subject']
        targets = plan.get('focus_tables', [])
        if intent not in ('detail', 'turnaround'):
            target = plan.get('aggregate_target') or subject
            targets = [target] if target != subject else []
            links = catalog.links(subject, targets) if targets else []
        else:
            links = catalog.links(subject, targets) if targets or not coordinated else []
        if intent == 'turnaround':
            sql_text, params = '-- ACDM 过站保障多时刻联合解析', ()
        elif intent == 'acdm_report':
            sql_text, params = f"-- ACDM 专题报告 ({plan.get('report_kind', 'general')}): {plan.get('report_start')} ~ {plan.get('report_end')}", ()
        elif intent == 'detail':
            stmt = compiler.detail(plan, links, settings.graph_main_limit, settings.graph_related_limit)
            sql_text, params = stmt.sql, list(stmt.params)
        else:
            stmt = compiler.aggregate(plan)
            sql_text, params = stmt.sql, list(stmt.params)
        cov_check = DataCoverageService(catalog).assess_plan(plan)
        matched_rules = plan.get('applied_rules') or []
        op_day_cfg = getattr(catalog, 'operational_day', None) or get_operational_day_config()
        op_window = plan.get('operational_window')
        if not op_window:
            use_op_day = str(day_mode or 'AUTO').upper() == 'OPERATIONAL' or (
                str(day_mode or 'AUTO').upper() == 'AUTO' and bool(op_day_cfg.get('enabled'))
            )
            date_val = next(
                (str(c.get('value'))[:10] for c in plan.get('conditions', []) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(c.get('value') or '')[:10])),
                None,
            )
            if use_op_day and date_val:
                w_start, w_end = get_operational_window(
                    datetime.strptime(date_val, '%Y-%m-%d').date(),
                    start_hour=int(op_day_cfg.get('startHour', 4)),
                )
                op_window = {
                    'start': w_start.strftime('%Y-%m-%d %H:%M:%S'),
                    'end': w_end.strftime('%Y-%m-%d %H:%M:%S'),
                    'startHour': int(op_day_cfg.get('startHour', 4)),
                }
        caps = getattr(catalog, 'capabilities', [])
        return {
            'ok': True,
            'resolved': True,
            'question': q,
            'source': source,
            'hints': hints,
            'plan': core.json_safe(plan),
            'sql': sql_text,
            'params': core.json_safe(params),
            'coverageCheck': cov_check,
            'capabilityStatus': gate.get('status', 'AVAILABLE'),
            'capabilityGate': gate,
            'capabilities': caps[:3],
            'matchedRules': matched_rules,
            'matchedTemplate': plan.get('plan_template'),
            'operationalWindow': op_window,
            'trace': trace.snapshot(),
        }
    except ValueError as exc:
        return {
            'ok': True,
            'resolved': False,
            'question': q,
            'error': str(exc),
            'hints': hints,
            'trace': trace.snapshot(),
        }


def list_feedback_combined(store, status='OPEN', search='', reason='', severity='', start_date='', end_date='', page=1, page_size=20) -> dict[str, Any]:
    return store.feedback_list(
        status=status, search=search, reason=reason,
        severity=severity, start_date=start_date, end_date=end_date,
        page=page, page_size=page_size,
    )


def promote_feedback_to_synonym(store, feedback_id: str, concept_code: str, term: str, actor: str, actor_name: str, admin_note: str = '') -> dict[str, Any]:
    fb = store.feedback_get(feedback_id)
    cleaned_term = str(term or '').strip()
    if not cleaned_term:
        raise ValueError('触发同义词不能为空')
    if _has_pg_governance():
        save_synonym({'conceptCode': concept_code, 'term': cleaned_term, 'termType': 'feedback_promoted', 'weight': 1.5})
        pub = publish_catalog_to_ontology(actor_name, f'不准反馈沉淀同义词：{cleaned_term} -> {concept_code}')
    else:
        state = core.ontology_document()
        doc = deepcopy(state['document'])
        concepts = doc.setdefault('concepts', {})
        if concept_code not in concepts:
            concepts[concept_code] = {'name': concept_code, 'domain': 'FIMS', 'concept_type': 'METRIC', 'status': 'PUBLISHED', 'version': 1}
        syns = doc.setdefault('synonyms', [])
        syns.append({'concept_code': concept_code, 'term': cleaned_term, 'term_type': 'feedback_promoted', 'weight': 1.5})
        pub = {'revision': core.update_ontology(doc, state['revision'])['revision'], 'versionNo': 0}

    note = admin_note.strip() or f'已沉淀同义词「{cleaned_term} → {concept_code}」并发布至本体'
    updated = store.update_feedback(feedback_id, 'FIXED', note, actor)
    store.audit(actor, 'feedback.promote', feedback_id, f'{cleaned_term} -> {concept_code}')
    return {'ok': True, 'feedback': updated, 'published': pub}
