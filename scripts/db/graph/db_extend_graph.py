# -*- coding: utf-8 -*-
"""扩展字段级图谱：并入数据级验证过的逻辑关联，输出 graph_full.json。"""
import os
import json
from scripts.paths import DATA_DIR

BASE = str(DATA_DIR)

FG = json.load(open(os.path.join(BASE, 'field_graph.json'), encoding='utf-8'))

# 逻辑关联（无外键约束，但字段名/数据已验证）：(from表.字段, to表.字段, 说明)
LOGICAL_LINKS = [
    ('contract_main.submission_id', 'contract_submission.id', '填报记录'),
    ('contract_main.submitter_user_id', 'app_user.id', '提交人'),
    ('contract_main.enterprise_scc', 'comp_enterprise_ledger.enterprise_scc', '企业台账'),
    ('contract_main.gzw_biz_id', 'gzw_business_data.biz_id', '国资业务数据'),
    ('contract_perf_settlement.related_contract_id', 'contract_main.id', '履约结算'),
    ('contract_perf_invoice.related_contract_id', 'contract_main.id', '履约发票'),
    ('contract_perf_delivery.related_contract_id', 'contract_main.id', '履约交货'),
    ('contract_perf_receipt.related_contract_id', 'contract_main.id', '履约收据'),
    ('contract_perf_settlement.submission_id', 'contract_performance_submission.id', '履约填报'),
    ('contract_perf_invoice.submission_id', 'contract_performance_submission.id', '履约填报'),
    ('contract_perf_delivery.submission_id', 'contract_performance_submission.id', '履约填报'),
    ('contract_perf_receipt.submission_id', 'contract_performance_submission.id', '履约填报'),
    ('contract_perf_shipping_attachment.sales_purchase_link_id',
     'contract_perf_sales_purchase_link.id', '履约购销关联'),
]

existing = set()
for l in FG['links']:
    existing.add((l['source'], l['target']))
logical = []
for a, b, note in LOGICAL_LINKS:
    if (a, b) not in existing and (b, a) not in existing:
        logical.append({'source': a, 'target': b, 'note': note, 'type': 'logical'})

# 原外键边标 type=fk
for l in FG['links']:
    l['type'] = 'fk'

FG['logical_links'] = logical
FG['links_all'] = FG['links'] + logical

with open(os.path.join(BASE, 'graph_full.json'), 'w', encoding='utf-8') as f:
    json.dump(FG, f, ensure_ascii=False, indent=1)

print('fk links:', len(FG['links']), 'logical links:', len(logical))
for l in logical:
    print('  LOGICAL', l['source'], '->', l['target'], '(', l['note'], ')')
