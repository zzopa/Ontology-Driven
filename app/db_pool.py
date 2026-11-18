"""Reuse read-only pg8000 sessions without sharing an active transaction."""
from contextlib import closing
from threading import Lock
from time import monotonic

from pg8000 import dbapi as pg


# Some queries borrow a second connection while holding the first.
_MAX_IDLE = 4
_MAX_AGE = 300


def _close(conn):
    try:
        conn.close()
    except (pg.Error, OSError):
        pass


def _begin(conn, repeatable_read, timeout_ms):
    transaction = ('ISOLATION LEVEL REPEATABLE READ, READ ONLY'
                   if repeatable_read else 'READ ONLY')
    try:
        with closing(conn.cursor()) as cur:
            cur.execute(f'SET TRANSACTION {transaction}; SET LOCAL statement_timeout = {int(timeout_ms)}')
    except BaseException:
        _close(conn)
        raise


class _ReadLease:
    def __init__(self, pool, conn, expires):
        self._pool = pool
        self._conn = conn
        self._expires = expires

    def __getattr__(self, name):
        if self._conn is None:
            raise pg.InterfaceError('connection is closed')
        return getattr(self._conn, name)

    def close(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            self._pool._release(conn, self._expires)


class ReadConnectionPool:
    def __init__(self):
        self._idle = []
        self._lock = Lock()

    def connect(self, options, repeatable_read, timeout_ms):
        self.close_idle()
        with self._lock:
            entry = self._idle.pop() if self._idle else None
        if entry is not None:
            conn, expires = entry
            try:
                _begin(conn, repeatable_read, timeout_ms)
            except (pg.Error, OSError):
                # Transaction setup also checks the socket before any business SQL runs.
                self.close_idle(all_connections=True)
            else:
                return _ReadLease(self, conn, expires)

        conn = pg.connect(**options)
        expires = monotonic() + _MAX_AGE
        _begin(conn, repeatable_read, timeout_ms)
        return _ReadLease(self, conn, expires)

    def _release(self, conn, expires):
        if expires > monotonic():
            try:
                conn.rollback()
            except (pg.Error, OSError):
                pass
            else:
                with self._lock:
                    if len(self._idle) < _MAX_IDLE and expires > monotonic():
                        self._idle.append((conn, expires))
                        return
        _close(conn)

    def close_idle(self, *, all_connections=False):
        deadline = float('inf') if all_connections else monotonic()
        with self._lock:
            expired = [conn for conn, expires in self._idle if expires <= deadline]
            self._idle = [(conn, expires) for conn, expires in self._idle if expires > deadline]
        for conn in expired:
            _close(conn)
