"""Validated query plans compiled to parameterized, read-only PostgreSQL SELECTs."""
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re


def identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class Statement:
    sql: str
    params: tuple = ()


def execute(conn, statement, preflight=False):
    cur = conn.cursor()
    try:
        if preflight:
            cur.execute('EXPLAIN (FORMAT JSON) ' + statement.sql, statement.params)
            cur.fetchall()
        cur.execute(statement.sql, statement.params)
        columns = [col[0] for col in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()


class Compiler:
    def __init__(self, catalog, access=None):
        self.catalog = catalog
        self.access = access
        self.scope_values = {}

    def statement(self, sql, params=()):
        """Bind source and user predicates in their actual SQL occurrence order."""
        values, ordinary = [], iter(params)
        def bind(match):
            values.append(self.scope_values[match.group(1)] if match.group(1) else next(ordinary))
            return '%s'
        compiled = re.sub(r':hb_scope_(\d+):|%s', bind, sql)
        sentinel = object()
        if next(ordinary, sentinel) is not sentinel:
            raise ValueError('SQL 绑定参数数量不一致')
        return Statement(compiled, tuple(values))

    def table(self, name):
        if name not in self.catalog.entities:
            raise ValueError('查询主体未开放或不存在：' + str(name))
        table = self.catalog.tables[name]
        if self.access:
            source, values = self.access.source(name, self.catalog)
            ordinary = iter(values)
            def marker(_match):
                key = str(len(self.scope_values))
                self.scope_values[key] = next(ordinary)
                return ':hb_scope_' + key + ':'
            return re.sub(r'%s', marker, source)
        return identifier(table.get('schema', 'public')) + '.' + identifier(
            table.get('name', name.split('.')[-1]))

    def expression(self, table, field, alias):
        attr = self.catalog.attribute(table, field)
        if 'column' in attr and 'path' in attr:
            base = alias + '.' + identifier(attr['column']) + '::jsonb'
            operator = '#>' if attr.get('structured') else '#>>'
            path = 'ARRAY[' + ','.join(literal(part) for part in attr['path']) + ']::text[]'
            return '(' + base + ' ' + operator + ' ' + path + ')'
        return alias + '.' + identifier(field)

    def validate(self, plan):
        if not isinstance(plan, dict):
            raise ValueError('查询计划必须是对象')
        subject = plan.get('subject')
        self.table(subject)
        if plan.get('unresolved'):
            raise ValueError('需要补充查询条件：' + str(plan['unresolved']))
        if plan.get('intent', 'detail') not in ('detail', 'count', 'sum', 'metric', 'turnaround', 'acdm_report'):
            raise ValueError('暂不支持该统计方式，请指定明细、数量或金额合计')
        plan.setdefault('intent', 'detail')
        for key in ('conditions','focus_tables','group_by'):
            plan.setdefault(key, [])
            if not isinstance(plan[key], list):
                raise ValueError(key + ' 必须是数组')
        ordering = plan.get('order_by', [])
        if not isinstance(ordering, list) or len(ordering) > 5:
            raise ValueError('排序字段必须是最多五项的数组')
        for item in ordering:
            if not isinstance(item, dict) or item.get('direction') not in ('asc', 'desc'):
                raise ValueError('排序方向仅支持 asc 或 desc')
            self.catalog.attribute(subject, item.get('field'))
            if item.get('field') in self.catalog.overrides.get('sensitive_fields', []):
                raise ValueError('当前问数入口不开放该敏感字段')
        has_metric_groups = plan.get('intent') == 'metric' and bool(plan.get('group_by'))
        if plan.get('limit') is not None:
            if type(plan['limit']) is not int or not 1 <= plan['limit'] <= 1000 or (not ordering and not has_metric_groups):
                raise ValueError('取前几条需要明确排序及 1—1000 的整数条数')
        if ordering and plan['intent'] != 'detail':
            raise ValueError('记录排序与条数仅适用于明细查询')
        if plan.get('limit') is not None and plan['intent'] != 'detail' and not has_metric_groups:
            raise ValueError('记录排序与条数仅适用于明细查询')
        if plan.get('sort_dir') is not None and plan.get('sort_dir') not in ('asc', 'desc'):
            raise ValueError('排序方向仅支持 asc 或 desc')
        if any(not isinstance(item, dict) for item in plan['conditions']):
            raise ValueError('筛选条件必须是对象')
        if any(not isinstance(item, str) for item in plan['focus_tables'] + plan['group_by']):
            raise ValueError('关联目标和分组字段必须是字符串')
        for condition in plan.get('conditions', []):
            table = condition.get('table') or subject
            col = condition.get('col')
            flt_spec = getattr(self.catalog, 'filters', {}).get(col)
            attr = self.catalog.entities.get(table, {}).get('attributes', {}).get(col)
            if attr is None and flt_spec is not None:
                vtype = flt_spec.get('value_type', 'string')
                attr = {'type': 'boolean' if vtype == 'boolean' else ('numeric' if vtype == 'number' else 'text')}
            else:
                attr = self.catalog.attribute(table, col)
            if col in self.catalog.overrides.get('sensitive_fields', []):
                raise ValueError('当前问数入口不开放该敏感字段')
            if condition.get('op') not in ('=', '!=', '>', '>=', '<', '<=', 'prefix', 'contains', 'in', 'null', 'notnull'):
                raise ValueError('条件运算符无效')
            if condition['op'] not in ('null', 'notnull') and condition.get('value') in (None, ''):
                raise ValueError('筛选值不能为空，不能丢弃该条件')
            if condition['op'] == 'in' and (not isinstance(condition['value'], list)
                                            or not 1 <= len(condition['value']) <= 50):
                raise ValueError('in 条件需要 1—50 个值')
            if table != subject:
                self.catalog.path(subject, table)
            if condition['op'] in ('>', '>=', '<', '<=') and any(
                    x in attr['type'] for x in ('numeric', 'integer', 'bigint', 'double', 'real')):
                try:
                    value = Decimal(str(condition['value']))
                    if not value.is_finite():
                        raise ValueError('数值条件必须是有限数')
                except InvalidOperation as exc:
                    raise ValueError('数值筛选条件无效') from exc
        for target in plan.get('focus_tables', []):
            self.catalog.path(subject, target)
        target = plan.get('aggregate_target') or subject
        self.table(target)
        if target != subject:
            self.catalog.path(subject, target)
        for field in plan.get('group_by', []):
            if hasattr(self.catalog, 'dimensions') and field in self.catalog.dimensions:
                continue
            self.catalog.attribute(target, field)
        if plan.get('time_bucket'):
            bucket = plan['time_bucket']
            if not isinstance(bucket, dict):
                raise ValueError('时间分组必须是对象')
            self.catalog.attribute(target, bucket.get('field'))
            if bucket.get('grain') not in ('year','month','day'):
                raise ValueError('时间分组粒度无效')
        if plan.get('intent') in ('sum', 'metric') or plan.get('metric'):
            self.metric(plan)
        return plan

    def metric(self, plan):
        key = plan.get('metric')
        if hasattr(self.catalog, 'metrics') and key and key in self.catalog.metrics:
            m = deepcopy(self.catalog.metrics[key])
            target = m.get('base_table') or plan.get('aggregate_target') or plan['subject']
            val_alias = m.get('value_alias') or key
            return {
                'key': key,
                'target': target,
                'name': m.get('name', key),
                'field': val_alias,
                'value_alias': val_alias,
                'unit': m.get('unit', ''),
                'aggregate_expression': m.get('aggregate_expression', 'COUNT(*)'),
                'default_filters': m.get('default_filters', {}),
                'joins_json': m.get('joins_json', []),
                'date_field': m.get('date_field', 'flight_date'),
                'is_global': True,
                **m
            }
        target = plan.get('aggregate_target') or plan['subject']
        metrics = self.catalog.entities[target]['metrics']
        if not key:
            defaults = [name for name, item in metrics.items() if item.get('default')]
            if len(defaults) == 1:
                key = defaults[0]
        if key not in metrics:
            raise ValueError('没有已定义的汇总口径，请在本体管理中配置指标或指定金额字段')
        metric = metrics[key]
        attr = self.catalog.attribute(target, metric['field'])
        if attr.get('aggregation') == 'nonadditive':
            raise ValueError('时点快照不能直接累加')
        return {'key': key, 'target': target, **metric}

    def _compile_registered_filter(self, flt_spec, op, value, alias, alias_map):
        expr = str(flt_spec.get('column_expr') or '').strip()
        if alias != 'c':
            expr = re.sub(r'\bc\.', alias + '.', expr)
        if flt_spec.get('operator') == 'exists':
            bound_val = f'%{value}%' if 'ilike' in expr.lower() else value
            count_q = expr.count('?')
            if count_q == 0:
                return f'EXISTS ({expr})', []
            compiled = expr.replace('?', '%s')
            return f'EXISTS ({compiled})', [bound_val] * count_q
        if flt_spec.get('join_indicator'):
            ind_alias = alias_map.get('std_flight_indicator')
            if ind_alias:
                if ind_alias != 'i':
                    expr = re.sub(r'\bi\.', ind_alias + '.', expr)
            else:
                sql_op = op if op in ('=', '!=', '>', '>=', '<', '<=') else '='
                return (
                    f'EXISTS (SELECT 1 FROM "public"."std_flight_indicator" i WHERE i.flight_id = {alias}.id AND ({expr}) {sql_op} %s)',
                    [value],
                )
        if op in ('null', 'notnull'):
            return f'({expr})' + (' IS NULL' if op == 'null' else ' IS NOT NULL'), []
        if op in ('prefix', 'contains'):
            escaped = str(value).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            return f'({expr})::text LIKE %s', [('%' if op == 'contains' else '') + escaped + '%']
        if op == 'in':
            return f'({expr}) IN (' + ','.join('%s' for _ in value) + ')', list(value)
        sql_op = op if op in ('=', '!=', '>', '>=', '<', '<=') else '='
        return f'({expr}) {sql_op} %s', [value]

    def predicate(self, table, conditions, alias, alias_map=None):
        sql, params = [], []
        filters_map = getattr(self.catalog, 'filters', {}) or {}
        table_attrs = self.catalog.entities.get(table, {}).get('attributes', {})
        aliases_dict = dict(alias_map or {})
        for cond in conditions:
            col = cond['col']
            op, value = cond['op'], cond.get('value')
            flt_spec = filters_map.get(col)
            use_filter = flt_spec is not None and (
                col not in table_attrs
                or flt_spec.get('operator') == 'exists'
                or flt_spec.get('join_indicator')
                or 'SELECT ' in str(flt_spec.get('column_expr') or '').upper()
            )
            if use_filter:
                part, part_params = self._compile_registered_filter(flt_spec, op, value, alias, aliases_dict)
                sql.append(part)
                params.extend(part_params)
                continue
            expr = self.expression(table, col, alias)
            if op in ('null', 'notnull'):
                sql.append(expr + (' IS NULL' if op == 'null' else ' IS NOT NULL'))
            elif op in ('prefix', 'contains'):
                escaped = str(value).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                sql.append(expr + '::text LIKE %s')
                params.append(('%' if op == 'contains' else '') + escaped + '%')
            elif op == 'in':
                sql.append(expr + ' IN (' + ','.join('%s' for _ in value) + ')')
                params.extend(value)
            else:
                attr = self.catalog.attribute(table, col)
                if op in ('>','>=','<','<=') and any(x in attr['type'] for x in ('text','character')):
                    raise ValueError('文本字段不能按数值直接比较，请配置标准化数值字段')
                sql.append(expr + ' ' + op + ' %s')
                params.append(value)
        return ' AND '.join(sql), params

    def joins(self, path, root_alias='t0', prefix='p'):
        aliases = {path[0]['parent']: root_alias} if path else {}
        clauses = []
        for index, edge in enumerate(path, 1):
            parent, child = edge['parent'], edge['table']
            child_alias = prefix + str(index)
            pairs = edge.get('pairs', [[edge['fcol'], edge['tcol']]])
            tests = []
            for from_col, to_col in pairs:
                left_col, right_col = ((from_col, to_col) if parent == edge['from_tab']
                                       else (to_col, from_col))
                left = self.expression(parent, left_col, aliases[parent])
                right = self.expression(child, right_col, child_alias)
                lt = self.catalog.attribute(parent, left_col)['type']
                rt = self.catalog.attribute(child, right_col)['type']
                if lt != rt:
                    left, right = left + '::text', right + '::text'
                tests.append(left + ' = ' + right)
            clauses.append('JOIN ' + self.table(child) + ' ' + child_alias + ' ON ' + ' AND '.join(tests))
            aliases[child] = child_alias
        return clauses, aliases

    def where(self, plan, aliases):
        grouped = {}
        for cond in plan.get('conditions', []):
            grouped.setdefault(cond.get('table') or plan['subject'], []).append(cond)
        clauses, params = [], []
        for table, conditions in grouped.items():
            if table in aliases:
                expr, values = self.predicate(table, conditions, aliases[table], aliases)
                clauses.append(expr)
                params.extend(values)
            else:
                path = self.catalog.path(plan['subject'], table)
                joins, nested = self.joins(path, 's0', 's')
                predicate, values = self.predicate(table, conditions, nested[table], nested)
                source = self.table(plan['subject']) + ' s0'
                # Match the complete root record; no assumptions about an id column.
                keys = self.catalog.tables[plan['subject']].get('primary_key', [])
                correlation = ' AND '.join('s0.' + identifier(key) + ' = ' +
                    aliases[plan['subject']] + '.' + identifier(key) for key in keys)
                if not correlation:
                    correlation = 'to_jsonb(s0) = to_jsonb(' + aliases[plan['subject']] + ')'
                clauses.append('EXISTS (SELECT 1 FROM ' + source + ' ' + ' '.join(joins) +
                               ' WHERE ' + correlation + ' AND ' + predicate + ')')
                params.extend(values)
        return (' WHERE ' + ' AND '.join(clauses)) if clauses else '', params

    def order(self, table, alias):
        keys = self.catalog.tables[table].get('primary_key', [])
        return ', '.join(alias + '.' + identifier(k) for k in keys) if keys else 'to_jsonb(' + alias + ')'

    def plan_order(self, plan, alias):
        ordering = [self.expression(plan['subject'], item['field'], alias) + ' ' + item['direction'].upper() + ' NULLS LAST'
                    for item in plan.get('order_by', [])]
        return ', '.join(ordering + [self.order(plan['subject'], alias)])

    def selected_source(self, plan):
        source = self.table(plan['subject'])
        if plan.get('limit') is None:
            return source, []
        where, params = self.where(plan, {plan['subject']: 'q0'})
        return ('(SELECT q0.* FROM ' + source + ' q0' + where + ' ORDER BY ' +
                self.plan_order(plan, 'q0') + ' LIMIT ' + str(plan['limit']) + ')'), params

    def matched(self, plan, target):
        subject = plan['subject']
        path = self.catalog.path(subject, target) if target != subject else []
        joins, aliases = self.joins(path)
        aliases[subject] = 't0'
        source, params = self.selected_source(plan)
        where, values = self.where(plan, aliases) if plan.get('limit') is None else ('', [])
        params.extend(values)
        return self.statement('SELECT DISTINCT to_jsonb(' + aliases[target] + ') AS row_data FROM ' +
                         source + ' t0 ' + ' '.join(joins) + where, tuple(params))

    def detail(self, plan, links, main_limit, related_limit):
        self.validate(plan)
        subject = plan['subject']
        source, params = self.selected_source(plan)
        where, values = self.where(plan, {subject: 't0'}) if plan.get('limit') is None else ('', [])
        params.extend(values)
        branches = ['(SELECT \'__main\'::text AS bucket, to_jsonb(t0) AS row_data, '
                    'COUNT(*) OVER()::bigint AS total_count FROM ' + source +
                    ' t0' + where + ' ORDER BY ' + self.plan_order(plan, 't0') +
                    ' LIMIT ' + str(int(main_limit)) + ')']
        for table in dict.fromkeys(edge['table'] for edge in links):
            matched = self.matched(plan, table)
            branches.append('(SELECT ' + literal(table) + '::text AS bucket, row_data, '
                            'COUNT(*) OVER()::bigint AS total_count FROM (' + matched.sql +
                            ') matched ORDER BY row_data LIMIT ' + str(int(related_limit)) + ')')
            params.extend(matched.params)
        return self.statement('\nUNION ALL\n'.join(branches), tuple(params))

    def aggregate(self, plan):
        self.validate(plan)
        metric = self.metric(plan) if (plan.get('intent') in ('sum', 'metric') or plan.get('metric')) else None
        if metric and metric.get('is_global'):
            target = metric.get('target') or plan.get('aggregate_target') or plan['subject']
            for f_col, f_spec in metric.get('default_filters', {}).items():
                if any(c.get('col') == f_col for c in plan.get('conditions', [])):
                    continue
                if isinstance(f_spec, dict) and 'op' in f_spec:
                    plan.setdefault('conditions', []).append({
                        'table': target,
                        'col': f_col,
                        'op': f_spec['op'],
                        'value': f_spec.get('value'),
                    })
                else:
                    plan.setdefault('conditions', []).append({
                        'table': target,
                        'col': f_col,
                        'op': '=',
                        'value': f_spec,
                    })
            join_aliases = {target: 'c'}
            join_clauses = []
            for j in metric.get('joins_json', []):
                j_table = j['table']
                j_alias = j.get('alias', 'j' + str(len(join_aliases)))
                join_aliases[j_table] = j_alias
                j_type = j.get('type', 'LEFT')
                join_clauses.append(f"{j_type} JOIN {self.table(j_table)} {j_alias} ON {j['on']}")
            filters_map = getattr(self.catalog, 'filters', {}) or {}
            needs_ind = any(
                filters_map.get(c.get('col'), {}).get('join_indicator')
                for c in plan.get('conditions', [])
            ) or 'delay_reason' in (plan.get('group_by') or [])
            if needs_ind and 'std_flight_indicator' not in join_aliases and 'std_flight_indicator' in self.catalog.entities:
                join_aliases['std_flight_indicator'] = 'i'
                join_key = 'c.pg_flight_id' if target == 'mv_acdm_dyn_flight_detail' else 'c.id'
                join_clauses.append(f"LEFT JOIN {self.table('std_flight_indicator')} i ON {join_key} = i.flight_id")

            where, params = self.where(plan, join_aliases)
            groups = plan.get('group_by', [])
            selection = []
            grouping = []
            for f in groups:
                if hasattr(self.catalog, 'dimensions') and f in self.catalog.dimensions:
                    dim = self.catalog.dimensions[f]
                    expr = dim['expression']
                    alias = dim.get('alias', f)
                    selection.append(f"{expr} AS {identifier(alias)}")
                    grouping.append(expr)
                else:
                    expr = f"c.{identifier(f)}"
                    selection.append(f"{expr} AS {identifier(f)}")
                    grouping.append(expr)

            agg_expr = metric.get('aggregate_expression', 'COUNT(*)')
            val_alias = metric.get('value_alias') or metric.get('field') or 'total'
            selection.append(f"{agg_expr} AS {identifier(val_alias)}")
            selection.append("COUNT(*) AS n")

            sql = f"SELECT {', '.join(selection)} FROM {self.table(target)} c"
            if join_clauses:
                sql += ' ' + ' '.join(join_clauses)
            sql += where
            if grouping:
                sql += f" GROUP BY {', '.join(grouping)}"
                sort_dir = str(plan.get('sort_dir') or '').upper()
                temporal_dims = {'date', 'month', 'week', 'hour', 'exec_date', 'hour_of_day', 'peak_hour', 'operation_plan_day', 'operation_exec_day'}
                if len(groups) == 1 and groups[0] in temporal_dims and not plan.get('limit') and not sort_dir:
                    sql += f" ORDER BY {grouping[0]} ASC"
                else:
                    dir_sql = sort_dir if sort_dir in ('ASC', 'DESC') else 'DESC'
                    sql += f" ORDER BY {identifier(val_alias)} {dir_sql} NULLS LAST, {grouping[0]} ASC"
            if plan.get('limit') is not None and grouping:
                sql += " LIMIT %s"
                params.append(int(plan['limit']))
            return self.statement(sql, tuple(params))

        target = plan.get('aggregate_target') or plan['subject']
        groups = list(dict.fromkeys((metric or {}).get('group_by', []) + plan.get('group_by', [])))
        if target == plan['subject'] and plan['intent'] == 'count' and not groups and not plan.get('time_bucket'):
            where, params = self.where(plan, {target: 't0'})
            return self.statement('SELECT COUNT(*) AS n FROM ' + self.table(target) + ' t0' + where, tuple(params))
        matched = self.matched(plan, target)

        def value(field):
            attr = self.catalog.attribute(target, field)
            if 'column' in attr:
                return '((row_data ->> ' + literal(attr['column']) + ')::jsonb #>> ARRAY[' + ','.join(literal(p) for p in attr['path']) + ']::text[])'
            return '(row_data ->> ' + literal(field) + ')'

        selection = [value(field) + ' AS ' + identifier(field) for field in groups]
        grouping = [value(field) for field in groups]
        bucket = plan.get('time_bucket')
        if bucket:
            length = {'year':4,'month':7,'day':10}[bucket['grain']]
            expr = 'substring(' + value(bucket['field']) + ' from 1 for ' + str(length) + ')'
            selection.append(expr + ' AS __period')
            grouping.append(expr)
        if metric:
            raw = value(metric['field'])
            numeric = '(CASE WHEN ' + raw + " ~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN " + raw + '::numeric END)'
            selection += ['COALESCE(SUM(' + numeric + '),0) AS total',
                          'COALESCE(MAX(' + numeric + '),0) AS mx', 'COUNT(*) AS n',
                          'COUNT(*) FILTER (WHERE ' + raw + ' IS NULL OR ' + raw + " = '') AS missing",
                          'COUNT(*) FILTER (WHERE ' + raw + " IS NOT NULL AND " + raw +
                          " <> '' AND NOT (" + raw + " ~ '^[+-]?[0-9]+([.][0-9]+)?$')) AS invalid"]
        else:
            selection += ['COUNT(*) AS n']
        sql = 'WITH matched AS (' + matched.sql + ') SELECT ' + ', '.join(selection) + ' FROM matched'
        if grouping:
            sql += ' GROUP BY ' + ','.join(grouping)
            sql += ' ORDER BY ' + ','.join(identifier(field) for field in groups) + (', ' if groups and bucket else '') + ('__period' if bucket else '')
        return self.statement(sql, matched.params)

    def page(self, plan, target, page, size):
        self.validate(plan)
        offset = (page - 1) * size
        if target == plan['subject']:
            source, params = self.selected_source(plan)
            where, values = self.where(plan, {target: 't0'}) if plan.get('limit') is None else ('', [])
            params.extend(values)
            return self.statement('SELECT * FROM ' + source + ' t0' + where +
                             ' ORDER BY ' + self.plan_order(plan, 't0') + ' LIMIT %s OFFSET %s',
                             tuple(params) + (size, offset))
        matched = self.matched(plan, target)
        return self.statement('SELECT row_data FROM (' + matched.sql +
                         ') matched ORDER BY row_data LIMIT %s OFFSET %s',
                         matched.params + (size, offset))


def json_safe(value):
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def merge_plans(base: dict, overrides: dict) -> dict:
    """合并上一轮查询计划与新增条件，用于多轮对话上下文继承。

    - subject/intent 继承 base
    - conditions 合并去重（同 table+col+op 的条件以 overrides 为准）
    - time_bucket 以 overrides 为准（时间覆写）
    - focus_tables/aggregate_target 以 overrides 为准（若 overrides 中有值）
    """
    merged = deepcopy(base)
    # conditions: 移除 base 中与 overrides 冲突的条件，再拼接 overrides 条件
    override_keys = {
        (c.get('table'), c.get('col'), c.get('op'))
        for c in overrides.get('conditions', [])
    }
    base_conditions = [
        c for c in merged.get('conditions', [])
        if (c.get('table'), c.get('col'), c.get('op')) not in override_keys
    ]
    merged['conditions'] = base_conditions + overrides.get('conditions', [])
    # time_bucket: 以 overrides 为准（时间覆写）
    if overrides.get('time_bucket') is not None:
        merged['time_bucket'] = overrides['time_bucket']
    # focus_tables: 以 overrides 为准（若非空）
    if overrides.get('focus_tables'):
        merged['focus_tables'] = overrides['focus_tables']
    # aggregate_target: 以 overrides 为准（若有值）
    if overrides.get('aggregate_target') is not None:
        merged['aggregate_target'] = overrides['aggregate_target']
    # metric: 以 overrides 为准（若有值）
    if overrides.get('metric') is not None:
        merged['metric'] = overrides['metric']
    return merged
