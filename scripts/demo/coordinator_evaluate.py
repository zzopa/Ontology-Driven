"""Read-only operations QA acceptance against independently built reference SQL."""
import argparse
from contextlib import closing
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
from pathlib import Path
import sqlite3
from time import monotonic

from app import core, llm
from app.admin.security import Vault
from app.agent.coordinator import Coordinator
from app.config import settings
from app.evidence import build_evidence, fallback_claims
from app.ontology import Catalog
from app.ops_governance import hydrate_catalog_in_memory
from app.query import Compiler, Statement, execute, identifier


def read_model():
    path = settings.admin_directory / 'management.sqlite3'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        active = json.loads(db.execute("SELECT value FROM preferences WHERE key='active_model'").fetchone()[0])
        row = db.execute('SELECT * FROM models WHERE id=? AND enabled=1', (active,)).fetchone()
        if not row:
            raise ValueError('当前内网模型未启用')
        model = dict(row)
    if not (settings.admin_directory / 'secret.key').exists():
        raise ValueError('原加密密钥不存在，不能创建替代密钥')
    model['key'] = Vault(settings.admin_directory).open(model['secret'])
    if not llm.local_model(model):
        raise ValueError('验收只允许当前明确授权的本机或内网模型')
    return model


def reference(catalog, case):
    expected = case['expected']
    table = expected['subject']
    meta = catalog.tables[table]
    source = identifier(meta.get('schema','public')) + '.' + identifier(meta['name'])
    conditions, values = [], []
    time_field = catalog.entities[table]['bindings'].get('time')
    requested_date = re.search(r'\d{4}-\d{2}-\d{2}',case['question'])
    if time_field and requested_date:
        conditions += ['t0.' + identifier(time_field) + ' >= %s', 't0.' + identifier(time_field) + ' < %s']
        start = date.fromisoformat(requested_date[0])
        values += [start.isoformat(),(start+timedelta(days=1)).isoformat()]
    if expected.get('filter'):
        condition = expected['filter']
        conditions.append('t0.' + identifier(condition['col']) + ' = %s')
        values.append(condition['value'])
    where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
    if expected['intent'] == 'count':
        return Statement('SELECT COUNT(*) AS n FROM ' + source + ' t0' + where, tuple(values))
    order = ','.join('t0.' + identifier(key) for key in meta.get('primary_key',[])) or 'to_jsonb(t0)'
    return Statement('SELECT to_jsonb(t0) AS row_data FROM ' + source + ' t0' + where + ' ORDER BY ' + order + ' LIMIT 50',tuple(values))


def evaluate(case, catalog, references, model):
    started, calls = monotonic(), 0
    def chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        return llm.chat(*args, **kwargs)
    report = {'id':case['id'], 'split':case['split'], 'question':case['question'], 'passed':False}
    conn = core.connect_db(repeatable_read=True)
    try:
        with llm.model_context(model) if model else llm.model_context({'url':'http://127.0.0.1','model':'disabled','key':''}):
            plan = Coordinator(catalog,chat if model else None,references).plan(case['question'])
            if plan.get('outcome'):
                report.update(outcome=plan['outcome'],reason=plan['answer'])
                return report
            expected = case['expected']
            if (plan['subject'],plan['intent']) != (expected['subject'],expected['intent']):
                report.update(reason='业务对象或查询方式与独立参考不符',subject=plan['subject'],intent=plan['intent'])
                return report
            compiler = Compiler(catalog)
            statement = compiler.aggregate(plan) if plan['intent']=='count' else compiler.detail(plan,[],50,200)
            actual, wanted = execute(conn,statement), execute(conn,reference(catalog,case))
            if plan['intent']=='count':
                same = actual == wanted
                main, total = actual, int(actual[0]['n'])
            else:
                main = [row['row_data'] for row in actual if row['bucket']=='__main']
                same = main == [row['row_data'] for row in wanted]
                total = max((int(row['total_count']) for row in actual),default=0)
            if not same:
                report['reason']='查询结果与独立参考SQL不符'
                return report
            evidence = build_evidence(catalog,plan,main,total,[],[])
            claims, answer = fallback_claims(catalog,plan,evidence,total)
            assert all(key in {f['id'] for f in evidence['facts']} for claim in claims for key in claim['fact_ids'])
            assert isinstance(answer,str) and answer
            report.update(passed=True,reference_sql_matches=True)
    except Exception as exc:
        report['error_type']=type(exc).__name__
        if isinstance(exc,(ValueError,AssertionError)):
            report['reason']=str(exc)[:300]
    finally:
        conn.close()
        report.update(seconds=round(monotonic()-started,3),model_calls=calls)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-model',action='store_true')
    parser.add_argument('--split',choices=['baseline','holdout','all'],default='all')
    parser.add_argument('--workers',type=int,choices=range(1,5),default=3)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--output',default='artifacts/evaluations/ops-coordinator.json')
    args = parser.parse_args()
    if settings.data_source != 'ops_ask':
        parser.error('请设置 HBASK_DATA_SOURCE=ops_ask；不会切换或写入业务数据')
    catalog = Catalog(json.loads(settings.schema_path.read_text(encoding='utf-8')),
                      json.loads(settings.ontology_path.read_text(encoding='utf-8')),settings)
    hydrate_catalog_in_memory(catalog)
    catalog.refresh_version()
    conn = core.connect_db()
    try:
        references = core.load_ops_references(conn,catalog)
    finally:
        conn.close()
    model = read_model() if args.live_model else None
    cases = json.loads((settings.ontology_path.parents[2]/'tests/ops_coordinator_cases.json').read_text(encoding='utf-8'))
    cases = [case for case in cases if args.split=='all' or case['split']==args.split]
    if args.limit:
        cases=cases[:args.limit]
    results=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending=[pool.submit(evaluate,case,catalog,references,model) for case in cases]
        for future in as_completed(pending):
            result=future.result(); results.append(result)
            if not result['passed'] or len(results)%15==0:
                print(json.dumps({'completed':len(results),'total':len(cases),'latest':result['id'],'passed':result['passed'],'reason':result.get('reason'),'error_type':result.get('error_type')},ensure_ascii=False),flush=True)
    passed=sum(result['passed'] for result in results)
    report={'catalog_version':catalog.version,'live_model':args.live_model,'model':model['model'] if model else None,
            'total':len(results),'passed':passed,'rate':passed/len(results) if results else 0,'cases':sorted(results,key=lambda r:r['id'])}
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False),flush=True)
    return 0 if passed==len(results) else 1


if __name__=='__main__':
    raise SystemExit(main())
