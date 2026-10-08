# -*- coding: utf-8 -*-
"""探测：查询一个合同（GL-XZ-2026001）可到达的所有节点（表+字段），并做数据级验证。"""
import pg8000.dbapi as pg
from app.config import db_options

conn = pg.connect(**db_options('hbairport'))
cur = conn.cursor()

CNO = 'GL-XZ-2026001'

def q(sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return cols, cur.fetchall()

# 1) 主记录关键字段
cols, rows = q("""
    SELECT id, contract_no, submission_id, submitter_user_id, submitter_department_id,
           enterprise_scc, gzw_biz_id, gzw_report_serial, related_contract_id,
           related_tender_id, indicator_code
    FROM contract_main WHERE contract_no = %s LIMIT 1""", (CNO,))
row = rows[0]
rec = dict(zip(cols, row))
mid = rec['id']
print('== 主记录锚点 ==')
for c in cols:
    print('  contract_main.%s = %s' % (c, rec[c]))

checks = []

def check(label, sql, params):
    try:
        c, r = q(sql, params)
        checks.append((label, len(r)))
        print('  [%s] %d 条' % (label, len(r)))
    except Exception as e:
        checks.append((label, 'ERR: ' + str(e).split('M')[0]))
        print('  [%s] 查询失败 %s' % (label, str(e)[:120]))

print('\n== 一级外键链路（数据库 FK） ==')
check('contract_counterparty 相对方 (main_id)', 'SELECT 1 FROM contract_counterparty WHERE main_id=%s LIMIT 1', (mid,))
check('contract_payment_node 付款节点 (main_id)', 'SELECT 1 FROM contract_payment_node WHERE main_id=%s LIMIT 1', (mid,))
check('contract_sales_purchase_link 购销 (main_id)', 'SELECT 1 FROM contract_sales_purchase_link WHERE main_id=%s LIMIT 1', (mid,))

print('\n== 二级：填报表链 ==')
check('contract_submission (submission_id)', 'SELECT 1 FROM contract_submission WHERE id=%s LIMIT 1', (rec['submission_id'],))
check('contract_submission 关联审批字段存在', """
    SELECT column_name FROM information_schema.columns
    WHERE table_name='contract_submission' AND column_name IN
    ('approval_workflow_id','approval_step_index','status','submitter_user_id','approval_form_id','workflow_instance_id')""", ())

print('\n== 二级：履约链（related_contract_id） ==')
for t in ['contract_perf_settlement', 'contract_perf_invoice', 'contract_perf_delivery',
          'contract_perf_receipt']:
    check(t + ' (related_contract_id)',
          'SELECT 1 FROM ' + t + ' WHERE related_contract_id=%s LIMIT 1', (mid,))
check('contract_perf_shipping_attachment (sales_purchase_link_id)', """
    SELECT 1 FROM contract_perf_shipping_attachment sa
    WHERE sa.sales_purchase_link_id IN (SELECT id FROM contract_perf_sales_purchase_link
        WHERE sales_contract_id=%s OR related_purchase_contract_id=%s) LIMIT 1""", (mid, mid))

print('\n== 二级：提交人 / 部门 / 企业 / 国资 ==')
check('app_user 提交人 (submitter_user_id)', 'SELECT 1 FROM app_user WHERE id=%s LIMIT 1', (rec['submitter_user_id'],))
check('department 部门 (submitter_department_id)', 'SELECT 1 FROM department WHERE id=%s LIMIT 1', (rec['submitter_department_id'],))
check('comp_enterprise_ledger 企业台账 (enterprise_scc)', """
    SELECT 1 FROM comp_enterprise_ledger WHERE enterprise_scc=%s LIMIT 1""", (rec['enterprise_scc'],))
check('gzw_business_data 国资业务数据 (gzw_biz_id)', """
    SELECT 1 FROM gzw_business_data WHERE biz_id=%s LIMIT 1""", (rec['gzw_biz_id'],))

print('\n== 三级：履约填报 → 审批流 ==')
check('contract_performance_submission (submission_id 抽样)', """
    SELECT 1 FROM contract_performance_submission WHERE id IN
    (SELECT submission_id FROM contract_perf_settlement WHERE related_contract_id=%s) LIMIT 1""", (mid,))
check('approval_workflow_def 审批流定义存在', """
    SELECT column_name FROM information_schema.columns
    WHERE table_name='approval_workflow_def' AND column_name IN
    ('biz_key','biz_type','workflow_key','indicator_code')""", ())

print('\n== 汇总 ==')
for label, n in checks:
    print('  %-46s %s' % (label, n))
conn.close()
