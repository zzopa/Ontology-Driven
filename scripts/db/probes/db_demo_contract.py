# -*- coding: utf-8 -*-
"""演示：按合同编号查询全链路信息。"""
import pg8000.dbapi as pg
import json
from app.config import db_options

CONN = db_options('hbairport')


def q(cur, sql, params=None):
    cur.execute(sql, params or [])
    cols = [d[0] for d in cur.description] if cur.description else []
    return cols, cur.fetchall()


def main():
    conn = pg.connect(**CONN)
    cur = conn.cursor()

    # 1) contract_main 字段
    cols, rows = q(cur, """
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_schema='public' AND table_name='contract_main'
        ORDER BY ordinal_position""")
    print('=== contract_main 字段（%d 个） ===' % len(rows))
    print([c for c, _ in rows])

    # 2) 找可能的"合同编号"字段：名字含 no / code / num / 编号
    cands = [c for c, t in rows if any(k in c.lower() for k in ('no', 'code', 'num', 'number', 'bh', 'bianhao'))]
    print('候选编号字段:', cands)

    # 3) 取 2 条样例数据
    cols2, rows2 = q(cur, 'SELECT * FROM contract_main LIMIT 2')
    for row in rows2:
        print('\n--- 样例合同 ---')
        for c, v in zip(cols2, row):
            if v is not None:
                s = str(v)
                print('  %s = %s' % (c, s[:80]))

    conn.close()


if __name__ == '__main__':
    main()
