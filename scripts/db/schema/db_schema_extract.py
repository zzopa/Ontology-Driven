# -*- coding: utf-8 -*-
"""读取 hbairport 库全部表结构，导出为 JSON 供后续生成知识图谱。"""
import pg8000.dbapi as pg
import json
import os
import sys
from app.config import db_options
from scripts.paths import DATA_DIR

BASE = str(DATA_DIR)
DB = db_options('hbairport')
OUT = os.path.join(BASE, 'hbairport_schema.json')


def main():
    conn = pg.connect(**DB)
    cur = conn.cursor()

    # 1) 所有业务表
    cur.execute("""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_schema, table_name
    """)
    tables = cur.fetchall()
    print('TABLE_COUNT:', len(tables))
    for s, t in tables:
        print('  ', s, t)

    # 2) 每张表字段
    cur.execute("""
        SELECT table_schema, table_name, column_name, data_type,
               character_maximum_length, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_schema, table_name, ordinal_position
    """)
    col_rows = cur.fetchall()

    # 3) 主键
    cur.execute("""
        SELECT tc.table_schema, tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY tc.table_schema, tc.table_name, kcu.ordinal_position
    """)
    pk_rows = cur.fetchall()

    # 4) 外键
    cur.execute("""
        SELECT tc.table_schema, tc.table_name, kcu.column_name,
               ccu.table_schema AS ref_schema, ccu.table_name AS ref_table,
               ccu.column_name AS ref_column, tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
         AND tc.table_schema = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY tc.table_schema, tc.table_name, kcu.ordinal_position
    """)
    fk_rows = cur.fetchall()

    # 5) 唯一约束 / 唯一索引
    cur.execute("""
        SELECT tc.table_schema, tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'UNIQUE'
          AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY tc.table_schema, tc.table_name
    """)
    uq_rows = cur.fetchall()

    # 6) 行数估计（pg_class 统计）
    cur.execute("""
        SELECT n.nspname, c.relname, c.reltuples::bigint
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY n.nspname, c.relname
    """)
    est_rows = cur.fetchall()
    est = {(s, t): r for s, t, r in est_rows}

    conn.close()

    # 组装
    cols_by_tab = {}
    for s, t, col, typ, clen, nul, dflt in col_rows:
        cols_by_tab.setdefault((s, t), []).append({
            'name': col,
            'type': typ + (f'({clen})' if clen else ''),
            'nullable': nul == 'YES',
            'default': dflt,
        })

    pks_by_tab = {}
    for s, t, col in pk_rows:
        pks_by_tab.setdefault((s, t), []).append(col)

    fks = []
    for s, t, col, rs, rt, rcol, cname in fk_rows:
        fks.append({
            'table': f'{s}.{t}', 'column': col,
            'ref_table': f'{rs}.{rt}', 'ref_column': rcol,
            'constraint': cname,
        })

    uq_by_tab = {}
    for s, t, col in uq_rows:
        uq_by_tab.setdefault((s, t), []).append(col)

    schema = []
    for s, t in tables:
        key = (s, t)
        schema.append({
            'schema': s,
            'table': t,
            'columns': cols_by_tab.get(key, []),
            'primary_key': pks_by_tab.get(key, []),
            'unique_keys': uq_by_tab.get(key, []),
            'est_rows': est.get(key, 0),
        })

    out = {
        'database': 'hbairport',
        'tables': schema,
        'foreign_keys': fks,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print('FK_COUNT:', len(fks))
    for f in fks:
        print('   ', f['table'], f['column'], '->', f['ref_table'], f['ref_column'])
    print('SAVED:', OUT)


if __name__ == '__main__':
    main()
