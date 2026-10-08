"""hbairport01 identity and enforced SQL sources, ported from gzwtb security.

No LLM output or client-supplied identifier is an authorization decision.
"""
from copy import copy, deepcopy
from dataclasses import dataclass
import hashlib
import json
import secrets
from time import time

from fastapi import HTTPException
from app.config import ROOT, settings
from app.query import Statement, execute, identifier
from app.admin.security import digest

COOKIE = 'hbask_business_session'
POLICY = json.loads((ROOT / 'data/business_permissions.json').read_text(encoding='utf-8'))


def norm(value):
    return str(value or '').strip().lower()


def merged_indicators(roles, menus):
    # IndicatorPolicy: empty union is legacy unrestricted, including no roles.
    if any(r.get('indicator_visibility') in (None, 'ALL') for r in roles):
        return None
    result = {norm(c) for r in roles for c in (r.get('indicator_codes_csv') or '').split(',') if norm(c)}
    if not result:
        return None
    for menu in menus:
        if menu.startswith('filling.'):
            grant = menu[len('filling.'):]
            result.add('contract-performance' if grant.startswith('contract-performance.segment.') else grant)
    return frozenset(result)


def subtree(anchor, all_sccs, parents=None):
    anchor = norm(anchor)
    if not anchor:
        return frozenset()
    group = POLICY['group_scc']
    parents = POLICY['enterprise_parents'] if parents is None else parents
    all_sccs = {norm(s) for s in all_sccs if norm(s)}
    children = {}
    placed = set()
    for child, parent in parents.items():
        if child not in all_sccs or child == group:
            continue
        parent = parent if parent in all_sccs else group
        if parent in all_sccs and parent != child:
            children.setdefault(parent, set()).add(child)
            placed.add(child)
    if group in all_sccs:
        children.setdefault(group, set()).update(all_sccs - placed - {group})
    result, pending = {anchor}, [anchor]
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in result:
                result.add(child)
                pending.append(child)
    return frozenset(result)


@dataclass(frozen=True)
class Access:
    user_id: str
    username: str
    display_name: str
    scope: str
    roles: tuple
    indicators: frozenset | None
    permissions: frozenset | None
    companies: frozenset
    revision: str
    department_id: str | None = None
    department_ids: frozenset = frozenset()
    role_names: tuple = ()

    @property
    def owner(self):
        return 'business:' + self.user_id

    @property
    def unrestricted(self):
        return 'admin' in self.roles or self.scope == 'ALL'

    @property
    def effective_scope(self):
        return 'ALL' if self.unrestricted else self.scope

    def profile(self):
        return {'id': self.user_id, 'username': self.username, 'display_name': self.display_name,
                'enabled': True, 'source': 'hbairport01', 'can_manage': 'admin' in self.roles,
                'data_scope': self.effective_scope, 'configured_data_scope': self.scope,
                'roles': list(self.roles), 'role_names': list(self.role_names)}

    def allowed(self, table):
        spec = POLICY['tables'].get(table)
        if not spec:
            return False
        if 'admin' in self.roles:
            return True
        grant = spec['indicator']
        base = grant.split('.segment.')[0]
        indicator_ok = self.indicators is None or grant in self.indicators or base in self.indicators
        # Segment permissions stay segment-specific, matching PermissionCatalogService.
        perm = grant + ':view'
        return indicator_ok and (self.permissions is None or perm in self.permissions)

    def source(self, table, catalog, seen=(), authorize=True):
        """Derived sources enforce scope even on intermediate joins and EXISTS."""
        if table in seen or table not in POLICY['tables'] or (authorize and not self.allowed(table)):
            raise HTTPException(403, '当前账号无权查询该业务数据')
        spec = POLICY['tables'][table]
        meta = catalog.tables[table]
        raw = identifier(meta.get('schema', 'public')) + '.' + identifier(meta.get('name', table))
        cols = {c['name'] for c in meta['columns']}
        # This table combines monthly pay and three separately authorized segments.
        hidden = payroll_hidden(self, table, cols)
        select = ','.join('a.' + identifier(c['name']) for c in meta['columns'] if c['name'] not in hidden)
        conditions, params = [], []
        if spec.get('parent') and (not self.unrestricted or spec.get('require_parent')):
            parent = spec['parent']
            if parent not in catalog.tables or spec['foreign_key'] not in cols:
                raise HTTPException(403, '此业务尚未配置可验证的数据归属')
            # Parent ownership is needed even when its screen is not granted.
            source, values = self.source(parent, catalog, seen + (table,), authorize=False)
            conditions.append('EXISTS (SELECT 1 FROM ' + source + ' p WHERE p.' +
                              identifier(spec['parent_key']) + ' = a.' + identifier(spec['foreign_key']) + ')')
            params.extend(values)
        elif not self.unrestricted:
            scc = [c for c in spec.get('scc', []) if c in cols]
            refs = [c for c in spec.get('contract_refs', []) if c in cols]
            if not (scc or refs) or not self.companies:
                conditions.append('FALSE')
            else:
                ors = []
                companies = sorted(self.companies)
                for col in scc:
                    ors.append('lower(trim(a.' + identifier(col) + ')) IN (' + ','.join('%s' for _ in companies) + ')')
                    params.extend(companies)
                for col in refs:
                    # ContractPerformanceService resolves national business IDs,
                    # not internal UUIDs, and checks both candidate enterprises.
                    cm = catalog.tables['contract_main']
                    contract = identifier(cm.get('schema', 'public')) + '.' + identifier(cm.get('name', 'contract_main'))
                    ors.append('(SELECT lower(trim(cp.enterprise_scc)) FROM ' + contract +
                               " cp WHERE cp.indicator_code='basic-contract' AND cp.record_type='MAIN' "
                               'AND cp.gzw_biz_id=trim(a.' + identifier(col) + ') LIMIT 1) IN (' +
                               ','.join('%s' for _ in companies) + ')')
                    params.extend(companies)
                conditions.append('(' + ' OR '.join(ors) + ')')
            if spec.get('empty_company_submitter_fallback'):
                # PartnerService preserves department/self ownership for legacy
                # records whose owner enterprise SCC has not been populated.
                col = identifier(spec['scc'][0])
                fallback = 'FALSE'
                if self.scope == 'SELF_ONLY':
                    fallback = 'a."submitter_user_id" = %s'
                    params.append(self.user_id)
                elif self.department_ids:
                    fallback = 'a."submitter_department_id" IN (' + ','.join('%s' for _ in self.department_ids) + ')'
                    params.extend(sorted(self.department_ids))
                conditions[0] = '(' + conditions[0] + ' OR ((a.' + col + ' IS NULL OR trim(a.' + col + ")='') AND " + fallback + '))'
            if self.scope == 'SELF_ONLY':
                if 'submitter_user_id' in cols:
                    conditions.append('a."submitter_user_id" = %s')
                    params.append(self.user_id)
                else:
                    conditions.append('FALSE')
        return '(SELECT ' + select + ' FROM ' + raw + ' a' + (' WHERE ' + ' AND '.join(conditions) if conditions else '') + ')', params

    def catalog(self, catalog):
        result = copy(catalog)
        allowed = {t for t in catalog.entities if self.allowed(t)}
        result.entities = {t: deepcopy(catalog.entities[t]) for t in allowed}
        result.overrides = deepcopy(catalog.overrides)
        result.overrides['relations'] = {t: {k: v for k, v in rel.items() if k in allowed}
                                        for t, rel in catalog.overrides.get('relations', {}).items() if t in allowed}
        result.adjacency = {t: [(k, e) for k, e in catalog.adjacency.get(t, []) if k in allowed] for t in allowed}
        for table, spec in result.entities.items():
            hidden = payroll_hidden(self, table, spec['attributes'])
            hidden.update(k for k, v in spec['attributes'].items() if v.get('column') in hidden)
            for col in hidden:
                spec['attributes'].pop(col, None)
            spec['metrics'] = {k: v for k, v in spec['metrics'].items() if v['field'] in spec['attributes']}
        result.version = hashlib.sha256((catalog.version + self.revision).encode()).hexdigest()
        return result


def payroll_hidden(access, table, columns):
    if table != 'comp_payroll_monthly' or 'admin' in access.roles:
        return set()
    hidden = set()
    for prefix, segment in [('cash_incentive_', 'cash-incentive'), ('equity_incentive_', 'equity-incentive'),
                            ('sell_equity_incentive_', 'sell-equity-incentive')]:
        grant = 'compensation-offboarding.segment.' + segment
        indicator_ok = access.indicators is None or grant in access.indicators or 'compensation-offboarding' in access.indicators
        perm_ok = access.permissions is None or grant + ':view' in access.permissions
        if not (indicator_ok and perm_ok):
            hidden.update(c for c in columns if c.startswith(prefix))
    return hidden


def load_access(conn, user_id):
    users = execute(conn, Statement('SELECT u.id,u.username,u.display_name,u.enabled,u.data_scope,u.department_id,d.tree_path,d.owner_org_scc '
                                  'FROM app_user u LEFT JOIN department d ON d.id=u.department_id WHERE u.id=%s', (user_id,)))
    if not users or not users[0]['enabled']:
        raise HTTPException(401, '业务账号已停用或不存在，请重新登录')
    u = users[0]
    scope = u['data_scope']
    if scope not in ('ALL', 'DEPT_ONLY', 'DEPT_TREE', 'SELF_ONLY'):
        raise HTTPException(403, '业务账号数据范围无效')
    roles = execute(conn, Statement('SELECT r.* FROM app_role r JOIN app_user_role ur ON ur.role_id=r.id '
                                   'WHERE ur.user_id=%s AND r.enabled', (user_id,)))
    menus = execute(conn, Statement('SELECT rm.role_id,m.id,m.code,m.enabled,m.menu_kind FROM app_role_menu rm '
                                   'JOIN app_sys_menu m ON m.id=rm.menu_id JOIN app_user_role ur ON ur.role_id=rm.role_id '
                                   'WHERE ur.user_id=%s', (user_id,)))
    permissions = execute(conn, Statement('SELECT rp.role_id,p.code,p.enabled FROM app_role_permission rp '
                                         'JOIN app_sys_permission p ON p.id=rp.permission_id JOIN app_user_role ur ON ur.role_id=rp.role_id '
                                         'WHERE ur.user_id=%s', (user_id,)))
    all_permissions = None
    union, menu_codes = set(), set()
    for role in roles:
        rid = role['id']
        explicit = [p for p in permissions if p['role_id'] == rid]
        leaf = [m for m in menus if m['role_id'] == rid and m['enabled'] and m['menu_kind'] == 'LEAF']
        menu_codes.update(norm(m['code']) for m in leaf)
        if explicit:
            union.update(norm(p['code']) for p in explicit if p['enabled'] and norm(p['code']))
        else:
            if all_permissions is None:
                all_permissions = execute(conn, Statement('SELECT code,parent_menu_id FROM app_sys_permission WHERE enabled'))
            for m in leaf:
                code = norm(m['code'])
                segment = code.startswith(('filling.compensation-offboarding.segment.', 'filling.process-mgmt.segment.'))
                prefix = code.removeprefix('filling.') + ':'
                union.update(norm(p['code']) for p in all_permissions if
                             (segment and norm(p['code']).startswith(prefix)) or (not segment and p['parent_menu_id'] == m['id']))
    codes = tuple(sorted({norm(r['code']) for r in roles}))
    anchor = norm(u['owner_org_scc'])
    if scope in ('DEPT_TREE', 'SELF_ONLY') and 'admin' not in codes:
        rows = execute(conn, Statement('SELECT scc FROM org_structure_record'))
        companies = subtree(anchor, [r['scc'] for r in rows])
    else:
        companies = frozenset({anchor}) if anchor else frozenset()
    indicators = merged_indicators(roles, menu_codes)
    perms = frozenset(union) if union else None
    dept_id = str(u['department_id']) if u['department_id'] else None
    dept_ids = frozenset({dept_id}) if dept_id else frozenset()
    if scope == 'DEPT_TREE' and dept_id and str(u['tree_path'] or '').strip():
        prefix = str(u['tree_path']).strip()
        rows = execute(conn, Statement('SELECT id,tree_path FROM department'))
        dept_ids = frozenset(str(r['id']) for r in rows if str(r['tree_path'] or '').strip().startswith(prefix))
    payload = {'user': str(u['id']), 'scope': scope, 'roles': codes,
               'indicators': sorted(indicators) if indicators is not None else None,
               'permissions': sorted(perms) if perms is not None else None, 'companies': sorted(companies),
               'policy': POLICY, 'department': dept_id, 'departments': sorted(dept_ids)}
    revision = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return Access(str(u['id']), u['username'], u['display_name'] or u['username'], scope, codes,
                  indicators, perms, companies, revision, dept_id, dept_ids,
                  tuple(r.get('name') or r['code'] for r in sorted(roles, key=lambda r: norm(r['code']))))


def identity(request):
    from app.admin import service
    from app import core
    store = service.get_store()
    with store.db() as db:
        row = db.execute('SELECT user_id FROM business_credentials WHERE token_hash=? AND expires>?',
                         (digest(request.cookies.get(COOKIE, '')), time())).fetchone()
    if not row:
        raise HTTPException(401, '请使用 hbairport01 业务账号登录')
    conn = core.connect_db()
    try:
        access = load_access(conn, row['user_id'])
    finally:
        conn.close()
    return {'owner': access.owner, 'user': access.profile(), 'access': access}


def login(username, password, client):
    import bcrypt
    from app.admin import service
    from app import core
    if not isinstance(username, str) or not isinstance(password, str) or not 1 <= len(username) <= 64 or not 1 <= len(password.encode()) <= 72:
        raise HTTPException(401, '账号或密码错误')
    store = service.get_store()
    scopes = ('business:ip:' + client, 'business:user:' + norm(username))
    with store.db() as db:
        for scope in scopes:
            row = db.execute('SELECT * FROM login_attempts WHERE scope=?', (scope,)).fetchone()
            if row and time() - row['window'] < 900 and row['failures'] >= 10:
                raise HTTPException(429, '登录尝试过多，请稍后重试')
    conn = core.connect_db()
    try:
        users = execute(conn, Statement('SELECT id,password_hash,enabled FROM app_user WHERE username=%s', (username,)))
        row = users[0] if users else None
        hashed = row['password_hash'] if row else None
        try:
            valid = bcrypt.checkpw(password.encode(), (hashed or DUMMY_HASH).encode())
        except (ValueError, TypeError):
            valid = False
        if not valid or not row or not row['enabled']:
            with store.db() as db:
                for scope in scopes:
                    db.execute('INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(scope) DO UPDATE SET '
                               'failures=CASE WHEN ?-window>=900 THEN 1 ELSE failures+1 END,'
                               'window=CASE WHEN ?-window>=900 THEN ? ELSE window END', (scope, time(), time(), time(), time()))
            raise HTTPException(401, '账号或密码错误，或账号已停用')
        access = load_access(conn, row['id'])
    finally:
        conn.close()
    token = secrets.token_urlsafe(32)
    with store.db() as db:
        db.execute('DELETE FROM business_credentials WHERE expires<?', (time(),))
        db.execute('INSERT INTO business_credentials VALUES (?,?,?)', (digest(token), access.user_id, time() + settings.admin_session_hours * 3600))
        db.execute('DELETE FROM login_attempts WHERE scope=?', (scopes[1],))
        store.log(db, access.owner, 'business.auth.login')
    return token, access.profile()


# Constant bcrypt dummy preserves password-work cost for unknown usernames.
DUMMY_HASH = '$2b$12$C6UzMDM.H6dfI/f/IKcEe.8luFyUWjdpOWMBNZMauTAqlAUbUeAKS'


def logout(token):
    from app.admin import service
    with service.get_store().db() as db:
        db.execute('DELETE FROM business_credentials WHERE token_hash=?', (digest(token or ''),))


def query_identity(request):
    return identity(request)
