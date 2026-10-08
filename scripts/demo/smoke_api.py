"""Exercise the running service's model/stream/pagination without business writes."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from time import perf_counter
import urllib.request
from http.cookiejar import CookieJar
from threading import local

from app.metadata import save_snapshot
CLIENT = local()


def request(path,body):
    req=urllib.request.Request('http://127.0.0.1:8088'+path,
        data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','X-Hbask-Request':'1'})
    if not hasattr(CLIENT, 'opener'):
        CLIENT.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    with CLIENT.opener.open(req,timeout=110) as response:
        return json.load(response)


def check(question,subject):
    started=perf_counter()
    try:
        value=request('/api/ask',{'question':question})
        assert value['ok'] and value['parse']['subject']==subject, '主体不符'
        if question=='查找信科公司李康平的情况':
            assert value['main_total']==1, '简称匹配不应被错误的企业名称条件过滤掉'
        if value.get('drill_token'):
            page=request('/api/records',{'token':value['drill_token'],'page':1})
            assert page['total']==value['main_total'], '分页统计不一致'
        return {'question':question,'passed':True,'source':value['source'],
            'main_total':value['main_total'],'validation':value.get('answer_validation'),
            'claims':len(value.get('claims',[])),'facts':len(value.get('evidence',{}).get('facts',[])),
            'timings':value['timings'],'seconds':round(perf_counter()-started,3)}
    except Exception as exc:
        return {'question':question,'passed':False,'error':str(exc)}


def main():
    cases=[('查找信科公司李康平的情况','comp_employee'),
           ('今年签了多少合同','contract_main'),
           ('资金总金额是多少','fund_flow_record'),
           ('按月份统计会议数量','triple_major_meeting_record')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        report=list(pool.map(lambda item:check(*item),cases))
    output={'passed':sum(r['passed'] for r in report),'total':len(report),'cases':report}
    save_snapshot(Path('artifacts/evaluations/model_api_smoke.json'),output)
    print(json.dumps(output,ensure_ascii=False,indent=2))
    if output['passed']!=output['total']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
