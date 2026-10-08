# -*- coding: utf-8 -*-
"""生成字段级图谱数据：字段节点 + 字段级外键边 + 表卡片元数据。"""
import os
import json
import math
from scripts.paths import DATA_DIR

BASE = str(DATA_DIR)

SCHEMA = os.path.join(BASE, 'hbairport_schema.json')
ANALYSIS = os.path.join(BASE, 'hbairport_analysis.json')
OUT = os.path.join(BASE, 'field_graph.json')


def bare(n):
    return n.split('.')[-1]


def short_type(t):
    t = t.replace('character varying', 'varchar').replace('timestamp with time zone', 'timestamptz')
    t = t.replace('character', 'char').replace('double precision', 'float8')
    if len(t) > 24:
        t = t[:23] + '…'
    return t


def main():
    with open(SCHEMA, encoding='utf-8') as f:
        schema = json.load(f)
    with open(ANALYSIS, encoding='utf-8') as f:
        analysis = json.load(f)

    tables = schema['tables']
    fks = schema['foreign_keys']

    # 域映射
    dom_of = {}
    for dom, st in analysis['domains'].items():
        for t in st['tables']:
            dom_of[t] = dom

    # 表索引：name -> {columns, pk, domain}
    tab_map = {}
    for t in tables:
        n = bare(t['table'])
        tab_map[n] = {
            'columns': {c['name']: c for c in t['columns']},
            'pk': set(t['primary_key']),
            'domain': dom_of.get(n, '其他'),
            'rows': max(t['est_rows'] or 0, 0),
        }

    # 字段节点：参与关联的表，其 主键字段 + 外键字段 作为图谱节点
    fk_info = []  # (from_table, from_col, to_table, to_col)
    for fk in fks:
        fk_info.append((bare(fk['table']), bare(fk['column']),
                        bare(fk['ref_table']), bare(fk['ref_column'])))

    # 被引用字段集合（去重）
    ref_cols = set((t, c) for _, _, t, c in fk_info)

    # 涉及的表
    involved_tables = set()
    for ft, fc, tt, tc in fk_info:
        involved_tables.add(ft)
        involved_tables.add(tt)

    field_nodes = []
    node_key = set()

    def add_node(tn, cn, is_fk=False, fk_target=''):
        """添加字段节点（去重；已存在时若有外键身份则补充标记）。"""
        nonlocal field_nodes
        if (tn, cn) in node_key:
            if is_fk:
                for nd in field_nodes:
                    if nd['key'] == tn + '.' + cn and not nd['fk']:
                        nd['fk'] = True
                        nd['fk_target'] = fk_target
            return
        node_key.add((tn, cn))
        col = tab_map[tn]['columns'].get(cn, {})
        field_nodes.append({
            'key': tn + '.' + cn, 'table': tn, 'col': cn,
            'domain': tab_map[tn]['domain'],
            'type': short_type(col.get('type', '')),
            'nullable': col.get('nullable', True),
            'pk': cn in tab_map[tn]['pk'],
            'fk': is_fk,
            'fk_target': fk_target,
        })

    # 先加入涉及表的所有主键字段（保证每个 PK 都可管理）
    for tn in involved_tables:
        for cn in tab_map[tn]['pk']:
            add_node(tn, cn)

    # 再补充外键字段与被引用字段
    for ft, fc, tt, tc in fk_info:
        add_node(ft, fc, is_fk=True, fk_target=tt + '.' + tc)
        add_node(tt, tc)

    links = []
    for ft, fc, tt, tc in fk_info:
        links.append({'source': ft + '.' + fc, 'target': tt + '.' + tc})

    # 表卡片数据（参与关联的表：字段清单）
    cards = []
    for tn in sorted(involved_tables):
        t = tab_map[tn]
        cols = []
        for cn, c in t['columns'].items():
            cols.append({
                'col': cn,
                'type': short_type(c.get('type', '')),
                'nullable': c.get('nullable', True),
                'pk': cn in t['pk'],
                'fk': any((tn, cn) == (ft, fc) for ft, fc, _, _ in fk_info),
                'fk_target': next((tt + '.' + tc for ft, fc, tt, tc in fk_info
                                   if (tn, cn) == (ft, fc)), ''),
            })
        cards.append({'table': tn, 'domain': t['domain'], 'rows': t['rows'],
                      'fields': cols, 'n_pk': len(t['pk'])})

    out = {
        'field_nodes': field_nodes,
        'links': links,
        'cards': cards,
        'stats': {
            'tables': len(tables),
            'fields': sum(len(t['columns']) for t in tables),
            'pk_fields': sum(len(t['primary_key']) for t in tables),
            'fks': len(fks),
            'link_tables': len(involved_tables),
            'link_fields': len(field_nodes),
        },
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print('stats:', out['stats'])
    print('field_nodes:', len(field_nodes), 'links:', len(links), 'cards:', len(cards))
    # 样例
    for n in field_nodes[:5]:
        print(n)


if __name__ == '__main__':
    main()
