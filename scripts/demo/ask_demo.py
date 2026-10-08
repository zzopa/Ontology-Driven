# -*- coding: utf-8 -*-
"""hbairport 知识图谱问答检索 Demo（交互式）— 带解析记忆层。

用法：
    python -m scripts.demo.ask_demo                进入交互问答（输入 q 退出）
    python -m scripts.demo.ask_demo "查询合同 GL-XZ-2026001 的所有信息"   单问单答

机制：
    1. 解析记忆层（借鉴 Hindsight TEMPR 思路）：
       - 企业/主体别名词典：天运达→企业、单子→合同编号等
       - 历史问答缓存：成功解析的问题存 ask_demo_memory.json，
         新问题通过「编号命中 / 相似度复用」直接继承解析参数
       - 时间感知：今年 / 上个月 → 自动转时间条件
    2. 沿知识图谱（35 外键 + 13 逻辑关联）BFS 展开相关表
    3. 逐层取数：主表先查，关联表用父表锚点值沿图匹配
    4. 结果按表展示
"""
import difflib
import json
import re
import os
import sys
from datetime import date
from app.config import db_options
from scripts.paths import DATA_DIR

BASE = str(DATA_DIR)
GRAPH = os.path.join(BASE, 'graph_full.json')
SCHEMA = os.path.join(BASE, 'hbairport_schema.json')
MEMORY_FILE = os.path.join(BASE, 'ask_demo_memory.json')
DB = db_options('hbairport')

SYS_PREFIX = ('app_', 'dict_', 'flyway', 'operation_', 'sync_', 'oa_',
              'geo_', 'va_', 'approval_', 'gzw_')

# 主体别名：业务词(含变体) -> (表, 编号字段, 显示名)
SUBJECTS = [
    ('合同', 'contract_main', 'contract_no', '合同主表'),
    ('单子', 'contract_main', 'contract_no', '合同主表'),
    ('contract', 'contract_main', 'contract_no', '合同主表'),
    ('员工', 'comp_employee', 'employee_no', '员工主表'),
    ('employee', 'comp_employee', 'employee_no', '员工主表'),
    ('薪酬', 'comp_payroll_monthly', None, '月度薪酬'),
    ('工资', 'comp_payroll_monthly', None, '月度薪酬'),
    ('payroll', 'comp_payroll_monthly', None, '月度薪酬'),
    ('采购', 'procurement_project', 'tender_code', '采购项目'),
    ('招标', 'procurement_project', 'tender_code', '采购项目'),
    ('procurement', 'procurement_project', 'tender_code', '采购项目'),
    ('资产', 'asset_card_record', 'card_no', '资产卡片'),
    ('卡片', 'asset_card_record', 'card_no', '资产卡片'),
    ('asset', 'asset_card_record', 'card_no', '资产卡片'),
    ('企业', 'comp_enterprise_ledger', 'enterprise_scc', '企业台账'),
    ('enterprise', 'comp_enterprise_ledger', 'enterprise_scc', '企业台账'),
    ('投资', 'inv_plan_project_info', 'project_code', '投资项目'),
    ('应收', 'receivables_record', 'receivable_no', '应收记录'),
    ('receivable', 'receivables_record', 'receivable_no', '应收记录'),
]

# 企业简称 -> 统一社会信用代码（静态已知；全量名单启动时从库构建）
ENT_ALIASES = {
    '天运达': '91420712MAG0C45Q72',
    '机场集团信息科技': '91420116MACTDG7E9W',
    '空港天运达': '91420712MAG0C45Q72',
}
ENT_NAMES = {}  # 全名 -> scc，启动时从 comp_enterprise_ledger 拉取

# 聚合字段（演示用）
AGG_FIELDS = {'contract_main': 'lump_sum_amount_wan'}


def load_json(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def load_memory():
    try:
        with open(MEMORY_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'entries': []}


def save_memory():
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(MEM, f, ensure_ascii=False, indent=1)


def coltype(schema, table, col):
    for t in schema['tables']:
        if t['table'] == table:
            for c in t['columns']:
                if c['name'] == col:
                    return c['type']
    return ''


def schema_coltype(table, col):
    return coltype(SCHEMA_DATA, table, col)


def load_enterprises(conn):
    """从库构建 企业全名 -> scc 映射。"""
    global ENT_NAMES
    try:
        _, rows = query(conn, 'SELECT DISTINCT enterprise_name, enterprise_scc '
                              'FROM comp_enterprise_ledger '
                              'WHERE enterprise_name IS NOT NULL AND enterprise_scc IS NOT NULL')
        ENT_NAMES = {r['enterprise_name']: r['enterprise_scc'] for r in rows}
        return len(ENT_NAMES)
    except Exception:
        return 0


def match_enterprise(text):
    """企业名命中 → scc（先静态简称，再库内全名子串）。"""
    for alias, scc in ENT_ALIASES.items():
        if alias in text:
            return scc
    for name, scc in ENT_NAMES.items():
        if name and name in text:
            return scc
    return None


def norm(s):
    return re.sub(r'[\s，。,.!?！？""''：:]', '', s.lower())


def memory_match(text):
    """记忆层命中：编号复用 > 相似度复用。返回解析参数 dict 或 None。"""
    if not MEM['entries']:
        return None
    nt = norm(text)
    m = re.search(r'\b[A-Z]{2,}-[A-Z]{2,}-\d{3,}\b', text)
    best, best_ratio = None, 0.0
    for e in MEM['entries']:
        p = e['parsed']
        if m and p.get('cond_val') == m.group(0):
            e['hits'] = e.get('hits', 0) + 1
            save_memory()
            return p, 'memory·编号复用'
        r = difflib.SequenceMatcher(None, nt, norm(e['q'])).ratio()
        if r > best_ratio:
            best, best_ratio = e, r
    if best and best_ratio >= 0.62:
        best['hits'] = best.get('hits', 0) + 1
        save_memory()
        return best['parsed'], 'memory·相似度复用(%.2f)' % best_ratio
    return None


def memory_store(text, parsed):
    """成功解析后入库（同主体同条件只累计命中）。"""
    for e in MEM['entries']:
        p = e['parsed']
        if p.get('cond_val') == parsed.get('cond_val') and p.get('subject') == parsed.get('subject'):
            e['hits'] = e.get('hits', 0) + 1
            save_memory()
            return
    MEM['entries'].append({'q': text, 'parsed': parsed, 'hits': 1})
    save_memory()


def parse(text):
    """规则解析。返回 (主体, 编号字段, 显示名, 意图, 条件列, 条件值, 运算符)。"""
    subject = no_col = disp = None
    for kw, tab, ncol, d in SUBJECTS:
        if kw.lower() in text.lower():
            subject, no_col, disp = tab, ncol, d
            break

    intent = 'detail'
    if subject and re.search(r'(多少|多少份|多少个|多少条|几条|几个|总数|合计|总计|总金额|总额)', text):
        intent = 'count'
        if re.search(r'(总金额|总额|合计|总计)', text) and subject in AGG_FIELDS:
            intent = 'sum'

    cond_col = cond_val = op = None
    if subject or match_enterprise(text):
        # 企业名命中且主体未识别 → 默认查合同（演示常见场景）
        if not subject and match_enterprise(text):
            subject, no_col, disp = 'contract_main', 'contract_no', '合同主表'
        m = re.search(r'\b[A-Z]{2,}-[A-Z]{2,}-\d{3,}\b', text)
        if m:
            cond_col, cond_val, op = no_col or 'contract_no', m.group(0), '='
        else:
            m2 = re.search(r'(?:编号|单号|代码|名称)[是为：:\s]+([A-Za-z0-9_\-\u4e00-\u9fff]{2,40})', text)
            if m2:
                cond_col, cond_val, op = (no_col or 'id'), m2.group(1), '='
        if not cond_col:
            m3 = re.search(r'(?:大于|超过|高于)\s*(\d{2,8})\s*(?:万|万元)?', text)
            if m3:
                cond_col, cond_val, op = 'lump_sum_amount_wan', float(m3.group(1)), '>'
            else:
                m3 = re.search(r'(?:小于|低于|不足)\s*(\d{2,8})\s*(?:万|万元)?', text)
                if m3:
                    cond_col, cond_val, op = 'lump_sum_amount_wan', float(m3.group(1)), '<'
        if not cond_col:
            m4 = re.search(r'(20\d{2})\s*年', text)
            if m4:
                cond_col, cond_val, op = 'signing_at', m4.group(1), 'prefix'
        if not cond_col:
            if re.search(r'今年', text):
                cond_col, cond_val, op = 'signing_at', str(date.today().year), 'prefix'
            elif re.search(r'上个月|上月', text):
                y, mo = (date.today().year, date.today().month - 1) if date.today().month > 1 \
                    else (date.today().year - 1, 12)
                cond_col, cond_val, op = 'signing_at', '%d-%02d' % (y, mo), 'prefix'
        if not cond_col:
            scc = match_enterprise(text)
            if scc:
                cond_col, cond_val, op = 'enterprise_scc', scc, '='
    return subject, no_col, disp, intent, cond_col, cond_val, op


def smart_parse(text):
    """记忆层优先，规则兜底，成功后入库。"""
    hit = memory_match(text)
    if hit:
        p, src = hit
        return (p['subject'], None, p.get('disp') or p['subject'],
                p.get('intent', 'detail'), p.get('cond_col'),
                p.get('cond_val'), p.get('op')), src
    p = parse(text)
    if p[0]:
        memory_store(text, {'subject': p[0], 'disp': p[2], 'intent': p[3],
                            'cond_col': p[4], 'cond_val': p[5], 'op': p[6]})
    return p, 'rule'


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


def bfs(adj, start):
    visited = {start}
    out = []
    queue = [(start, 0)]
    while queue:
        node, depth = queue.pop(0)
        if depth >= 2:
            continue
        for e in adj.get(node, []):
            nxt = e['to'] if not e.get('reversed') else e['from']
            if nxt in visited:
                continue
            if nxt.startswith(SYS_PREFIX):
                continue
            visited.add(nxt)
            out.append({'table': nxt, 'parent': node,
                        'from_tab': e['from'], 'fcol': e['fcol'],
                        'to_tab': e['to'], 'tcol': e['tcol'],
                        'type': e['type'], 'note': e.get('note', ''), 'level': depth + 1})
            queue.append((nxt, depth + 1))
    return out


def quote(v, typ):
    if v is None:
        return 'NULL'
    if typ and ('character' in typ or 'text' in typ):
        return "'%s'" % str(v).replace("'", "''")
    if typ and ('uuid' in typ):
        return "'%s'" % str(v)
    if isinstance(v, (int, float)):
        return str(v)
    return "'%s'" % str(v).replace("'", "''")


def query(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    try:
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return cols, rows
    except Exception:
        return [], []


def fetch_main(conn, subject, cond_col, cond_val, op, intent, limit=50):
    if intent == 'count':
        sql = 'SELECT COUNT(*) AS n FROM %s' % subject
        if cond_col and cond_val:
            if op == 'prefix':
                sql += " WHERE %s::text LIKE '%%%s%%'" % (cond_col, cond_val)
            elif op == '=':
                sql += ' WHERE %s = %s' % (cond_col, quote(cond_val, schema_coltype(subject, cond_col)))
            else:
                sql += ' WHERE %s %s %s' % (cond_col, op, cond_val)
        return query(conn, sql)
    if intent == 'sum':
        field = AGG_FIELDS[subject]
        sql = ('SELECT COALESCE(SUM(%s),0) AS total, COALESCE(MAX(%s),0) AS mx, '
               'COUNT(*) AS n FROM %s' % (field, field, subject))
        if cond_col and cond_val:
            if op == 'prefix':
                sql += " WHERE %s::text LIKE '%%%s%%'" % (cond_col, cond_val)
            elif op == '=':
                sql += ' WHERE %s = %s' % (cond_col, quote(cond_val, schema_coltype(subject, cond_col)))
            else:
                sql += ' WHERE %s %s %s' % (cond_col, op, cond_val)
        return query(conn, sql)
    sql = 'SELECT * FROM %s' % subject
    if cond_col and cond_val:
        if op == 'prefix':
            sql += " WHERE %s::text LIKE '%%%s%%'" % (cond_col, cond_val)
        elif op == '=':
            sql += ' WHERE %s = %s' % (cond_col, quote(cond_val, schema_coltype(subject, cond_col)))
        else:
            sql += ' WHERE %s %s %s' % (cond_col, op, cond_val)
    sql += ' LIMIT %d' % limit
    return query(conn, sql)


def fetch_related(conn, schema, links, main_rows):
    global main_tab
    results = []
    row_pool = {'main': main_rows}
    for ln in links:
        parent = ln['parent'] if ln['parent'] != main_tab else 'main'
        if parent == ln['from_tab'] or (parent == 'main' and ln['from_tab'] == main_tab):
            anchor_col, rel_col = ln['fcol'], ln['tcol']
        else:
            anchor_col, rel_col = ln['tcol'], ln['fcol']
        values = {r.get(anchor_col) for r in row_pool.get(parent, [])
                  if r.get(anchor_col) is not None}
        if not values:
            results.append({**ln, 'rows': [], 'cols': []})
            continue
        ttab, tcol = ln['table'], rel_col
        ttyp = coltype(schema, ttab, tcol)
        vals = [quote(v, ttyp) for v in values]
        sql = 'SELECT * FROM %s WHERE %s IN (%s) LIMIT 200' % (ttab, tcol, ','.join(vals))
        cols, rows = query(conn, sql)
        row_pool[ttab] = rows
        results.append({**ln, 'rows': rows, 'cols': cols})
    return results


def fmt_row(row, max_fields=12):
    picked = [f'{k}={v}' for k, v in row.items() if v is not None]
    if len(picked) > max_fields:
        return ' | '.join(picked[:max_fields]) + ' | …(%d 个字段)' % len(picked)
    return ' | '.join(picked)


def display(subject, disp, cond_col, cond_val, intent, main_cols, main_rows, results):
    print()
    print('=' * 78)
    if intent == 'count':
        n = main_rows[0]['n'] if main_rows else 0
        print(f'【统计】{disp}记录数：{n} 条', end='')
        if cond_col:
            print(f'（条件 {cond_col} 前缀 {cond_val}）' if cond_val else '')
        else:
            print()
        print('=' * 78)
        return
    if intent == 'sum':
        r = main_rows[0] if main_rows else {}
        total, mx, n = r.get('total', 0), r.get('mx', 0), r.get('n', 0)
        print(f'【统计】合同金额合计：{total:,.2f} 万元（{n} 条记录）')
        print(f'【提示】单笔最大值 {mx:,.2f} 万元——若总额异常，说明源数据含异常值，请核查 lump_sum_amount_wan')
        print('=' * 78)
        return
    print(f'【主表】{disp}（{subject}）命中 {len(main_rows)} 条')
    list_mode = len(main_rows) > 20
    for r in main_rows[:5]:
        print('  ' + fmt_row(r))
    if list_mode:
        print(f'  …（仅预览前 5 条，共 {len(main_rows)} 条）')
    print('-' * 78)
    for ln in results:
        tag = 'FK' if ln['type'] == 'fk' else '逻辑'
        note = ' · ' + ln['note'] if ln['note'] else ''
        line = f'【关联】{ln["table"]}（{tag}{note}）命中 {len(ln["rows"])} 条'
        if ln['level'] == 2:
            line += ' · 二级'
        print(line)
        if ln['rows'] and not list_mode:
            for r in ln['rows'][:3]:
                print('  ' + fmt_row(r))
        elif not ln['rows']:
            print('  （该主体下暂无数据）')
    print('=' * 78)


def answer(conn, text):
    global main_tab
    parsed, src = smart_parse(text)
    subject, no_col, disp, intent, cond_col, cond_val, op = parsed
    print(f'[解析] 主体={subject or "未识别"} 意图={intent} 条件={cond_col} {op} {cond_val} · 来源={src}')
    if not subject:
        print('未识别查询主体，可用：合同/单子/员工/工资/薪酬/采购/招标/资产/企业/投资/应收；或直接说“XX公司的合同”')
        return
    adj = ADJ
    links = bfs(adj, subject)
    print(f'[图谱] 沿 {len(G["links_all"])} 条关联边 BFS 展开 {len(links)} 张相关表')
    main_tab = subject
    main_cols, main_rows = fetch_main(conn, subject, cond_col, cond_val, op, intent)
    if intent in ('count', 'sum'):
        display(subject, disp, cond_col, cond_val, intent, main_cols, main_rows, [])
        return
    results = fetch_related(conn, SCHEMA_DATA, links, main_rows)
    display(subject, disp, cond_col, cond_val, intent, main_cols, main_rows, results)


def main():
    global G, SCHEMA_DATA, ADJ, MEM
    G = load_json(GRAPH)
    SCHEMA_DATA = load_json(SCHEMA)
    ADJ = build_adjacency(G)
    MEM = load_memory()
    import pg8000.dbapi as pg
    conn = pg.connect(**DB)
    n_ent = load_enterprises(conn)
    print('hbairport 知识图谱问答检索 Demo（解析记忆层版 · 输入 q 退出）')
    print(f'已加载：{len(G["links_all"])} 条图谱边 · {n_ent} 个企业名映射 · {len(MEM["entries"])} 条历史问答记忆')
    print('示例：查询合同 GL-XZ-2026001 的所有信息 / 查一下GL-XZ-2026001这个单子 / 今年签了多少合同 / 天运达的付款节点 / 上个月的合同')
    if len(sys.argv) > 1:
        answer(conn, ' '.join(sys.argv[1:]))
        return
    while True:
        try:
            q = input('\n> ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q.lower() in ('q', 'quit', 'exit', '退出'):
            break
        try:
            answer(conn, q)
        except Exception as e:
            print('[错误]', str(e)[:200])
    conn.close()
    print('已退出。')


if __name__ == '__main__':
    main()
