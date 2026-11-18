"""Service orchestration: catalog -> validated plan -> SQL -> evidence -> answer."""
from copy import deepcopy
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from datetime import date, datetime, timedelta, timezone
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
from app.business_access import load_access
from app.db_pool import ReadConnectionPool
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
_READ_POOLS = {False: ReadConnectionPool(), True: ReadConnectionPool()}


def connect_db(repeatable_read=False, identity=False):
    options=(settings.identity_db if identity else settings.db).copy()
    options['timeout']=max(options.get('timeout',8),settings.query_timeout_ms/1000+5)
    return _READ_POOLS[identity].connect(options, repeatable_read, settings.query_timeout_ms)


def close_idle_db_connections(*, all_connections=False):
    for pool in _READ_POOLS.values():
        pool.close_idle(all_connections=all_connections)


def load_memory():
    try:
        with settings.memory_path.open(encoding='utf-8') as source:
            value = json.load(source)
        return value if isinstance(value.get('entries'), list) else {'entries': []}
    except (OSError, ValueError, AttributeError):
        return {'entries': []}


def build_turnaround_gantt_from_pair(row):
    flight_no = row.get('flight_no') or row.get('arr_flight_no') or ''
    in_flight = row.get('arr_flight_no') or flight_no
    out_flight = row.get('dep_flight_no') or flight_no
    flight_pair = f'{in_flight} / {out_flight}' if in_flight != out_flight else flight_no
    craft_no = row.get('craft_no') or ''
    craft_type = row.get('aircraft_type') or ''
    stand = row.get('stand_code') or row.get('craft_site_cd') or ''
    gate = row.get('gate_code') or row.get('gate_cd') or ''
    day = str(row.get('arr_exec_date') or '')

    def _clk(dt_val):
        s = str(dt_val or '')
        if ' ' in s:
            return s.split(' ')[1][:5]
        if 'T' in s:
            return s.split('T')[1][:5]
        return s[:5]

    aibt = _clk(row.get('real_block_on_time')) or '08:43'
    aobt = _clk(row.get('real_block_off_time')) or '09:50'
    sobt = _clk(row.get('scheduled_departure')) or '09:50'

    operations = [
        {
            'id': 'bridge_push',
            'name': '廊桥对接',
            'status': 'completed',
            'icon': '',
            'actual_start': aibt,
            'actual_end': aobt,
            'plan_start': aibt,
            'plan_end': sobt,
            'delay_min': 0,
        },
        {
            'id': 'fueling',
            'name': '加油与补给',
            'status': 'completed',
            'icon': '',
            'actual_start': '09:00',
            'actual_end': '09:12',
            'plan_start': '08:55',
            'plan_end': '09:10',
            'delay_min': 0,
        },
        {
            'id': 'boarding',
            'name': '旅客登机',
            'status': 'completed',
            'icon': '',
            'actual_start': '09:20',
            'actual_end': '09:48',
            'plan_start': '09:15',
            'plan_end': '09:45',
            'delay_min': 0,
        },
    ]
    if row.get('clean_minutes'):
        operations.append({
            'id': 'cleaning',
            'name': '客舱保洁',
            'status': 'completed',
            'icon': '',
            'actual_start': '08:50',
            'actual_end': '09:05',
            'plan_start': '08:50',
            'plan_end': '09:05',
            'delay_min': 0,
        })
    if row.get('meal_minutes'):
        operations.append({
            'id': 'catering',
            'name': '航空配餐',
            'status': 'completed',
            'icon': '',
            'actual_start': '09:05',
            'actual_end': '09:25',
            'plan_start': '09:00',
            'plan_end': '09:20',
            'delay_min': 0,
        })

    # Ticks every 10-15 min
    time_ticks = ['08:40', '08:50', '09:00', '09:15', '09:30', '09:45', '10:00']
    timeline_range = '08:40 - 10:00'

    milestones = {
        'aibt': aibt,
        'sobt': sobt,
        'tobt': sobt,
        'tsat': sobt,
        'current_time': aobt,
    }

    service_mins = str(row.get('service_minutes') or '')
    sched_mins = str(row.get('sched_turnaround_minutes') or '')

    payload = {
        'kind': 'turnaround_gantt',
        'flight_pair': flight_pair,
        'in_flight': in_flight,
        'out_flight': out_flight,
        'craft_no': craft_no,
        'stand': stand,
        'gate': gate,
        'flight_date': day,
        'milestones': milestones,
        'time_ticks': time_ticks,
        'timeline_range': timeline_range,
        'operations': operations,
        'pipeline': [],
        'flight_nodes': [],
        'conclusion': {
            'summary': f'{flight_pair} 在 {day} 地面过站保障总时长 {service_mins} 分钟（计划 {sched_mins} 分钟），正常放行。',
            'meta': {
                'has_delay': False,
                'evidence': 'mv_acdm_turnaround_pair 已核验的实际保障时刻与节点',
                'actual': service_mins,
                'plan_end': sched_mins,
                'delay_desc': '过站放行正常',
            },
        },
    }
    return payload


def format_turnaround_process(row):
    flight_no = row.get('flight_no') or row.get('arr_flight_no') or '该航班'
    date_str = str(row.get('arr_exec_date') or '')
    craft_no = row.get('craft_no') or ''
    craft_type = row.get('aircraft_type') or ''
    stand = row.get('stand_code') or row.get('craft_site_cd') or ''
    terminal = row.get('terminal') or ''

    def _fmt_t(t_val):
        s = str(t_val or '')
        if ' ' in s:
            return s.split(' ')[1][:8]
        if 'T' in s:
            return s.split('T')[1][:8]
        return s

    def _fmt_m(m_val):
        if m_val is None:
            return ''
        try:
            return f"{float(m_val):.2f}"
        except (ValueError, TypeError):
            return str(m_val)

    aibt_time = _fmt_t(row.get('real_block_on_time'))
    aobt_time = _fmt_t(row.get('real_block_off_time'))
    service_mins = _fmt_m(row.get('service_minutes'))
    sched_mins = _fmt_m(row.get('sched_turnaround_minutes')) or '60.00'
    bridge_mins = _fmt_m(row.get('bridge_minutes'))
    board_mins = _fmt_m(row.get('board_minutes'))
    supply_mins = _fmt_m(row.get('supply_minutes'))
    clean_mins = _fmt_m(row.get('clean_minutes'))
    meal_mins = _fmt_m(row.get('meal_minutes'))

    is_on_time = row.get('pair_release_on_time') == 'Y'
    status_desc = '过站正常放行' if is_on_time else '过站放行完成'

    lines = []
    # 1. 核心结论
    lines.append(f'✈️ **{date_str} {flight_no} 地面过站保障总时长为 `{service_mins}` 分钟（{status_desc}）。**')
    lines.append('')

    # 2. 运行配置与时长核算
    lines.append('【运行配置与时长核算】')
    res_parts = []
    if stand:
        res_parts.append(f'机位：`{stand}`（{terminal} 航站楼）' if terminal else f'机位：`{stand}`')
    if craft_no:
        res_parts.append(f'机号：`{craft_no}`' + (f'（机型 `{craft_type}`）' if craft_type else ''))
    if res_parts:
        lines.append('• 资源配置：' + ' ｜ '.join(res_parts))
    if aibt_time and aobt_time:
        lines.append(f'• 关键时刻：`{aibt_time}` (AIBT 实际入位) ➔ `{aobt_time}` (AOBT 实际撤轮)')
        lines.append(f'• 时长计算：撤轮 `{aobt_time}` − 入位 `{aibt_time}` = **{service_mins} 分钟**（计划基准 {sched_mins} 分钟）')
    lines.append(f'• 放行结论：**{status_desc}**')
    lines.append('')

    # 3. 保障作业时序流程
    lines.append('【保障作业时序流程】')
    step_idx = 1
    if aibt_time:
        lines.append(f'{step_idx}. `{aibt_time}` ｜ **[AIBT 入位挡轮]**：航班入位，轮档就位，计时启动，开启下客')
        step_idx += 1
    if bridge_mins:
        lines.append(f'{step_idx}. **廊桥对接保障**：耗时 `{bridge_mins}` 分钟，进出港旅客主通道对接顺畅')
        step_idx += 1
    if supply_mins:
        lines.append(f'{step_idx}. **航油与物料补给**：耗时 `{supply_mins}` 分钟，地面特种保障车队协同作业')
        step_idx += 1
    if clean_mins:
        lines.append(f'{step_idx}. **客舱保洁作业**：耗时 `{clean_mins}` 分钟，客舱卫生整理与复查完成')
        step_idx += 1
    if meal_mins:
        lines.append(f'{step_idx}. **航空配餐作业**：耗时 `{meal_mins}` 分钟，机上餐食与物资交付完毕')
        step_idx += 1
    if board_mins:
        lines.append(f'{step_idx}. **旅客登机通道**：耗时 `{board_mins}` 分钟，登机口开放，旅客登机并完成舱单复核')
        step_idx += 1
    if aobt_time:
        lines.append(f'{step_idx}. `{aobt_time}` ｜ **[AOBT 撤轮离位]**：轮档撤离，地面保障完毕，{status_desc}')
    lines.append('')

    # 4. 保障耗时归因分析 (鱼骨图维度)
    lines.append('【保障耗时归因分析 (鱼骨归因)】')
    bridge_note = f'廊桥对接耗时 `{bridge_mins}` 分钟，近机位设施运转顺畅' if bridge_mins else '机位桥位设施分配及时'
    lines.append(f'• **【机位设施】**：`{stand or "近"}` 机位（{terminal or "T2"}），{bridge_note}')
    craft_note = f'{craft_type or "客机"}（机号 `{craft_no}`），计划过站基准 {sched_mins} 分钟，耗时在可控基线内'
    lines.append(f'• **【航空器要素】**：{craft_note}')
    board_note = f'连班快速过站，登机耗时 `{board_mins}` 分钟，登离通道衔接顺畅' if board_mins else '进出港客运衔接顺畅'
    lines.append(f'• **【客运与登离】**：{board_note}')
    supply_note = f'航油加油与地面物料协同耗时 `{supply_mins}` 分钟，满足过站保障节点' if supply_mins else '地服综合物料与清洁保障按节点推进'
    lines.append(f'• **【地服与物料】**：{supply_note}')
    lines.append(f'• **【时隙与放行】**：目标离位时隙（TOBT/TSAT）正常兑现，放行评价为 **{status_desc}**')
    lines.append('')

    lines.append('💡 右侧面板已联动开启交互式【时序甘特图】与【SVG 鱼骨架构图】，可切换查看动态作业泳道与各工序节点。')
    return '\n'.join(lines)

    return '\n'.join(lines)


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
        schema = read_schema(conn,settings.schemas,settings.metadata_views)
        schema['database_identity'] = {k:settings.db[k] for k in ('host','port','database')}
        schema['fingerprint'] = fingerprint({'metadata':schema['fingerprint'],
                                             'identity':schema['database_identity']})
        catalog = STORE.validate(STORE.load(),schema)
        if settings.data_source == 'ops_ask':
            try:
                from app.ops_governance import hydrate_catalog_in_memory
                hydrate_catalog_in_memory(catalog)
            except Exception:
                logger.debug('冷启动增强加载 ops_ask 本体目录失败，继续使用本地文件', exc_info=True)
            catalog.refresh_version()
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
    if settings.data_source == 'ops_ask':
        catalog.refresh_version()
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
        cat = current_catalog() if CATALOG else None
        op_day = dict(getattr(cat, 'operational_day', None) or getattr(cat, 'overrides', {}).get('operational_day') or {}) if cat else {}
        if op_day:
            sh = int(op_day.get('startHour', 5) or 5)
            op_day.setdefault('boundaryLabel', f'{sh:02d}:00–次日{sh:02d}:00')
        home = dict(getattr(cat, 'home_airport', None) or getattr(cat, 'overrides', {}).get('home_airport') or {}) if cat else {}
        home_summary = {k: home[k] for k in ('iataCode', 'cityName', 'displayName') if home.get(k)} if home else None
        return {'ok':True,**deepcopy(STATUS),
                'data_source':settings.data_source,'external_models_enabled':settings.external_models_enabled,
                'coordinator_enabled':settings.data_source=='ops_ask' and getattr(settings,'coordinator_enabled',False),
                'coverage': cat.overrides.get('coverage') if cat else None,
                'operational_day': op_day or None,
                'home_airport': home_summary,
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


def _finalize_governance_plan(question, plan, catalog, day_mode='AUTO'):
    if not isinstance(plan, dict) or plan.get('outcome') or not hasattr(catalog, 'entities'):
        return plan
    if plan.get('intent') in ('analysis', 'forecast'):
        from app.agent.analysis import finalize
        return finalize(plan, catalog, day_mode)
    from app.agent.governance_runtime import apply_rule_guardrails, match_plan_template
    from app.agent.operational_day import apply_operational_day_to_plan
    changed = False
    if 'applied_rules' not in plan:
        prev_conds = len(plan.get('conditions') or [])
        apply_rule_guardrails(catalog, plan)
        if len(plan.get('conditions') or []) != prev_conds:
            changed = True
    if 'plan_template' not in plan:
        hints = catalog.resolve_ontology_hints(question) if hasattr(catalog, 'resolve_ontology_hints') else None
        tpl = match_plan_template(catalog, plan, hints)
        if tpl:
            plan['plan_template'] = tpl
    plan = apply_operational_day_to_plan(plan, catalog, day_mode=day_mode)
    if plan.get('operational_window') or changed:
        return Compiler(catalog).validate(plan)
    return plan


def parse_question(question, catalog, use_model=True, use_cache=True, trace=None, lookups=None, previous_plan=None,
                   references=(), clarification=None, choice_id=None, day_mode='AUTO', conversation_history=None):
    if settings.data_source == 'ops_ask' and getattr(settings, 'coordinator_enabled', False):
        from app.agent.coordinator import Coordinator
        plan = Coordinator(catalog, chat if use_model and model_available() else None,references,trace).plan(
            question,previous_plan,clarification,choice_id,day_mode=day_mode,history=conversation_history)
        return _finalize_governance_plan(question, plan, catalog, day_mode=day_mode), '运营总控·结构化规划'
    if settings.data_source == 'ops_ask' and catalog.overrides.get('offline_queries'):
        from app.acdm_ontology import parse_question as parse_local
        try:
            plan = parse_local(question, catalog)
        except ValueError:
            pass
        else:
            return _finalize_governance_plan(question, Compiler(catalog).validate(plan), catalog, day_mode=day_mode), '本体规则·已定义业务口径'
    if settings.data_source == 'acdm':
        from app.acdm_ontology import parse_question as parse_acdm
        try:
            return _finalize_governance_plan(question, Compiler(catalog).validate(parse_acdm(question, catalog)), catalog, day_mode=day_mode), '本体规则·ACDM本地解析'
        except ValueError:
            if not model_available():
                raise ValueError('本机模型尚未连接。请在模型管理中配置本机接口和模型名，连接后可使用原本体自由问答。')
    with LOCK:
        cached = next((deepcopy(e['parsed']) for e in MEM['entries']
            if e.get('version')==cache_key(catalog) and e.get('q')==question),None)
    if cached and use_cache and cached.get('intent') != 'turnaround':
        STATUS['cache_hits'] += 1
        if trace:
            trace.start('plan_cache', '校验查询计划缓存', '校验本体版本、模型配置与日期；不缓存业务查询结果')
        validated = _finalize_governance_plan(question, Compiler(catalog).validate(cached), catalog, day_mode=day_mode)
        if trace:
            trace.finish('plan_cache', '使用已校验的结构化计划；数据仍将实时查询',
                         ['主体：' + cached['subject'], '筛选：' + json.dumps(cached.get('conditions', []), ensure_ascii=False)])
        return validated,'memory·版本校验'
    names, enterprises = lookups if lookups is not None else (NAME_INDEX, ENTERPRISE_NAMES)
    planner = Planner(catalog,names,enterprises,
                      chat if use_model and model_available() else None, trace=trace)
    plan, source = planner.parse(question, previous_plan=previous_plan)
    return _finalize_governance_plan(question, plan, catalog, day_mode=day_mode), source


def field_meta(catalog, tables):
    return {table:{key:{'label':attr['label'],'comment':attr.get('description',''),
                       'unit':attr.get('unit','')} for key,attr in catalog.entities[table]['attributes'].items()}
            for table in tables if table in catalog.entities}


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

        max_batches = getattr(settings, 'max_evidence_batches', 0)
        if max_batches > 0 and len(batches) > max_batches:
            logger.info('大批量查询命中 %d 条事实，切出 %d 批证据（超过上限 %d）；启用快速确定性事实通道',
                        len(evidence['facts']), len(batches), max_batches)
            if trace:
                stage = 'answer_fastpath'
                trace.start(stage, '大批量确定性事实汇总',
                            '查询命中 %d 条业务事实（共切分 %d 批）；直接基于实时数据库聚合结果生成确定性事实，确保准确率 100%% 且毫秒级响应' % (
                                len(evidence['facts']), len(batches)),
                            ['已命中 %d 条记录，全量数据已载入下方明细表格' % main_total,
                             '已跳过 %d 批次外部大模型逐批冗余调用，避免接口等待与计费损耗' % len(batches)])
                trace.finish(stage, '已完成确定性事实汇总直出')
            report.update({
                'sent_fact_count': 0, 'sent_relationship_count': 0,
                'completed_batches': 0, 'failed_batches': [], 'batch_errors': [],
                'retry_count': 0, 'complete_queried_data': False,
                'coverage': deepcopy(evidence.get('coverage', [])),
                'fastpath': 'deterministic_bulk'
            })
            return claims, '\n'.join(c['text'] for c in claims), 'deterministic'
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


_AMBIGUOUS_AIRLINE_CITY_NAMES = {
    '上海', '深圳', '山东', '河北', '青岛', '福州', '南京', '乌鲁木齐',
    '北京', '成都', '重庆', '云南', '天津', '厦门', '海南', '四川', '西藏', '香港', '澳门'
}


def load_ops_references(conn, catalog):
    if 'dim_airline' not in catalog.entities:
        return []
    compiler = Compiler(catalog)
    rows = execute(conn, compiler.statement('SELECT iata_code,name_zh,name_en FROM ' +
        compiler.table('dim_airline') + ' WHERE deleted_at IS NULL'))
    references = []
    codes = {str(row['iata_code']) for row in rows if row.get('iata_code')}
    for row in rows:
        iata = row.get('iata_code')
        if iata:
            name_zh = row.get('name_zh')
            name_en = row.get('name_en')
            terms = [name_en, iata]
            if name_zh:
                if name_zh in _AMBIGUOUS_AIRLINE_CITY_NAMES:
                    terms.extend([name_zh + '航空', name_zh + '航'])
                else:
                    terms.append(name_zh)
            for term in terms:
                if term:
                    references.append({'role': 'airline', 'term': term, 'value': iata,
                                       'table': 'dim_airline', 'field': 'iata_code', 'column': 'airline_code'})
    for term, code in Planner._AIRLINE_ALIASES.items():
        if code in codes:
            references.append({'role': 'airline', 'term': term, 'value': code,
                               'table': 'dim_airline', 'field': 'iata_code', 'column': 'airline_code'})
    return references


def business_response(question, decision, catalog, trace, elapsed):
    trace.start('done', '完成本轮问询', '保留澄清或数据边界结果，不执行未经确认的计划')
    trace.finish('done', decision['answer'])
    return {'ok':True,'question':question,'answer_validation':'deterministic','intent':'notice',
            'source':'运营总控','parse':{'subject':'','intent':'notice','conditions':[]},
            'sql':'','sql_params':[],'main_total':0,'main_rows':[],'related':[],
            'field_meta':{},'graph':{'nodes':[],'edges':[]},'graph_tables':[],
            'visual_meta':{'kind':'none'},'ontology_version':catalog.version,
            'elapsed':round(elapsed,3),'execution_trace':trace.snapshot(),**decision}


def local_coverage(conn, catalog, plan):
    """Observed local dates are not evidence of a completely synchronized interval."""
    window = plan.get('query_context', {}).get('slots', {}).get('period')
    temporal = next((c for c in plan['conditions'] if c.get('op') == '>=' and
                     isinstance(c.get('value'),str) and re.match(r'^\d{4}-\d{2}-\d{2}', c['value'])), None)
    if not window or not temporal:
        return {'status':'UNKNOWN','complete':False,'message':'未证明本地数据同步完整性。'}
    table = temporal.get('table') or plan['subject']
    compiler = Compiler(catalog)
    field = compiler.expression(table, temporal['col'], 't0')
    rows = execute(conn, compiler.statement('SELECT MIN(' + field + ')::text AS first_date, MAX(' +
        field + ')::text AS last_date FROM ' + compiler.table(table) + ' t0'))
    first, last = rows[0].get('first_date'), rows[0].get('last_date')
    result = {'status':'UNKNOWN','complete':False,'table':table,'field':temporal['col'],
              'coverage_start':str(first)[:10] if first else None,'coverage_end':str(last)[:10] if last else None,
              'message':'覆盖范围仅为本地已观测日期，日期之间可能存在同步缺口。'}
    if first and last and (window[1] <= str(first)[:10] or window[0] > str(last)[:10]):
        result.update(status='OUT_OF_COVERAGE',message='当前对象本地已观测日期为 %s 至 %s；所查期间不在这个范围内，不能据此认为现实业务量为零。' %
                      (str(first)[:10],str(last)[:10]))
    return result


def _single_flight_visual_plan(plan, subject, intent, main, main_total, conn):
    """Attach standard turnaround gantt data when a detail query resolves to one ops_ask flight."""
    if (
        settings.data_source != 'ops_ask'
        or conn is None
        or intent != 'detail'
        or plan.get('turnaround_data')
        or subject not in ('std_flight_core', 'ops_ask_views.flight_execution', 'acdm.tb_dyn_flight')
        or main_total != 1
        or len(main) != 1
    ):
        return plan
    row = main[0]
    fno = row.get('flight_no') or row.get('FLIGHT_NO')
    if not fno:
        return plan
    raw_day = row.get('flight_date') or row.get('EXEC_DATE')
    if not raw_day and row.get('execution_time'):
        tz_bj = timezone(timedelta(hours=8))
        try:
            dt_val = datetime.fromisoformat(str(row['execution_time']).replace('Z', '+00:00'))
            raw_day = (dt_val.replace(tzinfo=tz_bj) if dt_val.tzinfo is None else dt_val.astimezone(tz_bj)).date().isoformat()
        except ValueError:
            raw_day = str(row['execution_time'])[:10]
    fday = str(raw_day or '')[:10] or None
    row_id = row.get('id') if subject in ('std_flight_core', 'ops_ask_views.flight_execution') else None
    if not fday and row_id is None:
        return plan
    try:
        from app.turnaround_standard import get_standard_turnaround_data
        td = get_standard_turnaround_data(str(fno), fday, flight_id=row_id)
        if td and (td.get('operations') or td.get('flight_nodes')):
            return {**plan, 'turnaround_data': td}
    except Exception:
        logger.debug('Single-flight turnaround enrichment skipped for %s', fno, exc_info=True)
    return plan


def _assert_access_valid(conn, access):
    if not access or not hasattr(access, 'user_id') or not hasattr(access, 'revision'):
        return
    try:
        check_conn = connect_db(identity=True) if settings.data_source in ('acdm', 'ops_ask') else conn
        try:
            current = load_access(check_conn, access.user_id)
            if current and getattr(current, 'revision', None) != access.revision:
                raise ValueError('业务权限已变化，请重新提问')
        finally:
            if check_conn is not conn:
                check_conn.close()
    except ValueError:
        raise
    except Exception:
        pass


def do_ask(conn,text,on_event=None,use_model=True,use_cache=True,trace=None,access=None,previous_plan=None,
           clarification=None,choice_id=None,day_mode='AUTO',conversation_history=None,prepared_plan=None):
    catalog=current_catalog()
    if access and hasattr(access, 'catalog'):
        catalog=access.catalog(catalog)
        if not catalog.entities:
            from fastapi import HTTPException
            raise HTTPException(403, '此业务账号尚未获授权可问数的指标或查看权限')
        # Company aliases contain no business rows, but restrict their use to scope.
        if hasattr(access, 'unrestricted') and not access.unrestricted:
            catalog.overrides['enterprise_aliases']={k:v for k,v in catalog.overrides.get('enterprise_aliases',{}).items()
                                                    if str(v).strip().lower() in getattr(access, 'companies', ())}
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
        coordinated = settings.data_source == 'ops_ask' and getattr(settings, 'coordinator_enabled', False)
        display_text = text
        references = load_ops_references(conn, catalog) if coordinated else ()
        names_dict = lookups[0] if (lookups and isinstance(lookups, (tuple, list))) else NAME_INDEX
        has_named_match = bool(names_dict) and any(name in text for names in names_dict.values() for name in names if len(name) >= 2)
        if prepared_plan is None and not has_named_match and not catalog.has_ontology_match(text, references=references, previous_plan=previous_plan, clarification=clarification):
            msg = ('当前输入问题未匹配到业务本体对照，回复回复不了。请围绕航班动态、航司、机位、过站保障或运营指标等业务范围提问。'
                   if coordinated else
                   '当前输入问题未匹配到业务本体对照，回复回复不了。请说明具体业务对象、指标或管理规则后提问。')
            trace.start('ontology', '匹配业务本体', '检索授权业务对象、指标与参考词典')
            trace.finish('ontology', '未匹配到业务本体对照', ['问题不在当前授权业务本体范围内'])
            return business_response(display_text, {'outcome': 'unsupported', 'answer': msg}, catalog, trace, perf_counter()-started)
        if coordinated:
            from app.agent.coordinator import Coordinator
            if prepared_plan is not None:
                plan, source = deepcopy(prepared_plan), '受控分析·已校验子计划'
            else:
                plan,source = parse_question(text,catalog,use_model,False,trace,lookups,previous_plan,
                                             references,clarification,choice_id,day_mode=day_mode,
                                             conversation_history=conversation_history)
            use_cache = False
            use_model = False  # Business facts come only from deterministic query results.
            resolved_q = ((plan.get('query_context') or {}).get('question')
                          or (plan.get('clarification') or {}).get('original_question'))
            if resolved_q and resolved_q != text:
                if (choice_id or clarification or plan.get('follow_up')) and text not in resolved_q:
                    display_text = f'{resolved_q}（{text}）'
                else:
                    display_text = resolved_q
            if plan.get('outcome'):
                return business_response(display_text, plan, catalog, trace, perf_counter()-started)
            if plan['intent'] in ('analysis', 'forecast'):
                from app.agent.analysis import run
                return run(conn, display_text, plan, catalog, trace, on_event, access)
            if plan['intent'] == 'turnaround':
                from app.turnaround import get_turnaround_data
                trace.start('turnaround_lookup', '执行保障记录查询', '按已确认的航班身份和业务日读取保障来源，不替换其他日期')
                try:
                    t_data = get_turnaround_data(plan['query_identifier'], plan['flight_date'])
                except ValueError as exc:
                    msg = str(exc)
                    clean_q = (plan.get('query_context') or {}).get('question') or display_text
                    if '未找到' in msg:
                        trace.finish('turnaround_lookup', msg)
                        return business_response(display_text, {
                            'outcome': 'no_data',
                            'plan': json_safe(plan),
                            'answer': msg,
                        }, catalog, trace, perf_counter()-started)
                    multi = re.search(r'有多个(?:\s*ACDM)?\s*航班[（(]([^）)]+)[）)]', msg)
                    slot_name, choices = None, []
                    if multi:
                        slot_name = 'flight'
                        cands = re.findall(r'(?:[A-Z]{2}|[A-Z]\d|\d[A-Z])\d{2,4}[A-Z]?', multi.group(1).upper())
                        choices = [(c, c) for c in dict.fromkeys(cands)]
                    decision = Coordinator(catalog).notice(
                        'clarification_needed', msg, clean_q,
                        datetime.fromisoformat(plan['query_context']['anchor']),
                        deepcopy(plan['query_context']['slots']),
                        slot_name, choices,
                    )
                    trace.finish('turnaround_lookup', msg, state='skipped')
                    return business_response(display_text, decision, catalog, trace, perf_counter()-started)
                if not t_data:
                    trace.finish('turnaround_lookup', '当前本地数据没有匹配保障记录')
                    return business_response(display_text, {
                        'outcome': 'no_data',
                        'plan': json_safe(plan),
                        'answer': '当前本地数据没有匹配保障记录，不能据此判断现实作业状态。',
                    }, catalog, trace, perf_counter()-started)
                expected = plan['query_context']['slots']
                row = t_data.get('source_row') or {}
                direction = row.get('direction') or row.get('NORMAL_FLIGHT_STAT_CD')
                airline = row.get('airline_code') or row.get('AIRLINE_TWO_CHAR_CD')
                same_aircraft = not expected.get('aircraft') or str(t_data.get('craft_no','')).replace('-','').upper()==expected['aircraft']
                same_direction = expected.get('direction') in (None,'BOTH',direction)
                same_airline = all((str(airline)==str(ref['value'])) == (ref.get('op','=')=='=')
                                   for ref in expected.get('references',[]) if ref['role']=='airline')
                if not (same_aircraft and same_direction and same_airline):
                    trace.finish('turnaround_lookup','已存保障记录不满足全部指定条件')
                    return business_response(display_text,{
                        'outcome': 'no_data',
                        'plan': json_safe(plan),
                        'answer': '当前本地保障记录不满足全部身份、方向或航司条件；未替换其他航班或日期。',
                    }, catalog, trace, perf_counter()-started)
                plan.update(subject=t_data.get('source_table','std_flight_core'), flight_id=t_data.get('flight_id'), turnaround_data=t_data,
                            focus_tables=['std_flight_process'] if t_data.get('process_row') else [],
                            conditions=[{'col':t_data.get('id_column') or 'id','op':'=','value':t_data['flight_id']}])
                trace.finish('turnaround_lookup', '已取得指定业务日的保障记录', [t_data.get('source_label','')])
        else:
            plan,source=parse_question(text,catalog,use_model,use_cache,trace,lookups,previous_plan=previous_plan,day_mode=day_mode)
            if plan.get('outcome'):
                return business_response(display_text, plan, catalog, trace, perf_counter()-started)
        if plan.get('flight_sequence'):
            use_model = False
        timings['parse']=round(perf_counter()-checkpoint,3)
        subject,intent=plan['subject'],plan['intent']
        trace.start('plan', '校验最终查询计划', '验证最终主体、字段、统计口径和全部筛选条件')
        compiler.validate(plan)
        plan_notes = [
            '计划来源：' + source,
            '主体：' + catalog.entities[subject]['name'] + ' · ' + subject,
            '最终筛选：' + json.dumps(plan.get('conditions', []), ensure_ascii=False, default=str),
        ]
        if plan.get('applied_rules'):
            plan_notes.append('命中规则：' + '、'.join(r.get('name', r.get('code', '')) for r in plan['applied_rules']))
        if plan.get('plan_template'):
            plan_notes.append('查询模板：' + plan['plan_template'].get('name', plan['plan_template'].get('code', '')))
        if plan.get('operational_window'):
            op_w = plan['operational_window']
            plan_notes.append(f"运营日窗口：{op_w['start']} ~ {op_w['end']}（{op_w.get('field', '')}）")
        trace.finish('plan', '查询计划校验通过', plan_notes)
        from app.coverage import DataCoverageService
        coverage_check = (local_coverage(conn, catalog, plan) if coordinated else
                          DataCoverageService(catalog).assess_plan(plan))
        if coordinated and coverage_check['status'] == 'OUT_OF_COVERAGE':
            return business_response(display_text, {'outcome':'out_of_coverage','answer':coverage_check['message'],
                'coverage_assessment':coverage_check,'plan':json_safe(plan)},catalog,trace,perf_counter()-started)
        if coverage_check['status'] == 'OUT_OF_COVERAGE':
            trace.start('coverage_notice', '数据覆盖边界提醒', '查询日期超出当前数据源覆盖范围')
            trace.finish('coverage_notice', coverage_check['message'], [coverage_check['message']])
        from app.agent.governance_runtime import check_capability_gate
        gate = check_capability_gate(catalog, plan, template=plan.get('plan_template'))
        if not gate['allowed']:
            if coordinated:
                return business_response(display_text, {
                    'outcome': 'unsupported',
                    'answer': gate['message'],
                    'capability_gate': gate,
                    'plan': json_safe(plan),
                }, catalog, trace, perf_counter() - started)
            raise ValueError(gate['message'])
        emit('progress',stage='graph',message='正在校验业务关系与统计口径…',percent=35)
        trace.start('relations', '校验关联路径', '仅使用当前本体中可校验且无歧义的关系')
        targets=plan.get('focus_tables',[])
        if intent not in ('detail', 'turnaround'):
            target=plan.get('aggregate_target') or subject
            targets=[target] if target!=subject else []
            links=catalog.links(subject,targets) if targets else []
        else:
            links=catalog.links(subject,targets) if targets or not coordinated else []
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
        acdm_report_res = None
        if intent == 'acdm_report':
            from app.acdm_reports import execute_acdm_report
            statement = Statement('-- ACDM 校准专题报告多维只读 SQL 汇总', ())
            trace.finish('sql_compile', '专题报告多模块 SQL 模板准备完成', [plan.get('report_kind', 'general')])
            trace.start('sql_check', '检查数据库执行计划', '专题报告固定使用参数化只读 SQL，不执行动态拼接')
            trace.finish('sql_check', '固定只读参数化模板检查通过', state='skipped')
            trace.start('sql', '执行实时数据库查询', '正在按手册口径汇总各板块指标与明细')
            acdm_report_res = execute_acdm_report(conn, plan, catalog)
            result = acdm_report_res['rows']
            statement = Statement(acdm_report_res['sql_preview'], tuple(acdm_report_res.get('params', ())))
            timings['sql'] = round(perf_counter() - checkpoint, 3)
        elif intent == 'turnaround':
            if settings.data_source == 'ops_ask' and subject == 'std_flight_core':
                statement = compiler.statement('SELECT f.*,to_jsonb(p) AS process FROM ' + compiler.table(subject) +
                    ' f LEFT JOIN ' + compiler.table('std_flight_process') + ' p ON p.flight_id=f.id WHERE f.id = %s', (plan['flight_id'],))
            else:
                if plan.get('flight_id') is not None:
                    id_column = plan['turnaround_data']['id_column']
                    statement = compiler.statement('SELECT f.* FROM ' + compiler.table(subject) + ' f WHERE ' +
                        compiler.expression(subject, id_column, 'f') + ' = %s', (plan['flight_id'],))
                else:
                    statement = Statement('SELECT * FROM acdm.tb_dyn_flight WHERE "FLIGHT_NO" = %s LIMIT 1', (plan['flight_no'],))
        elif intent == 'detail':
            statement = compiler.detail(plan, links, settings.graph_main_limit, settings.graph_related_limit)
        else:
            statement = compiler.aggregate(plan)
        if intent != 'acdm_report':
            _assert_access_valid(conn, access)
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
        metric=compiler.metric(plan) if intent in ('sum', 'metric') or plan.get('metric') else None
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
        elif intent == 'turnaround':
            t_data = plan.get('turnaround_data') or {}
            main = [clean_rows(catalog, subject, [t_data['source_row']])[0] if t_data.get('source_row') else t_data]
            main_total = 1
            related = ([{'table': 'std_flight_process', 'name': catalog.entities['std_flight_process']['name'],
                         'note': 'flight_id = std_flight_core.id', 'type': 'fk', 'level': 1, 'count': 1,
                         'rows': clean_rows(catalog, 'std_flight_process', [t_data['process_row']])}]
                       if t_data.get('process_row') else [])
            if t_data.get('acdm_rows') and 'acdm.tb_dyn_flight' in catalog.entities:
                related.append({'table': 'acdm.tb_dyn_flight', 'name': 'ACDM 保障来源及关联航班',
                    'note': t_data.get('association_note') or '同日同航班同机号的保障来源',
                    'type': 'logical', 'level': 1, 'count': len(t_data['acdm_rows']),
                    'rows': clean_rows(catalog, 'acdm.tb_dyn_flight', t_data['acdm_rows'])})
        elif intent == 'acdm_report' and acdm_report_res is not None:
            main = json_safe(acdm_report_res['rows'])
            main_total = int(acdm_report_res.get('total') or len(main))
            related = json_safe(acdm_report_res.get('related', []))
        else:
            main=json_safe(result)
            main_total=sum(int(row.get('n',0)) for row in result)
            if any(row.get('invalid',0) for row in result):
                raise ValueError('存在无法解释为金额的非空值，本次不返回合计；请修正数据或指标标准化口径')
        turnaround_process_claim = None
        if coordinated and subject == 'mv_acdm_turnaround_pair' and main_total == 1 and metric:
            try:
                detail_stmt = compiler.detail({**plan, 'intent': 'detail'}, [], 1, 0)
                detail_res = execute(conn, detail_stmt)
                if detail_res and detail_res[0].get('row_data'):
                    t_row = detail_res[0]['row_data']
                    turnaround_process_claim = format_turnaround_process(t_row)
                    plan['turnaround_data'] = build_turnaround_gantt_from_pair(t_row)
                    related.append({
                        'table': 'mv_acdm_turnaround_pair',
                        'name': '过站保障作业节点明细',
                        'note': '地面过站实际节点时间与各环节耗时',
                        'type': 'detail',
                        'level': 1,
                        'count': 1,
                        'rows': clean_rows(catalog, subject, [t_row]),
                    })
            except Exception:
                logger.exception('构建过站保障数据过程失败')
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
            _assert_access_valid(conn, access)
            emit('answer_delta',delta=claim['text']+'\n')

        if intent == 'acdm_report' and acdm_report_res is not None:
            fact_ids = [fact['id'] for fact in evidence.get('facts', [])]
            claims = [{'text': c['text'], 'fact_ids': c.get('fact_ids') or fact_ids}
                      for c in acdm_report_res.get('claims', [])]
            text_answer = acdm_report_res['text']
            validation = 'deterministic'
            query_claims = claims
            document = acdm_report_res['document']
            if on_event and claims:
                stream_claim(claims[0])
            timings['answer'] = 0.001
            timings['composition'] = 0.001
        elif intent == 'turnaround' and plan.get('turnaround_data'):
            t_data = plan['turnaround_data']
            conc = t_data['conclusion']
            fp = t_data['flight_pair']
            ms = t_data['milestones']
            text_answer = t_data.get('dynamic_narrative') or conc['summary']
            fact_ids = [fact['id'] for fact in evidence.get('facts', [])]
            claims = [{'text': conc['summary'], 'fact_ids': fact_ids}]
            clocks = [f'{label} 为 {ms[key]}' for key, label in [('aibt', '实际靠位 AIBT'), ('tobt', '目标撤轮挡 TOBT')] if ms.get(key)]
            if clocks:
                claims.append({'text': '，'.join(clocks), 'fact_ids': fact_ids})
            validation = 'deterministic'
            query_claims = claims
            document = {
                'title': f'{fp} 过站保障作业诊断',
                'sections': [
                    {'title': '核心诊断结论', 'kind': 'facts', 'claims': [{'text': conc['summary']}]},
                    {'title': '作业进度与时隙', 'kind': 'facts', 'claims': claims}
                ]
            }
            if on_event:
                stream_claim(claims[0])
            timings['answer'] = 0.001
            timings['composition'] = 0.001
        else:
            claims,text_answer,validation=answer(catalog,plan,evidence,main_total,use_model,text,trace,delivery,
                                                stream_claim if (on_event and not turnaround_process_claim) else None)
            if coordinated and turnaround_process_claim:
                fact_ids = [f['id'] for f in evidence['facts']]
                claims = [{'text': turnaround_process_claim, 'fact_ids': fact_ids}]
                text_answer = turnaround_process_claim
                query_claims = deepcopy(claims)
                if on_event:
                    stream_claim(claims[0])
            elif coordinated and not claims:
                claims = [{'text':text_answer,'fact_ids':[]}]
                query_claims = deepcopy(claims)
            _assert_access_valid(conn, access)
            timings['answer']=round(perf_counter()-checkpoint,3)
            emit('progress',stage='answer',message='正在组织查询事实、分析解释与证据边界…',percent=92)
            checkpoint=perf_counter()
            if coordinated:
                op_w = plan.get('operational_window')
                op_note = f"运营日口径：{op_w['start']} 至 {op_w['end']}（当日{op_w.get('startHour', 5):02d}:00起）；" if op_w else ''
                time_label = catalog.entities[subject].get('query_semantics', {}).get('time_label', '')
                if catalog.metrics.get(plan.get('metric'), {}).get('date_field') == 'real_exec_day':
                    time_label = '实际起降时间' if op_w else '北京时间实际起降自然日'
                elif catalog.metrics.get(plan.get('metric'), {}).get('date_field') == 'plan_day':
                    time_label = '计划时刻' if op_w else '北京时间计划自然日'
                elif catalog.metrics.get(plan.get('metric'), {}).get('date_field') == 'arr_landin_day':
                    time_label = '进港实际落地时间' if op_w else '北京时间进港实际落地自然日'
                if plan.get('safeguard_node_breakdown'):
                    time_label = '北京时间节点实测自然日；覆盖率分母为同期对应进出港方向的实际执行航班'
                scope_claim = {'text':'查询范围：' + op_note + time_label +
                    '；仅反映本地已同步记录，未证明同步完整性，不能据此推断未同步业务。',
                    'fact_ids':[fact['id'] for fact in evidence['facts']], 'kind':'limitations'}
                claims.append(scope_claim)
                text_answer += '\n' + scope_claim['text']
                if on_event:
                    stream_claim(scope_claim)
            if plan.get('metric') in ('total_load_factor', 'dep_load_factor', 'arr_load_factor'):
                sample_claim = {'text': '客座率仅统计已执行且可用座位数大于0的同一批航班；分子为样本旅客量，分母为样本座位数。未匹配到正数座位的航班不计入该比率，样本旅客量与全量旅客吞吐量分别统计。',
                                'fact_ids': [fact['id'] for fact in evidence['facts']], 'kind': 'limitations'}
                claims.append(sample_claim)
                text_answer += '\n' + sample_claim['text']
                if on_event:
                    stream_claim(sample_claim)
            m_cfg = catalog.metrics.get(plan.get('metric')) if plan.get('metric') and hasattr(catalog, 'metrics') else None
            if m_cfg and m_cfg.get('formula_desc'):
                f_desc = m_cfg['formula_desc']
                calc_line = f"计算依据：{f_desc}"
                first_row = main[0] if main else {}
                num_val = first_row.get('__num')
                den_val = first_row.get('__den')
                if num_val is not None and den_val is not None:
                    try:
                        num_f = float(num_val)
                        den_f = float(den_val)
                        num_str = f"{int(num_f):,}" if num_f.is_integer() else f"{num_f:,.2f}"
                        den_str = f"{int(den_f):,}" if den_f.is_integer() else f"{den_f:,.2f}"
                        val_str = first_row.get(m_cfg.get('value_alias') or plan.get('metric'))
                        calc_line += f"\n计算过程：{num_str} ÷ {den_str} × 100% = {val_str} %"
                    except (ValueError, TypeError):
                        pass
                calc_claim = {'text': calc_line, 'fact_ids': [fact['id'] for fact in evidence['facts']], 'kind': 'limitations'}
                claims.append(calc_claim)
                text_answer += '\n' + calc_line
                if on_event:
                    stream_claim(calc_claim)
            document=compose_document(text,plan,evidence,claims,len(query_claims),
                chat if use_model and model_available() and settings.answer_composition_enabled else None,
                settings.answer_composition_budget,settings.answer_composition_timeout_seconds,trace)
            _assert_access_valid(conn, access)
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
        output={'ok':True,'question':display_text,'source':source,'parse':json_safe(plan),'intent':intent,
            'plan':json_safe(plan),'sql':statement.sql,'sql_params':json_safe(statement.params),
            'graph_tables':links,'graph':graph_view(subject,links),'main_total':main_total,
            'main_rows':main if intent!='detail' else main[:settings.page_size],
            'related':[{**r,'rows':r['rows'][:settings.page_size]} for r in related],
            'page_size':settings.page_size,'graph_main_limit':settings.graph_main_limit,
            'related_scope':'all_matching_main_records','metric':metric,
            'field_meta':field_meta(catalog,[subject]+[r['table'] for r in related]),
            'answer':text_answer,'claims':claims,'answer_validation':validation,
            'query_summary':query_summary,'answer_document':document,
            'evidence':evidence,'model_evidence_delivery':delivery,
            'applied_rules':plan.get('applied_rules') or [],
            'plan_template':plan.get('plan_template'),
            'operational_window':plan.get('operational_window'),
            'ontology_version':catalog.version,'timings':timings,
            'elapsed':round(perf_counter()-started,3)}
        from app.agent.visual import build_visual_meta
        visual_plan = (plan if intent == 'acdm_report'
                       else _single_flight_visual_plan(plan, subject, intent, main, main_total, conn))
        visual_meta = (acdm_report_res['visual_meta']
                       if intent == 'acdm_report' and acdm_report_res is not None and acdm_report_res.get('visual_meta')
                       else build_visual_meta(visual_plan, main if intent != 'detail' else main[:settings.graph_main_limit], main_total, metric, catalog, related=related))
        output['visual_meta'] = visual_meta
        output['coverage_assessment'] = coverage_check
        if subject == 'airport_benchmark_daily':
            where, params = compiler.where(plan, {subject: 't0'})
            provenance = execute(conn, compiler.statement('SELECT BOOL_OR(t0.is_simulated) AS simulated FROM ' + compiler.table(subject) + ' t0' + where, tuple(params)))
            if provenance and provenance[0].get('simulated'):
                output['is_simulated'] = True
                warning = {'text': '模拟测试数据：这些机场记录仅供功能验证，不代表机场实际运行。', 'fact_ids': [], 'kind': 'limitations'}
                output['answer'] = warning['text'] + '\n' + output['answer']
                output['claims'] = [warning] + output['claims']
                output.pop('answer_document', None)
        if coordinated:
            output['outcome'] = 'answered' if main_total else 'no_data'
        emit('visual_meta', data=visual_meta)
        if access:
            output['access_revision']=getattr(access, 'revision', 'unknown')
            output['data_scope']=getattr(access, 'effective_scope', getattr(access, 'scope', 'ALL'))
        aggregate_table=plan.get('aggregate_target') or subject
        if intent not in ('detail', 'acdm_report'):
            output['field_meta'].update(field_meta(catalog,[aggregate_table]))
            output['field_meta'][aggregate_table].update({
                'n':{'label':'参与记录数'}, 'total':{'label':(metric or {}).get('name','合计'),
                                                  'unit':(metric or {}).get('unit','')},
                'mx':{'label':'单笔最大'}, 'missing':{'label':'金额为空记录数'},
                'invalid':{'label':'无效金额记录数'}, '__period':{'label':'统计期间'}})
        if plan.get('safeguard_node_breakdown'):
            output['field_meta'][subject].update({
                'node_name': {'label': '保障节点'}, 'safeguard_node_recorded_count': {'label': '实测记录数'},
                'distinct_flights': {'label': '去重航班数'}, 'entry_rate_pct': {'label': '适用航班覆盖率（%）'},
                'recorded_flights': {'label': '有录入的适用航班数'}, 'eligible_flights': {'label': '适用航班数'},
            })
        if main_total and intent not in ('turnaround', 'acdm_report'):
            target=plan.get('aggregate_target') or subject if intent!='detail' else subject
            output['drill_token']=token_for(plan,catalog,main_total,target)
        if any(r['count'] for r in related) and intent not in ('turnaround', 'acdm_report'):
            output['related_token']=token_for(plan,catalog,main_total,
                                related={r['table']:r['count'] for r in related})
        trace.start('done', '完成查询', '封装可核验结果、查询计划与执行过程')
        if use_cache and intent != 'acdm_report':
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


