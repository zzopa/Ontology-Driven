# -*- coding: utf-8 -*-
"""分析 hbairport 表结构：业务域分组、跨域外键、强关联子图。"""
import os
import json
import re
from scripts.paths import DATA_DIR

BASE = str(DATA_DIR)

SCHEMA = os.path.join(BASE, 'hbairport_schema.json')

# 业务域规则（按表名前缀匹配，越长的前缀越优先）
DOMAIN_RULES = [
    ('app_role', '权限管理'),
    ('app_user', '权限管理'),
    ('app_sys', '权限管理'),
    ('approval_', '审批流程'),
    ('asset_', '资产管理'),
    ('bank_account', '资金账户'),
    ('comp_', '人力薪酬'),
    ('comprehensive_', '综合接待'),
    ('contract_', '合同管理'),
    ('dict_', '数据字典'),
    ('department', '组织架构'),
    ('filling_', '数据填报'),
    ('fin_', '财务预算'),
    ('fund_flow', '资金账户'),
    ('geo_', '地理信息'),
    ('guarantee_', '担保管理'),
    ('gzw_', '国资上报'),
    ('inv_plan_', '投资计划'),
    ('lending_', '借贷管理'),
    ('loan_guarantee', '担保管理'),
    ('oa_', '系统同步'),
    ('operation_audit', '系统审计'),
    ('org_structure_', '组织架构'),
    ('partner_', '合作方管理'),
    ('process_mgmt_', '流程管理'),
    ('procurement_', '采购管理'),
    ('receivables_', '应收管理'),
    ('sync_task', '系统同步'),
    ('va_', '数据验证'),
    ('flyway_', '系统元数据'),
]


def domain_of(table):
    for prefix, dom in DOMAIN_RULES:
        if table.startswith(prefix):
            return dom
    return '其他'


def bare(name):
    return name.split('.')[-1]


def main():
    with open(SCHEMA, encoding='utf-8') as f:
        data = json.load(f)

    tables = data['tables']
    fks = data['foreign_keys']

    # 表 -> 域
    dom_by_tab = {}
    for t in tables:
        dom_by_tab[bare(t['table'])] = domain_of(bare(t['table']))

    # 域统计
    dom_stats = {}
    for t in tables:
        dom = dom_by_tab[bare(t['table'])]
        dom_stats.setdefault(dom, {'count': 0, 'tables': []})
        dom_stats[dom]['count'] += 1
        dom_stats[dom]['tables'].append(bare(t['table']))

    # 外键：域内 vs 跨域
    cross_fks = []
    for fk in fks:
        fk['from'] = bare(fk['table'])
        fk['to'] = bare(fk['ref_table'])
        d1 = dom_by_tab[fk['from']]
        d2 = dom_by_tab[fk['to']]
        fk['from_domain'] = d1
        fk['to_domain'] = d2
        if d1 != d2:
            cross_fks.append(fk)

    print('=== 业务域统计（按表数排序） ===')
    for dom, st in sorted(dom_stats.items(), key=lambda x: -x[1]['count']):
        print(f"  {dom}: {st['count']} 张")

    print('\n=== 跨域外键（强关联主干） ===')
    for fk in cross_fks:
        print(f"  [{fk['from_domain']}] {fk['table']}.{fk['column']} -> "
              f"[{fk['to_domain']}] {fk['ref_table']}.{fk['ref_column']}")

    # 域间连接矩阵
    links_between = {}
    for fk in fks:
        d1, d2 = fk['from_domain'], fk['to_domain']
        key = tuple(sorted([d1, d2]))
        links_between.setdefault(key, 0)
        links_between[key] += 1

    print('\n=== 域间关联强度（边数） ===')
    for (d1, d2), n in sorted(links_between.items(), key=lambda x: -x[1]):
        print(f"  {d1} <-> {d2}: {n} 条外键")

    # 有外键参与的表（强关联子图）
    involved = set()
    for fk in fks:
        involved.add(fk['from'])
        involved.add(fk['to'])
    print(f'\n=== 参与外键关联的表：{len(involved)} 张 / 共 {len(tables)} 张 ===')

    # 保存分析结果
    out = {
        'domains': {d: {'count': st['count'], 'tables': st['tables']}
                    for d, st in dom_stats.items()},
        'cross_fks': cross_fks,
        'all_fks': fks,
        'involved_tables': sorted(involved),
    }
    with open(os.path.join(BASE, 'hbairport_analysis.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('\nSAVED: hbairport_analysis.json')


if __name__ == '__main__':
    main()
