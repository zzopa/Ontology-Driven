"""Check the running API against read-only database counts; do not print employee rows."""
import json
import argparse
from http.cookiejar import CookieJar
from pathlib import Path
import urllib.request

from app import core
from app.config import settings
from app.metadata import save_snapshot
from app.ontology import Catalog
from app.query import Compiler, Statement, execute


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8088')
    args = parser.parse_args()
    catalog = Catalog(json.loads(settings.schema_path.read_text(encoding='utf-8')), core.STORE.load(), settings)
    company = catalog.overrides['enterprise_aliases']['信科公司']
    compiler = Compiler(catalog)
    spec = catalog.entities['comp_employee']
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    cases = [('查找信科公司夏姓员工的信息', '夏', 'detail'),
             ('查找信科公司姓夏的员工信息', '夏', 'detail'),
             ('信科公司夏姓员工有几名', '夏', 'count'),
             ('信科公司欧阳姓员工的信息', '欧阳', 'detail'),
             ('信科公司姓李的员工有多少人', '李', 'count')]
    report = []
    conn = core.connect_db()
    try:
        for question, surname, intent in cases:
            where, params = compiler.predicate('comp_employee', [
                {'col':spec['bindings']['company'],'op':'=','value':company},
                {'col':spec['bindings']['name'],'op':'prefix','value':surname}], 't0')
            expected = execute(conn, Statement('SELECT COUNT(*) AS n FROM ' + compiler.table('comp_employee') + ' t0 WHERE ' + where, tuple(params)))[0]['n']
            req = urllib.request.Request(args.base_url.rstrip('/') + '/api/ask/stream',
                data=json.dumps({'question':question},ensure_ascii=False).encode(),
                headers={'Content-Type':'application/json','X-Hbask-Request':'1'})
            with opener.open(req,timeout=110) as response:
                events = [json.loads(line) for line in response if line.strip()]
            errors = [event['message'] for event in events if event['type']=='error']
            assert not errors, errors
            result = next(event['data'] for event in events if event['type']=='result')
            assert result['main_total'] == expected, (result['main_total'], expected)
            assert result['intent'] == intent
            assert result['parse']['subject'] == 'comp_employee'
            assert surname+'%' in result['sql_params']
            assert any(c['col']==spec['bindings']['company'] and c['value']==company for c in result['parse']['conditions'])
            steps = result['execution_trace']
            assert all(step['state'] in ('completed','skipped') for step in steps)
            assert {'connect','plan','sql_compile','sql','evidence','answer','done'} <= {step['id'] for step in steps}
            assert any(event['type']=='step' for event in events)
            report.append({'question':question,'passed':True,'count':expected,'source':result['source'],
                           'steps':len(steps),'elapsed':result['elapsed']})
    finally:
        conn.close()
    output = {'passed':len(report),'total':len(cases),'base_url':args.base_url,
              'database':settings.db['database'],'cases':report}
    save_snapshot(Path('artifacts/evaluations/surname_trace_regression.json'), output)
    print(json.dumps(output,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
