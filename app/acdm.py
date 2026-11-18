"""Read-only access to the intermediate SQLite snapshot used for local import."""
import json
import sqlite3
import re
from time import monotonic





from app.config import ROOT

DATA = ROOT / 'data/acdm'



def snapshot():
    try:
        name = (DATA / 'current.txt').read_text(encoding='utf-8').strip()
        path = (DATA / name).resolve()
        if path.parent != DATA.resolve() or not name.startswith('snapshot-'):
            raise ValueError('本地副本路径无效')
        metadata = json.loads((path / 'schema.json').read_text(encoding='utf-8'))
        return path, metadata
    except FileNotFoundError as exc:
        raise ValueError('尚未完成数据复制，请先运行 scripts/service/launchers/copy-acdm.cmd') from exc


def execute(path, metadata, sql, parameters=()):
    if not isinstance(sql, str) or not 1 <= len(sql) <= 30000:
        raise ValueError('SQL 长度无效')
    allowed = {tuple(name.split('.')) for name in metadata['tables']}
    functions = {'count','sum','avg','min','max','round','abs','coalesce','ifnull','nullif',
                 'date','datetime','strftime','julianday','substr','length','upper','lower','trim',
                 'row_number','rank','dense_rank','lag','lead','like','cast','total'}
    conn = sqlite3.connect(':memory:', uri=True)
    try:
        for owner in sorted({owner for owner, _ in allowed}):
            if owner not in {'ACDM','FIMS','FORMFILL'}:
                raise ValueError('副本包含未允许的 schema')
            conn.execute('ATTACH DATABASE ? AS "' + owner + '"',
                         ((path / (owner + '.sqlite3')).as_uri() + '?mode=ro',))
        conn.execute('PRAGMA query_only=ON')
        def authorize(action, arg1, arg2, database, _trigger):
            if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and (database, arg1) in allowed:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or '').lower() in functions:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        conn.set_authorizer(authorize)
        deadline = monotonic() + 15
        conn.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        cursor = conn.execute(sql, parameters)
        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchmany(201)
        return {'columns': columns, 'rows': [[v.hex() if isinstance(v, bytes) else v for v in row]
                                            for row in rows[:200]],
                'truncated': len(rows) > 200, 'sql': sql, 'parameters': list(parameters)}
    except sqlite3.Error as exc:
        raise ValueError('本地只读查询失败：' + str(exc)) from exc
    finally:
        conn.close()


