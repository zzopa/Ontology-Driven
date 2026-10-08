"""Validated query plans compiled to parameterized, read-only PostgreSQL SELECTs."""
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
        if plan.get('intent', 'detail') not in ('detail', 'count', 'sum'):
            raise ValueError('暂不支持该统计方式，请指定明细、数量或金额合计')
        plan.setdefault('intent', 'detail')
        for key in ('conditions','focus_tables','group_by'):
            plan.setdefault(key, [])
            if not isinstance(plan[key], list):
                raise ValueError(key + ' 必须是数组')
        if any(not isinstance(item, dict) for item in plan['conditions']):
            raise ValueError('筛选条件必须是对象')
        if any(not isinstance(item, str) for item in plan['focus_tables'] + plan['group_by']):
            raise ValueError('关联目标和分组字段必须是字符串')
        for condition in plan.get('conditions', []):
            table = condition.get('table') or subject
            attr = self.catalog.attribute(table, condition.get('col'))
            if condition.get('col') in self.catalog.overrides.get('sensitive_fields', []):
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
            self.catalog.attribute(target, field)
        if plan.get('time_bucket'):
            bucket = plan['time_bucket']
            if not isinstance(bucket, dict):
                raise ValueError('时间分组必须是对象')
            self.catalog.attribute(target, bucket.get('field'))
            if bucket.get('grain') not in ('year','month','day'):
                raise ValueError('时间分组粒度无效')
        if plan.get('intent') == 'sum':
            self.metric(plan)
        return plan

    def metric(self, plan):
        target = plan.get('aggregate_target') or plan['subject']
        metrics = self.catalog.entities[target]['metrics']
        key = plan.get('metric')
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

    def predicate(self, table, conditions, alias):
        sql, params = [], []
        for cond in conditions:
            expr = self.expression(table, cond['col'], alias)
            op, value = cond['op'], cond.get('value')
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
                attr = self.catalog.attribute(table, cond['col'])
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
                expr, values = self.predicate(table, conditions, aliases[table])
                clauses.append(expr)
                params.extend(values)
            else:
                path = self.catalog.path(plan['subject'], table)
                joins, nested = self.joins(path, 's0', 's')
                predicate, values = self.predicate(table, conditions, nested[table])
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

    def matched(self, plan, target):
        subject = plan['subject']
        path = self.catalog.path(subject, target) if target != subject else []
        joins, aliases = self.joins(path)
        aliases[subject] = 't0'
        where, params = self.where(plan, aliases)
        return self.statement('SELECT DISTINCT to_jsonb(' + aliases[target] + ') AS row_data FROM ' +
                         self.table(subject) + ' t0 ' + ' '.join(joins) + where, tuple(params))

    def detail(self, plan, links, main_limit, related_limit):
        self.validate(plan)
        subject = plan['subject']
        where, params = self.where(plan, {subject: 't0'})
        branches = ['(SELECT \'__main\'::text AS bucket, to_jsonb(t0) AS row_data, '
                    'COUNT(*) OVER()::bigint AS total_count FROM ' + self.table(subject) +
                    ' t0' + where + ' ORDER BY ' + self.order(subject, 't0') +
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
        target = plan.get('aggregate_target') or plan['subject']
        metric = self.metric(plan) if plan['intent'] == 'sum' else None
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
            where, params = self.where(plan, {target: 't0'})
            return self.statement('SELECT * FROM ' + self.table(target) + ' t0' + where +
                             ' ORDER BY ' + self.order(target, 't0') + ' LIMIT %s OFFSET %s',
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
