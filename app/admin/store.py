"""SQLite management data, transactional access control and audit trail."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
from threading import RLock
from time import time
from urllib.parse import urlsplit

from app.admin.security import Vault, digest

def now():
    return datetime.now(timezone.utc).isoformat()


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
                CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(owner, updated_at);
                CREATE INDEX IF NOT EXISTS message_conversation ON messages(conversation_id, created_at);
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
            db.execute('DELETE FROM business_credentials WHERE expires < ?', (time(),))
            db.execute('DELETE FROM token_grants WHERE expires < ?', (time(),))
            db.execute('PRAGMA user_version=2')

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
