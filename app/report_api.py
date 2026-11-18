"""Same-origin, owner-scoped report center endpoints."""
import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app import reporting
from app.admin import service
from app.admin.api import body, page_number
from app.admin.auth import require_role, same_origin
from app.config import settings

router = APIRouter()


def account(request, write=False):
    if write:
        same_origin(request)
    if settings.data_source != 'ops_ask':
        raise HTTPException(403, '报告中心仅在ops_ask数据源开放')
    return require_role(request, 'admin')


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise HTTPException(400, '标识无效')
    return value


@router.get('/api/reports')
def reports(request: Request):
    user = account(request)
    return reporting.list_reports(service.get_store(), user['owner'], page_number(request))


@router.post('/api/reports', status_code=202)
async def create_report(request: Request):
    user, data = account(request, True), await body(request)
    store = service.get_store()
    if 'message_id' in data:
        valid_id(data['message_id'])
        cid = data.get('conversation_id')
        if not isinstance(cid, str):
            raise HTTPException(400, '请指定报告来源会话')
        valid_id(cid)
        conversation = await run_in_threadpool(store.conversation, cid, user['owner'])
        message = next((m for m in conversation['messages'] if m['id'] == data['message_id']), None)
        if not message or message['status'] != 'completed' or not message.get('result') or message['result'].get('outcome', 'answered') != 'answered':
            raise HTTPException(400, '该消息没有可导出的已完成结果')
        rid = reporting.enqueue_snapshot(store, user['owner'], user['user']['id'], message['result'], message['id'])
    else:
        payload = reporting.validate_request(data, user['user']['id'])
        rid = reporting.enqueue(store, user['owner'], payload)
    return {'ok': True, 'report': reporting.public_report(reporting.get_report(store, user['owner'], rid))}


@router.get('/api/reports/{report_id}')
def report_detail(report_id: str, request: Request):
    user, store = account(request), service.get_store()
    row = reporting.get_report(store, user['owner'], valid_id(report_id))
    return {'ok': True, 'report': reporting.public_report(row),
            'result': json.loads(store.vault.open(row['result'])) if row['result'] else None}


@router.get('/api/reports/{report_id}/download')
def download_report(report_id: str, request: Request):
    user, store = account(request), service.get_store()
    row = reporting.get_report(store, user['owner'], valid_id(report_id))
    if row['status'] != 'completed' or not row['pdf_path']:
        raise HTTPException(409, '报告尚未生成完成')
    directory = (store.directory / 'reports').resolve()
    path = (directory / row['pdf_path']).resolve()
    if path.parent != directory or not path.is_file():
        raise HTTPException(404, '报告文件不存在')
    pdf = store.vault.cipher.decrypt(path.read_bytes())
    return Response(pdf, media_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="acdm-report-{report_id}.pdf"',
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.post('/api/reports/{report_id}/retry')
def retry_report(report_id: str, request: Request):
    user, store = account(request, True), service.get_store()
    with store.db() as db:
        if not db.execute("UPDATE reports SET status='queued',attempts=0,next_attempt=0,lease_until=0,error=NULL WHERE id=? AND owner=? AND status='failed'", (valid_id(report_id), user['owner'])).rowcount:
            raise HTTPException(409, '只能重试本人已失败的报告')
        store.log(db, user['owner'], 'report.retry', report_id)
    return {'ok': True}


@router.get('/api/report-subscriptions')
def subscriptions(request: Request):
    user = account(request)
    with service.get_store().db() as db:
        rows = db.execute('SELECT id,kind,period,send_time,status,include_benchmarks,last_period FROM report_subscriptions WHERE owner=? ORDER BY created_at DESC', (user['owner'],)).fetchall()
    return {'ok': True, 'items': [dict(r) for r in rows]}


@router.post('/api/report-subscriptions')
async def create_subscription(request: Request):
    user = account(request, True)
    sid = reporting.subscribe(service.get_store(), user['owner'], user['user']['id'], await body(request))
    return {'ok': True, 'id': sid}


@router.patch('/api/report-subscriptions/{subscription_id}')
async def update_subscription(subscription_id: str, request: Request):
    user, data = account(request, True), await body(request)
    sid = reporting.subscribe(service.get_store(), user['owner'], user['user']['id'], data, valid_id(subscription_id))
    return {'ok': True, 'id': sid}


@router.delete('/api/report-subscriptions/{subscription_id}')
def delete_subscription(subscription_id: str, request: Request):
    user, store = account(request, True), service.get_store()
    with store.db() as db:
        if not db.execute('DELETE FROM report_subscriptions WHERE id=? AND owner=?', (valid_id(subscription_id), user['owner'])).rowcount:
            raise HTTPException(404, '订阅不存在或无权访问')
        store.log(db, user['owner'], 'report.unsubscribe', subscription_id)
    return {'ok': True}


@router.get('/api/notifications')
def notifications(request: Request):
    user, page = account(request), page_number(request)
    with service.get_store().db() as db:
        rows = db.execute('SELECT id,report_id,title,is_read,created_at FROM notifications WHERE owner=? ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET ?', (user['owner'], (page - 1) * 20)).fetchall()
        total, unread = db.execute('SELECT COUNT(*),COUNT(*) FILTER (WHERE is_read=0) FROM notifications WHERE owner=?', (user['owner'],)).fetchone()
    return {'ok': True, 'items': [dict(r) for r in rows], 'unread': unread, 'total': total, 'page': page}


@router.patch('/api/notifications/{notification_id}')
def read_notification(notification_id: str, request: Request):
    user = account(request, True)
    with service.get_store().db() as db:
        if not db.execute('UPDATE notifications SET is_read=1 WHERE id=? AND owner=?', (valid_id(notification_id), user['owner'])).rowcount:
            raise HTTPException(404, '通知不存在或无权访问')
    return {'ok': True}
