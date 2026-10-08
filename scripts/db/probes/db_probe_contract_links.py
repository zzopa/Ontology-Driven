# -*- coding: utf-8 -*-
"""探索合同关联表的关联字段。"""
import pg8000.dbapi as pg
from app.config import db_options

conn = pg.connect(**db_options('hbairport'))
cur = conn.cursor()

TABLES = ['contract_perf_settlement', 'contract_perf_invoice', 'contract_perf_delivery',
          'contract_perf_receipt', 'contract_perf_sales_purchase_link',
          'contract_perf_shipping_attachment', 'contract_submission',
          'contract_performance_submission', 'contract_payment_node',
          'contract_counterparty', 'contract_sales_purchase_link']

for t in TABLES:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position
    """, (t,))
    cols = [r[0] for r in cur.fetchall()]
    keys = [c for c in cols if any(k in c.lower() for k in
                                   ('main', 'contract', 'perf', 'submission', 'no', 'link'))]
    print('%-38s %3d cols | %s' % (t, len(cols), ', '.join(keys)))

conn.close()
