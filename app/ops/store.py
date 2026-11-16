"""Separate, transactional operational ledger; no question/session tables."""
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
import json
import sqlite3
import uuid

from app.admin.store import text
from .domain import Conflict, KINDS, dt, fingerprint, interval, iso, now

TABLES = {'resources', 'rosters', 'tasks', 'transfers', 'policies', 'grants', 'alerts', 'proposals', 'work_orders'}


def uid():
    return uuid.uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def record(row):
    if row is None:
        return None
    row = dict(row)
    payload = json.loads(row.pop('payload', '{}'))
    return {**payload, **row}


def items(db, table, terminal=None):
    assert table in TABLES
    return [record(r) for r in db.execute('SELECT * FROM ' + table + (' WHERE terminal=?' if terminal else ''), (terminal,) if terminal else ())]


def event(db, actor, action, target, detail=None):
    db.execute('INSERT INTO events(actor,action,target,detail,at) VALUES (?,?,?,?,?)',
               (str(actor), action, str(target), encode(detail or {}), iso(now())))


class OpsStore:
    def __init__(self, path, clock=None, simulation=False):
        self.path = Path(path)
        self.clock = clock or now
        self.simulation = simulation
        self.path.parent.mkdir(parents=True, exist_ok=True)
        init_conn = sqlite3.connect(self.path, timeout=15)
        try:
            init_conn.execute('PRAGMA journal_mode=WAL')
            init_conn.execute('PRAGMA busy_timeout=15000')
            init_conn.executescript('''
                CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY,terminal TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS rosters(id TEXT PRIMARY KEY,terminal TEXT NOT NULL,resource_id TEXT NOT NULL REFERENCES resources(id),start_at TEXT NOT NULL,end_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,terminal TEXT NOT NULL,resource_id TEXT NOT NULL REFERENCES resources(id),flight_id TEXT NOT NULL,start_at TEXT NOT NULL,end_at TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
                CREATE INDEX IF NOT EXISTS ops_tasks_resource ON tasks(resource_id,start_at,end_at);
                CREATE TABLE IF NOT EXISTS transfers(terminal TEXT NOT NULL,from_stand TEXT NOT NULL,to_stand TEXT NOT NULL,minutes INTEGER NOT NULL,PRIMARY KEY(terminal,from_stand,to_stand));
                CREATE TABLE IF NOT EXISTS policies(terminal TEXT NOT NULL,kind TEXT NOT NULL,duration_minutes INTEGER,grace_minutes INTEGER NOT NULL,confirmed INTEGER NOT NULL,PRIMARY KEY(terminal,kind));
                CREATE TABLE IF NOT EXISTS grants(user_id TEXT NOT NULL,role TEXT NOT NULL,terminal TEXT NOT NULL,resource_id TEXT NOT NULL DEFAULT '',UNIQUE(user_id,role,terminal,resource_id));
                CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,terminal TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,resolved_at TEXT,ack_actor TEXT);
                CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,terminal TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,version TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS work_orders(id TEXT PRIMARY KEY,terminal TEXT NOT NULL,resource_id TEXT NOT NULL,flight_id TEXT NOT NULL,proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),state TEXT NOT NULL,payload TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,detail TEXT NOT NULL,at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ops_events_target ON events(target,id);
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
        finally:
            init_conn.close()

    @contextmanager
    def db(self, write=True):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=15000')
        try:
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def view(self, terminal, db=None):
        if db is None:
            with self.db(False) as conn:
                return self.view(terminal, conn)
        # shortcut: 单航站楼试点读取全台账，扩展多站或万级任务前改为活跃任务查询与分页历史。
        value = {t: items(db, t, terminal) for t in ('resources', 'rosters', 'tasks', 'transfers', 'policies', 'proposals', 'work_orders')}
        value['policies'] = {p['kind']: p for p in value['policies']}
        for kind in KINDS:
            value['policies'].setdefault(kind, {'terminal': terminal, 'kind': kind, 'duration_minutes': None, 'grace_minutes': 5, 'confirmed': False})
        value['settings'] = self.state('settings:' + terminal, {'shadow_mode': True, 'dispatch_enabled': self.simulation}, db)
        return value

    def state(self, key, default=None, db=None):
        if db is None:
            with self.db(False) as conn:
                return self.state(key, default, conn)
        row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set_state(self, key, value, db=None):
        if db is None:
            with self.db() as conn:
                return self.set_state(key, value, conn)
        db.execute('INSERT INTO state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, encode(value)))

    def grants(self, user_id=None):
        with self.db(False) as db:
            return [dict(r) for r in db.execute('SELECT * FROM grants' + (' WHERE user_id=?' if user_id else ''), (str(user_id),) if user_id else ())]

    def resource(self, data, terminal, actor, resource_id=None):
        with self.db() as db:
            old = record(db.execute('SELECT * FROM resources WHERE id=? AND terminal=?', (resource_id, terminal)).fetchone()) if resource_id else None
            if resource_id and not old:
                raise ValueError('班组不存在')
            if old and data.get('revision') != old['revision']:
                raise Conflict('班组状态已更新，请刷新')
            value = {**(old or {}), **data}
            kind = value.get('kind')
            if kind not in KINDS:
                raise ValueError('请选择有效的保障类别')
            if old and kind != old['kind']:
                raise ValueError('班组保障类别不能直接改变；请停用原班组并登记新班组')
            value['name'] = text(value.get('name'), '班组名称', 80)
            value['position'] = text(value.get('position', ''), '所在机位', 24, False)
            value['availability'] = value.get('availability', 'unknown')
            if value['availability'] not in ('idle', 'busy', 'unknown') or ('confirm_position' in data and type(data['confirm_position']) is not bool):
                raise ValueError('请填写有效的班组忙闲状态及确认标记')
            value['qualified_until'] = iso(value.get('qualified_until'))
            if data.get('qualified_until') and value['qualified_until'] is None:
                raise ValueError('资质有效时间无效')
            for key in ('enabled', 'qualifications_confirmed', 'equipment_confirmed'):
                if type(value.get(key, False)) is not bool:
                    raise ValueError('班组确认状态格式无效')
                value[key] = value.get(key, False)
            if data.get('confirm_position'):
                value['confirmed_at'] = iso(self.clock())
            elif not old:
                value['confirmed_at'] = None
            else:
                value['confirmed_at'] = old.get('confirmed_at')
                if value['position'] != old.get('position') or value['availability'] != old.get('availability'):
                    value['confirmed_at'] = None
            ident = resource_id or uid()
            revision = old['revision'] + 1 if old else 1
            payload = {k: value[k] for k in ('name', 'position', 'availability', 'qualified_until', 'enabled', 'qualifications_confirmed', 'equipment_confirmed', 'confirmed_at')}
            db.execute('INSERT INTO resources VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision', (ident, terminal, kind, encode(payload), revision))
            event(db, actor, 'resource.save', ident, payload)
            return {**payload, 'id': ident, 'terminal': terminal, 'kind': kind, 'revision': revision}

    def roster(self, data, terminal, actor):
        start, end = interval(data.get('start_at'), data.get('end_at'))
        rid = text(data.get('resource_id'), '班组', 64)
        with self.db() as db:
            if not db.execute('SELECT id FROM resources WHERE id=? AND terminal=?', (rid, terminal)).fetchone():
                raise ValueError('班组不存在')
            if db.execute('SELECT id FROM rosters WHERE resource_id=? AND start_at<? AND end_at>?', (rid, iso(end), iso(start))).fetchone():
                raise Conflict('排班窗口已存在交叠')
            ident = uid()
            db.execute('INSERT INTO rosters VALUES (?,?,?,?,?)', (ident, terminal, rid, iso(start), iso(end)))
            event(db, actor, 'roster.add', ident)
            return ident

    def task(self, data, flight, terminal, actor):
        start, end = interval(data.get('start_at'), data.get('end_at'))
        deadline = dt(data.get('latest_end_at'))
        if data.get('latest_end_at') and (not deadline or deadline < end or deadline - start > timedelta(days=2)):
            raise ValueError('最晚保障结束时间应不早于原计划结束，窗口不得超过48小时')
        with self.db() as db:
            resource = record(db.execute('SELECT * FROM resources WHERE id=? AND terminal=?', (data.get('resource_id'), terminal)).fetchone())
            if not resource:
                raise ValueError('班组不存在')
            if db.execute("SELECT id FROM tasks WHERE flight_id=? AND status!='cancelled' AND json_extract(payload,'$.kind')=?", (flight['id'], resource['kind'])).fetchone():
                raise Conflict('该航班已有此类保障任务，请先取消或处理原任务')
            ident = uid()
            existing = db.execute("SELECT payload FROM tasks WHERE flight_id=? AND terminal=? AND status!='cancelled'", (flight['id'], terminal)).fetchall()
            if any(json.loads(x['payload']).get('stand') != flight['stand'] for x in existing):
                raise Conflict('同一航班的机位资料不一致，请先核实现有任务')
            payload = {'kind': resource['kind'], 'flight_no': flight['flight_no'], 'stand': flight['stand'], 'latest_end_at': iso(deadline), 'stand_source': data.get('stand_source', '源系统'), 'source': '人工台账', 'actual_start': None, 'actual_end': None}
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,1)', (ident, terminal, resource['id'], flight['id'], iso(start), iso(end), 'planned', encode(payload)))
            event(db, actor, 'task.add', ident, payload)
            return ident

    def transfer(self, data, terminal, actor):
        origin, destination = text(data.get('from_stand'), '起点机位', 24), text(data.get('to_stand'), '终点机位', 24)
        minutes = data.get('minutes')
        if type(minutes) is not int or not 0 <= minutes <= 120 or (origin != destination and minutes == 0):
            raise ValueError('请输入1至120分钟的实测转场时间')
        with self.db() as db:
            db.execute('INSERT INTO transfers VALUES (?,?,?,?) ON CONFLICT(terminal,from_stand,to_stand) DO UPDATE SET minutes=excluded.minutes', (terminal, origin, destination, minutes))
            event(db, actor, 'transfer.save', origin + '→' + destination, {'minutes': minutes})

    def policy(self, data, terminal, actor):
        kind, duration, grace = data.get('kind'), data.get('duration_minutes'), data.get('grace_minutes', 5)
        if kind not in KINDS or type(duration) is not int or not 1 <= duration <= 240 or type(grace) is not int or not 0 <= grace <= 60 or type(data.get('confirmed')) is not bool:
            raise ValueError('请填写有效的作业类别、标准耗时、宽限时间及业务确认状态')
        with self.db() as db:
            db.execute('INSERT INTO policies VALUES (?,?,?,?,?) ON CONFLICT(terminal,kind) DO UPDATE SET duration_minutes=excluded.duration_minutes,grace_minutes=excluded.grace_minutes,confirmed=excluded.confirmed', (terminal, kind, duration, grace, int(data['confirmed'])))
            event(db, actor, 'policy.save', kind, data)

    def grant(self, data, actor):
        user = text(data.get('user_id'), '原系统用户ID', 100)
        terminal = text(data.get('terminal'), '航站楼', 16)
        role = data.get('role')
        rid = str(data.get('resource_id') or '')
        if role not in ('viewer', 'dispatcher', 'crew'):
            raise ValueError('运行岗位无效')
        with self.db() as db:
            if role == 'crew' and not db.execute('SELECT id FROM resources WHERE id=? AND terminal=?', (rid, terminal)).fetchone():
                raise ValueError('班组报工岗位必须绑定本航站楼班组')
            if data.get('revoke'):
                db.execute('DELETE FROM grants WHERE user_id=? AND role=? AND terminal=? AND resource_id=?', (user, role, terminal, rid))
            else:
                db.execute('INSERT OR IGNORE INTO grants VALUES (?,?,?,?)', (user, role, terminal, rid))
            event(db, actor, 'grant.revoke' if data.get('revoke') else 'grant.add', user, {'role': role, 'terminal': terminal, 'resource_id': rid})

    def persist_alerts(self, terminal, alerts, observed=None):
        at = iso(observed or now())
        current = {a['id'] for a in alerts}
        with self.db() as db:
            for alert in alerts:
                old = db.execute('SELECT status FROM alerts WHERE id=?', (alert['id'],)).fetchone()
                state = old['status'] if old and old['status'] != 'resolved' else 'open'
                db.execute('INSERT INTO alerts VALUES (?,?,?,?,?,?,NULL,NULL) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,status=excluded.status,last_seen=excluded.last_seen,resolved_at=NULL', (alert['id'], terminal, encode(alert), state, at, at))
                if not old or old['status'] == 'resolved':
                    event(db, 'watcher', 'alert.open', alert['id'])
            for old in db.execute("SELECT id FROM alerts WHERE terminal=? AND status!='resolved'", (terminal,)).fetchall():
                if old['id'] not in current:
                    db.execute("UPDATE alerts SET status='resolved',resolved_at=? WHERE id=?", (at, old['id']))
                    event(db, 'watcher', 'alert.resolve', old['id'])

    def alerts(self, terminal):
        with self.db(False) as db:
            return items(db, 'alerts', terminal)

    def acknowledge(self, ident, terminal, actor):
        with self.db() as db:
            row = db.execute('SELECT status FROM alerts WHERE id=? AND terminal=?', (ident, terminal)).fetchone()
            if not row or row['status'] == 'resolved':
                raise Conflict('告警已解除或不存在，请刷新')
            db.execute("UPDATE alerts SET status='acknowledged',ack_actor=? WHERE id=?", (str(actor), ident))
            event(db, actor, 'alert.acknowledge', ident)

    def events(self, terminal):
        with self.db(False) as db:
            return [dict(r) for r in db.execute('SELECT e.* FROM events e JOIN work_orders w ON w.id=e.target WHERE w.terminal=? ORDER BY e.id DESC', (terminal,))]


def dispatch_version(view):
    return fingerprint({k: view[k] for k in ('resources', 'rosters', 'tasks', 'transfers', 'policies', 'settings')})
