from types import SimpleNamespace

from app.ontology import Catalog


def fixture_catalog(namespace='public'):
    definitions = {
        'comp_employee': [('id','integer','主键'),('full_name','text','姓名'),('enterprise_scc','text','企业代码')],
        'comp_payroll_monthly': [('id','integer','主键'),('employee_id','integer','员工ID'),('amount','numeric','工资(元)'),('payroll_month','text','薪酬月份')],
        'contract_main': [('id','integer','主键'),('contract_no','text','合同编号'),('amount','numeric','金额(万元)'),('signing_at','text','签订时间'),('enterprise_scc','text','企业代码')],
        'fund_flow_record': [('id','integer','主键'),('related_contract_id','integer','关联合同'),('related_triple_major_id','integer','关联会议'),('transaction_amount','numeric','交易金额(元)'),('account_balance','numeric','余额(元)'),('currency','text','币种'),('transaction_type','text','交易类型'),('transaction_at','text','交易日期'),('our_unit_scc','text','企业代码')],
        'triple_major_meeting_record': [('id','integer','主键'),('meeting_code','text','会议编号'),('enterprise_scc','text','企业代码'),('fields_json','jsonb','会议内容')],
        'project_budget': [('id','integer','主键'),('budget_amount','numeric','预算金额(元)')],
    }
    tables = [{'schema':namespace,'name':table,'table':table,
               'comment':'项目预算明细' if table=='project_budget' else table,
               'primary_key':['id'],'unique_constraints':[], 'est_rows':0,
               'columns':[{'name':key,'type':typ,'comment':comment,'nullable':key!='id','default':None}
                          for key,typ,comment in cols]} for table,cols in definitions.items()]
    overrides = {
        'entities': {
            'comp_employee': {'name':'员工','aliases':['员工','人员'],'identity_fields':['full_name'],
                'bindings':{'name':'full_name','company':'enterprise_scc'},'name_lookup':True},
            'comp_payroll_monthly': {'name':'薪酬','aliases':['薪酬','工资'],'identity_fields':['payroll_month'],
                'bindings':{'time':'payroll_month'},'metrics':{'pay':{'field':'amount','unit':'元','name':'工资合计','default':True}}},
            'contract_main': {'name':'合同','aliases':['合同'],'identity_fields':['contract_no'],
                'bindings':{'code':'contract_no','time':'signing_at','company':'enterprise_scc'},
                'metrics':{'amount':{'name':'合同金额合计','field':'amount','unit':'万元','default':True}}},
            'fund_flow_record': {'name':'资金流水','aliases':['资金','资金流水'],'identity_fields':['transaction_at'],
                'bindings':{'time':'transaction_at','company':'our_unit_scc'},
                'attributes':{'account_balance':{'aggregation':'nonadditive','aliases':['余额']}},
                'metrics':{'amount':{'name':'交易金额合计','field':'transaction_amount','unit':'元','default':True,
                                     'group_by':['currency','transaction_type']}}},
            'triple_major_meeting_record': {'name':'会议','aliases':['会议','会议信息'],'identity_fields':['meeting_name'],
                'bindings':{'code':'meeting_code','time':'meeting_time','company':'enterprise_scc'},
                'json_attributes':{'meeting_name':{'column':'fields_json','path':['会议名称'],'label':'会议名称'},
                                   'meeting_time':{'column':'fields_json','path':['开始时间'],'label':'会议开始时间'}}},
        },
        'enterprise_aliases':{'信科':'SCC-XK','信科公司':'SCC-XK'},
        'relations':{'comp_employee':{'comp_payroll_monthly':{'name':'薪酬记录','aliases':['工资','薪酬']}},
                     'contract_main':{'fund_flow_record':{'name':'资金流水','aliases':['资金流水','实际付款']}},
                     'triple_major_meeting_record':{'fund_flow_record':{'name':'资金流水','aliases':['涉及资金','关联资金']}}},
        'logical_links':[
            {'source':'comp_payroll_monthly.employee_id','target':'comp_employee.id'},
            {'source':'fund_flow_record.related_contract_id','target':'contract_main.id'},
            {'source':'fund_flow_record.related_triple_major_id','target':'triple_major_meeting_record.id'},
        ],
        'sensitive_fields':['id_card','mobile','gzw_encrypted_payload'],
    }
    cfg=SimpleNamespace(excluded_prefixes=('sys_',),excluded_suffixes=('_serial_bucket',),max_depth=3,max_related_tables=12)
    return Catalog({'database':'test','tables':tables,'foreign_keys':[]},overrides,cfg)
