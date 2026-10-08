"""Freeze 30 seeded DB-backed questions; independently audit the production pipeline.

Reference SQL is specified when preparing questions, never derived from a model plan.
Each test compares generated results/reference results in one read-only REPEATABLE READ
transaction. Model planning bypasses memory. Failed cases remain in the manifest.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import random
import secrets
from time import perf_counter

from app import core
from app.metadata import fingerprint, save_snapshot
from app.query import Statement, execute, json_safe

DIRECTORY=Path('artifacts/evaluations/regression30')
MANIFEST=DIRECTORY/'questions.json'
MEETING_NAME='triple-major_mtg_会议名称'
MEETING_TIME='triple-major_mtg_会议时间开始时间'


def prepare():
    core.initialize()
    seed=secrets.randbits(31)
    rng=random.Random(seed)
    conn=core.connect_db(True)
    cases=[]
    def choose(sql,params=()):
        rows=execute(conn,Statement(sql,params))
        if not rows:
            raise RuntimeError('抽样池无可查询数据：'+sql)
        return rng.choice(rows)
    def add(domain,question,intent,table,sql,params=(),related=None):
        cases.append({'id':'Q%02d'%(len(cases)+1),'domain':domain,'question':question,
            'intent':intent,'target':table,'reference_sql':sql,'params':json_safe(list(params)),
            'related':related or []})
    def detail(domain,question,table,where='',params=(),related=None):
        add(domain,question,'detail',table,'SELECT to_jsonb(t) AS row_data FROM public.'+table+' t '+where,params,related)
    def money(field,table,where='',groups=()):
        numeric='CASE WHEN '+field+"::text ~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN "+field+'::numeric END'
        return ('SELECT '+(','.join(groups)+',' if groups else '')+'COUNT(*) AS n,COALESCE(SUM('+numeric+'),0) AS total,'+
            'COALESCE(MAX('+numeric+'),0) AS mx,COUNT(*) FILTER (WHERE '+field+' IS NULL OR '+field+"::text='') AS missing,"+
            'COUNT(*) FILTER (WHERE '+field+' IS NOT NULL AND '+field+"::text<>'' AND NOT ("+field+"::text ~ '^[+-]?[0-9]+([.][0-9]+)?$')) AS invalid FROM public."+
            table+' '+where+(' GROUP BY '+','.join(groups) if groups else ''))
    def relation(table,sql,params):
        return {'table':table,'reference_sql':sql,'params':json_safe(list(params))}
    try:
        names_sql="SELECT e.full_name FROM comp_employee e WHERE e.full_name IS NOT NULL AND length(e.full_name)>=2 AND (SELECT COUNT(*) FROM comp_employee x WHERE x.full_name=e.full_name)=1"
        e=choose(names_sql)
        detail('员工',f'查询员工 {e["full_name"]} 的基本信息','comp_employee','WHERE full_name=%s',(e['full_name'],))
        e=choose(names_sql+' AND EXISTS (SELECT 1 FROM comp_employee_employment_history h WHERE h.employee_id=e.id)')
        ref=relation('comp_employee_employment_history','SELECT COUNT(*) AS n FROM comp_employee_employment_history h WHERE employee_id IN (SELECT id FROM comp_employee WHERE full_name=%s)',(e['full_name'],))
        detail('员工',f'查看员工 {e["full_name"]} 的任职经历','comp_employee','WHERE full_name=%s',(e['full_name'],),[ref])
        e=choose(names_sql+' AND EXISTS (SELECT 1 FROM comp_payroll_monthly p WHERE p.employee_id=e.id)')
        add('员工',f'{e["full_name"]} 有多少条工资记录','count','comp_payroll_monthly','SELECT COUNT(*) AS n FROM comp_payroll_monthly WHERE employee_id IN (SELECT id FROM comp_employee WHERE full_name=%s)',(e['full_name'],))
        e=choose("SELECT e.full_name,p.payroll_month FROM comp_payroll_monthly p JOIN comp_employee e ON e.id=p.employee_id WHERE (SELECT COUNT(*) FROM comp_employee x WHERE x.full_name=e.full_name)=1 AND p.payroll_month ~ '^20[0-9]{2}-[0-9]{2}$' GROUP BY e.full_name,p.payroll_month HAVING COUNT(*) FILTER (WHERE NOT (p.sal_wage_total ~ '^[+-]?[0-9]+([.][0-9]+)?$'))=0 AND COUNT(p.sal_wage_total)>0")
        year,month=e['payroll_month'].split('-')
        add('员工',f'{e["full_name"]} {year}年{int(month)}月的应发工资合计是多少','sum','comp_payroll_monthly',money('sal_wage_total','comp_payroll_monthly','WHERE employee_id IN (SELECT id FROM comp_employee WHERE full_name=%s) AND payroll_month=%s'),(e['full_name'],e['payroll_month']))
        e=choose(names_sql+' AND EXISTS (SELECT 1 FROM comp_employee_employment_history h WHERE h.employee_id=e.id)')
        add('员工',f'员工 {e["full_name"]} 有多少条任职经历','count','comp_employee_employment_history','SELECT COUNT(*) AS n FROM comp_employee_employment_history WHERE employee_id IN (SELECT id FROM comp_employee WHERE full_name=%s)',(e['full_name'],))

        code_pool="SELECT contract_no FROM contract_main c WHERE contract_no IS NOT NULL AND length(contract_no)>0"
        c=choose(code_pool)
        detail('合同',f'查询合同编号为「{c["contract_no"]}」的基本信息','contract_main','WHERE contract_no=%s',(c['contract_no'],))
        c=choose(code_pool+' AND EXISTS (SELECT 1 FROM contract_payment_node n WHERE n.main_id=c.id)')
        ref=relation('contract_payment_node','SELECT COUNT(*) AS n FROM contract_payment_node WHERE main_id IN (SELECT id FROM contract_main WHERE contract_no=%s)',(c['contract_no'],))
        detail('合同',f'合同编号为「{c["contract_no"]}」的付款节点明细','contract_main','WHERE contract_no=%s',(c['contract_no'],),[ref])
        c=choose(code_pool+' AND EXISTS (SELECT 1 FROM contract_payment_node n WHERE n.main_id=c.id)')
        add('合同',f'合同编号为「{c["contract_no"]}」有多少条付款节点','count','contract_payment_node','SELECT COUNT(*) AS n FROM contract_payment_node WHERE main_id IN (SELECT id FROM contract_main WHERE contract_no=%s)',(c['contract_no'],))
        c=choose('SELECT DISTINCT substring(signing_at::text from 1 for 7) AS month FROM contract_main WHERE signing_at IS NOT NULL')
        year,month=c['month'].split('-')
        add('合同',f'{year}年{int(month)}月签订的合同总金额是多少','sum','contract_main',money('lump_sum_amount_wan','contract_main','WHERE signing_at::text LIKE %s'),(c['month']+'%',))
        aliases=core.current_catalog().overrides['enterprise_aliases']
        company=rng.choice(list(aliases))
        add('合同',f'{company}有多少份合同记录','count','contract_main','SELECT COUNT(*) AS n FROM contract_main WHERE enterprise_scc=%s',(aliases[company],))

        f=choose('SELECT DISTINCT substring(transaction_at::text from 1 for 7) AS month FROM fund_flow_record WHERE transaction_at IS NOT NULL')
        year,month=f['month'].split('-')
        add('资金',f'{year}年{int(month)}月资金流水的交易金额合计是多少','sum','fund_flow_record',money('transaction_amount','fund_flow_record','WHERE transaction_at::text LIKE %s',('currency','transaction_type')),(f['month']+'%',))
        f=choose("SELECT DISTINCT currency FROM fund_flow_record WHERE currency IS NOT NULL AND currency<>''")
        add('资金',f'资金流水中币种为「{f["currency"]}」的记录有多少笔','count','fund_flow_record','SELECT COUNT(*) AS n FROM fund_flow_record WHERE currency=%s',(f['currency'],))
        threshold=rng.choice([100,200,500,1000])
        add('资金',f'资金流水交易金额大于{threshold}万元的记录有多少笔','count','fund_flow_record','SELECT COUNT(*) AS n FROM fund_flow_record WHERE transaction_amount>%s',(threshold*10000,))
        company=rng.choice(list(aliases))
        add('资金',f'{company}有多少笔资金流水','count','fund_flow_record','SELECT COUNT(*) AS n FROM fund_flow_record WHERE our_unit_scc=%s',(aliases[company],))
        f=choose("SELECT transaction_no FROM fund_flow_record WHERE transaction_no IS NOT NULL AND transaction_no<>'' LIMIT 5000")
        detail('资金',f'查询资金流水交易编号为「{f["transaction_no"]}」的明细','fund_flow_record','WHERE transaction_no=%s',(f['transaction_no'],))

        m=choose("SELECT fields_json::jsonb ->> %s AS name FROM triple_major_meeting_record WHERE length(fields_json::jsonb ->> %s)>0",(MEETING_NAME,MEETING_NAME))
        detail('会议',f'会议名称为「{m["name"]}」的明细','triple_major_meeting_record','WHERE fields_json::jsonb ->> %s=%s',(MEETING_NAME,m['name']))
        m=choose("SELECT DISTINCT substring(fields_json::jsonb ->> %s from 1 for 7) AS month FROM triple_major_meeting_record WHERE length(fields_json::jsonb ->> %s)>=7",(MEETING_TIME,MEETING_TIME))
        year,month=m['month'].split('-')
        add('会议',f'{year}年{int(month)}月共有多少条会议记录','count','triple_major_meeting_record','SELECT COUNT(*) AS n FROM triple_major_meeting_record WHERE (fields_json::jsonb ->> %s) LIKE %s',(MEETING_TIME,m['month']+'%'))
        add('会议',f'{year}年按月份统计会议数量','count','triple_major_meeting_record','SELECT substring(fields_json::jsonb ->> %s from 1 for 7) AS __period,COUNT(*) AS n FROM triple_major_meeting_record WHERE (fields_json::jsonb ->> %s) LIKE %s GROUP BY 1',(MEETING_TIME,MEETING_TIME,year+'%'))
        company=rng.choice(list(aliases))
        add('会议',f'{company}有多少条会议记录','count','triple_major_meeting_record','SELECT COUNT(*) AS n FROM triple_major_meeting_record WHERE enterprise_scc=%s',(aliases[company],))
        m=choose("SELECT meeting_code FROM triple_major_meeting_record WHERE meeting_code IS NOT NULL AND meeting_code<>''")
        detail('会议',f'会议编号为「{m["meeting_code"]}」的详细信息','triple_major_meeting_record','WHERE meeting_code=%s',(m['meeting_code'],))

        add('资产','资产卡片一共有多少条记录','count','asset_card_record','SELECT COUNT(*) AS n FROM asset_card_record')
        a=choose("SELECT asset_no FROM asset_card_record WHERE asset_no IS NOT NULL AND asset_no<>''")
        detail('资产',f'资产卡片编号为「{a["asset_no"]}」的详细信息','asset_card_record','WHERE asset_no=%s',(a['asset_no'],))
        a=choose("SELECT asset_name FROM asset_card_record WHERE asset_name IS NOT NULL AND asset_name<>''")
        detail('资产',f'资产卡片名称为「{a["asset_name"]}」的明细','asset_card_record','WHERE asset_name=%s',(a['asset_name'],))
        a=choose("SELECT owner_unit_name FROM asset_card_record WHERE owner_unit_name IS NOT NULL AND owner_unit_name<>''")
        add('资产',f'资产卡片中资产权属单位为「{a["owner_unit_name"]}」的记录有多少条','count','asset_card_record','SELECT COUNT(*) AS n FROM asset_card_record WHERE owner_unit_name=%s',(a['owner_unit_name'],))
        a=choose("SELECT asset_category_name FROM asset_card_record WHERE asset_category_name IS NOT NULL AND asset_category_name<>''")
        add('资产',f'资产卡片中资产类别名称为「{a["asset_category_name"]}」的记录有多少条','count','asset_card_record','SELECT COUNT(*) AS n FROM asset_card_record WHERE asset_category_name=%s',(a['asset_category_name'],))

        add('采购','采购项目共有多少条记录','count','procurement_project','SELECT COUNT(*) AS n FROM procurement_project')
        p=choose("SELECT bidding_code FROM procurement_project WHERE bidding_code IS NOT NULL AND bidding_code<>''")
        detail('采购',f'采购项目编号为「{p["bidding_code"]}」的基本信息','procurement_project','WHERE bidding_code=%s',(p['bidding_code'],))
        p=choose("SELECT purchase_name FROM procurement_project WHERE purchase_name IS NOT NULL AND purchase_name<>''")
        detail('采购',f'采购项目名称为「{p["purchase_name"]}」的明细','procurement_project','WHERE purchase_name=%s',(p['purchase_name'],))
        p=choose("SELECT bidding_code FROM procurement_project p WHERE bidding_code IS NOT NULL AND EXISTS (SELECT 1 FROM procurement_award a WHERE a.project_id=p.id)")
        ref=relation('procurement_award','SELECT COUNT(*) AS n FROM procurement_award WHERE project_id IN (SELECT id FROM procurement_project WHERE bidding_code=%s)',(p['bidding_code'],))
        detail('采购',f'采购项目编号为「{p["bidding_code"]}」的中标明细','procurement_project','WHERE bidding_code=%s',(p['bidding_code'],),[ref])
        p=choose("SELECT bidding_code FROM procurement_project p WHERE bidding_code IS NOT NULL AND EXISTS (SELECT 1 FROM procurement_bid_submission b WHERE b.project_id=p.id)")
        add('采购',f'采购项目编号为「{p["bidding_code"]}」有多少条投标记录','count','procurement_bid_submission','SELECT COUNT(*) AS n FROM procurement_bid_submission WHERE project_id IN (SELECT id FROM procurement_project WHERE bidding_code=%s)',(p['bidding_code'],))
        assert len(cases)==30
        # Verify the complete manifest has actual results before freezing, not after testing.
        for case in cases:
            rows=execute(conn,Statement(case['reference_sql'],tuple(case['params'])))
            total=len(rows) if case['intent']=='detail' else sum(r['n'] for r in rows)
            if not total:
                raise RuntimeError(case['id']+' 抽样没有实际记录；本次准备失败，未发布题集')
        document={'seed':seed,'prepared_at':datetime.now(timezone.utc).isoformat(),
                  'database':core.SCHEMA['database'],'cases':cases}
        document['manifest_hash']=fingerprint(cases)
        save_snapshot(MANIFEST,document)
        print(json.dumps({'manifest':str(MANIFEST),'seed':seed,'cases':len(cases),'hash':document['manifest_hash']},ensure_ascii=False),flush=True)
    finally:
        conn.close()


def canonical(value):
    if isinstance(value,dict):
        return {k:canonical(v) for k,v in sorted(value.items())}
    if isinstance(value,list):
        return [canonical(v) for v in value]
    if value is None or isinstance(value,bool):
        return value
    try:
        return format(Decimal(str(value)).normalize(),'f')
    except Exception:
        return str(value)


def audit(case):
    started=perf_counter()
    conn=core.connect_db(True)
    output={'id':case['id'],'domain':case['domain'],'question':case['question'],
            'reference_sql':case['reference_sql'],'reference_params':case['params'],'passed':False}
    try:
        expected=execute(conn,Statement(case['reference_sql'],tuple(case['params'])))
        actual=core.do_ask(conn,case['question'],use_model=True,use_cache=False)
        output.update({'answer':actual['answer'],'plan':actual['plan'],'source':actual['source'],
            'sql':actual['sql'],'sql_params':actual['sql_params'],'validation':actual['answer_validation'],
            'claims':actual['claims'],'main_total':actual['main_total'],'timings':actual['timings']})
        assert actual['intent']==case['intent'],'查询意图不符'
        target=actual['plan'].get('aggregate_target') or actual['plan']['subject']
        if case['intent']=='detail':
            target=actual['plan']['subject']
        assert target==case['target'],'查询粒度不符'
        if case['intent']=='detail':
            rows=[r['row_data'] for r in expected]
            assert actual['main_total']==len(rows),'完整命中数不符'
            expected_keys={str(r['id']) for r in rows}
            facts=[f for f in actual['evidence']['facts'] if f['table']==case['target']]
            assert facts and all(str(f['source']['record_key']['id']) in expected_keys for f in facts),'返回了参考条件外的主记录'
            assert {str(r['id']) for r in actual['main_rows']}.issubset(expected_keys),'卡片包含条件外记录'
            for related in case['related']:
                count=execute(conn,Statement(related['reference_sql'],tuple(related['params'])))[0]['n']
                matching=next((r for r in actual['related'] if r['table']==related['table']),None)
                assert matching and matching['count']==count,'关联范围/数量不符：'+related['table']
            output['reference_total']=len(rows)
        else:
            normalize=lambda rows:sorted(json.dumps(canonical(row),sort_keys=True,ensure_ascii=False) for row in rows)
            assert normalize(actual['main_rows'])==normalize(expected),'数值或分组与独立参考SQL不符'
            output['reference_rows']=json_safe(expected)
        # Re-read each fact by its source PK, independent of the generated query.
        for fact in actual['evidence']['facts']:
            if fact['kind']!='record':
                continue
            table=fact['table']
            source=execute(conn,Statement('SELECT to_jsonb(t) AS row_data FROM public.'+table+' t WHERE id=%s',
                                         (fact['source']['record_key']['id'],)))
            assert len(source)==1,'证据来源记录不存在/不唯一'
            raw=source[0]['row_data']
            for field,prop in fact['properties'].items():
                if field in raw:
                    value=raw[field]
                else:
                    attr=core.CATALOG.entities[table]['attributes'][field]
                    value=raw.get(attr['column'])
                    if isinstance(value,str):
                        value=json.loads(value)
                    for part in attr['path']:
                        value=value.get(part) if isinstance(value,dict) else None
                from app.evidence import sanitize
                value=sanitize(value,set(core.CATALOG.overrides.get('sensitive_fields',[])))
                assert canonical(prop['value'])==canonical(json_safe(value)),'事实字段与来源记录不符：'+table+'.'+field
        output['evidence_facts']=len(actual['evidence']['facts'])
        output['answer_audit']='待逐条核验结论语义；引用及数值检查不等于无编造'
        output['data_passed']=True
        # Detailed free-form answers require a field-level audit, not self-grading.
        output['passed']=case['intent']!='detail'
    except Exception as exc:
        output['error']=str(exc)
    finally:
        conn.close()
    output['seconds']=round(perf_counter()-started,3)
    print(json.dumps({k:output.get(k) for k in ('id','data_passed','passed','error','seconds')},ensure_ascii=False),flush=True)
    return output


def run(label):
    core.initialize()
    manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
    assert len(manifest['cases'])==30 and fingerprint(manifest['cases'])==manifest['manifest_hash']
    results=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(audit,case) for case in manifest['cases']]
        for future in as_completed(futures):
            results.append(future.result())
            save_snapshot(DIRECTORY/(label+'.json'),{'manifest_hash':manifest['manifest_hash'],'cases':sorted(results,key=lambda c:c['id'])})
    results.sort(key=lambda c:c['id'])
    report={'manifest_hash':manifest['manifest_hash'],'database':core.SCHEMA['database'],
            'catalog_version':core.CATALOG.version,'tested_at':datetime.now(timezone.utc).isoformat(),
            'seed':manifest['seed'],'total':30,'passed':sum(r['passed'] for r in results),
            'data_passed':sum(bool(r.get('data_passed')) for r in results),'cases':results}
    save_snapshot(DIRECTORY/(label+'.json'),report)
    print(json.dumps({k:report[k] for k in ('total','passed','data_passed','seed')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--label',default='initial')
    args=parser.parse_args()
    if args.prepare:
        if MANIFEST.exists():
            raise SystemExit('题集已冻结；不得覆盖失败题集')
        prepare()
    else:
        run(args.label)
