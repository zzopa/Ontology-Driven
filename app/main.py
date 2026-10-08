"""FastAPI HTTP 层；业务逻辑位于 app.core。"""
from contextlib import asynccontextmanager
from ipaddress import ip_address, ip_network
from queue import Queue
from threading import Thread
import json
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app import core, llm
from app.admin import service as admin_service
from app.admin.api import router as admin_router
from app.admin.auth import identity, require_role, same_origin
from app.config import settings
from app.execution import ExecutionTrace
from app import business_access


logger = logging.getLogger(__name__)
LOCAL_NETWORK = ip_network('127.0.0.0/8')


def allowed_client(host):
    try:
        address = ip_address(host)
        return address.is_loopback or address in LOCAL_NETWORK or address in settings.lan_cidr
    except (TypeError, ValueError):
        return False


@asynccontextmanager
async def lifespan(_app):
    store = admin_service.get_store()
    llm.set_provider(store.model)
    stats = await run_in_threadpool(core.initialize)
    logger.info('知识图谱服务就绪：数据库=%s，业务对象=%s，启动耗时=%ss',
                stats['database'], stats['queryable_entities'], stats['startup_seconds'])
    yield


app = FastAPI(title='hbairport 图谱问答', lifespan=lifespan,
              docs_url=None, redoc_url=None)
app.include_router(admin_router)

# Next.js static export; business APIs stay same-origin on the existing FastAPI port.
app.mount('/_next', StaticFiles(directory=settings.page_path.parent / '_next', check_dir=False), name='next-assets')


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    return JSONResponse({'ok': False, 'error': exc.detail},
                        status_code=exc.status_code)


@app.exception_handler(ValueError)
async def invalid_value(_request: Request, exc: ValueError):
    return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.middleware('http')
async def restrict_to_lan(request: Request, call_next):
    if not allowed_client(request.client.host if request.client else None):
        return PlainTextResponse('forbidden', status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # Only explicitly trusted hosts can embed the application. Never open to every origin.
    ancestors = "'self' " + ' '.join(settings.embed_parent_origins)
    response.headers['Content-Security-Policy'] = 'frame-ancestors ' + ancestors.strip()
    return response


async def request_data(request: Request):
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def with_connection(operation, *args):
    conn = core.connect_db()
    try:
        return operation(conn, *args)
    finally:
        conn.close()


@app.get('/', response_class=HTMLResponse)
@app.get('/index.html', response_class=HTMLResponse)
def index():
    return HTMLResponse(settings.page_path.read_text(encoding='utf-8'),
                        headers={'Cache-Control': 'no-cache'})


@app.post('/api/ask')
async def ask(request: Request):
    data = await request_data(request)
    prepared = prepare_question(request, data)
    try:
        result = await run_in_threadpool(run_question, prepared)
        return JSONResponse(result)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception('问答请求失败')
        raise HTTPException(status_code=500, detail='查询失败，请稍后重试或联系管理员') from exc


def prepare_question(request, data):
    same_origin(request)
    question = data.get('question')
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise HTTPException(400, '问题需要 1–4000 个字符')
    conversation_id = data.get('conversation_id')
    if conversation_id is not None and (not isinstance(conversation_id, str) or len(conversation_id) != 32):
        raise HTTPException(400, '会话标识无效')
    value = business_access.query_identity(request)
    store = admin_service.get_store()
    model = store.model()
    conversation_id, message_id = store.begin_question(value['owner'], question.strip(), model['model'], conversation_id)
    return {'question': question.strip(), 'model': model, 'conversation_id': conversation_id, 'message_id': message_id,
            'business_user_id': value['access'].user_id if value.get('access') else None}


def run_question(prepared, on_event=None):
    store = admin_service.get_store()
    trace = ExecutionTrace(on_event)
    trace.start('connect', '连接业务数据库', '建立只读数据库连接，设置查询超时')
    try:
        with llm.model_context(prepared['model']):
            conn = core.connect_db()
            trace.finish('connect', '只读数据库连接已就绪', ['业务数据库：' + settings.db['database']])
            try:
                access = business_access.load_access(conn, prepared['business_user_id']) if prepared.get('business_user_id') else None
                options = {'access': access} if access else {}
                result = core.do_ask(conn, prepared['question'], on_event=on_event, trace=trace, **options)
                if access and business_access.load_access(conn,access.user_id).revision != access.revision:
                    raise ValueError('业务权限已变化，请重新提问')
            finally:
                conn.close()
        result.update({'conversation_id': prepared['conversation_id'], 'message_id': prepared['message_id'],
                       'execution_trace': trace.snapshot()})
        store.complete_question(prepared['message_id'], result, token_ttl=settings.drill_ttl_seconds)
        return result
    except Exception as exc:
        message = str(exc.detail)[:200] if isinstance(exc, HTTPException) else str(exc)[:200] if isinstance(exc, ValueError) else '查询失败，请重试或联系管理员'
        trace.fail_active(message)
        store.complete_question(prepared['message_id'], error=message, execution_trace=trace.snapshot())
        raise


def stream_question(prepared):
    events = Queue()
    sentinel = object()

    def work():
        try:
            events.put({'type': 'progress', 'stage': 'connect',
                        'message': '正在连接业务数据库，准备查询…', 'percent': 5,
                        'conversation_id': prepared['conversation_id']})
            result = run_question(prepared, events.put)
            events.put({'type': 'result', 'data': result})
        except Exception as exc:
            logger.exception('流式问答失败')
            events.put({'type': 'error', 'message': str(exc.detail)[:200] if isinstance(exc, HTTPException) else str(exc)[:200] if isinstance(exc, ValueError) else '查询失败，请重试或联系管理员'})
        finally:
            events.put(sentinel)

    Thread(target=work, daemon=True).start()
    while True:
        event = events.get()
        if event is sentinel:
            break
        yield (json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8')


@app.post('/api/ask/stream')
async def ask_stream(request: Request):
    data = await request_data(request)
    prepared = prepare_question(request, data)
    return StreamingResponse(
        stream_question(prepared), media_type='application/x-ndjson; charset=utf-8',
        headers={'Cache-Control': 'no-cache, no-transform',
                 'X-Accel-Buffering': 'no', 'Connection': 'close'})


@app.post('/api/records')
async def records(request: Request):
    same_origin(request)
    data = await request_data(request)
    value = business_access.query_identity(request)
    if not admin_service.get_store().check_token(data.get('token'), value['owner']):
        raise HTTPException(403, '明细令牌已过期或不属于当前会话，请重新提问')
    try:
        result = await run_in_threadpool(
            with_connection, core.fetch_drill_page, data.get('token'), data.get('page'), value.get('access'))
        return JSONResponse(result)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception('主表明细加载失败')
        raise HTTPException(status_code=500, detail='主表明细加载失败') from exc


@app.post('/api/related')
async def related(request: Request):
    same_origin(request)
    data = await request_data(request)
    value = business_access.query_identity(request)
    if not admin_service.get_store().check_token(data.get('token'), value['owner']):
        raise HTTPException(403, '明细令牌已过期或不属于当前会话，请重新提问')
    try:
        result = await run_in_threadpool(
            with_connection, core.fetch_related_page,
            data.get('token'), data.get('table'), data.get('page'), value.get('access'))
        return JSONResponse(result)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception('关联表明细加载失败')
        raise HTTPException(status_code=500, detail='关联明细加载失败') from exc


def require_ontology_editor(request: Request):
    same_origin(request)
    return require_role(request, 'admin')['user']


@app.get('/ontology', response_class=HTMLResponse)
def ontology_page():
    return HTMLResponse((settings.page_path.parent / 'ontology.html').read_text(encoding='utf-8'),
                        headers={'Cache-Control': 'no-cache'})


@app.get('/embed', response_class=HTMLResponse)
def embedded_page():
    return HTMLResponse((settings.page_path.parent / 'embed.html').read_text(encoding='utf-8'),
                        headers={'Cache-Control': 'no-cache'})


@app.get('/admin', response_class=HTMLResponse)
def admin_page():
    return HTMLResponse((settings.page_path.parent / 'admin.html').read_text(encoding='utf-8'),
                        headers={'Cache-Control': 'no-cache'})


@app.get('/api/ui-config')
def ui_config():
    # This is an allowlist, not the private database/model configuration.
    return {'embed_parent_origins': list(settings.embed_parent_origins)}


@app.get('/api/status')
def service_status():
    return core.status()


@app.get('/api/ontology')
def ontology_get(request: Request):
    account = identity(request)
    value = core.ontology_document()
    value['can_edit'] = account['user']['can_manage']
    return value


@app.put('/api/ontology')
async def ontology_save(request: Request):
    user = require_ontology_editor(request)
    data = await request_data(request)
    try:
        result = await run_in_threadpool(core.update_ontology, data.get('document'), data.get('revision'))
        admin_service.get_store().audit(user['id'], 'ontology.save', result['revision'])
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/api/ontology/history')
def ontology_history():
    return {'ok': True, 'history': core.STORE.history()}


@app.post('/api/ontology/rollback')
async def ontology_rollback(request: Request):
    user = require_ontology_editor(request)
    data = await request_data(request)
    try:
        result = await run_in_threadpool(core.rollback_ontology, data.get('revision'), data.get('expected'))
        admin_service.get_store().audit(user['id'], 'ontology.rollback', data.get('revision', ''))
        return result
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/api/metadata/refresh')
async def metadata_refresh(request: Request):
    user = require_ontology_editor(request)
    result = {'ok': True, 'status': await run_in_threadpool(core.initialize)}
    admin_service.get_store().audit(user['id'], 'metadata.refresh')
    return result


@app.post('/api/ontology/validate-relations')
async def relation_validation(request: Request):
    user = require_ontology_editor(request)
    from app.validation import profile_relations
    from app.metadata import save_snapshot
    catalog = core.current_catalog()
    def check():
        report = with_connection(profile_relations, catalog)
        save_snapshot(settings.schema_path.parent / 'relation_validation.json', report)
        return {'ok': True, 'report': report}
    result = await run_in_threadpool(check)
    admin_service.get_store().audit(user['id'], 'ontology.validate_relations')
    return result
