"""User and permission management for PostgreSQL sys_user, sys_role, and sys_data_scope."""
from datetime import datetime, timezone
import re
import bcrypt
from fastapi import HTTPException


def _dt_iso(dt):
    if not dt:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def get_roles(conn):
    cur = conn.cursor()
    try:
        cur.execute('SELECT id, code, name, description FROM sys_role ORDER BY id ASC')
        rows = cur.fetchall()
        return [
            {'id': int(r[0]), 'code': r[1], 'name': r[2], 'description': r[3] or ''}
            for r in rows
        ]
    finally:
        cur.close()


def get_users(conn, search='', role_code='', page=1, page_size=20):
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    cur = conn.cursor()
    try:
        where_clauses = ['1=1']
        params = []
        if search:
            pat = '%' + search.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            where_clauses.append('(u.username ILIKE %s OR u.display_name ILIKE %s)')
            params.extend([pat, pat])
        if role_code:
            where_clauses.append('EXISTS (SELECT 1 FROM sys_user_role ur JOIN sys_role r ON r.id = ur.role_id WHERE ur.user_id = u.id AND r.code = %s)')
            params.append(role_code.strip().upper())

        where_sql = ' WHERE ' + ' AND '.join(where_clauses)
        cur.execute('SELECT COUNT(*) FROM sys_user u' + where_sql, params)
        total = cur.fetchone()[0]

        sql = (
            'SELECT u.id, u.username, u.display_name, u.enabled, u.overview_profile, u.created_at, u.updated_at '
            'FROM sys_user u' + where_sql +
            ' ORDER BY u.id ASC LIMIT %s OFFSET %s'
        )
        cur.execute(sql, [*params, page_size, (page - 1) * page_size])
        user_rows = cur.fetchall()
        if not user_rows:
            return {'ok': True, 'items': [], 'total': total, 'page': page, 'page_size': page_size}

        user_ids = [r[0] for r in user_rows]

        # Fetch roles for all fetched users
        cur.execute(
            'SELECT ur.user_id, r.id, r.code, r.name FROM sys_user_role ur '
            'JOIN sys_role r ON r.id = ur.role_id WHERE ur.user_id = ANY(%s) ORDER BY r.id ASC',
            (user_ids,)
        )
        roles_by_user = {}
        for uid, rid, rcode, rname in cur.fetchall():
            roles_by_user.setdefault(uid, []).append({'id': int(rid), 'code': rcode, 'name': rname})

        # Fetch data scopes for all fetched users
        cur.execute(
            'SELECT id, user_id, scope_type, scope_value FROM sys_data_scope '
            'WHERE user_id = ANY(%s) ORDER BY id ASC',
            (user_ids,)
        )
        scopes_by_user = {}
        for sid, uid, stype, sval in cur.fetchall():
            scopes_by_user.setdefault(uid, []).append({'id': int(sid), 'scope_type': stype, 'scope_value': sval})

        items = []
        for r in user_rows:
            uid = r[0]
            items.append({
                'id': int(uid),
                'username': r[1],
                'display_name': r[2] or r[1],
                'enabled': bool(r[3]),
                'overview_profile': r[4] or 'OPS',
                'created_at': _dt_iso(r[5]),
                'updated_at': _dt_iso(r[6]),
                'roles': roles_by_user.get(uid, []),
                'data_scopes': scopes_by_user.get(uid, []),
            })

        return {'ok': True, 'items': items, 'total': total, 'page': page, 'page_size': page_size}
    finally:
        cur.close()


def get_user_by_id(conn, user_id):
    cur = conn.cursor()
    try:
        cur.execute(
            'SELECT id, username, display_name, enabled, overview_profile, created_at, updated_at '
            'FROM sys_user WHERE id = %s',
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, '用户不存在')

        uid = row[0]
        cur.execute(
            'SELECT r.id, r.code, r.name, r.description FROM sys_user_role ur '
            'JOIN sys_role r ON r.id = ur.role_id WHERE ur.user_id = %s ORDER BY r.id ASC',
            (uid,)
        )
        roles = [{'id': int(r[0]), 'code': r[1], 'name': r[2], 'description': r[3] or ''} for r in cur.fetchall()]

        cur.execute(
            'SELECT id, scope_type, scope_value FROM sys_data_scope WHERE user_id = %s ORDER BY id ASC',
            (uid,)
        )
        scopes = [{'id': int(r[0]), 'scope_type': r[1], 'scope_value': r[2]} for r in cur.fetchall()]

        return {
            'ok': True,
            'user': {
                'id': int(uid),
                'username': row[1],
                'display_name': row[2] or row[1],
                'enabled': bool(row[3]),
                'overview_profile': row[4] or 'OPS',
                'created_at': _dt_iso(row[5]),
                'updated_at': _dt_iso(row[6]),
                'roles': roles,
                'data_scopes': scopes,
            }
        }
    finally:
        cur.close()


def create_user(conn, payload):
    username = str(payload.get('username') or '').strip()
    if not username or len(username) > 64 or not re.match(r'^[a-zA-Z0-9_\-]+$', username):
        raise HTTPException(400, '账号必须是 1–64 位的字母、数字、下划线或连字符')

    password = str(payload.get('password') or '').strip()
    if len(password) < 6 or len(password.encode('utf-8')) > 72:
        raise HTTPException(400, '密码长度需在 6–72 个字符之间')

    display_name = str(payload.get('display_name') or username).strip()[:64]
    overview_profile = str(payload.get('overview_profile') or 'OPS').strip().upper()
    if overview_profile not in ('OPS', 'MARKET', 'LEADERSHIP'):
        overview_profile = 'OPS'
    enabled = bool(payload.get('enabled', True))

    role_ids = payload.get('role_ids', [])
    data_scopes = payload.get('data_scopes', [])

    cur = conn.cursor()
    try:
        cur.execute('SELECT 1 FROM sys_user WHERE username = %s', (username,))
        if cur.fetchone():
            raise HTTPException(400, f'账号「{username}」已存在')

        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(10)).decode('utf-8')
        cur.execute(
            'INSERT INTO sys_user (username, password_hash, display_name, enabled, overview_profile, created_at, updated_at) '
            'VALUES (%s, %s, %s, %s, %s, now(), now()) RETURNING id',
            (username, hashed, display_name, enabled, overview_profile)
        )
        new_uid = cur.fetchone()[0]

        # Insert roles
        for rid in role_ids:
            try:
                cur.execute('INSERT INTO sys_user_role (user_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING', (new_uid, int(rid)))
            except Exception:
                pass

        # Insert data scopes
        for scope in data_scopes:
            stype = str(scope.get('scope_type') or 'ALL').strip().upper()
            sval = str(scope.get('scope_value') or '').strip() or None
            if stype == 'ALL':
                sval = None
            cur.execute(
                'INSERT INTO sys_data_scope (user_id, scope_type, scope_value) VALUES (%s, %s, %s)',
                (new_uid, stype, sval)
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return get_user_by_id(conn, new_uid)


def update_user(conn, user_id, payload):
    cur = conn.cursor()
    try:
        cur.execute('SELECT id, username FROM sys_user WHERE id = %s', (user_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(404, '用户不存在')

        updates = []
        params = []
        if 'display_name' in payload:
            updates.append('display_name = %s')
            params.append(str(payload['display_name'] or '').strip()[:64])
        if 'enabled' in payload:
            updates.append('enabled = %s')
            params.append(bool(payload['enabled']))
        if 'overview_profile' in payload:
            prof = str(payload['overview_profile'] or 'OPS').strip().upper()
            if prof in ('OPS', 'MARKET', 'LEADERSHIP'):
                updates.append('overview_profile = %s')
                params.append(prof)

        updates.append('updated_at = now()')
        if updates:
            sql = f"UPDATE sys_user SET {', '.join(updates)} WHERE id = %s"
            cur.execute(sql, [*params, user_id])

        # Replace roles if provided
        if 'role_ids' in payload and isinstance(payload['role_ids'], list):
            cur.execute('DELETE FROM sys_user_role WHERE user_id = %s', (user_id,))
            for rid in payload['role_ids']:
                try:
                    cur.execute('INSERT INTO sys_user_role (user_id, role_id) VALUES (%s, %s)', (user_id, int(rid)))
                except Exception:
                    pass

        # Replace data scopes if provided
        if 'data_scopes' in payload and isinstance(payload['data_scopes'], list):
            cur.execute('DELETE FROM sys_data_scope WHERE user_id = %s', (user_id,))
            for scope in payload['data_scopes']:
                stype = str(scope.get('scope_type') or 'ALL').strip().upper()
                sval = str(scope.get('scope_value') or '').strip() or None
                if stype == 'ALL':
                    sval = None
                cur.execute(
                    'INSERT INTO sys_data_scope (user_id, scope_type, scope_value) VALUES (%s, %s, %s)',
                    (user_id, stype, sval)
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return get_user_by_id(conn, user_id)


def reset_password(conn, user_id, new_password):
    password = str(new_password or '').strip()
    if len(password) < 6 or len(password.encode('utf-8')) > 72:
        raise HTTPException(400, '密码长度需在 6–72 个字符之间')

    cur = conn.cursor()
    try:
        cur.execute('SELECT 1 FROM sys_user WHERE id = %s', (user_id,))
        if not cur.fetchone():
            raise HTTPException(404, '用户不存在')

        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(10)).decode('utf-8')
        cur.execute('UPDATE sys_user SET password_hash = %s, updated_at = now() WHERE id = %s', (hashed, user_id))
        conn.commit()
        return {'ok': True, 'message': '密码重置成功'}
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def delete_user(conn, user_id, current_user_id=None):
    if current_user_id and str(current_user_id) == str(user_id):
        raise HTTPException(400, '不能删除当前登录的账号')

    cur = conn.cursor()
    try:
        cur.execute('SELECT username FROM sys_user WHERE id = %s', (user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, '用户不存在')

        if row[0] == 'admin':
            raise HTTPException(400, '不能删除系统内置 admin 账号')

        cur.execute('DELETE FROM sys_user_role WHERE user_id = %s', (user_id,))
        cur.execute('DELETE FROM sys_data_scope WHERE user_id = %s', (user_id,))
        cur.execute('DELETE FROM sys_user WHERE id = %s', (user_id,))
        conn.commit()
        return {'ok': True, 'message': f"用户「{row[0]}」已删除"}
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def get_scope_options(conn):
    cur = conn.cursor()
    try:
        # Active airlines in flights first
        cur.execute('''
            SELECT f.airline_code, COALESCE(a.name_zh, f.airline_code), COUNT(*) as cnt
            FROM std_flight_core f
            LEFT JOIN dim_airline a ON a.iata_code = f.airline_code
            WHERE f.airline_code IS NOT NULL AND f.airline_code != ''
            GROUP BY f.airline_code, a.name_zh
            ORDER BY cnt DESC
        ''')
        active_codes = set()
        airlines = []
        for r in cur.fetchall():
            code, name = r[0], r[1]
            active_codes.add(code)
            label = f"{name} ({code})" if name and name != code else code
            airlines.append({'code': code, 'name': label})

        # Add other airlines from dim_airline
        cur.execute('SELECT iata_code, name_zh FROM dim_airline WHERE iata_code IS NOT NULL AND iata_code != \'\' ORDER BY iata_code ASC')
        for r in cur.fetchall():
            code, name = r[0], r[1]
            if code not in active_codes:
                label = f"{name} ({code})" if name else code
                airlines.append({'code': code, 'name': label})

        # Terminals from dim_gate and std_flight_core
        cur.execute('''
            SELECT DISTINCT terminal_code FROM dim_gate WHERE terminal_code IS NOT NULL AND terminal_code != ''
            UNION
            SELECT DISTINCT terminal FROM std_flight_core WHERE terminal IS NOT NULL AND terminal != ''
        ''')
        t_raw = sorted([r[0] for r in cur.fetchall()])
        terminals = []
        for t in t_raw:
            terminals.append({'code': t, 'name': f'{t} 航站楼' if not t.endswith('航站楼') else t})
        for num in ('1', '2', '3'):
            if num not in [item['code'] for item in terminals]:
                terminals.append({'code': num, 'name': f'{num} 号航站楼 (代码 {num})'})

        # Airports from std_flight_core flights
        cur.execute('''
            SELECT ap.iata_code, COALESCE(ap.name_zh, ap.city_name, ap.short_name, ap.iata_code), COUNT(*) as cnt
            FROM (
                SELECT origin_airport as ap_code FROM std_flight_core WHERE origin_airport IS NOT NULL
                UNION ALL
                SELECT dest_airport as ap_code FROM std_flight_core WHERE dest_airport IS NOT NULL
            ) t
            LEFT JOIN dim_airport ap ON ap.iata_code = t.ap_code
            WHERE ap.iata_code IS NOT NULL
            GROUP BY ap.iata_code, ap.name_zh, ap.city_name, ap.short_name
            ORDER BY cnt DESC
            LIMIT 50
        ''')
        airports = [{'code': r[0], 'name': f'{r[1]} ({r[0]})'} for r in cur.fetchall()]

        directions = [
            {'code': 'ARR', 'name': 'ARR · 进港 (到达)'},
            {'code': 'DEP', 'name': 'DEP · 出港 (起飞)'},
        ]

        return {
            'ok': True,
            'airlines': airlines,
            'terminals': terminals,
            'airports': airports,
            'directions': directions,
        }
    finally:
        cur.close()


def get_profiles(conn):
    cur = conn.cursor()
    try:
        cur.execute('''
            SELECT code, name, description, target_audience, focus_metrics, default_scenarios, 
                   system_prompt_hint, is_builtin, enabled, updated_at
            FROM sys_profile ORDER BY is_builtin DESC, code ASC
        ''')
        rows = cur.fetchall()
        items = []
        for r in rows:
            items.append({
                'code': r[0],
                'name': r[1],
                'description': r[2] or '',
                'target_audience': r[3] or '',
                'focus_metrics': r[4] or '',
                'default_scenarios': r[5] or '',
                'system_prompt_hint': r[6] or '',
                'is_builtin': bool(r[7]),
                'enabled': bool(r[8]),
                'updated_at': _dt_iso(r[9]),
            })
        return {'ok': True, 'items': items}
    finally:
        cur.close()


def create_profile(conn, payload):
    code = str(payload.get('code') or '').strip().upper()
    if not code or len(code) > 32 or not re.match(r'^[A-Z0-9_\-]+$', code):
        raise HTTPException(400, '画像编码必须是 1–32 位的大写字母、数字或下划线')
    name = str(payload.get('name') or '').strip()
    if not name or len(name) > 64:
        raise HTTPException(400, '画像名称必填且长度在 64 字符以内')
    description = str(payload.get('description') or '').strip()
    target_audience = str(payload.get('target_audience') or '').strip()
    focus_metrics = str(payload.get('focus_metrics') or '').strip()
    default_scenarios = str(payload.get('default_scenarios') or '').strip()
    system_prompt_hint = str(payload.get('system_prompt_hint') or '').strip()
    enabled = bool(payload.get('enabled', True))

    cur = conn.cursor()
    try:
        cur.execute('SELECT 1 FROM sys_profile WHERE code = %s', (code,))
        if cur.fetchone():
            raise HTTPException(400, f'画像编码「{code}」已存在')

        cur.execute('''
            INSERT INTO sys_profile (code, name, description, target_audience, focus_metrics, default_scenarios, system_prompt_hint, is_builtin, enabled, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, false, %s, now(), now())
        ''', (code, name, description, target_audience, focus_metrics, default_scenarios, system_prompt_hint, enabled))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return {'ok': True, 'message': f'画像「{name}」已创建', 'code': code}


def update_profile(conn, code, payload):
    cur = conn.cursor()
    try:
        cur.execute('SELECT code, is_builtin FROM sys_profile WHERE code = %s', (code,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, '画像不存在')

        name = str(payload.get('name') or '').strip()
        if not name:
            raise HTTPException(400, '画像名称必填')
        description = str(payload.get('description') or '').strip()
        target_audience = str(payload.get('target_audience') or '').strip()
        focus_metrics = str(payload.get('focus_metrics') or '').strip()
        default_scenarios = str(payload.get('default_scenarios') or '').strip()
        system_prompt_hint = str(payload.get('system_prompt_hint') or '').strip()
        enabled = bool(payload.get('enabled', True))

        cur.execute('''
            UPDATE sys_profile SET
                name = %s,
                description = %s,
                target_audience = %s,
                focus_metrics = %s,
                default_scenarios = %s,
                system_prompt_hint = %s,
                enabled = %s,
                updated_at = now()
            WHERE code = %s
        ''', (name, description, target_audience, focus_metrics, default_scenarios, system_prompt_hint, enabled, code))
        conn.commit()
        return {'ok': True, 'message': f'画像「{name}」已更新', 'code': code}
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def delete_profile(conn, code):
    cur = conn.cursor()
    try:
        cur.execute('SELECT is_builtin, name FROM sys_profile WHERE code = %s', (code,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, '画像不存在')
        if row[0]:
            raise HTTPException(400, f'系统内置画像「{row[1]}」不可删除')

        cur.execute('SELECT COUNT(*) FROM sys_user WHERE overview_profile = %s', (code,))
        used_count = cur.fetchone()[0]
        if used_count > 0:
            raise HTTPException(400, f'该画像已被 {used_count} 个用户使用，请先将用户改为其他画像后再删除')

        cur.execute('DELETE FROM sys_profile WHERE code = %s', (code,))
        conn.commit()
        return {'ok': True, 'message': f'画像「{row[1]}」已删除'}
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
