# -*- coding: utf-8 -*-
"""按合同编号查询全链路信息（演示，动态列名版）。"""
import pg8000.dbapi as pg
from app.config import db_options

CONTRACT_NO = 'GL-XZ-2026001'
conn = pg.connect(**db_options('hbairport'))
cur = conn.cursor()

def run(label, sql, params):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    print('\n### %s：%d 条' % (label, len(rows)))
    for row in rows:
        vals = []
        for c, v in zip(cols, row):
            if v is not None:
                vals.append('%s=%s' % (c, str(v)[:48]))
        print('   ', ' | '.join(vals))
    return cols, rows

# 1) 主表
cols, main = run('合同主表 contract_main', """
    SELECT id, contract_no, contract_name, title, subject_matter, status,
           enterprise_name, enterprise_scc, contract_category_level1,
           contract_category_level2, contract_phase, record_type,
           lump_sum_amount_wan, estimated_total_price_wan, winning_amount_wan,
           guarantee_fee_wan, guarantee_form, collateral_value_wan,
           currency_code, signing_at, performance_start_at, performance_term_days,
           pricing_type, receipt_payment_direction, procurement_method,
           via_bidding_procurement, tender_code, related_tender_id,
           related_contract_no, related_contract_id, source, source_id,
           gzw_biz_id, gzw_report_serial, submission_id, submitter_user_id,
           submitter_department_id, approval_remark, approval_step_index,
           contract_text_content, contract_attachment_ref, remark
    FROM contract_main WHERE contract_no = %s LIMIT 1""", (CONTRACT_NO,))
if not main:
    print('未找到合同：', CONTRACT_NO)
    conn.close()
    raise SystemExit
mid = main[0][0]

# 2) 相对方
run('合同相对方 contract_counterparty', 'SELECT * FROM contract_counterparty WHERE main_id = %s', (mid,))

# 3) 付款节点
run('付款节点 contract_payment_node', 'SELECT * FROM contract_payment_node WHERE main_id = %s', (mid,))

# 4) 购销关联
run('购销关联 contract_sales_purchase_link', """
    SELECT * FROM contract_sales_purchase_link
    WHERE main_id = %s OR sales_contract_id = %s OR purchase_contract_id = %s""",
    (mid, mid, mid))

# 5) 履约结算
run('履约结算 contract_perf_settlement', 'SELECT * FROM contract_perf_settlement WHERE related_contract_id = %s', (mid,))

# 6) 履约发票
run('履约发票 contract_perf_invoice', 'SELECT * FROM contract_perf_invoice WHERE related_contract_id = %s', (mid,))

# 7) 履约交货
run('履约交货 contract_perf_delivery', 'SELECT * FROM contract_perf_delivery WHERE related_contract_id = %s', (mid,))

# 8) 履约收据
run('履约收据 contract_perf_receipt', 'SELECT * FROM contract_perf_receipt WHERE related_contract_id = %s', (mid,))

# 9) 填报记录
sub_id = main[0][cols.index('submission_id')]
run('填报记录 contract_submission', 'SELECT * FROM contract_submission WHERE id = %s', (sub_id,))

conn.close()
