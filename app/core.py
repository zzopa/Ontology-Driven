"""Service orchestration: catalog -> validated plan -> SQL -> evidence -> answer."""
from copy import deepcopy
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from datetime import date
import json
import logging
import re
import secrets
import urllib.error
from threading import RLock
from time import perf_counter, time

from app.config import settings
from app.agent.prompts import ANALYSIS_PROMPT
from app.agent.response import compose_document
from app.evidence import build_evidence, enrich, fallback_claims, sanitize, validate_claims
from app.evidence_transport import dumps as evidence_json, evidence_batches, restore_batches
from app.execution import ExecutionTrace
from app.llm import chat, model_available, model_fingerprint
from app.management import OntologyStore
from app.metadata import fingerprint, read_schema, save_snapshot
from app.ontology import Catalog
from app.planner import Planner
from app.query import Compiler, Statement, execute, identifier, json_safe

logger = logging.getLogger(__name__)
GRAPH_MAIN_LIMIT = settings.graph_main_limit
GRAPH_RELATED_LIMIT = settings.graph_related_limit
CATALOG = None
GRAPH = SCHEMA = ADJ = None
MEM = {'entries': []}
FIELD_COMMENTS = {}
DRILL_QUERIES = {}
LOCK = RLock()
STORE = OntologyStore(settings.ontology_path, settings)
NAME_INDEX, ENTERPRISE_NAMES = {}, {}
STATUS = {'ready': False, 'requests': 0, 'failures': 0, 'cache_hits': 0}
DURATIONS = []


def connect_db(repeatable_read=False):
    import pg8000.dbapi as pg
    options=settings.db.copy()
    options['timeout']=max(options.get('timeout',8),settings.query_timeout_ms/1000+5)
    conn = pg.connect(**options)
    cur = conn.cursor()
    try:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
                    if repeatable_read else 'SET TRANSACTION READ ONLY')
        cur.execute("SELECT set_config('statement_timeout', %s, true)",
                    (str(settings.query_timeout_ms),))
    except Exception:
        conn.close()
        raise
    finally:
        cur.close()
    return conn


def load_memory():
    try:
        with settings.memory_path.open(encoding='utf-8') as source:
            value = json.load(source)
        return value if isinstance(value.get('entries'), list) else {'entries': []}
    except (OSError, ValueError, AttributeError):
        return {'entries': []}


def cache_key(catalog):
    return 'ontology-flow-v2.4:' + catalog.version + ':' + model_fingerprint() + ':' + date.today().isoformat()


def load_lookups(conn, catalog, access=None):
    names, enterprises = {}, {}
    compiler = Compiler(catalog, access)
    for table, spec in catalog.entities.items():
        name = spec['bindings'].get('name')
        if spec.get('name_lookup') and name in spec['attributes']:
            rows = execute(conn, compiler.statement('SELECT DISTINCT ' +
                compiler.expression(table, name, 't0') + ' AS name FROM ' +
                compiler.table(table) + ' t0 WHERE ' + compiler.expression(table,name,'t0') +
                " IS NOT NULL LIMIT 50000"))
            names[table] = {r['name'] for r in rows if isinstance(r['name'],str) and r['name']}
        if spec.get('enterprise_lookup') and name:
            key = spec['bindings'].get('company')
            rows = execute(conn, compiler.statement('SELECT DISTINCT ' +
                compiler.expression(table,name,'t0') + ' AS name, ' +
                compiler.expression(table,key,'t0') + ' AS code FROM ' + compiler.table(table) + ' t0'))
            candidates = {}
            for row in rows:
                if row['name'] and row['code']:
                    candidates.setdefault(row['name'],set()).add(row['code'])
            enterprises.update({name: next(iter(codes)) for name,codes in candidates.items() if len(codes)==1})
    return names, enterprises


def metadata_diff(old, new):
    previous = {t['table']:t for t in (old or {}).get('tables',[])}
    current = {t['table']:t for t in new['tables']}
    changed = []
    for name in previous.keys() & current.keys():
        before = {k:v for k,v in previous[name].items() if k != 'est_rows'}
        after = {k:v for k,v in current[name].items() if k != 'est_rows'}
        if before != after:
            changed.append(name)
    return {'added_tables': sorted(current.keys()-previous.keys()),
            'removed_tables': sorted(previous.keys()-current.keys()),
            'changed_tables': sorted(changed)}


def initialize():
    with STORE.lock:
        return _initialize()


def _initialize():
    global CATALOG, GRAPH, SCHEMA, MEM, FIELD_COMMENTS, NAME_INDEX, ENTERPRISE_NAMES
    started = perf_counter()
    old = None
    try:
        old = json.loads(settings.schema_path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        pass
    conn = connect_db()
    try:
        schema = read_schema(conn,settings.schemas)
        schema['database_identity'] = {k:settings.db[k] for k in ('host','port','database')}
        schema['fingerprint'] = fingerprint({'metadata':schema['fingerprint'],
                                             'identity':schema['database_identity']})
        catalog = STORE.validate(STORE.load(),schema)
        names, enterprises = load_lookups(conn,catalog)
    finally:
        conn.close()
    diff = metadata_diff(old,schema)
    if old:
        save_snapshot(settings.schema_path.parent / 'previous_schema.json',old)
    save_snapshot(settings.schema_path,schema)
    save_snapshot(settings.graph_path,catalog.graph)
    save_snapshot(settings.schema_path.parent / 'ontology_compiled.json',catalog.public())
    with LOCK:
        CATALOG, GRAPH, SCHEMA = catalog,catalog.graph,schema
        MEM = load_memory()
        NAME_INDEX, ENTERPRISE_NAMES = names,enterprises
        FIELD_COMMENTS = {t:{field:attr.get('description','')
            for field,attr in spec['attributes'].items()} for t,spec in catalog.entities.items()}
        DRILL_QUERIES.clear()
        STATUS.update({'ready':True,'database':schema['database'],'tables':len(schema['tables']),
            'queryable_entities':len(catalog.entities),'fields':len(catalog.graph['field_nodes']),
            'relations':len(catalog.graph['links_all']),'version':catalog.version,
            'refreshed_at':schema['refreshed_at'],'metadata_diff':diff,
            'startup_seconds':round(perf_counter()-started,3),'warnings':catalog.warnings,
            'relation_validation':catalog.graph['stats']})
    return deepcopy(STATUS)


def current_catalog():
    if CATALOG is None:
        raise RuntimeError('服务尚未完成元数据初始化')
    return CATALOG


def activate_catalog(catalog, lookups=None):
    global CATALOG,GRAPH,FIELD_COMMENTS,NAME_INDEX,ENTERPRISE_NAMES
    if lookups is None:
        conn = connect_db()
        try:
            lookups = load_lookups(conn,catalog)
        finally:
            conn.close()
    names, enterprises = lookups
    save_snapshot(settings.graph_path,catalog.graph)
    save_snapshot(settings.schema_path.parent / 'ontology_compiled.json',catalog.public())
    with LOCK:
        CATALOG, GRAPH = catalog,catalog.graph
        NAME_INDEX, ENTERPRISE_NAMES = names, enterprises
        FIELD_COMMENTS = {t:{field:attr.get('description','') for field,attr in spec['attributes'].items()}
                          for t,spec in catalog.entities.items()}
        DRILL_QUERIES.clear()
        STATUS.update({'version':catalog.version,'queryable_entities':len(catalog.entities),
                       'relations':len(catalog.graph['links_all']),'warnings':catalog.warnings})


def ontology_document():
    with STORE.lock:
        catalog = current_catalog()
        document = STORE.load()
        return {'ok':True,'revision':fingerprint(document),'document':document,'catalog':catalog.public()}


def update_ontology(document, expected):
    with STORE.lock:
        catalog = STORE.validate(document,current_catalog().schema)
        conn = connect_db()
        try:
            lookups = load_lookups(conn,catalog)
        finally:
            conn.close()
        catalog = STORE.save(document,expected,current_catalog().schema)
        activate_catalog(catalog,lookups)
        return ontology_document()


def rollback_ontology(revision, expected):
    with STORE.lock:
        if revision not in {item['revision'] for item in STORE.history()}:
            raise ValueError('回滚版本不存在')
        document=json.loads((STORE.history_dir / (revision+'.json')).read_text(encoding='utf-8'))['document']
        return update_ontology(document,expected)


def status():
    with LOCK:
        return {'ok':True,**deepcopy(STATUS),
                'average_seconds':round(sum(DURATIONS)/len(DURATIONS),3) if DURATIONS else 0}


def remember_plan(question, plan, catalog):
    with LOCK:
        key = cache_key(catalog)
        existing = next((e for e in MEM['entries'] if e.get('version')==key and e.get('q')==question),None)
        if existing:
            existing.update({'parsed':json_safe(plan),'hits':existing.get('hits',0)+1})
        else:
            MEM['entries'].append({'q':question,'parsed':json_safe(plan),'version':key,'hits':1})
        save_snapshot(settings.memory_path,MEM)


def parse_question(question, catalog, use_model=True, use_cache=True, trace=None, lookups=None):
    with LOCK:
        cached = next((deepcopy(e['parsed']) for e in MEM['entries']
            if e.get('version')==cache_key(catalog) and e.get('q')==question),None)
    if cached and use_cache:
        STATUS['cache_hits'] += 1
        if trace:
            trace.start('plan_cache', '校验查询计划缓存', '校验本体版本、模型配置与日期；不缓存业务查询结果')
        validated = Compiler(catalog).validate(cached)
        if trace:
            trace.finish('plan_cache', '使用已校验的结构化计划；数据仍将实时查询',
                         ['主体：' + cached['subject'], '筛选：' + json.dumps(cached.get('conditions', []), ensure_ascii=False)])
        return validated,'memory·版本校验'
    names, enterprises = lookups if lookups is not None else (NAME_INDEX, ENTERPRISE_NAMES)
    planner = Planner(catalog,names,enterprises,
                      chat if use_model and model_available() else None, trace=trace)
    return planner.parse(question)


def field_meta(catalog, tables):
    return {table:{key:{'label':attr['label'],'comment':attr.get('description',''),
                       'unit':attr.get('unit','')} for key,attr in catalog.entities[table]['attributes'].items()}
            for table in tables}


def clean_rows(catalog,table,rows):
    hidden=set(catalog.overrides.get('sensitive_fields',[]))
    return [sanitize(enrich(json_safe(row),catalog.entities[table]),hidden) for row in rows]


def graph_view(subject,links):
    nodes=[{'id':subject,'level':0,'main':True}]
    seen={subject}
    for edge in links:
        if edge['table'] not in seen:
            nodes.append({'id':edge['table'],'level':edge['level'],'main':False})
            seen.add(edge['table'])
    return {'nodes':nodes,'edges':[{'source':e['from_tab'],'target':e['to_tab'],
                'fcol':e['fcol'],'tcol':e['tcol'],'pairs':e['pairs'],
                'type':e['type'],'note':e['note']} for e in links]}


def token_for(plan,catalog,total,target=None,related=None):
    token=secrets.token_urlsafe(24)
    now=time()
    with LOCK:
        for key in [key for key,item in DRILL_QUERIES.items()
                    if now-item['created']>settings.drill_ttl_seconds]:
            del DRILL_QUERIES[key]
        DRILL_QUERIES[token]={'plan':deepcopy(plan),'version':catalog.version,
            'created':now,'total':total,'target':target or plan['subject'],'related':related}
    return token


def saved_query(token,page,access=None):
    if not isinstance(token,str) or type(page) is not int or page<1:
        raise ValueError('明细参数无效')
    catalog=current_catalog()
    if access:
        catalog=access.catalog(catalog)
        if not catalog.entities:
            from fastapi import HTTPException
            raise HTTPException(403, '此业务账号尚未获授权可问数的指标或查看权限')
    with LOCK:
        saved=deepcopy(DRILL_QUERIES.get(token))
    if not saved or saved['version']!=catalog.version or time()-saved['created']>settings.drill_ttl_seconds:
        raise ValueError('查询已过期或本体已更新，请重新提问')
    return catalog,saved


def fetch_drill_page(conn,token,page,access=None):
    catalog,saved=saved_query(token,page,access)
    target=saved['target']
    rows=execute(conn,Compiler(catalog,access).page(saved['plan'],target,page,settings.page_size))
    if target!=saved['plan']['subject']:
        rows=[row['row_data'] for row in rows]
    return {'ok':True,'subject':target,'rows':clean_rows(catalog,target,rows),
            'total':saved['total'],'page':page,'page_size':settings.page_size,
            'field_meta':field_meta(catalog,[target])}


def fetch_related_page(conn,token,table,page,access=None):
    catalog,saved=saved_query(token,page,access)
    counts=saved.get('related') or {}
    if table not in counts:
        raise ValueError('关联表不属于当前查询')
    rows=execute(conn,Compiler(catalog,access).page(saved['plan'],table,page,settings.page_size))
    return {'ok':True,'table':table,'rows':clean_rows(catalog,table,[r['row_data'] for r in rows]),
            'total':counts[table],'page':page,'page_size':settings.page_size,
            'field_meta':field_meta(catalog,[table])}


def build_query_sql(subject,conditions,links,intent,main_limit=GRAPH_MAIN_LIMIT,related_limit=GRAPH_RELATED_LIMIT):
    catalog=current_catalog()
    plan={'subject':subject,'conditions':conditions,'intent':intent,'focus_tables':[]}
    compiler=Compiler(catalog)
    return (compiler.detail(plan,links,main_limit,related_limit) if intent=='detail'
            else compiler.aggregate(plan)).sql


def model_rows(rows, budget):
    """列名只传一次、全空列省略；不在行或 JSON 中间截断。"""
    if not rows or budget <= 2:
        return '{"columns":[],"rows":[]}', 0, 0
    columns = [key for key in rows[0]
               if any(row.get(key) is not None for row in rows)]
    prefix = '{"columns":' + json.dumps(columns, ensure_ascii=False, separators=(',', ':')) + ',"rows":['
    suffix = ']}'
    packed, used = [], len(prefix) + len(suffix)
    if used > budget:
        return '{"columns":[],"rows":[]}', 0, 0
    for row in rows:
        item = json.dumps([row.get(key) for key in columns], ensure_ascii=False,
                          default=str, separators=(',', ':'))
        if used + len(item) + (1 if packed else 0) > budget:
            break
        packed.append(item)
        used += len(item) + (1 if len(packed) > 1 else 0)
    return prefix + ','.join(packed) + suffix, len(packed), used



def answer(catalog,plan,evidence,main_total,use_model,question='',trace=None,delivery=None,on_claim=None):
    # Older query snapshots lack these scope fields; the actual query remains authoritative.
    evidence={**evidence,'scope':{**evidence.get('scope',{}),
        'main_table':plan['subject'],'main_name':catalog.entities[plan['subject']]['name'],
        'main_total':main_total,'name_field':catalog.entities[plan['subject']]['bindings'].get('name'),
        'person_records':bool(catalog.entities[plan['subject']].get('name_lookup'))}}
    claims,text=fallback_claims(catalog,plan,evidence,main_total)
    published=set()

    def publish(claim):
        if claim['text'] in published:
            return False
        if on_claim:
            # Called only from the coordinator thread, after fact validation.
            # Authorization/transport errors must propagate, not be swallowed as
            # model errors or cause subsequent unapproved content to be emitted.
            on_claim(deepcopy(claim))
        published.add(claim['text'])
        return True

    def warn(message):
        claim={'text':message,'fact_ids':deepcopy(claims[0]['fact_ids']),'kind':'limitations'}
        if publish(claim):
            claims.append(claim)
        else:
            # System coverage warnings keep their authoritative category even
            # if a model previously returned the same text as a finding.
            for previous in claims:
                if previous['text']==message:
                    previous['kind']='limitations'

    # The verified query summary is available before model packing or requests.
    for claim in claims:
        publish(claim)
    if not claims:
        publish({'text':text,'fact_ids':[]})
    validation='deterministic'
    if evidence['facts'] and use_model and model_available():
        report = delivery if delivery is not None else {}
        report.update({'queried_fact_count': len(evidence['facts']), 'sent_fact_count': 0,
                       'queried_property_count': sum(len(f.get('properties',{})) for f in evidence['facts']),
                       'sent_property_count': 0,
                       'queried_relationship_count': len(evidence.get('relationships', [])),
                       'sent_relationship_count': 0, 'completed_batches': 0,
                       'failed_batches': [], 'batch_errors': [], 'retry_count': 0,
                       'complete_queried_data': False})
        try:
            batches=evidence_batches(evidence,settings.evidence_budget)
            # Verify packing by losslessly restoring every value and relationship.
            restored, edges = restore_batches(batches)
            if ({f['id']: f for f in restored} != {f['id']: f for f in evidence['facts']} or
                    sorted(evidence_json(e) for e in edges) != sorted(evidence_json(e) for e in evidence.get('relationships', []))):
                raise ValueError('分批证据与原始查询结果不一致')
        except Exception:
            report['error'] = '完整证据打包失败，未发送不完整证据；请核对证据预算配置'
            logger.exception('完整证据打包失败')
            warn(report['error'])
            return claims,'\n'.join(c['text'] for c in claims),'evidence_fallback'
        report['batch_count'] = len(batches)
        report['batch_characters'] = [len(evidence_json(b.payload)) for b in batches]
        report['fragmented_fact_count'] = len({f['fact_id'] for b in batches for f in b.payload['record_fragments']})
        checked, sent_ids, sent_edges, sent_parts = {}, set(), [], {}
        prompt=ANALYSIS_PROMPT

        def submit(pool,i,attempt=0):
            batch=batches[i-1]
            stage='answer_batch_%d' % i
            if trace and not attempt:
                trace.start(stage,'传递并核验第 %d/%d 批证据' % (i,len(batches)),
                            '完整发送本批记录与字段值；不把本批记录数作为全量命中数',
                            ['本批 %d 条完整事实、%d 条关联、%d 个长记录分片，%d 字符' % (
                                len(batch.payload['facts']),len(batch.payload['relationships']),
                                len(batch.payload['record_fragments']),report['batch_characters'][i-1])])
            elif trace:
                trace.update(stage,'模型请求暂未完成，正在重试第 %d 次；原始证据不变' % attempt)
            # Provider configuration is frozen per request, including in workers.
            return pool.submit(copy_context().run,chat,prompt,
                evidence_json({'question':question,'plan':plan,'evidence':batch.payload}),
                max_tokens=1800,timeout=settings.evidence_timeout_seconds,
                enable_thinking=settings.evidence_enable_thinking)

        def consume(i,future):
            batch=batches[i-1]
            stage='answer_batch_%d' % i
            received=False
            accepted=[]
            try:
                content=future.result()
                received=True
                report['completed_batches'] += 1
                sent_ids.update(f['id'] for f in batch.payload['facts'])
                sent_edges.extend(batch.payload['relationships'])
                for part in batch.payload['record_fragments']:
                    sent_parts.setdefault(part['fact_id'], set()).add(part['part'])
                match=re.search(r'\{.*\}',content,re.S)
                payload=json.loads(match.group()) if match else {}
                batch_claims=payload.get('claims')
                if batch_claims != []:
                    accepted=validate_claims(batch_claims,batch.evidence)
                    checked[i]=accepted
                if trace:
                    trace.finish(stage,'本批证据已传递；可核验结论已保留')
            except Exception as exc:
                logger.info('第 %d 批模型回答未通过检查或生成失败，保留事实摘要并继续后续批次',i)
                report['failed_batches'].append(i)
                # Safe categories only: raw provider errors may echo request data.
                report['batch_errors'].append({'batch':i,
                    'stage':'answer_validation' if received else 'provider_request',
                    'error_type':type(exc).__name__})
                if trace:
                    trace.finish(stage,('本批证据已传递，但回答核验未通过；保留事实摘要' if received else
                        '本批模型请求失败，传递未确认；保留数据库事实并继续后续批次'),state='failed')
            # Release completed, checked claims now; never wait for a slower
            # batch, and never stream raw unvalidated model tokens.
            for claim in accepted:
                if publish(claim):
                    claims.append(claim)
        workers=min(settings.evidence_workers,len(batches))
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='evidence') as pool:
            pending={submit(pool,i):(i,0) for i in range(1,workers+1)}
            next_batch=workers+1
            while pending:
                finished,_=wait(pending,timeout=10,return_when=FIRST_COMPLETED)
                if not finished and trace:
                    for i,attempt in sorted(pending.values()):
                        trace.update('answer_batch_%d' % i,'正在等待模型响应；本批原始证据已提交请求%s' % (
                            '（重试 %d 次）' % attempt if attempt else ''))
                for future in sorted(finished,key=lambda f:pending[f]):
                    i,attempt=pending.pop(future)
                    error=future.exception()
                    # Retry transient request failures only, not rejected claims,
                    # auth failures or permanent client errors. No retry data loss.
                    transient=isinstance(error,(TimeoutError,ConnectionError,urllib.error.URLError))
                    if isinstance(error,urllib.error.HTTPError):
                        transient=error.code in (408,429,500,502,503,504)
                    if transient and attempt<settings.evidence_retries:
                        report['retry_count']+=1
                        pending[submit(pool,i,attempt+1)]=(i,attempt+1)
                        continue
                    consume(i,future)
                    if next_batch<=len(batches):
                        pending[submit(pool,next_batch)]=(next_batch,0)
                        next_batch+=1
        for fid,parts in sent_parts.items():
            expected = next(p['parts'] for b in batches for p in b.payload['record_fragments'] if p['fact_id']==fid)
            if len(parts)==expected:
                sent_ids.add(fid)
        report.update({'sent_fact_count': len(sent_ids), 'sent_relationship_count': len(sent_edges),
                       'sent_property_count':sum(len(f.get('properties',{})) for f in evidence['facts'] if f['id'] in sent_ids),
                       'complete_queried_data': len(sent_ids)==len(evidence['facts']) and
                           len(sent_edges)==len(evidence.get('relationships', [])),
                       'coverage': deepcopy(evidence.get('coverage', []))})
        # A deterministic count/identity overview survives model omissions or failure.
        validation=('evidence_fallback' if report['failed_batches'] else
                    'source_and_numeric_checked' if checked else 'deterministic')
        if not report['complete_queried_data']:
            warning='部分证据的模型传递未确认完成，以上保留数据库实际命中结果，不能视为全部关联数据的模型解读。'
            warn(warning)
        if report['fragmented_fact_count']:
            warning='超长记录已完整分片打包；只有请求完成才计为已传递，不凭单个分片推断整条记录。原始完整内容可在明细查看。'
            warn(warning)
        text='\n'.join(c['text'] for c in claims)
    return claims,text,validation


def do_ask(conn,text,on_event=None,use_model=True,use_cache=True,trace=None,access=None):
    catalog=current_catalog()
    if access:
        catalog=access.catalog(catalog)
        if not catalog.entities:
            from fastapi import HTTPException
            raise HTTPException(403, '此业务账号尚未获授权可问数的指标或查看权限')
        # Company aliases contain no business rows, but restrict their use to scope.
        if not access.unrestricted:
            catalog.overrides['enterprise_aliases']={k:v for k,v in catalog.overrides.get('enterprise_aliases',{}).items()
                                                    if str(v).strip().lower() in access.companies}
    compiler=Compiler(catalog,access)
    started=perf_counter()
    timings={}
    trace = trace or ExecutionTrace(on_event)
    def emit(kind,**value):
        if on_event:
            on_event({'type':kind,**value})
    try:
        emit('progress',stage='parse',message='正在匹配业务本体与查询条件…',percent=12)
        checkpoint=perf_counter()
        lookups=load_lookups(conn,catalog,access) if access else None
        plan,source=parse_question(text,catalog,use_model,use_cache,trace,lookups)
        timings['parse']=round(perf_counter()-checkpoint,3)
        subject,intent=plan['subject'],plan['intent']
        trace.start('plan', '校验最终查询计划', '验证最终主体、字段、统计口径和全部筛选条件')
        compiler.validate(plan)
        trace.finish('plan', '查询计划校验通过',
                     ['计划来源：' + source, '主体：' + catalog.entities[subject]['name'] + ' · ' + subject,
                      '最终筛选：' + json.dumps(plan.get('conditions', []), ensure_ascii=False, default=str)])
        emit('progress',stage='graph',message='正在校验业务关系与统计口径…',percent=35)
        trace.start('relations', '校验关联路径', '仅使用当前本体中可校验且无歧义的关系')
        targets=plan.get('focus_tables',[])
        if intent!='detail':
            target=plan.get('aggregate_target') or subject
            targets=[target] if target!=subject else []
            links=catalog.links(subject,targets) if targets else []
        else:
            links=catalog.links(subject,targets)
        for condition in plan.get('conditions',[]):
            table=condition.get('table') or subject
            if table!=subject:
                for edge in catalog.path(subject,table):
                    if not any(e['table']==edge['table'] for e in links):
                        links.append(edge)
        trace.finish('relations', '已确认 %d 条表关联路径' % len(links),
                     ['%s → %s · %s' % (edge['parent'], edge['table'], edge['note']) for edge in links])
        emit('progress',stage='sql',message='正在执行完整范围查询，明细分页展示…',percent=58)
        checkpoint=perf_counter()
        trace.start('sql_compile', '编译参数化 SQL', '从已校验计划编译只读 SQL；筛选值与 SQL 分离绑定')
        statement=(compiler.detail(plan,links,settings.graph_main_limit,settings.graph_related_limit)
                   if intent=='detail' else compiler.aggregate(plan))
        if access:
            from app.business_access import load_access
            if load_access(conn,access.user_id).revision != access.revision:
                raise ValueError('业务权限已变化，请重新提问')
        trace.finish('sql_compile', '参数化 SQL 编译完成',
                     [statement.sql, '绑定参数：' + json.dumps(json_safe(statement.params), ensure_ascii=False)])
        trace.start('sql_check', '检查数据库执行计划', '通过 EXPLAIN 验证字段和关联 SQL')
        if settings.preflight:
            execute(conn, Statement('EXPLAIN (FORMAT JSON) ' + statement.sql, statement.params))
            trace.finish('sql_check', '数据库执行计划检查通过；没有修改业务数据')
        else:
            trace.finish('sql_check', '配置未启用 EXPLAIN；本体与参数化编译检查已通过', state='skipped')
        trace.start('sql', '执行实时数据库查询', '正在查询全部匹配范围；预览与分页不改变筛选范围')
        result=execute(conn,statement)
        timings['sql']=round(perf_counter()-checkpoint,3)
        metric=compiler.metric(plan) if intent=='sum' else None
        related=[]
        if intent=='detail':
            main=[r['row_data'] for r in result if r['bucket']=='__main']
            main_total=max((int(r['total_count']) for r in result if r['bucket']=='__main'),default=0)
            main=clean_rows(catalog,subject,main)
            for table in dict.fromkeys(e['table'] for e in links):
                matches=[r for r in result if r['bucket']==table]
                edge=next(e for e in links if e['table']==table)
                semantic=catalog.overrides.get('relations',{}).get(subject,{}).get(table,{})
                related.append({'table':table,'name':semantic.get('name',catalog.entities[table]['name']),
                    'note':edge['note'],'type':edge['type'],'level':edge['level'],
                    'count':max((int(r['total_count']) for r in matches),default=0),
                    'rows':clean_rows(catalog,table,[r['row_data'] for r in matches])})
        else:
            main=json_safe(result)
            main_total=sum(int(row.get('n',0)) for row in result)
            if any(row.get('invalid',0) for row in result):
                raise ValueError('存在无法解释为金额的非空值，本次不返回合计；请修正数据或指标标准化口径')
        trace.finish('sql', '实时查询完成，命中 %d 条记录' % main_total,
                     ['%s：%d 条关联记录' % (row['name'], row['count']) for row in related])
        # A named record must not silently merge distinct identities.
        name_field=catalog.entities[subject]['bindings'].get('name')
        if (intent=='detail' and catalog.entities[subject].get('name_lookup') and main_total>1 and
                any(c.get('col')==name_field and c.get('op')=='=' for c in plan['conditions'])):
            raise ValueError('当前条件匹配到 %d 条同名人员档案，请补充所属企业或档案标识后查询' % main_total)
        emit('progress',stage='answer',message='正在构建来源证据并校验回答…',percent=78)
        trace.start('evidence', '整理来源证据', '整理真实返回记录、字段注释、来源和证据覆盖范围；移除敏感内容')
        evidence=build_evidence(catalog,plan,main,main_total,related,links,metric)
        trace.finish('evidence', '已整理 %d 条来源事实' % len(evidence['facts']),
                     ['%s：提供 %d / 命中 %d 条' % (row['name'], row['provided'], row['total'])
                      for row in evidence.get('coverage', [])])
        query_claims,query_summary=fallback_claims(catalog,plan,evidence,main_total)
        checkpoint=perf_counter()
        trace.start('answer', '生成并核验回答', '依据来源事实生成摘要，检查引用、数值与单位；不补造数据库记录')
        delivery={}
        def stream_claim(claim):
            # Permissions may change while an external batch is in flight. Check
            # immediately before each progressive disclosure, not just at final.
            if access and load_access(conn,access.user_id).revision != access.revision:
                raise ValueError('业务权限已变化，请重新提问')
            emit('answer_delta',delta=claim['text']+'\n')

        claims,text_answer,validation=answer(catalog,plan,evidence,main_total,use_model,text,trace,delivery,
                                            stream_claim if on_event else None)
        if access and load_access(conn,access.user_id).revision != access.revision:
            raise ValueError('业务权限已变化，请重新提问')
        timings['answer']=round(perf_counter()-checkpoint,3)
        emit('progress',stage='answer',message='正在组织查询事实、分析解释与证据边界…',percent=92)
        checkpoint=perf_counter()
        document=compose_document(text,plan,evidence,claims,len(query_claims),
            chat if use_model and model_available() and settings.answer_composition_enabled else None,
            settings.answer_composition_budget,settings.answer_composition_timeout_seconds,trace)
        if access and load_access(conn,access.user_id).revision != access.revision:
            raise ValueError('业务权限已变化，请重新提问')
        timings['composition']=round(perf_counter()-checkpoint,3)
        validation_labels = {'deterministic': '使用确定性事实摘要',
                             'evidence_fallback': '模型回答未通过检查或生成失败，已使用事实摘要',
                             'source_and_numeric_checked': '回答已通过来源、数值与单位检查'}
        trace.finish('answer', validation_labels.get(validation, validation),
                     (['未查到匹配记录；不补造业务档案'] if not main_total else
                      ['来源检查不等于完整语义证明，可继续核对返回明细']) +
                     (['模型传递：%d/%d 条已读取事实，%d/%d 条关联；完成 %d/%d 批' % (
                        delivery.get('sent_fact_count',0),delivery.get('queried_fact_count',0),
                        delivery.get('sent_relationship_count',0),delivery.get('queried_relationship_count',0),
                        delivery.get('completed_batches',0),delivery.get('batch_count',0)),
                       '已读取证据完整传递' if delivery.get('complete_queried_data') else '证据传递未全部确认完成，保留数据库事实摘要']
                      if delivery else ['未启用模型证据解读或本次使用确定性统计']))
        output={'ok':True,'question':text,'source':source,'parse':json_safe(plan),'intent':intent,
            'plan':json_safe(plan),'sql':statement.sql,'sql_params':json_safe(statement.params),
            'graph_tables':links,'graph':graph_view(subject,links),'main_total':main_total,
            'main_rows':main if intent!='detail' else main[:5],
            'related':[{**r,'rows':r['rows'][:settings.page_size]} for r in related],
            'page_size':settings.page_size,'graph_main_limit':settings.graph_main_limit,
            'related_scope':'all_matching_main_records','metric':metric,
            'field_meta':field_meta(catalog,[subject]+[r['table'] for r in related]),
            'answer':text_answer,'claims':claims,'answer_validation':validation,
            'query_summary':query_summary,'answer_document':document,
            'evidence':evidence,'model_evidence_delivery':delivery,
            'ontology_version':catalog.version,'timings':timings,
            'elapsed':round(perf_counter()-started,3)}
        if access:
            output['access_revision']=access.revision
            output['data_scope']=access.effective_scope
        aggregate_table=plan.get('aggregate_target') or subject
        if intent != 'detail':
            output['field_meta'].update(field_meta(catalog,[aggregate_table]))
            output['field_meta'][aggregate_table].update({
                'n':{'label':'参与记录数'}, 'total':{'label':(metric or {}).get('name','合计'),
                                                  'unit':(metric or {}).get('unit','')},
                'mx':{'label':'单笔最大'}, 'missing':{'label':'金额为空记录数'},
                'invalid':{'label':'无效金额记录数'}, '__period':{'label':'统计期间'}})
        if main_total:
            target=plan.get('aggregate_target') or subject if intent!='detail' else subject
            output['drill_token']=token_for(plan,catalog,main_total,target)
        if any(r['count'] for r in related):
            output['related_token']=token_for(plan,catalog,main_total,
                                related={r['table']:r['count'] for r in related})
        trace.start('done', '完成查询', '封装可核验结果、查询计划与执行过程')
        if use_cache:
            remember_plan(output['question'],plan,catalog)
        with LOCK:
            STATUS['requests']+=1
            DURATIONS.append(output['elapsed'])
            del DURATIONS[:-1000]
        emit('progress',stage='done',message='查询及证据校验完成',percent=100)
        trace.finish('done', '查询完成，执行过程已保留')
        output['execution_trace'] = trace.snapshot()
        return output
    except Exception as exc:
        trace.fail_active(str(exc)[:200] if isinstance(exc, ValueError) else '本步骤执行失败，请稍后重试或联系管理员')
        with LOCK:
            STATUS['failures']+=1
        raise


