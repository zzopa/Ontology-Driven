from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.acdm import execute, DATA
from scripts.db.acdm_snapshot import local_value
from datetime import datetime
from decimal import Decimal


class ACDMSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=DATA)
        self.path = Path(self.temp.name)
        conn = sqlite3.connect(self.path / 'FIMS.sqlite3')
        try:
            conn.execute('CREATE TABLE TB_DYN_FLIGHT (FLIGHT_ID INTEGER, PLAN_TIME TEXT)')
            conn.executemany('INSERT INTO TB_DYN_FLIGHT VALUES (?,?)',
                             [(i, '2026-10-09 10:00:00') for i in range(205)])
            conn.commit()
        finally:
            conn.close()
        self.meta = {'start': '2026-10-04', 'end_exclusive': '2026-10-11',
                     'tables': {'FIMS.TB_DYN_FLIGHT': {}}}

    def tearDown(self):
        self.temp.cleanup()

    def test_aggregate_and_display_limit(self):
        self.assertEqual(execute(self.path, self.meta, 'SELECT COUNT(*) FROM FIMS.TB_DYN_FLIGHT')['rows'], [[205]])
        detail = execute(self.path, self.meta, 'SELECT * FROM FIMS.TB_DYN_FLIGHT')
        self.assertEqual(len(detail['rows']), 200)
        self.assertTrue(detail['truncated'])
        grouped = execute(self.path, self.meta, 'SELECT date(PLAN_TIME),COUNT(*) FROM FIMS.TB_DYN_FLIGHT GROUP BY date(PLAN_TIME)')
        self.assertEqual(grouped['rows'], [['2026-10-09', 205]])

    def test_write_and_escape_attempts_denied(self):
        for sql in ['DELETE FROM FIMS.TB_DYN_FLIGHT', 'DROP TABLE FIMS.TB_DYN_FLIGHT',
                    'UPDATE FIMS.TB_DYN_FLIGHT SET FLIGHT_ID=0',
                    'PRAGMA FIMS.table_info(TB_DYN_FLIGHT)', "ATTACH DATABASE ':memory:' AS other",
                    'SELECT * FROM FIMS.sqlite_master', "SELECT load_extension('anything')",
                    'SELECT 1; DELETE FROM FIMS.TB_DYN_FLIGHT']:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                execute(self.path, self.meta, sql)
        self.assertEqual(execute(self.path, self.meta, 'SELECT COUNT(*) FROM FIMS.TB_DYN_FLIGHT')['rows'], [[205]])

    def test_value_preservation(self):
        self.assertEqual(local_value(datetime(2026,10,9,12,30)), '2026-10-09 12:30:00')
        self.assertEqual(local_value(Decimal('123456789012345678.123')), '123456789012345678.123')



class CopiedACDMTests(unittest.TestCase):
    def test_copied_databases_integrity_and_known_query(self):
        from app.acdm import snapshot
        path, metadata = snapshot()
        self.assertEqual(len(metadata['tables']), 12)
        for file in path.glob('*.sqlite3'):
            conn = sqlite3.connect(file.as_uri() + '?mode=ro', uri=True)
            try:
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            finally:
                conn.close()
        sql = '''WITH flights AS (
            SELECT FLIGHT_ID,ROW_NUMBER() OVER (
                PARTITION BY AIRLINE_TWO_CHAR_CD || FLIGHT_NO,PLAN_TIME,IS_OFF_IN ORDER BY FLIGHT_ID) AS rn
            FROM FIMS.TB_DYN_FLIGHT WHERE PLAN_TIME >= '2026-10-09' AND PLAN_TIME < '2026-10-10'
            AND FLIGHT_TASK_CD <> 'A/N') SELECT COUNT(*) AS plan_flights FROM flights WHERE rn=1'''
        result = execute(path, metadata, sql)
        self.assertGreater(result['rows'][0][0], 0)

