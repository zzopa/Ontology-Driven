"""Additive /ops pages and /api/ops endpoints with independent scope checks."""
from functools import lru_cache
from contextvars import ContextVar
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from app.admin.auth import identity, same_origin
from app.config import settings
from .domain import Conflict, REPLAY_DATA_DATE, dt, iso, now, replay_time
from .service import OpsService
from .source import OpsSource
from .store import OpsStore, encode, event, items, record
from . import static_data

logger = logging.getLogger(__name__)
OPS_MODE = ContextVar('ops_mode', default='replay')
OPS_AT = ContextVar('ops_at', default=None)
# 暂时把全部运行工作台切到只读测试 JSON；恢复业务接入时关闭此开关。
STATIC_TEST_DATA = True


class OpsRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            if STATIC_TEST_DATA:
                try:
                    if request.url.path.startswith('/api/ops/'):
                        return JSONResponse(await run_in_threadpool(static_response, request), headers={'Cache-Control': 'no-store'})
                    return await original(request)
                except HTTPException:
                    raise
                except (ValueError, TypeError) as exc:
                    raise HTTPException(400, str(exc)) from exc
                except Exception as exc:
                    logger.exception('静态测试数据读取失败')
                    raise HTTPException(503, '静态测试 JSON 暂不可用；未回退到真实业务数据') from exc
            mode = request.query_params.get('mode', 'replay')
            if mode not in ('live', 'replay'):
                raise HTTPException(400, '运行模式请选择历史预演或实时时钟')
            selected = request.query_params.get('at')
            observed = dt(selected) if selected else None
            if selected is not None and (mode != 'replay' or not observed or 'T' not in selected or observed > now() or observed.date().isoformat() > REPLAY_DATA_DATE):
                raise HTTPException(400, '请选择有效的历史预演日期和时间（北京时间）；当前数据截至' + REPLAY_DATA_DATE)
            token = OPS_MODE.set(mode)
            time_token = OPS_AT.set(observed)
            try:
                return await original(request)
            except HTTPException:
                raise
            except Conflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except Exception as exc:
                logger.exception('独立运行工作台请求失败')
                raise HTTPException(503, '运行工作台暂不可用，请检查运行台账或数据源；原问询入口不受此接口影响') from exc
            finally:
                OPS_AT.reset(time_token)
                OPS_MODE.reset(token)
        return handle


router = APIRouter(route_class=OpsRoute)


def static_response(request):
    user = identity(request)['user']
    if request.method != 'GET':
        raise HTTPException(405, '当前全部 /ops 页面为静态 JSON 测试展示，审批、派单和报工等写操作已暂停')
    try:
        data = static_data.load_fixture()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.exception('静态测试 JSON 文件缺失或格式无效')
        raise HTTPException(503, '静态测试 JSON 文件缺失或格式无效；未回退到真实业务数据') from exc
    path = request.url.path[len('/api/ops'):]
    if path == '/me':
        admin = 'admin' in user.get('roles', [])
        grants = [] if admin else service_for_mode('replay').store.grants(user['id'])
        terminals = data['meta']['terminals'] if admin else sorted({g['terminal'] for g in grants} & set(data['meta']['terminals']))
        return {'ok': True, 'user': {'id': str(user['id']), 'display_name': user['display_name']}, 'admin': admin,
                'grants': grants, 'terminals': terminals, 'mode': 'replay', 'data_mode': 'static-test', 'read_only': True,
                'fixture_time': data['meta']['captured_at'], 'fixture_label': data['meta']['title']}
    terminal = terminal_name(request.query_params.get('terminal', 'T2'))
    if path == '/users' and 'admin' not in user.get('roles', []):
        raise HTTPException(403, '仅原系统管理员可检索授权账号')
    roles = ('viewer', 'dispatcher', 'crew') if path == '/work-orders' else ('dispatcher',) if path == '/flight-options' else ('viewer', 'dispatcher')
    actor = scope(request, terminal, roles)
    if terminal == 'ALL' and not actor['admin']:
        raise HTTPException(403, '全场静态测试视图仅限管理员；请使用已授权航站楼')
    return static_data.response(data, path, terminal, request.query_params, actor)


def get_service():
    return service_for_mode(OPS_MODE.get())


@lru_cache(maxsize=2)
def service_for_mode(mode):
    path = Path(os.environ.get('HBASK_OPS_LEDGER', settings.admin_directory.parent / 'operations' / 'ops.sqlite3'))
    replay = mode == 'replay'
    if replay:
        path = path.with_name(path.stem + '-replay' + path.suffix)
    clock = lambda: (OPS_AT.get() or replay_time(now())) if replay else now()
    return OpsService(OpsStore(path, clock=clock, simulation=replay), OpsSource(), mode=mode, clock=clock)


def terminal_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,16}', value):
        raise ValueError('航站楼编号无效')
    return value


def scope(request, terminal, roles=('viewer', 'dispatcher'), write=False):
    terminal_name(terminal)
    account = identity(request)
    user = account['user']
    if write:
        same_origin(request)
    admin = 'admin' in user.get('roles', [])
    grants = get_service().store.grants(user['id']) if not admin else []
    valid = [g for g in grants if g['terminal'] == terminal]
    if not admin and not any(g['role'] in roles for g in valid):
        raise HTTPException(403, '未获当前航站楼的运行岗位授权')
    return {'id': str(user['id']), 'user': user, 'admin': admin,
            'roles': ['admin'] if admin else sorted({g['role'] for g in valid}),
            'resource_ids': None if admin or any(g['role'] == 'dispatcher' for g in valid) else {g['resource_id'] for g in valid if g['role'] == 'crew'}}


async def body(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 65536:
            raise HTTPException(413, '请求内容过大')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('请求必须是有效JSON') from exc
    if not isinstance(value, dict):
        raise ValueError('请求格式无效')
    return value


def page(relative):
    path = settings.page_path.parent / relative
    if not path.is_file():
        raise HTTPException(503, '运行工作台尚未构建，请在 frontend 执行 npm run build')
    return HTMLResponse(path.read_text(encoding='utf-8'), headers={'Cache-Control': 'no-cache'})


@router.get('/ops', response_class=HTMLResponse)
def overview_page():
    return page('ops.html')


@router.get('/ops/flights', response_class=HTMLResponse)
def flights_page():
    return page('ops/flights.html')


@router.get('/ops/dispatch', response_class=HTMLResponse)
def dispatch_page():
    return page('ops/dispatch.html')


@router.get('/ops/work-orders', response_class=HTMLResponse)
def orders_page():
    return page('ops/work-orders.html')


@router.get('/ops/resources', response_class=HTMLResponse)
def resources_page():
    return page('ops/resources.html')


@router.get('/api/ops/me')
def me(request: Request):
    user = identity(request)['user']
    admin = 'admin' in user.get('roles', [])
    grants = get_service().store.grants(user['id']) if not admin else []
    return {'ok': True, 'user': {'id': str(user['id']), 'display_name': user['display_name']}, 'admin': admin,
            'grants': grants, 'terminals': ['T2', 'T3'] if admin else sorted({g['terminal'] for g in grants}),
            'mode': get_service().mode}


@router.get('/api/ops/snapshot')
@router.get('/api/ops/flights')
def snapshot(request: Request, terminal: str = 'T2', minutes: int = 90):
    scope(request, terminal)
    if minutes not in (30, 60, 90, 120, 180):
        raise ValueError('时间窗口请选择30、60、90、120或180分钟')
    return {'ok': True, **get_service().snapshot(terminal, minutes)}


@router.get('/api/ops/flights/{flight_id}')
def flight_detail(request: Request, flight_id: str, terminal: str = 'T2'):
    scope(request, terminal)
    service = get_service()
    data = service.source.read(terminal, observed=service.clock(), ids=[flight_id], details=True, replay=service.mode == 'replay')
    if not data['flights']:
        raise ValueError('当前授权航站楼没有该航班')
    flight, source = data['flights'][0], data['source']
    from app.turnaround_standard import build_standard_turnaround
    # Adapt only this view; the original question service and its readers are unchanged.
    process = flight.pop('_process', {})
    raw_flight = flight.pop('_flight', {**flight, 'stand_code': flight['stand'], 'gate_code': flight['gate']})
    ext = process.get('ext_json') or {}
    warnings = []
    try:
        ext = json.loads(ext) if isinstance(ext, str) else ext
    except ValueError:
        ext = None
    if not isinstance(ext, dict):
        warnings.append({'field': '扩展资料', 'value': None, 'reason': '源扩展资料格式异常，未用于生成节点时刻'})
        ext = {}
    process['ext_json'] = ext
    for key, field in (('actual_on_block', 'aibt'), ('scheduled_off_block', 'sobt'), ('tobt', 'tobt'), ('tsat', 'tsat')):
        if not process.get(key):
            process[key] = flight.get(field)
    for container in (raw_flight, process, process['ext_json']):
        for key, value in list(container.items()):
            if ('actual_' in key.lower() or key.lower().startswith('real_') or '_act_' in key.lower() or key in ('LOAD_START_TIME', 'LOAD_END_TIME', 'boarding_start', 'boarding_end', 'first_baggage', 'last_baggage')) and dt(value) and dt(value) > service.clock() + (timedelta(0) if service.mode == 'replay' else timedelta(minutes=1)):
                warnings.append({'field': key, 'value': iso(value), 'reason': '预演时刻之后的实绩暂不展示' if service.mode == 'replay' else '源实绩晚于当前观测时刻，未计为完成'})
                container[key] = None
    keys = {'cleaning': ('REAL_START_CLEAN_TIME', 'REAL_END_CLEAN_TIME', 'ALTERATE_START_CLEAN_TIME', 'ALTERATE_END_CLEAN_TIME'),
            'fueling': ('REAL_START_SUPPLY_TIME', 'REAL_END_SUPPLY_TIME', 'ALTERATE_START_SUPPLY_TIME', 'ALTERATE_END_SUPPLY_TIME'),
            'catering': ('REAL_MEAL_START_TIME', 'REAL_MEAL_END_TIME', 'ALTERATE_MEAL_START_TIME', 'ALTERATE_MEAL_END_TIME'),
            'loading': ('LOAD_START_TIME', 'LOAD_END_TIME', 'PLAN_LOAD_START_TIME', 'PLAN_LOAD_END_TIME'),
            'boarding': ('REAL_BEGIN_BOARD_TIME', 'REAL_END_BOARD_TIME', 'ALTERATE_BEGIN_BOARD_TIME', 'ALTERATE_END_BOARD_TIME')}
    for op in flight['operations']:
        for key, val in zip(keys[op['kind']], (None if op.get('invalid_time') else op['actual_start'], None if op.get('invalid_time') else op['actual_end'], op['plan_start'], op['plan_end'])):
            process['ext_json'][key] = val
        if op['kind'] == 'boarding':
            process.update(boarding_start=None if op.get('invalid_time') else op['actual_start'], boarding_end=None if op.get('invalid_time') else op['actual_end'])
        if op.get('invalid_time'):
            typed_keys = {'cleaning': ('real_start_clean_time', 'real_end_clean_time'), 'fueling': ('real_start_supply_time', 'real_end_supply_time'),
                          'catering': ('real_meal_start_time', 'real_meal_end_time'), 'loading': ('load_start_time', 'load_end_time'), 'boarding': ('boarding_start', 'boarding_end')}
            for key in typed_keys[op['kind']]:
                process[key] = None
            warnings.append({'field': op['name'], 'value': op['actual_end'] or op['actual_start'], 'reason': '源作业时序异常；原值保留在保障记录中'})
    value = build_standard_turnaround(raw_flight, process, flight['flight_no'], True)
    value.pop('source_row', None)
    value.pop('process_row', None)
    linked = service.source.linked(terminal, flight_id, observed=service.clock(), replay=service.mode == 'replay')
    return {'ok': True, 'flight': flight, 'turnaround': value, 'source': source, 'linked_flights': linked, 'quality_notes': warnings}


@router.get('/api/ops/alerts')
def alerts(request: Request, terminal: str = 'T2'):
    scope(request, terminal)
    return {'ok': True, 'alerts': get_service().store.alerts(terminal)}


@router.post('/api/ops/alerts/{ident}/acknowledge')
async def acknowledge(request: Request, ident: str, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, write=True)
    service = get_service()
    await run_in_threadpool(service.snapshot, terminal, persist=True)
    await run_in_threadpool(service.store.acknowledge, ident, terminal, actor['id'])
    return {'ok': True}


@router.get('/api/ops/resources')
def resources(request: Request, terminal: str = 'T2'):
    actor = scope(request, terminal)
    view = get_service().store.view(terminal)
    for key in ('proposals', 'work_orders'):
        view.pop(key)
    view['grants'] = get_service().store.grants() if actor['admin'] else []
    return {'ok': True, **view}


@router.post('/api/ops/resources')
async def create_resource(request: Request, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    return {'ok': True, 'resource': await run_in_threadpool(get_service().store.resource, await body(request), terminal, actor['id'])}


@router.patch('/api/ops/resources/{ident}')
async def update_resource(request: Request, ident: str, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    return {'ok': True, 'resource': await run_in_threadpool(get_service().store.resource, await body(request), terminal, actor['id'], ident)}


@router.post('/api/ops/resources/{ident}/confirm')
async def confirm_resource(request: Request, ident: str, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher', 'crew'), True)
    if actor['resource_ids'] is not None and ident not in actor['resource_ids']:
        raise HTTPException(403, '只能确认所属班组')
    data = await body(request)
    if set(data) - {'revision', 'position', 'availability'}:
        raise ValueError('状态确认接口只接受当前机位、忙闲状态与版本')
    return {'ok': True, 'resource': await run_in_threadpool(get_service().store.resource, {'revision': data.get('revision'), 'position': data.get('position'), 'availability': data.get('availability', 'unknown'), 'confirm_position': True}, terminal, actor['id'], ident)}


@router.get('/api/ops/rosters')
def rosters(request: Request, terminal: str = 'T2'):
    scope(request, terminal)
    return {'ok': True, 'rosters': get_service().store.view(terminal)['rosters']}


@router.post('/api/ops/rosters')
async def add_roster(request: Request, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    return {'ok': True, 'id': await run_in_threadpool(get_service().store.roster, await body(request), terminal, actor['id'])}


@router.post('/api/ops/tasks')
async def add_task(request: Request, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    data = await body(request)
    service = get_service()
    flight, _ = await run_in_threadpool(service.source.flight, terminal, str(data.get('flight_id', '')), service.clock(), replay=service.mode == 'replay')
    if flight['cancelled']:
        raise ValueError('航班已取消，不能登记派工作业')
    if not flight['stand']:
        from app.admin.store import text
        flight['stand'] = text(data.get('stand'), '人工确认机位', 24)
        data['stand_source'] = '人工确认'
    else:
        data['stand_source'] = '源系统'
    return {'ok': True, 'id': await run_in_threadpool(get_service().store.task, data, flight, terminal, actor['id'])}


@router.post('/api/ops/tasks/{ident}/cancel')
async def cancel_task(request: Request, ident: str, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    data = await body(request)
    def cancel():
        with get_service().store.db() as db:
            task = record(db.execute('SELECT * FROM tasks WHERE id=? AND terminal=?', (ident, terminal)).fetchone())
            if not task or task.get('work_order_id') or task['status'] != 'planned' or task['revision'] != data.get('revision'):
                raise Conflict('任务已变化或已生成工单，请在工单中心处理')
            db.execute("UPDATE tasks SET status='cancelled',revision=revision+1 WHERE id=?", (ident,))
            event(db, actor['id'], 'task.cancel', ident)
    await run_in_threadpool(cancel)
    return {'ok': True}


@router.post('/api/ops/tasks/{ident}/report')
async def report_task(request: Request, ident: str, terminal: str = 'T2'):
    data = await body(request)
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',) if data.get('action') == 'verify' else ('dispatcher', 'crew'), True)
    if type(data.get('revision')) is not int or not isinstance(data.get('note', ''), str):
        raise ValueError('任务版本或说明无效')
    await run_in_threadpool(get_service().report_task, ident, data.get('action'), data['revision'], terminal, actor['id'], actor['resource_ids'], data.get('note', ''), data.get('actual_at'))
    return {'ok': True}


@router.get('/api/ops/flight-options')
def flight_options(request: Request, terminal: str = 'T2', q: str = '', date: str = ''):
    scope(request, terminal, ('dispatcher',))
    if len(q) > 20:
        raise ValueError('航班检索条件过长')
    service = get_service()
    observed = dt(date + 'T12:00:00+08:00') if date else service.clock()
    if not observed:
        raise ValueError('业务日无效')
    flights = service.source.read(terminal, observed, all_day=True, replay=service.mode == 'replay')['flights']
    flights = [f for f in flights if f['flight_date'] == observed.date().isoformat() and q.strip().upper() in f['flight_no'].upper() and not f['cancelled']]
    return {'ok': True, 'flights': flights[:200], 'total': len(flights)}


@router.post('/api/ops/transfers')
async def save_transfer(request: Request, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    await run_in_threadpool(get_service().store.transfer, await body(request), terminal, actor['id'])
    return {'ok': True}


@router.post('/api/ops/policies')
async def save_policy(request: Request, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    await run_in_threadpool(get_service().store.policy, await body(request), terminal, actor['id'])
    return {'ok': True}


@router.post('/api/ops/grants')
async def save_grant(request: Request):
    account = await run_in_threadpool(identity, request)
    same_origin(request)
    if 'admin' not in account['user'].get('roles', []):
        raise HTTPException(403, '仅原系统管理员可维护运行岗位授权')
    data = await body(request)
    terminal_name(data.get('terminal'))
    if not data.get('revoke') and not await run_in_threadpool(existing_users, user_id=str(data.get('user_id', ''))):
        raise ValueError('请选择仍处于启用状态的原系统业务账号')
    await run_in_threadpool(get_service().store.grant, data, account['user']['id'])
    return {'ok': True}


def existing_users(query='', user_id=None):
    from app.core import connect_db
    conn = connect_db(identity=True)
    try:
        cur = conn.cursor()
        if user_id is not None:
            cur.execute('SELECT id::text,username,display_name FROM app_user WHERE id::text=%s AND enabled', (user_id,))
        else:
            pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            cur.execute('SELECT id::text,username,display_name FROM app_user WHERE enabled AND (username ILIKE %s OR display_name ILIKE %s) ORDER BY username LIMIT 30', (pattern, pattern))
        return [{'id': row[0], 'username': row[1], 'display_name': row[2]} for row in cur.fetchall()]
    finally:
        conn.close()


@router.get('/api/ops/users')
def users(request: Request, q: str = ''):
    account = identity(request)
    if 'admin' not in account['user'].get('roles', []):
        raise HTTPException(403, '仅原系统管理员可检索授权账号')
    if not q.strip():
        return {'ok': True, 'users': []}
    if len(q) > 64:
        raise ValueError('账号检索条件过长')
    return {'ok': True, 'users': existing_users(q.strip())}


@router.post('/api/ops/settings')
async def save_settings(request: Request, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, (), True)
    data = await body(request)
    if set(data) != {'shadow_mode', 'dispatch_enabled'} or any(type(v) is not bool for v in data.values()):
        raise ValueError('运行设置无效')
    store = get_service().store
    def save():
        with store.db() as db:
            store.set_state('settings:' + terminal, data, db)
            event(db, actor['id'], 'settings.save', terminal, data)
    await run_in_threadpool(save)
    return {'ok': True}


@router.get('/api/ops/proposals')
def proposals(request: Request, terminal: str = 'T2'):
    scope(request, terminal)
    return {'ok': True, 'proposals': list(reversed(get_service().store.view(terminal)['proposals']))}


@router.post('/api/ops/proposals')
async def preview(request: Request, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    return await run_in_threadpool(get_service().preview, await body(request), terminal, actor['id'])


@router.post('/api/ops/replay/prepare')
async def prepare_replay(request: Request, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    return await run_in_threadpool(get_service().prepare_replay, terminal, actor['id'])


@router.post('/api/ops/proposals/{ident}/approve')
async def approve(request: Request, ident: str, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    data = await body(request)
    return {'ok': True, 'order': await run_in_threadpool(get_service().approve, ident, data.get('version'), data.get('idempotency_key'), terminal, actor['id'])}


@router.post('/api/ops/proposals/{ident}/reject')
async def reject(request: Request, ident: str, terminal: str = 'T2'):
    actor = await run_in_threadpool(scope, request, terminal, ('dispatcher',), True)
    await run_in_threadpool(get_service().reject, ident, (await body(request)).get('version'), terminal, actor['id'])
    return {'ok': True}


@router.get('/api/ops/work-orders')
def work_orders(request: Request, terminal: str = 'T2'):
    actor = scope(request, terminal, ('viewer', 'dispatcher', 'crew'))
    view = get_service().store.view(terminal)
    orders = view['work_orders']
    if actor['resource_ids'] is not None and 'viewer' not in actor['roles']:
        orders = [o for o in orders if o['resource_id'] in actor['resource_ids']]
    for order in orders:
        order.pop('approval_key', None)
    tasks = [t for t in view['tasks'] if not t.get('work_order_id')]
    if actor['resource_ids'] is not None and 'viewer' not in actor['roles']:
        tasks = [t for t in tasks if t['resource_id'] in actor['resource_ids']]
    ids = {o['id'] for o in orders}
    events = [e for e in get_service().store.events(terminal) if e['target'] in ids]
    resources = view['resources'] if actor['resource_ids'] is None or 'viewer' in actor['roles'] else [r for r in view['resources'] if r['id'] in actor['resource_ids']]
    flight_ids = sorted({o['flight_id'] for o in orders} | {t['flight_id'] for t in tasks})
    source_flights, source_error = [], ''
    try:
        for offset in range(0, len(flight_ids), 100):
            service = get_service()
            source_flights.extend(service.source.read(terminal, observed=service.clock(), ids=flight_ids[offset:offset + 100], replay=service.mode == 'replay')['flights'])
    except Exception:
        logger.exception('工单源实绩对照不可用')
        source_error = '源实绩暂不可用'
    return {'ok': True, 'orders': list(reversed(orders)), 'events': events, 'resources': resources,
            'manual_tasks': tasks, 'source_flights': source_flights, 'source_error': source_error}


@router.post('/api/ops/work-orders/{ident}/{action}')
async def order_action(request: Request, ident: str, action: str, terminal: str = 'T2'):
    roles = ('dispatcher',) if action in ('verify', 'cancel') else ('dispatcher', 'crew')
    actor = await run_in_threadpool(scope, request, terminal, roles, True)
    data = await body(request)
    if type(data.get('revision')) is not int or not isinstance(data.get('note', ''), str):
        raise ValueError('工单版本或处理说明无效')
    result = await run_in_threadpool(get_service().transition, ident, action, data['revision'], terminal, actor['id'], actor['resource_ids'], data.get('note', ''), data.get('actual_at'))
    result.pop('approval_key', None)
    return {'ok': True, 'order': result}


@router.post('/api/ops/assist')
async def assist(request: Request, terminal: str = 'T2'):
    await run_in_threadpool(scope, request, terminal, write=True)
    data = await body(request)
    value = await run_in_threadpool(get_service().snapshot, terminal)
    if data.get('snapshot_id') and data['snapshot_id'] != value['snapshot_id']:
        raise Conflict('运行快照已更新，请刷新后重新提问')
    return get_service().assist(value, data.get('question'))
