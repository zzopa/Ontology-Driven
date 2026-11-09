"""数据库 schema 与本地配置文件的差异比对。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import ROOT

_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _normalize_type(type_str: str) -> str:
    return re.sub(r'\(.*\)', '', type_str or '').strip().lower()


def _load_json(path: Path) -> dict:
    with path.open(encoding='utf-8') as source:
        return json.load(source)


def _collect_metrics(metrics_schema: dict) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for cube in metrics_schema.get('cubes', {}).values():
        table = cube.get('sql_table')
        if not table:
            continue
        cols = tables.setdefault(table, set())
        for group in ('measures', 'dimensions', 'time_dimensions'):
            for item in (cube.get(group) or {}).values():
                sql = item.get('sql', '')
                if sql and _IDENTIFIER.match(sql):
                    cols.add(sql)
    return tables


def _collect_graph(field_graph: dict) -> dict[str, dict[str, str]]:
    tables: dict[str, dict[str, str]] = {}
    for node in field_graph.get('field_nodes', []):
        table, col = node.get('table'), node.get('col')
        if not table or not col:
            continue
        tables.setdefault(table, {})[col] = node.get('type', '')
    return tables


def _collect_ontology(business_ontology: dict) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for table, entity in business_ontology.get('entities', {}).items():
        cols = tables.setdefault(table, set())
        cols.update((entity.get('bindings') or {}).values())
        for metric in (entity.get('metrics') or {}).values():
            field = metric.get('field')
            if field:
                cols.add(field)
        cols.update(entity.get('identity_fields') or [])
        cols.update((entity.get('attributes') or {}).keys())
    return tables


def compare_schema_to_configs(
    db_schema: dict,
    metrics_schema: dict | None = None,
    field_graph: dict | None = None,
    business_ontology: dict | None = None,
) -> dict[str, Any]:
    data_dir = ROOT / 'data'
    if metrics_schema is None:
        metrics_schema = _load_json(data_dir / 'metrics_schema.json')
    if field_graph is None:
        field_graph = _load_json(data_dir / 'field_graph.json')
    if business_ontology is None:
        business_ontology = _load_json(data_dir / 'business_ontology.json')

    metrics_cols = _collect_metrics(metrics_schema)
    graph_cols = _collect_graph(field_graph)
    ontology_cols = _collect_ontology(business_ontology)

    config_tables = set(metrics_cols) | set(graph_cols) | set(ontology_cols)

    db_tables: set[str] = set()
    db_columns: dict[str, dict[str, str]] = {}
    for table in db_schema.get('tables', []):
        name = table['table']
        db_tables.add(name)
        db_columns[name] = {col['name']: col['type'] for col in table.get('columns', [])}

    new_tables = sorted(db_tables - config_tables)
    orphaned_tables = sorted(config_tables - db_tables)

    new_columns: list[dict[str, str]] = []
    for table in sorted(db_tables & config_tables):
        referenced = (metrics_cols.get(table, set())
                      | set(graph_cols.get(table, {}))
                      | ontology_cols.get(table, set()))
        for col, col_type in sorted(db_columns[table].items()):
            if col not in referenced:
                new_columns.append({'table': table, 'column': col, 'type': col_type})

    type_changes: list[dict[str, str]] = []
    for table in sorted(graph_cols):
        db_cols = db_columns.get(table)
        if not db_cols:
            continue
        for col in sorted(graph_cols[table]):
            db_type = db_cols.get(col)
            if db_type is None:
                continue
            config_type = graph_cols[table][col]
            if _normalize_type(db_type) != _normalize_type(config_type):
                type_changes.append({'table': table, 'column': col,
                                     'config_type': config_type, 'db_type': db_type})

    orphaned_sources: dict[tuple[str, str], list[str]] = {}
    for source_name, source_cols in (('metrics_schema', metrics_cols),
                                     ('field_graph', {t: set(c) for t, c in graph_cols.items()}),
                                     ('business_ontology', ontology_cols)):
        for table, cols in source_cols.items():
            if table not in db_tables:
                continue
            db_cols = db_columns[table]
            for col in cols:
                if col not in db_cols:
                    orphaned_sources.setdefault((table, col), []).append(source_name)
    orphaned_columns = [{'table': t, 'column': c, 'source': ', '.join(s)}
                        for (t, c), s in sorted(orphaned_sources.items())]

    return {
        'new_tables': new_tables,
        'new_columns': new_columns,
        'type_changes': type_changes,
        'orphaned_tables': orphaned_tables,
        'orphaned_columns': orphaned_columns,
        'summary': {
            'new_tables': len(new_tables),
            'new_columns': len(new_columns),
            'type_changes': len(type_changes),
            'orphaned_tables': len(orphaned_tables),
            'orphaned_columns': len(orphaned_columns),
        },
    }
