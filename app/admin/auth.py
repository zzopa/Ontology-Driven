"""Same-origin writes and server-side, revocable cookie authentication."""
from fastapi import HTTPException, Request
from app import business_access
from app.config import settings

COOKIE = business_access.COOKIE


def same_origin(request: Request):
    origin = request.headers.get('origin')
    if origin:
        if origin != str(request.base_url).rstrip('/'):
            raise HTTPException(403, '不接受跨站写操作')
    elif request.headers.get('x-hbask-request') != '1':
        raise HTTPException(403, '写操作缺少同源请求标识')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, '不接受跨站写操作')


def identity(request):
    return business_access.identity(request)


def require_role(request, *roles):
    value = identity(request)
    if not set(value['access'].roles).intersection(roles):
        raise HTTPException(403, '当前账号没有此管理权限')
    return value


def set_cookie(response, token, request, hours=None):
    response.set_cookie(COOKIE, token, max_age=int((hours or settings.admin_session_hours) * 3600),
                        httponly=True, secure=request.url.scheme == 'https', samesite='lax', path='/')
