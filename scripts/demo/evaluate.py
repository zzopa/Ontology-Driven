"""Read-only live baseline. Reference results are compared in the same transaction."""
import json
from pathlib import Path
from time import perf_counter

from app import core
from app.planner import Planner
from app.query import Compiler, Statement, execute
from app.metadata import save_snapshot


CASES = [
    ('员工有多少人','comp_employee','count','SELECT COUNT(*) AS n FROM comp_employee'),
    ('合同有多少份','contract_main','count','SELECT COUNT(*) AS n FROM contract_main'),
    ('资金流水有多少笔','fund_flow_record','count','SELECT COUNT(*) AS n FROM fund_flow_record'),
    ('会议有多少条','triple_major_meeting_record','count','SELECT COUNT(*) AS n FROM triple_major_meeting_record'),
    ('合同总金额是多少','contract_main','sum',None),
    ('资金总金额是多少','fund_flow_record','sum',None),
    ('今年会议有多少条','triple_major_meeting_record','count',None),
    ('去年会议有多少条','triple_major_meeting_record','count',None),
    ('2026年9月会议有多少条','triple_major_meeting_record','count',None),
    ('按月份统计会议数量','triple_major_meeting_record','count',None),
    ('今年资金有多少笔','fund_flow_record','count',None),
    ('去年资金有多少笔','fund_flow_record','count',None),
    ('2026年9月资金总金额','fund_flow_record','sum',None),
    ('按月份统计资金总金额','fund_flow_record','sum',None),
    ('信科资金有多少笔','fund_flow_record','count',None),
    ('信科公司会议有多少条','triple_major_meeting_record','count',None),
    ('查找信科公司李康平的情况','comp_employee','detail',None),
    ('查找信科李康平的情况','comp_employee','detail',None),
    ('李康平工资合计','comp_payroll_monthly','sum',None),
    ('李康平2026年9月工资合计','comp_payroll_monthly','sum',None),
    ('合同金额大于100万元有多少份','contract_main','count',None),
    ('资金金额大于100万元有多少笔','fund_flow_record','count',None),
    ('会议名称为不存在的验收会议有多少条','triple_major_meeting_record','count',None),
    ('资金流水账户余额总额是多少',None,None,'快照'),
    ('2026年13月会议有多少条',None,None,'月份无效'),
]


def reference(catalog,plan):
    """Independent SQL for configured baseline domains (evaluation only)."""
    table=plan['subject']
    source='public.'+table
    fields=catalog.entities[table]['attributes']
    clauses,params=[],[]
    for c in plan['conditions']:
        other=c.get('table') or table
        col=c['col']
        if other!=table:
            if table=='comp_payroll_monthly' and other=='comp_employee':
                clauses.append('employee_id IN (SELECT id FROM public.comp_employee WHERE full_name=%s)')
                params.append(c['value'])
                continue
            raise ValueError('参考查询没有定义该关联')
        attr=fields[col]
        if 'path' in attr:
            expr='fields_json::jsonb ->> '+"'"+attr['path'][0].replace("'","''")+"'"
        else:
            expr=col
        op=c['op']
        if op in ('prefix','contains'):
            clauses.append('('+expr+')::text LIKE %s')
            params.append(('%' if op=='contains' else '')+str(c['value'])+'%')
        else:
            clauses.append(expr+' '+op+' %s')
            params.append(c['value'])
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    groups=[]
    select=[]
    if plan['intent']=='sum':
        cfg=Compiler(catalog).metric(plan)
        groups=list(cfg.get('group_by',[]))
        select.extend(groups)
    if plan.get('time_bucket'):
        attr=fields[plan['time_bucket']['field']]
        expr="fields_json::jsonb ->> '"+attr['path'][0]+"'" if 'path' in attr else plan['time_bucket']['field']
        length={'year':4,'month':7,'day':10}[plan['time_bucket']['grain']]
        expr='substring(('+expr+')::text from 1 for '+str(length)+')'
        select.append(expr+' AS __period')
        groups.append(expr)
    if plan['intent']=='sum':
        field=cfg['field']
        raw="CASE WHEN "+field+"::text ~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN "+field+'::numeric END'
        select += ['COALESCE(SUM('+raw+'),0) AS total','COUNT(*) AS n',
                   'COUNT(*) FILTER (WHERE '+field+' IS NULL OR '+field+"::text='') AS missing",
                   'COUNT(*) FILTER (WHERE '+field+" IS NOT NULL AND "+field+"::text<>'' AND NOT ("+field+"::text ~ '^[+-]?[0-9]+([.][0-9]+)?$')) AS invalid"]
    else:
        select.append('COUNT(*) AS n')
    return Statement('SELECT '+','.join(select)+' FROM '+source+where+
                     (' GROUP BY '+','.join(groups) if groups else ''),tuple(params))


def main():
    stats=core.initialize()
    catalog=core.current_catalog()
    planner=Planner(catalog,core.NAME_INDEX,core.ENTERPRISE_NAMES)
    conn=core.connect_db()
    report=[]
    try:
        for question,subject,intent,ref in CASES:
            started=perf_counter()
            try:
                plan,_=planner.parse(question)
                if subject is None:
                    raise AssertionError('应该拒绝此问题')
                assert (plan['subject'],plan['intent'])==(subject,intent), plan
                comp=Compiler(catalog)
                if intent=='detail':
                    actual=execute(conn,Statement('SELECT COUNT(*) AS n FROM ('+comp.matched(plan,subject).sql+') matched',comp.matched(plan,subject).params))
                else:
                    actual=execute(conn,comp.aggregate(plan))
                teacher=Statement(ref) if ref and ref.startswith('SELECT') else reference(catalog,plan)
                expected=execute(conn,teacher)
                keys=list(expected[0]) if expected else []
                normalize=lambda rows:sorted([json.dumps({k:str(row[k]) for k in keys},sort_keys=True) for row in rows])
                assert normalize(actual)==normalize(expected), '结果与参考SQL不一致'
                data_warning=None
                if any(row.get('invalid',0) for row in actual):
                    try:
                        core.do_ask(conn,question,use_model=False)
                    except ValueError as exc:
                        assert '非空值' in str(exc), str(exc)
                        data_warning='已拒绝不完整合计：'+str(exc)
                    else:
                        raise AssertionError('非数字金额应该拒绝回答')
                report.append({'question':question,'passed':True,'subject':subject,'intent':intent,
                               'reference_sql':teacher.sql,'data_warning':data_warning,'groups':len(actual),'seconds':round(perf_counter()-started,3)})
            except ValueError as exc:
                report.append({'question':question,'passed':subject is None and ref in str(exc),'message':str(exc)})
            except Exception as exc:
                report.append({'question':question,'passed':False,'message':str(exc)})
                conn.rollback()
    finally:
        conn.close()
    output={'database':stats['database'],'catalog_version':catalog.version,
            'passed':sum(c['passed'] for c in report),'total':len(report),'cases':report}
    save_snapshot(Path('artifacts/evaluations/live_baseline.json'),output)
    print(json.dumps(output,ensure_ascii=False,indent=2))
    if output['passed']!=output['total']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
