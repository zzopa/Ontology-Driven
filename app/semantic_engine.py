"""指标语义层编译器：根据声明式配置自动组装确定性 SQL。

与本体层解耦：本体层负责实体识别与 JOIN 拓扑导航，
语义层负责固化指标口径、维度与时间聚合粒度，
由代码编译器自动组装标准 SQL，杜绝大模型在复杂指标计算中的语法错误。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import ROOT

_AGG_FUNCS: dict[str, str] = {
    'sum': 'SUM',
    'count': 'COUNT',
    'avg': 'AVG',
    'max': 'MAX',
    'min': 'MIN',
    'count_distinct': 'COUNT',
}

_FILTER_OPS = {'=', '!=', '>', '>=', '<', '<=', 'like', 'in', 'is_null', 'is_not_null'}


def _identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if value is None:
        return 'NULL'
    escaped = str(value).replace("'", "''")
    return "'" + escaped + "'"


class SemanticEngine:
    """加载指标定义配置并编译结构化查询规范为标准 SQL。"""

    def __init__(self, schema_path: Path | None = None) -> None:
        path = schema_path or (ROOT / 'data' / 'metrics_schema.json')
        with path.open(encoding='utf-8') as source:
            self.schema: dict[str, Any] = json.load(source)
        self.cubes: dict[str, Any] = self.schema.get('cubes', {})

    def _cube(self, name: str) -> dict[str, Any]:
        cube = self.cubes.get(name)
        if cube is None:
            raise ValueError('未知的指标 Cube：%s，请在 metrics_schema.json 中定义' % name)
        return cube

    def _measure(self, cube_name: str, measure_name: str) -> dict[str, Any]:
        cube = self._cube(cube_name)
        measure = cube.get('measures', {}).get(measure_name)
        if measure is None:
            available = ', '.join(cube.get('measures', {}).keys())
            raise ValueError(
                '指标 %s 不存在于 Cube %s，可用指标：%s' % (measure_name, cube_name, available))
        return measure

    def _dimension(self, cube_name: str, dim_name: str) -> dict[str, Any]:
        cube = self._cube(cube_name)
        dim = cube.get('dimensions', {}).get(dim_name)
        if dim is not None:
            return dim
        td = cube.get('time_dimensions', {}).get(dim_name)
        if td is not None:
            return td
        available = ', '.join(list(cube.get('dimensions', {}).keys()) +
                              list(cube.get('time_dimensions', {}).keys()))
        raise ValueError(
            '维度 %s 不存在于 Cube %s，可用维度：%s' % (dim_name, cube_name, available))

    def _time_dimension(self, cube_name: str, name: str) -> dict[str, Any]:
        cube = self._cube(cube_name)
        td = cube.get('time_dimensions', {}).get(name)
        if td is None:
            available = ', '.join(cube.get('time_dimensions', {}).keys())
            raise ValueError(
                '时间维度 %s 不存在于 Cube %s，可用时间维度：%s' % (name, cube_name, available))
        return td

    def _table_ref(self, cube: dict[str, Any]) -> str:
        schema = cube.get('schema', 'public')
        table = cube['sql_table']
        return _identifier(schema) + '.' + _identifier(table)

    def _measure_sql(self, cube_name: str, measure_name: str) -> str:
        cube = self._cube(cube_name)
        measure = self._measure(cube_name, measure_name)
        agg_type = measure.get('type', 'sum')
        func = _AGG_FUNCS.get(agg_type)
        if func is None:
            raise ValueError('不支持的聚合类型：%s' % agg_type)
        column = measure['sql']
        expr = self._column_expr(column)
        if measure.get('numeric_text'):
            expr = ("(CASE WHEN " + expr + " ~ '^[+-]?[0-9]+([.][0-9]+)?$' "
                    "THEN " + expr + "::numeric END)")
        if agg_type == 'count_distinct':
            return 'COUNT(DISTINCT ' + expr + ')'
        if agg_type == 'count':
            return func + '(' + expr + ')'
        return func + '(' + expr + ')'

    def _column_expr(self, column: str) -> str:
        return 't0.' + _identifier(column)

    def _dimension_sql(self, cube_name: str, dim_name: str) -> str:
        cube = self._cube(cube_name)
        dim = cube.get('dimensions', {}).get(dim_name)
        if dim is None:
            raise ValueError('维度 %s 不是普通分析维度' % dim_name)
        return 't0.' + _identifier(dim['sql'])

    def _time_dimension_sql(self, cube_name: str, name: str, granularity: str) -> str:
        td = self._time_dimension(cube_name, name)
        granularities = td.get('granularities', [])
        if granularity not in granularities:
            raise ValueError(
                '时间粒度 %s 不被支持，可用粒度：%s' % (granularity, ', '.join(granularities)))
        col = 't0.' + _identifier(td['sql'])
        return "DATE_TRUNC('%s', %s)" % (granularity, col)

    def _filter_sql(self, cube_name: str, flt: dict[str, Any]) -> str:
        member = flt.get('member')
        op = flt.get('op', '=')
        value = flt.get('value')
        if not member:
            raise ValueError('过滤条件缺少 member 字段')
        if op not in _FILTER_OPS:
            raise ValueError('不支持的过滤运算符：%s' % op)
        cube = self._cube(cube_name)
        all_dims = {**cube.get('dimensions', {}), **cube.get('time_dimensions', {})}
        if member not in all_dims:
            raise ValueError('过滤维度 %s 不存在于 Cube %s' % (member, cube_name))
        dim_cfg = all_dims[member]
        col = 't0.' + _identifier(dim_cfg['sql'])
        if op in ('is_null',):
            return col + ' IS NULL'
        if op in ('is_not_null',):
            return col + ' IS NOT NULL'
        if op == 'in':
            if not isinstance(value, list) or not value:
                raise ValueError('in 过滤条件需要非空值列表')
            return col + ' IN (' + ', '.join(_literal(v) for v in value) + ')'
        if op == 'like':
            return col + "::text LIKE " + _literal(str(value))
        if op in ('>', '>=', '<', '<='):
            return col + ' ' + op + ' ' + _literal(value)
        return col + ' ' + op + ' ' + _literal(value)

    def compile(self, query_spec: dict[str, Any]) -> str:
        """接收结构化查询规范，返回确定性 SQL 字符串。

        query_spec 结构：
            cube: str             — 指标 Cube 名称
            measures: list[str]   — 选定的指标键
            dimensions: list[str] — 分析维度键（普通维度）
            time_dimension: dict  — {name, granularity} 时间维度与粒度
            filters: list[dict]   — {member, op, value} 过滤条件
            order_by: list[dict]   — {member, direction} 排序
            limit: int | None      — 返回行数限制
        """
        if not isinstance(query_spec, dict):
            raise ValueError('查询规范必须是 JSON 对象')
        cube_name = query_spec.get('cube')
        if not cube_name:
            raise ValueError('查询规范缺少 cube 字段')
        cube = self._cube(cube_name)
        measures = query_spec.get('measures', [])
        if not isinstance(measures, list) or not measures:
            raise ValueError('查询规范必须包含至少一个 measure')
        dimensions = query_spec.get('dimensions', []) or []
        time_dim = query_spec.get('time_dimension')
        filters = query_spec.get('filters', []) or []
        order_by = query_spec.get('order_by', []) or []
        limit = query_spec.get('limit')

        select_parts: list[str] = []
        group_parts: list[str] = []

        for dim_name in dimensions:
            dim_cfg = cube.get('dimensions', {}).get(dim_name)
            if dim_cfg is None:
                available = ', '.join(cube.get('dimensions', {}).keys())
                raise ValueError(
                    '维度 %s 不存在于 Cube %s，可用维度：%s' % (dim_name, cube_name, available))
            col_sql = 't0.' + _identifier(dim_cfg['sql'])
            select_parts.append(col_sql + ' AS ' + _identifier(dim_name))
            group_parts.append(col_sql)

        if time_dim:
            td_name = time_dim.get('name')
            granularity = time_dim.get('granularity')
            if not td_name or not granularity:
                raise ValueError('time_dimension 必须包含 name 和 granularity')
            td_sql = self._time_dimension_sql(cube_name, td_name, granularity)
            alias = '%s_%s' % (td_name, granularity)
            select_parts.append(td_sql + ' AS ' + _identifier(alias))
            group_parts.append(td_sql)

        for measure_name in measures:
            self._measure(cube_name, measure_name)
            agg_sql = self._measure_sql(cube_name, measure_name)
            select_parts.append(agg_sql + ' AS ' + _identifier(measure_name))

        if not select_parts:
            raise ValueError('查询规范必须包含至少一个 measure 或 dimension')

        where_parts: list[str] = []
        for flt in filters:
            if not isinstance(flt, dict):
                raise ValueError('过滤条件必须是 JSON 对象')
            where_parts.append(self._filter_sql(cube_name, flt))

        table_ref = self._table_ref(cube)
        sql = 'SELECT ' + ', '.join(select_parts) + '\nFROM ' + table_ref + ' AS t0'
        if where_parts:
            sql += '\nWHERE ' + ' AND '.join(where_parts)
        if group_parts:
            sql += '\nGROUP BY ' + ', '.join(group_parts)

        if order_by:
            order_parts: list[str] = []
            for item in order_by:
                if not isinstance(item, dict):
                    raise ValueError('排序条件必须是 JSON 对象')
                member = item.get('member')
                direction = item.get('direction', 'asc').lower()
                if direction not in ('asc', 'desc'):
                    raise ValueError('排序方向必须是 asc 或 desc')
                all_measures = cube.get('measures', {})
                all_dims = {**cube.get('dimensions', {}), **cube.get('time_dimensions', {})}
                if member in all_measures:
                    order_parts.append(_identifier(member) + ' ' + direction.upper())
                elif member in all_dims:
                    if member in cube.get('dimensions', {}):
                        col = 't0.' + _identifier(all_dims[member]['sql'])
                    else:
                        col = 't0.' + _identifier(all_dims[member]['sql'])
                    order_parts.append(col + ' ' + direction.upper())
                else:
                    raise ValueError('排序字段 %s 不存在' % member)
            if order_parts:
                sql += '\nORDER BY ' + ', '.join(order_parts)

        if limit is not None:
            if not isinstance(limit, int) or limit < 1:
                raise ValueError('limit 必须是正整数')
            sql += '\nLIMIT ' + str(limit)

        return sql

    def available_measures(self, cube_name: str) -> list[str]:
        return list(self._cube(cube_name).get('measures', {}).keys())

    def available_dimensions(self, cube_name: str) -> list[str]:
        cube = self._cube(cube_name)
        return list(cube.get('dimensions', {}).keys()) + list(cube.get('time_dimensions', {}).keys())
