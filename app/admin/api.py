"""Protected administration and owner-scoped conversation endpoints."""
from time import perf_counter
import urllib.error

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import core, llm
from app.admin import service, glossary
from app.admin.auth import COOKIE, identity, require_role, same_origin, set_cookie
from app.config import settings
from app import business_access

router = APIRouter()


async def body(request):
    try:
        value = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, '需要有效的 JSON 请求') from exc
    if not isinstance(value, dict):
        raise HTTPException(400, '需要 JSON 对象')
    return value


def page_number(request):
    try:
        page = int(request.query_params.get('page', 1))
        if not 1 <= page <= 100000:
            raise ValueError()
        return page
    except ValueError as exc:
        raise HTTPException(400, '页码无效') from exc


@router.get('/api/business-auth/status')
@router.get('/api/auth/status')
def auth_status(request: Request):
    try:
        value = identity(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        value = None
    return {'ok': True, 'initialized': True, 'can_setup': False, 'allow_guest': False,
            'source': 'hbairport01', 'user': value['user'] if value else None}


@router.post('/api/business-auth/login')
@router.post('/api/auth/login')
async def login(request: Request):
    same_origin(request)
    data = await body(request)
    token, user = await run_in_threadpool(business_access.login, data.get('username'), data.get('password'), request.client.host)
    business_access.logout(request.cookies.get(COOKIE))
    response = JSONResponse({'ok': True, 'user': user})
    set_cookie(response, token, request)
    return response


@router.post('/api/business-auth/logout')
@router.post('/api/auth/logout')
def logout(request: Request):
    same_origin(request)
    business_access.logout(request.cookies.get(COOKIE))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, path='/')
    response.delete_cookie('hbask_session', path='/')
    return response


@router.get('/api/admin/overview')
def overview(request: Request):
    account = identity(request)
    return {'ok': True, 'counts': service.get_store().overview(None if account['user']['can_manage'] else account['owner']), 'status': core.status(),
            'active_model': next((item for item in service.get_store().models() if item['active']), None)}


@router.get('/api/admin/models')
def models(request: Request):
    require_role(request, 'admin')
    return {'ok': True, 'items': service.get_store().models()}


@router.post('/api/admin/models')
@router.put('/api/admin/models/{model_id}')
async def model_save(request: Request, model_id: str = None):
    same_origin(request)
    user = require_role(request, 'admin')['user']
    return {'ok': True, 'model': service.get_store().save_model(await body(request), user['id'], model_id)}


@router.post('/api/admin/models/{model_id}/activate')
def model_activate(request: Request, model_id: str):
    same_origin(request)
    user = require_role(request, 'admin')['user']
    service.get_store().activate_model(model_id, user['id'])
    return {'ok': True}


@router.delete('/api/admin/models/{model_id}')
def model_delete(request: Request, model_id: str):
    same_origin(request)
    user = require_role(request, 'admin')['user']
    service.get_store().delete_model(model_id, user['id'])
    return {'ok': True}


@router.post('/api/admin/models/{model_id}/test')
async def model_test(request: Request, model_id: str):
    same_origin(request)
    user = require_role(request, 'admin')['user']
    model = service.get_store().model(model_id)
    def test():
        started = perf_counter()
        try:
            with llm.model_context(model):
                content = llm.chat('这是连接检测，请简短回复 OK。', 'OK', max_tokens=16, timeout=12)
            if not isinstance(content, str) or not content.strip():
                return {'ok': True, 'connected': False, 'message': '接口返回空内容，请检查模型的兼容性'}
            # Never expose arbitrary provider text/errors (may echo API credentials).
            return {'ok': True, 'connected': True, 'message': '模型接口响应正常', 'seconds': round(perf_counter()-started, 2)}
        except urllib.error.HTTPError as exc:
            return {'ok': True, 'connected': False, 'message': f'模型接口 HTTP {exc.code}，请检查模型标识、密钥和额度'}
        except Exception:
            return {'ok': True, 'connected': False, 'message': '连接失败或响应格式不兼容，请核对完整接口地址与网络'}
    value = await run_in_threadpool(test)
    service.get_store().audit(user['id'], 'model.test', model_id, '成功' if value.get('connected') else '失败')
    return value


@router.get('/api/business-conversations')
@router.get('/api/conversations')
def conversations(request: Request):
    value = identity(request)
    if request.query_params.get('scope') == 'all':
        raise HTTPException(403, '只能查看自己的问答会话')
    result = service.get_store().conversations(value['owner'], search=request.query_params.get('search', '')[:120],
                                               archived=request.query_params.get('archived') == 'true', page=page_number(request))
    for item in result['items']:
        item['owner_name'] = value['user']['display_name']
    return result


def conversation_access(request):
    value = identity(request)
    if request.query_params.get('scope') == 'all':
        raise HTTPException(403, '只能查看自己的问答会话')
    return value, False


@router.get('/api/business-conversations/{conversation_id}')
@router.get('/api/conversations/{conversation_id}')
def conversation(request: Request, conversation_id: str):
    value, all_access = conversation_access(request)
    result = service.get_store().conversation(conversation_id, value['owner'], all_access)
    result['conversation']['owner_name'] = value['user']['display_name']
    if value.get('access'):
        # Stored snapshots cannot be safely re-filtered after grants change.
        for message in result['messages']:
            snapshot = message.get('result')
            if not snapshot:
                message['execution_trace'] = None
            if snapshot and snapshot.get('access_revision') != value['access'].revision:
                message['result'] = None
                message['execution_trace'] = None
                message['error'] = '业务权限已变化，历史数据已隐藏；请重新提问获取当前授权范围内的结果'
    return result


@router.patch('/api/business-conversations/{conversation_id}')
@router.patch('/api/conversations/{conversation_id}')
async def conversation_edit(request: Request, conversation_id: str):
    same_origin(request)
    value, all_access = conversation_access(request)
    service.get_store().edit_conversation(conversation_id, value['owner'], await body(request), all_access)
    return {'ok': True}


@router.delete('/api/business-conversations/{conversation_id}')
@router.delete('/api/conversations/{conversation_id}')
def conversation_delete(request: Request, conversation_id: str):
    same_origin(request)
    value, all_access = conversation_access(request)
    service.get_store().delete_conversation(conversation_id, value['owner'], all_access)
    return {'ok': True}


@router.get('/api/admin/glossary')
def glossary_get(request: Request):
    identity(request)
    state = core.ontology_document()
    return {'ok': True, 'revision': state['revision'], 'items': glossary.entries(state['document'], core.current_catalog()),
            'entities': {key: {'name': spec['name'], 'attributes': {k: v['label'] for k, v in spec['attributes'].items()}}
                         for key, spec in core.current_catalog().entities.items()},
            'enterprises': [{'name': name, 'code': code} for name, code in sorted(core.ENTERPRISE_NAMES.items())]}


@router.post('/api/admin/glossary')
@router.put('/api/admin/glossary/{entry_id}')
@router.delete('/api/admin/glossary/{entry_id}')
async def glossary_save(request: Request, entry_id: str = None):
    same_origin(request)
    user = require_role(request, 'admin')['user']
    data = await body(request)
    def save():
        with core.STORE.lock:
            state = core.ontology_document()
            if data.get('revision') != state['revision']:
                raise ValueError('本体已变化，请刷新词条后再提交')
            draft = glossary.apply(state['document'], core.current_catalog(), data, entry_id,
                                   request.method == 'DELETE', set(core.ENTERPRISE_NAMES.values()))
            result = core.update_ontology(draft, state['revision'])
            service.get_store().audit(user['id'], 'glossary.' + request.method.lower(), entry_id or data.get('kind', ''))
            return {'ok': True, 'revision': result['revision']}
    return await run_in_threadpool(save)


@router.get('/api/admin/settings')
def preferences(request: Request):
    require_role(request, 'admin')
    return {'ok': True, 'settings': service.get_store().preferences(), 'runtime': {
        'host': settings.host, 'port': settings.port, 'lan_cidr': str(settings.lan_cidr),
        'database': settings.db['database'], 'database_host': settings.db['host'],
        'page_size': settings.page_size, 'query_timeout_ms': settings.query_timeout_ms,
        'answer_composition_enabled': settings.answer_composition_enabled,
        'answer_composition_budget': settings.answer_composition_budget,
        'answer_composition_timeout_seconds': settings.answer_composition_timeout_seconds,
        'session_hours': settings.admin_session_hours, 'embed_parent_origins': list(settings.embed_parent_origins)}}


@router.put('/api/admin/settings')
async def preferences_save(request: Request):
    same_origin(request)
    user = require_role(request, 'admin')['user']
    return {'ok': True, 'settings': service.get_store().save_preferences(await body(request), user['id'])}


@router.post('/api/admin/retention/purge')
def purge(request: Request):
    same_origin(request)
    user = require_role(request, 'admin')['user']
    return {'ok': True, 'deleted': service.get_store().purge(user['id'])}


@router.get('/api/admin/audit')
def audit(request: Request):
    require_role(request, 'admin')
    return service.get_store().audit_list(page_number(request), request.query_params.get('search', '')[:100])
