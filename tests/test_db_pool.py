import unittest
from unittest.mock import Mock, patch

from pg8000 import dbapi as pg

from app.db_pool import ReadConnectionPool


class ReadConnectionPoolTests(unittest.TestCase):
    def setUp(self):
        self.pool = ReadConnectionPool()
        self.addCleanup(self.pool.close_idle, all_connections=True)

    @patch('app.db_pool.pg.connect')
    def test_only_reuses_returned_connection_and_starts_a_new_read_only_transaction(self, connect):
        first, second = Mock(), Mock()
        connect.side_effect = [first, second]

        one = self.pool.connect({}, False, 1000)
        two = self.pool.connect({}, True, 2000)
        self.assertIs(one.cursor(), first.cursor.return_value)
        self.assertIs(two.cursor(), second.cursor.return_value)
        one.close()
        one.close()
        first.rollback.assert_called_once()
        with self.assertRaises(pg.InterfaceError):
            one.cursor()

        three = self.pool.connect({}, True, 1500)
        self.assertIs(three.cursor(), first.cursor.return_value)
        first.cursor.return_value.execute.assert_called_with(
            'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY; SET LOCAL statement_timeout = 1500')
        self.assertEqual(connect.call_count, 2)
        first.close.assert_not_called()
        two.close()
        three.close()

    @patch('app.db_pool.pg.connect')
    def test_broken_idle_connection_is_replaced_once(self, connect):
        stale, fresh = Mock(), Mock()
        connect.side_effect = [stale, fresh]
        self.pool.connect({}, False, 1000).close()
        stale.cursor.return_value.execute.side_effect = pg.InterfaceError('network error')

        lease = self.pool.connect({}, False, 1000)
        self.assertIs(lease.cursor(), fresh.cursor.return_value)
        stale.close.assert_called_once()
        self.assertEqual(connect.call_count, 2)
        lease.close()

    @patch('app.db_pool.monotonic', return_value=10)
    @patch('app.db_pool.pg.connect')
    def test_reaper_closes_old_idle_sessions_and_leaves_borrowed_sessions_alone(self, connect, clock):
        idle, borrowed, fresh = Mock(), Mock(), Mock()
        connect.side_effect = [idle, borrowed, fresh]
        one = self.pool.connect({}, False, 1000)
        two = self.pool.connect({}, False, 1000)
        one.close()

        clock.return_value = 310
        self.pool.close_idle()
        idle.close.assert_called_once()
        borrowed.close.assert_not_called()
        two.close()
        borrowed.close.assert_called_once()

        three = self.pool.connect({}, False, 1000)
        self.assertIs(three.cursor(), fresh.cursor.return_value)
        three.close()

    @patch('app.db_pool.pg.connect')
    def test_failed_rollback_discards_connection(self, connect):
        broken, fresh = Mock(), Mock()
        connect.side_effect = [broken, fresh]
        broken.rollback.side_effect = pg.InterfaceError('network error')

        self.pool.connect({}, False, 1000).close()
        broken.close.assert_called_once()
        lease = self.pool.connect({}, False, 1000)
        self.assertIs(lease.cursor(), fresh.cursor.return_value)
        lease.close()
