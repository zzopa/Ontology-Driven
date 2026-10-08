# -*- coding: utf-8 -*-
"""图谱驱动 SQL 拼接引擎：一句话 → 关联表字段 → 拼接 SQL → 可选执行验证。

用法：
    python -m scripts.demo.graph2sql "查询合同 GL-XZ-2026001 的所有信息" [--run] [--depth 2] [--all]
"""
import json
import re
import os
import sys

from app.config import db_options
from scripts.paths import DATA_DIR

BASE = str(DATA_DIR)
GRAPH = os.path.join(BASE, 'graph_full.json')
SCHEMA = os.path.join(BASE, 'hbairport_schema.json')

DB = db_options('hbairport')

# 系统/支撑域表前缀：默认查询不展开（业务无关），--all 时展开
SYS_PREFIX = ('app_', 'dict_', 'flyway', 'operation_', 'sync_', 'oa_',
              'geo_', 'va_', 'approval_', 'gzw_')

# 查询主体别名映射：业务词 -> (表, 编号字段, 显示名)
SUBJECTS = [
    ('合同', 'contract_main', 'contract_no', '合同主表'),
    ('contract', 'contract_main', 'contract_no', '合同主表'),
    ('员工', 'comp_employee', 'employee_no', '员工主表'),
    ('employee', 'comp_employee', 'employee_no', '员工主表'),
    ('薪酬', 'comp_payroll_monthly', 'id', '月度薪酬'),
    ('payroll', 'comp_payroll_monthly', 'id', '月度薪酬'),
    ('采购', 'procurement_project', 'tender_code', '采购项目'),
    ('procurement', 'procurement_project', 'tender_code', '采购项目'),
    ('资产', 'asset_card_record', 'card_no', '资产卡片'),
    ('asset', 'asset_card_record', 'card_no', '资产卡片'),
    ('企业', 'comp_enterprise_ledger', 'enterprise_scc', '企业台账'),
    ('enterprise', 'comp_enterprise_ledger', 'enterprise_scc', '企业台账'),
    ('投资', 'inv_plan_project_info', 'project_code', '投资项目'),
    ('应收', 'receivables_record', 'receivable_no', '应收记录'),
    ('receivable', 'receivables_record', 'receivable_no', '应收记录'),
]


def bare(n):
    return n.split('.')[-1]


def load_json(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def table_columns_all(schema, table):
    """从全量 schema 取某表字段 {name: type}（无表则空）。"""
    for t in schema['tables']:
        if bare(t['table']) == table:
            return {c['name']: c['type'] for c in t['columns']}
    return {}


def col_type(schema, table, col):
    return table_columns_all(schema, table).get(col, '')


def build_adjacency(graph):
    adj = {}
    for l in graph['links_all']:
        ft, fc = l['source'].split('.')
        tt, tc = l['target'].split('.')
        e = {'from': ft, 'fcol': fc, 'to': tt, 'tcol': tc,
             'type': l.get('type', 'fk'), 'note': l.get('note', '')}
        if ft == tt:
            continue
        adj.setdefault(ft, []).append(e)
        adj.setdefault(tt, []).append({**e, 'reversed': True})
    return adj


def bfs_reach(adj, start, max_depth, exclude_sys):
    visited = {start}
    joins = []
    queue = [(start, 0)]
    while queue:
        node, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for e in adj.get(node, []):
            nxt = e['to'] if not e.get('reversed') else e['from']
            if nxt in visited:
                continue
            if exclude_sys and nxt.startswith(SYS_PREFIX):
                continue
            visited.add(nxt)
            joins.append({
                'table': nxt,
                'cond_from_tab': e['from'], 'cond_from_col': e['fcol'],
                'cond_to_tab': e['to'], 'cond_to_col': e['tcol'],
                'type': e['type'], 'note': e.get('note', ''),
            })
            queue.append((nxt, depth + 1))
    return visited, joins


def build_sql(schema, graph, subject, cond_col, cond_val, max_depth, exclude_sys):
    adj = build_adjacency(graph)
    visited, joins = bfs_reach(adj, subject, max_depth, exclude_sys)

    # 别名：主体 t0，关联表按展开顺序 t1..tn
    aliases = {subject: 't0'}
    for i, j in enumerate(joins):
        aliases[j['table']] = 't%d' % (i + 1)

    # SELECT：主表全部 + 各关联表全部（别名前缀）
    sel = ['t0.*']
    for j in joins:
        a = aliases[j['table']]
        for col in table_columns_all(schema, j['table']):
            sel.append('%s.%s AS %s_%s' % (a, col, a, col))
    select = 'SELECT\n  ' + ',\n  '.join(sel)

    # FROM + JOIN（条件用别名；类型不一致自动统一转 text 比较）
    joins_sql = []
    for j in joins:
        a = aliases[j['table']]
        ft, fc, tt, tc = (aliases[j['cond_from_tab']], j['cond_from_col'],
                          aliases[j['cond_to_tab']], j['cond_to_col'])
        t1 = col_type(schema, j['cond_from_tab'], j['cond_from_col'])
        t2 = col_type(schema, j['cond_to_tab'], j['cond_to_col'])
        if t1 and t2 and t1 != t2:
            cond = '(%s.%s::text = %s.%s::text)' % (ft, fc, tt, tc)
        else:
            cond = '%s.%s = %s.%s' % (ft, fc, tt, tc)
        joins_sql.append('LEFT JOIN %s %s ON %s' % (j['table'], a, cond))
    body = 'FROM %s t0' % subject
    if joins_sql:
        body += '\n' + '\n'.join(joins_sql)

    where = ''
    if cond_col and cond_val:
        v = "'%s'" % cond_val.replace("'", "''")
        where = "\nWHERE t0.%s = %s" % (cond_col, v)

    return select + '\n' + body + where, visited, joins, aliases


def run_sql(sql):
    import pg8000.dbapi as pg
    conn = pg.connect(**DB)
    cur = conn.cursor()
    try:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return len(rows), len(cols)
    finally:
        conn.close()


def main():
    args = sys.argv[1:]
    text = '查询合同 GL-XZ-2026001 的所有信息'
    run = False
    max_depth = 2
    exclude_sys = True
    rest = []
    for a in args:
        if a == '--run':
            run = True
        elif a == '--all':
            exclude_sys = False
        elif a.startswith('--depth='):
            max_depth = int(a.split('=')[1])
        else:
            rest.append(a)
    if rest:
        text = ' '.join(rest)

    graph = load_json(GRAPH)
    schema = load_json(SCHEMA)

    subject, no_col, disp = None, None, None
    for kw, tab, ncol, dsp in SUBJECTS:
        if kw.lower() in text.lower():
            subject, no_col, disp = tab, ncol, dsp
            break
    print('输入：', text)
    if not subject:
        print('未识别到查询主体，可用主体词：合同/contract、员工、薪酬、采购、资产、企业、投资、应收')
        return

    # 条件
    m = re.search(r'\b[A-Z]{2,}-[A-Z]{2,}-\d{3,}\b', text)
    cond_col, cond_val = None, None
    if m:
        cond_col, cond_val = no_col or 'contract_no', m.group(0)
    else:
        m2 = re.search(r'(?:编号|单号|代码|名称)[是为：:\s]+([A-Za-z0-9_\-\u4e00-\u9fff]{2,40})', text)
        if m2:
            cond_col, cond_val = (no_col or 'id'), m2.group(1)

    sql, visited, joins, aliases = build_sql(schema, graph, subject, cond_col, cond_val,
                                             max_depth, exclude_sys)

    print('\n【1. 查询主体】%s（%s）' % (disp, subject))
    print('【2. 条件】%s = %s' % (cond_col, cond_val))
    print('\n【3. 图谱展开的关联表（%d 张）】' % len(joins))
    for j in joins:
        print('   %-32s %s.%s = %s.%s · %s%s' % (
            j['table'],
            j['cond_from_tab'], j['cond_from_col'],
            j['cond_to_tab'], j['cond_to_col'],
            'FK' if j['type'] == 'fk' else '逻辑',
            ' · ' + j['note'] if j['note'] else ''))
    print('\n【4. 拼接的 SQL】（%d 表 · %d 字段）' % (len(visited), 1 + sum(
        len(table_columns_all(schema, t)) for t in visited if t != subject)))
    print(sql)

    if run:
        try:
            n, c = run_sql(sql)
            print('\n【5. 执行验证】返回 %d 行 · %d 列' % (n, c))
        except Exception as e:
            print('\n【5. 执行失败】', str(e)[:200])


if __name__ == '__main__':
    main()
