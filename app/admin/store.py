"""SQLite management data, transactional access control and audit trail."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
from threading import RLock
from time import time
from urllib.parse import urlsplit

from app.admin.security import Vault, digest

SHANGHAI_TZ = timezone(timedelta(hours=8))


def now():
    return datetime.now(timezone.utc).isoformat()


def utc_iso(value):
    """Normalize an ISO timestamp or datetime object to canonical UTC ISO string."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    text_value = str(value).strip()
    if not text_value:
        return None
    dt = datetime.fromisoformat(text_value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def text(value, label, maximum=200, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError(label + '格式无效')
    return value.strip()


def model_url(value):
    value = text(value, '模型地址', 2048)
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError('模型地址必须是完整 HTTP(S) 接口，不接受凭据、查询参数或片段')
    return value


class AdminStore:
    def __init__(self, directory, initial=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'management.sqlite3'
        self.lock = RLock()
        if self.path.exists() and not (self.directory / 'secret.key').exists() and not os.environ.get('HBASK_ADMIN_SECRET_KEY'):
            raise ValueError('管理库已存在但加密密钥缺失，请恢复原 secret.key，不能重新生成密钥')
        self.vault = Vault(self.directory)
        self.backup_legacy_accounts()
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS models (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, model TEXT NOT NULL,
                  secret TEXT NOT NULL, enabled INTEGER NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversations (
                  id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                  archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                  question TEXT NOT NULL, result TEXT, status TEXT NOT NULL, error TEXT,
                  model TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT);
                CREATE TABLE IF NOT EXISTS token_grants (
                  token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
                  target TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (
                  scope TEXT PRIMARY KEY, failures INTEGER NOT NULL, window REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS business_credentials (
                  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sync_runs (
                  id TEXT PRIMARY KEY,
                  job_key TEXT NOT NULL,
                  title TEXT NOT NULL,
                  source_origin TEXT NOT NULL,
                  mode TEXT NOT NULL,
                  status TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  elapsed_ms INTEGER NOT NULL DEFAULT 0,
                  fetched INTEGER NOT NULL DEFAULT 0,
                  inserted INTEGER NOT NULL DEFAULT 0,
                  updated INTEGER NOT NULL DEFAULT 0,
                  skipped INTEGER NOT NULL DEFAULT 0,
                  failed INTEGER NOT NULL DEFAULT 0,
                  watermark_before TEXT,
                  watermark_after TEXT,
                  actor TEXT NOT NULL,
                  actor_name TEXT NOT NULL,
                  params_json TEXT,
                  detail_json TEXT,
                  message TEXT,
                  backup_file TEXT,
                  external_key TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS ask_feedback (
                  id TEXT PRIMARY KEY,
                  conversation_id TEXT,
                  message_id TEXT UNIQUE,
                  user_id TEXT NOT NULL,
                  username TEXT NOT NULL DEFAULT '',
                  display_name TEXT NOT NULL DEFAULT '',
                  question_text TEXT NOT NULL,
                  answer_text TEXT NOT NULL,
                  response_summary TEXT,
                  reason_codes TEXT NOT NULL,
                  comment_text TEXT NOT NULL DEFAULT '',
                  status TEXT NOT NULL DEFAULT 'OPEN',
                  admin_note TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(owner, updated_at);
                CREATE INDEX IF NOT EXISTS message_conversation ON messages(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS sync_runs_started ON sync_runs(started_at DESC);
                CREATE INDEX IF NOT EXISTS ask_feedback_status ON ask_feedback(status, updated_at DESC);
            ''')
            # executescript ends the outer transaction; start one for the migration and seed.
            db.execute('BEGIN IMMEDIATE')
            # Accounts live only in hbairport01; preserve app data and archive the old store first.
            db.execute('DROP TABLE IF EXISTS credentials')
            db.execute('DROP TABLE IF EXISTS users')
            db.execute("DELETE FROM preferences WHERE key='allow_guest'")
            # Additive migration preserves existing questions, results and keys.
            if 'execution_trace' not in {row['name'] for row in db.execute('PRAGMA table_info(messages)')}:
                db.execute('ALTER TABLE messages ADD COLUMN execution_trace TEXT')
            if initial:
                for key in ('retention_days',):
                    db.execute('INSERT OR IGNORE INTO preferences VALUES (?,?)', (key, json.dumps(initial[key])))
                if not db.execute('SELECT 1 FROM models').fetchone():
                    db.execute('INSERT INTO models VALUES (?,?,?,?,?,?,?)', ('default', '默认模型',
                               initial['url'], initial['model'], self.vault.seal(initial.get('key', '')), 1, now()))
                    db.execute('INSERT OR IGNORE INTO preferences VALUES (?,?)', ('active_model', '"default"'))
            db.execute("UPDATE messages SET status='interrupted', error='服务重启，查询未完成' WHERE status='running'")
            db.execute("UPDATE sync_runs SET status='interrupted', message='服务重启，同步状态未回传', finished_at=COALESCE(finished_at,?) WHERE status='running'", (now(),))
            db.execute('DELETE FROM business_credentials WHERE expires < ?', (time(),))
            db.execute('DELETE FROM token_grants WHERE expires < ?', (time(),))
            db.execute('PRAGMA user_version=4')

    def backup_legacy_accounts(self):
        if not self.path.exists():
            return
        # SQLite backup includes committed WAL data; do this before opening a write transaction.
        with closing(sqlite3.connect(self.path)) as source:
            if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('users','credentials')").fetchone():
                return
            backup = self.directory / ('management.before-unified-users-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.sqlite3')
            with closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
            backup.chmod(0o600)

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def log(db, actor, action, target='', detail=''):
        db.execute('INSERT INTO audit(actor,action,target,detail,created_at) VALUES (?,?,?,?,?)',
                   (actor, action, target, detail, now()))

    def audit(self, actor, action, target='', detail=''):
        with self.db() as db:
            self.log(db, actor, action, target, detail)

    def preferences(self):
        with self.db() as db:
            return {row['key']: json.loads(row['value']) for row in db.execute('SELECT * FROM preferences')}

    def save_preferences(self, data, actor):
        if type(data.get('retention_days')) is not int or not 1 <= data['retention_days'] <= 3650:
            raise ValueError('保留天数无效（1–3650 天）')
        with self.db() as db:
            for key in ('retention_days',):
                db.execute('INSERT OR REPLACE INTO preferences VALUES (?,?)', (key, json.dumps(data[key])))
            self.log(db, actor, 'settings.update', detail='会话保留策略')
        return self.preferences()

    def models(self):
        active = self.preferences().get('active_model')
        with self.db() as db:
            return [{k: row[k] for k in ('id', 'name', 'url', 'model', 'enabled', 'updated_at')} |
                    {'has_key': bool(row['secret']), 'active': row['id'] == active}
                    for row in db.execute('SELECT * FROM models ORDER BY updated_at DESC')]

    def model(self, model_id=None):
        with self.db() as db:
            if not model_id:
                current = db.execute("SELECT value FROM preferences WHERE key='active_model'").fetchone()
                model_id = json.loads(current[0]) if current else None
            row = db.execute('SELECT * FROM models WHERE id=? AND enabled=1', (model_id,)).fetchone()
            if not row:
                raise ValueError('模型不存在或已停用')
            return {**dict(row), 'key': self.vault.open(row['secret'])}

    def save_model(self, data, actor, model_id=None):
        name, url, model = text(data.get('name'), '名称', 80), model_url(data.get('url')), text(data.get('model'), '模型标识', 200)
        if type(data.get('enabled', True)) is not bool:
            raise ValueError('模型启用状态无效')
        key = data.get('api_key', '')
        if not isinstance(key, str) or len(key) > 4096 or any(c in key for c in '\r\n'):
            raise ValueError('API Key 格式无效')
        with self.db() as db:
            old = db.execute('SELECT * FROM models WHERE id=?', (model_id,)).fetchone() if model_id else None
            if model_id and not old:
                raise ValueError('模型不存在')
            active = db.execute("SELECT value FROM preferences WHERE key='active_model'").fetchone()
            if old and active and json.loads(active[0]) == model_id and not data.get('enabled', True):
                raise ValueError('请先切换当前模型，再停用此配置')
            secret = '' if data.get('clear_key') is True else self.vault.seal(key) if key else old['secret'] if old else ''
            model_id = model_id or secrets.token_hex(12)
            db.execute('INSERT OR REPLACE INTO models VALUES (?,?,?,?,?,?,?)',
                       (model_id, name, url, model, secret, int(data.get('enabled', True)), now()))
            self.log(db, actor, 'model.update' if old else 'model.create', model_id)
        return next(item for item in self.models() if item['id'] == model_id)

    def activate_model(self, model_id, actor):
        self.model(model_id)
        with self.db() as db:
            row = db.execute('SELECT enabled FROM models WHERE id=?', (model_id,)).fetchone()
            if not row or not row['enabled']:
                raise ValueError('模型不存在或已停用')
            db.execute('INSERT OR REPLACE INTO preferences VALUES (?,?)', ('active_model', json.dumps(model_id)))
            self.log(db, actor, 'model.activate', model_id)

    def delete_model(self, model_id, actor):
        with self.db() as db:
            active = db.execute("SELECT value FROM preferences WHERE key='active_model'").fetchone()
            if active and json.loads(active[0]) == model_id:
                raise ValueError('不能删除当前使用的模型，请先切换')
            if not db.execute('DELETE FROM models WHERE id=?', (model_id,)).rowcount:
                raise ValueError('模型不存在')
            self.log(db, actor, 'model.delete', model_id)

    @staticmethod
    def check_conversation(db, conversation_id, owner, all_access=False):
        row = db.execute('SELECT * FROM conversations WHERE id=?', (conversation_id,)).fetchone()
        if not row or (row['owner'] != owner and not all_access):
            raise ValueError('会话不存在或无权访问')
        return row

    def conversations(self, owner, all_access=False, search='', archived=False, page=1):
        where, params = ['c.archived=?'], [int(archived)]
        if not all_access:
            where.append('c.owner=?'); params.append(owner)
        if search:
            where.append("c.title LIKE ? ESCAPE '\\'")
            pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            params.append(pattern)
        clause = ' WHERE ' + ' AND '.join(where)
        joins = ' FROM conversations c'
        with self.db() as db:
            total = db.execute('SELECT COUNT(*)' + joins + clause, params).fetchone()[0]
            rows = db.execute('SELECT c.*,c.owner AS owner_name, '
                              '(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count' +
                              joins + clause + ' ORDER BY c.updated_at DESC LIMIT 20 OFFSET ?', [*params, (page - 1) * 20])
            return {'ok': True, 'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': 20}

    def begin_question(self, owner, question, model, conversation_id=None):
        with self.db() as db:
            if conversation_id:
                row = self.check_conversation(db, conversation_id, owner)
                if row['archived']:
                    raise ValueError('会话已归档，请恢复后提问')
            else:
                conversation_id = secrets.token_hex(16)
                db.execute('INSERT INTO conversations VALUES (?,?,?,0,?,?)', (conversation_id, owner, question[:80], now(), now()))
            message_id = secrets.token_hex(16)
            db.execute('INSERT INTO messages (id,conversation_id,question,result,status,error,model,created_at,completed_at) '
                       'VALUES (?,?,?,NULL,\'running\',NULL,?,?,NULL)',
                       (message_id, conversation_id, self.vault.seal(question), model, now()))
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now(), conversation_id))
            return conversation_id, message_id

    def complete_question(self, message_id, result=None, error=None, token_ttl=1800, execution_trace=None):
        with self.db() as db:
            row = db.execute('SELECT c.owner,m.conversation_id FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=?', (message_id,)).fetchone()
            if not row:  # Account may have deleted the conversation while a query completed.
                return
            snapshot = dict(result) if result else None
            for key in ('drill_token', 'related_token'):
                token = snapshot.pop(key, None) if snapshot else None
                if token:
                    db.execute('INSERT OR REPLACE INTO token_grants VALUES (?,?,?)', (digest(token), row['owner'], time() + token_ttl))
            encrypted = self.vault.seal(json.dumps(snapshot, ensure_ascii=False, default=str)) if snapshot else None
            steps = execution_trace if execution_trace is not None else (result or {}).get('execution_trace', [])
            encrypted_trace = self.vault.seal(json.dumps(steps, ensure_ascii=False, default=str)) if steps else None
            db.execute('UPDATE messages SET result=?,status=?,error=?,completed_at=?,execution_trace=? WHERE id=?',
                       (encrypted, 'completed' if result else 'failed', error, now(), encrypted_trace, message_id))
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (now(), row['conversation_id']))

    def conversation(self, conversation_id, owner, all_access=False):
        with self.db() as db:
            row = self.check_conversation(db, conversation_id, owner, all_access)
            messages = []
            for item in db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at', (conversation_id,)):
                value = dict(item)
                value['question'] = self.vault.open(value['question'])
                value['result'] = json.loads(self.vault.open(value['result'])) if value['result'] else None
                value['execution_trace'] = json.loads(self.vault.open(value['execution_trace'])) if value['execution_trace'] else []
                messages.append(value)
            return {'ok': True, 'conversation': dict(row), 'messages': messages}

    def latest_completed_plan(self, conversation_id, owner):
        if not conversation_id:
            return None
        with self.db() as db:
            rows = db.execute(
                "SELECT m.result FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                "WHERE m.conversation_id=? AND c.owner=? AND m.status='completed' AND m.result IS NOT NULL "
                "ORDER BY m.created_at DESC",
                (conversation_id, owner)
            ).fetchall()
            for row in rows:
                try:
                    result = json.loads(self.vault.open(row['result']))
                except (ValueError, TypeError):
                    continue
                if result.get('outcome', 'answered') in ('answered','no_data','out_of_coverage') and result.get('plan'):
                    return result['plan']
            return None

    def clarification(self, conversation_id, owner, message_id):
        with self.db() as db:
            self.check_conversation(db, conversation_id, owner)
            row = db.execute('SELECT id,result FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1',
                             (conversation_id,)).fetchone()
            if not row or row['id'] != message_id or not row['result']:
                raise ValueError('澄清已结束或不属于本会话，请重新提问')
            result = json.loads(self.vault.open(row['result']))
            if result.get('outcome') != 'clarification_needed' or not result.get('clarification'):
                raise ValueError('此消息没有待确认的查询条件')
            return result['clarification']

    def edit_conversation(self, conversation_id, owner, data, all_access=False):
        with self.db() as db:
            self.check_conversation(db, conversation_id, owner, all_access)
            if 'title' in data:
                db.execute('UPDATE conversations SET title=? WHERE id=?', (text(data['title'], '会话名称', 120), conversation_id))
            if 'archived' in data:
                if type(data['archived']) is not bool:
                    raise ValueError('归档状态无效')
                db.execute('UPDATE conversations SET archived=? WHERE id=?', (int(data['archived']), conversation_id))
            self.log(db, owner, 'conversation.update', conversation_id)

    def delete_conversation(self, conversation_id, owner, all_access=False):
        with self.db() as db:
            self.check_conversation(db, conversation_id, owner, all_access)
            db.execute('DELETE FROM conversations WHERE id=?', (conversation_id,))
            self.log(db, owner, 'conversation.delete', conversation_id)

    def check_token(self, token, owner):
        if not isinstance(token, str):
            return False
        with self.db() as db:
            return bool(db.execute('SELECT 1 FROM token_grants WHERE token_hash=? AND owner=? AND expires>?', (digest(token), owner, time())).fetchone())

    def overview(self, owner=None):
        with self.db() as db:
            where, params = (' WHERE c.owner=?', (owner,)) if owner else ('', ())
            counts = {'conversations': db.execute('SELECT COUNT(*) FROM conversations c' + where, params).fetchone()[0]}
            for name, predicate in {'messages': '1=1', 'failed_queries': "m.status IN ('failed','interrupted')",
                                    'running': "m.status='running'"}.items():
                sql = 'SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE ' + predicate
                counts[name] = db.execute(sql + (' AND c.owner=?' if owner else ''), params).fetchone()[0]
            counts['models'] = db.execute('SELECT COUNT(*) FROM models').fetchone()[0]
            return counts

    def audit_list(self, page=1, search=''):
        with self.db() as db:
            pattern = '%' + search[:100] + '%'
            joins = ' FROM audit a'
            where = ' WHERE a.action LIKE ? OR a.target LIKE ?'
            total = db.execute('SELECT COUNT(*)' + joins + where, (pattern, pattern)).fetchone()[0]
            rows = db.execute('SELECT a.*,a.actor AS actor_name' + joins + where +
                              ' ORDER BY a.id DESC LIMIT 30 OFFSET ?', (pattern, pattern, (page - 1) * 30))
            return {'ok': True, 'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': 30}

    def purge(self, actor):
        days = self.preferences().get('retention_days', 90)
        cutoff = datetime.fromtimestamp(time() - days * 86400, timezone.utc).isoformat()
        with self.db() as db:
            # Never delete an in-flight conversation.
            count = db.execute("DELETE FROM conversations WHERE updated_at<? AND NOT EXISTS (SELECT 1 FROM messages WHERE conversation_id=conversations.id AND status='running')", (cutoff,)).rowcount
            db.execute('DELETE FROM business_credentials WHERE expires<?', (time(),))
            db.execute('DELETE FROM token_grants WHERE expires<?', (time(),))
            self.log(db, actor, 'retention.purge', detail=f'已清理 {count} 个超过 {days} 天的会话')
            return count

    @staticmethod
    def _sync_run_dict(row):
        data = dict(row)
        for src, dst in (('params_json', 'params'), ('detail_json', 'detail')):
            raw = data.pop(src, None)
            try:
                data[dst] = json.loads(raw) if raw else None
            except ValueError:
                data[dst] = None
        return data

    def active_sync_run(self):
        with self.db() as db:
            row = db.execute("SELECT * FROM sync_runs WHERE status='running' ORDER BY started_at DESC LIMIT 1").fetchone()
            return self._sync_run_dict(row) if row else None

    def sync_run(self, run_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM sync_runs WHERE id=?', (run_id,)).fetchone()
            if not row:
                raise ValueError('同步记录不存在')
            return self._sync_run_dict(row)

    def begin_sync_run(self, job_key, title, source_origin, mode, actor, actor_name, params=None):
        run_id = secrets.token_hex(12)
        started = now()
        with self.db() as db:
            if db.execute("SELECT 1 FROM sync_runs WHERE status='running' LIMIT 1").fetchone():
                raise ValueError('已有同步任务正在执行，请等待当前任务完成')
            db.execute(
                'INSERT INTO sync_runs(id,job_key,title,source_origin,mode,status,started_at,'
                'elapsed_ms,fetched,inserted,updated,skipped,failed,actor,actor_name,params_json,message) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (run_id, job_key, title, source_origin, mode, 'running', started,
                 0, 0, 0, 0, 0, 0, actor, actor_name,
                 json.dumps(params or {}, ensure_ascii=False), '任务正在执行…'),
            )
            row = db.execute('SELECT * FROM sync_runs WHERE id=?', (run_id,)).fetchone()
            return self._sync_run_dict(row)

    def complete_sync_run(self, run_id, *, status, elapsed_ms=0, fetched=0, inserted=0, updated=0,
                          skipped=0, failed=0, watermark_before=None, watermark_after=None,
                          detail=None, message='', backup_file='', finished_at=None):
        completed = utc_iso(finished_at) or now()
        with self.db() as db:
            updated_rows = db.execute(
                'UPDATE sync_runs SET status=?,finished_at=?,elapsed_ms=?,fetched=?,inserted=?,updated=?,'
                'skipped=?,failed=?,watermark_before=?,watermark_after=?,detail_json=?,message=?,backup_file=? '
                'WHERE id=?',
                (status, completed, max(0, int(elapsed_ms)), max(0, int(fetched)), max(0, int(inserted)),
                 max(0, int(updated)), max(0, int(skipped)), max(0, int(failed)),
                 watermark_before or '', watermark_after or '',
                 json.dumps(detail, ensure_ascii=False) if detail is not None else None,
                 (message or '')[:500], backup_file or '', run_id),
            ).rowcount
            if not updated_rows:
                raise ValueError('同步记录不存在')
            return self._sync_run_dict(db.execute('SELECT * FROM sync_runs WHERE id=?', (run_id,)).fetchone())

    def update_sync_run_progress(self, run_id, *, elapsed_ms=0, fetched=0, inserted=0, updated=0,
                                 skipped=0, failed=0, detail=None, message='', backup_file=''):
        """Persist intermediate step totals and live status message while a sync run is executing."""
        with self.db() as db:
            db.execute(
                'UPDATE sync_runs SET elapsed_ms=?,fetched=?,inserted=?,updated=?,'
                'skipped=?,failed=?,detail_json=?,message=?,backup_file=? '
                "WHERE id=? AND status='running'",
                (max(0, int(elapsed_ms)), max(0, int(fetched)), max(0, int(inserted)),
                 max(0, int(updated)), max(0, int(skipped)), max(0, int(failed)),
                 json.dumps(detail, ensure_ascii=False) if detail is not None else None,
                 (message or '')[:500], backup_file or '', run_id),
            )

    def sync_runs_count(self):
        with self.db() as db:
            return db.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]

    def reconcile_external_sync_runs(self, entries):
        """Record external/scheduled runs seen in sync_job_state without duplicating UI-initiated runs."""
        if not entries:
            return 0
        added = 0
        covering_jobs = {
            'flights_fims': ('flights_fims', 'flights_scheduled', 'full_pipeline', 'full_sync'),
            'flights_acdm': ('flights_acdm', 'flights_scheduled', 'full_pipeline', 'full_sync'),
            'codeshares': ('codeshares', 'flights_fims', 'flights_scheduled', 'full_pipeline', 'full_sync'),
            'production': ('production', 'full_pipeline', 'full_sync'),
            'master_data': ('master_data', 'full_pipeline', 'full_sync'),
            'flights_scheduled': ('flights_scheduled', 'full_pipeline', 'full_sync'),
        }
        with self.db() as db:
            for item in entries:
                external_key = item.get('external_key')
                finished = utc_iso(item.get('finished_at'))
                if not external_key or not finished:
                    continue
                if db.execute('SELECT 1 FROM sync_runs WHERE external_key=?', (external_key,)).fetchone():
                    continue
                finished_dt = datetime.fromisoformat(finished)
                parents = covering_jobs.get(item['job_key'], (item['job_key'],))
                placeholders = ','.join('?' for _ in parents)
                candidates = db.execute(
                    f"SELECT started_at, finished_at FROM sync_runs WHERE source_origin != 'external' AND job_key IN ({placeholders})",
                    parents,
                ).fetchall()
                overlapped = False
                for cand in candidates:
                    try:
                        s_dt = datetime.fromisoformat(utc_iso(cand['started_at']) or now())
                        f_dt = datetime.fromisoformat(utc_iso(cand['finished_at']) or now())
                        if (s_dt.timestamp() - 15) <= finished_dt.timestamp() <= (f_dt.timestamp() + 30):
                            overlapped = True
                            break
                    except ValueError:
                        continue
                if overlapped:
                    continue
                started = utc_iso(item.get('started_at')) or finished
                failed = max(0, int(item.get('failed') or 0))
                status = 'partial' if failed > 0 else 'completed'
                db.execute(
                    'INSERT OR IGNORE INTO sync_runs('
                    'id,job_key,title,source_origin,mode,status,started_at,finished_at,elapsed_ms,'
                    'fetched,inserted,updated,skipped,failed,watermark_before,watermark_after,'
                    'actor,actor_name,params_json,detail_json,message,backup_file,external_key) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (secrets.token_hex(12), item['job_key'], item['title'], 'external',
                     item.get('mode') or 'INCREMENTAL', status, started, finished,
                     max(0, int(item.get('elapsed_ms') or 0)),
                     max(0, int(item.get('fetched') or 0)),
                     max(0, int(item.get('inserted') or 0)),
                     max(0, int(item.get('updated') or 0)),
                     max(0, int(item.get('skipped') or 0)),
                     failed,
                     item.get('watermark_before') or '',
                     item.get('watermark_after') or '',
                     'sync-service', '外部/定时同步',
                     json.dumps({'origin': 'sync_job_state'}, ensure_ascii=False),
                     json.dumps(item.get('detail'), ensure_ascii=False) if item.get('detail') is not None else None,
                     (item.get('message') or '')[:500], '', external_key),
                )
                added += 1
        return added

    def _sync_summary(self, db):
        row = db.execute(
            "SELECT COUNT(*) AS total_runs, "
            "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed_runs, "
            "SUM(CASE WHEN status IN ('failed','interrupted') THEN 1 ELSE 0 END) AS failed_runs, "
            "SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running_runs, "
            "COALESCE(SUM(fetched), 0) AS total_fetched, "
            "COALESCE(SUM(inserted), 0) AS total_inserted, "
            "COALESCE(SUM(updated), 0) AS total_updated, "
            "COALESCE(SUM(failed), 0) AS total_failed, "
            "MAX(CASE WHEN status IN ('completed','partial') THEN COALESCE(finished_at, started_at) END) AS latest_success_at "
            "FROM sync_runs"
        ).fetchone()
        local_today = datetime.now(SHANGHAI_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        today_row = db.execute(
            "SELECT COUNT(*) AS today_runs, COALESCE(SUM(fetched), 0) AS today_fetched "
            "FROM sync_runs WHERE started_at >= ?",
            (local_today,),
        ).fetchone()
        return {
            'total_runs': row['total_runs'] or 0,
            'completed_runs': row['completed_runs'] or 0,
            'failed_runs': row['failed_runs'] or 0,
            'running_runs': row['running_runs'] or 0,
            'total_fetched': row['total_fetched'] or 0,
            'total_inserted': row['total_inserted'] or 0,
            'total_updated': row['total_updated'] or 0,
            'total_failed': row['total_failed'] or 0,
            'today_runs': today_row['today_runs'] or 0,
            'today_fetched': today_row['today_fetched'] or 0,
            'latest_success_at': row['latest_success_at'],
        }

    def sync_summary(self):
        with self.db() as db:
            return self._sync_summary(db)

    def sync_runs(self, page=1, page_size=20, job_key='', status='', search=''):
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        where, params = ['1=1'], []
        if job_key:
            where.append('job_key=?')
            params.append(job_key[:60])
        if status:
            where.append('status=?')
            params.append(status[:30])
        if search:
            escaped = '%' + search[:100].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            where.append("(title LIKE ? ESCAPE '\\' OR COALESCE(message,'') LIKE ? ESCAPE '\\' OR actor_name LIKE ? ESCAPE '\\')")
            params.extend([escaped, escaped, escaped])
        clause = ' WHERE ' + ' AND '.join(where)
        with self.db() as db:
            total = db.execute('SELECT COUNT(*) FROM sync_runs' + clause, params).fetchone()[0]
            rows = db.execute(
                'SELECT * FROM sync_runs' + clause + ' ORDER BY started_at DESC LIMIT ? OFFSET ?',
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            return {
                'ok': True,
                'items': [self._sync_run_dict(row) for row in rows],
                'total': total,
                'page': page,
                'page_size': page_size,
                'summary': self._sync_summary(db),
            }

    _FEEDBACK_STATUSES = ('OPEN', 'INVESTIGATING', 'FIXED', 'WONT_FIX')
    _FEEDBACK_REASONS = ('WRONG_INTENT', 'WRONG_DATA', 'WRONG_FILTER', 'NO_ANSWER', 'OTHER')
    _HIGH_SEVERITY_REASONS = {'WRONG_DATA', 'WRONG_INTENT', 'NO_ANSWER'}

    def _feedback_dict(self, row):
        codes = json.loads(row['reason_codes'] or '[]')
        summary = json.loads(row['response_summary']) if row['response_summary'] else None
        severity = 'HIGH' if any(c in self._HIGH_SEVERITY_REASONS for c in codes) else 'NORMAL'
        return {
            'id': row['id'],
            'conversation_id': row['conversation_id'] or '',
            'message_id': row['message_id'] or '',
            'user_id': row['user_id'],
            'username': row['username'],
            'display_name': row['display_name'],
            'question_text': row['question_text'],
            'answer_text': row['answer_text'],
            'response_summary': summary,
            'reason_codes': codes,
            'comment': row['comment_text'],
            'severity': severity,
            'status': row['status'],
            'admin_note': row['admin_note'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    def submit_feedback(self, user, payload):
        question_text = text(payload.get('question_text'), '问题内容', 4000)
        answer_text = str(payload.get('answer_text') or '').strip()[:8000]
        raw_codes = payload.get('reason_codes')
        if not isinstance(raw_codes, list) or not raw_codes:
            raise ValueError('请至少选择一个反馈原因')
        codes = [c for c in dict.fromkeys(str(x).strip().upper() for x in raw_codes) if c in self._FEEDBACK_REASONS]
        if not codes:
            raise ValueError('反馈原因编码无效')
        comment = str(payload.get('comment') or '').strip()[:2000]
        conv_id = str(payload.get('conversation_id') or '').strip()[:64]
        msg_id = str(payload.get('message_id') or '').strip()[:64] or secrets.token_hex(12)
        summary = payload.get('response_summary')
        summary_json = json.dumps(summary, ensure_ascii=False, default=str)[:16000] if isinstance(summary, dict) else None
        ts = now()
        fid = secrets.token_hex(12)
        with self.db() as db:
            existing = db.execute('SELECT * FROM ask_feedback WHERE message_id=?', (msg_id,)).fetchone()
            if existing:
                db.execute(
                    'UPDATE ask_feedback SET reason_codes=?, comment_text=?, response_summary=COALESCE(?,response_summary), updated_at=? WHERE id=?',
                    (json.dumps(codes, ensure_ascii=False), comment, summary_json, ts, existing['id']),
                )
                row = db.execute('SELECT * FROM ask_feedback WHERE id=?', (existing['id'],)).fetchone()
            else:
                db.execute(
                    'INSERT INTO ask_feedback(id,conversation_id,message_id,user_id,username,display_name,question_text,answer_text,response_summary,reason_codes,comment_text,status,admin_note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (fid, conv_id, msg_id, user['id'], user.get('username', ''), user.get('display_name', ''),
                     question_text, answer_text, summary_json, json.dumps(codes, ensure_ascii=False), comment,
                     'OPEN', '', ts, ts),
                )
                row = db.execute('SELECT * FROM ask_feedback WHERE id=?', (fid,)).fetchone()
            self.log(db, user['id'], 'ask.feedback', row['id'], ','.join(codes))
            return self._feedback_dict(row)

    def feedback_counts(self):
        with self.db() as db:
            counts = {k: 0 for k in self._FEEDBACK_STATUSES}
            total = 0
            for status, n in db.execute('SELECT status, COUNT(*) FROM ask_feedback GROUP BY status'):
                counts[status] = n
                total += n
            counts['ALL'] = total
            return counts

    def feedback_list(self, status='', search='', reason='', severity='', start_date='', end_date='', page=1, page_size=20):
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        where, params = ['1=1'], []
        if status and status != 'ALL':
            where.append('status=?')
            params.append(status[:30])
        if reason:
            where.append('reason_codes LIKE ?')
            params.append('%' + reason[:40] + '%')
        sev = str(severity or '').strip().upper()
        high_pats = ['%' + r + '%' for r in sorted(self._HIGH_SEVERITY_REASONS)]
        if sev == 'HIGH':
            where.append('(' + ' OR '.join('reason_codes LIKE ?' for _ in high_pats) + ')')
            params.extend(high_pats)
        elif sev == 'NORMAL':
            where.append('(' + ' AND '.join('reason_codes NOT LIKE ?' for _ in high_pats) + ')')
            params.extend(high_pats)
        if start_date and re.match(r'^\d{4}-\d{2}-\d{2}$', start_date.strip()):
            where.append('substr(created_at, 1, 10) >= ?')
            params.append(start_date.strip())
        if end_date and re.match(r'^\d{4}-\d{2}-\d{2}$', end_date.strip()):
            where.append('substr(created_at, 1, 10) <= ?')
            params.append(end_date.strip())
        if search:
            esc = '%' + search[:100].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            where.append("(question_text LIKE ? ESCAPE '\\' OR username LIKE ? ESCAPE '\\' OR display_name LIKE ? ESCAPE '\\' OR admin_note LIKE ? ESCAPE '\\')")
            params.extend([esc, esc, esc, esc])
        clause = ' WHERE ' + ' AND '.join(where)
        with self.db() as db:
            total = db.execute('SELECT COUNT(*) FROM ask_feedback' + clause, params).fetchone()[0]
            rows = db.execute(
                'SELECT * FROM ask_feedback' + clause + ' ORDER BY updated_at DESC LIMIT ? OFFSET ?',
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            counts = {k: 0 for k in self._FEEDBACK_STATUSES}
            all_total = 0
            for st_key, n in db.execute('SELECT status, COUNT(*) FROM ask_feedback GROUP BY status'):
                counts[st_key] = n
                all_total += n
            counts['ALL'] = all_total
            return {
                'ok': True,
                'items': [self._feedback_dict(row) for row in rows],
                'total': total,
                'page': page,
                'page_size': page_size,
                'counts': counts,
            }

    def feedback_get(self, feedback_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM ask_feedback WHERE id=?', (feedback_id,)).fetchone()
            if not row:
                raise ValueError('反馈记录不存在')
            return self._feedback_dict(row)

    def update_feedback(self, feedback_id, status, admin_note, actor):
        st = str(status or '').strip().upper()
        if st not in self._FEEDBACK_STATUSES:
            raise ValueError('反馈状态无效')
        note = str(admin_note or '').strip()[:1000]
        ts = now()
        with self.db() as db:
            cur = db.execute(
                'UPDATE ask_feedback SET status=?, admin_note=?, updated_at=? WHERE id=?',
                (st, note, ts, feedback_id),
            )
            if cur.rowcount == 0:
                raise ValueError('反馈记录不存在')
            self.log(db, actor, 'feedback.update', feedback_id, f'status={st}')
            return self._feedback_dict(db.execute('SELECT * FROM ask_feedback WHERE id=?', (feedback_id,)).fetchone())
