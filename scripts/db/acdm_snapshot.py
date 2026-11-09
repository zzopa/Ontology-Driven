"""Copy a bounded Oracle snapshot; never import airport-server application code."""
import argparse
import ast
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'data/acdm'


def identifier(name):
    if not re.fullmatch(r'[A-Z][A-Z0-9_$#]*', name):
        raise ValueError('Invalid Oracle identifier')
    return '"' + name + '"'


def local_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'read'):
        return value.read()
    return value


def business_rules(source):
    path = source / 'agents/tools/acdm_assistant_tools.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    text = path.read_text(encoding='utf-8')
    rules = {}
    examples = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == 'SQLS':
                examples = ast.literal_eval(node.value)
            if name in {'EXPOSED_TABLES', 'COLUMN_METADATA'}:
                rules[name] = ast.literal_eval(node.value)
            if name in {'_METRIC_DEFINITIONS', '_LEFT_JOIN_TEMPLATE'}:
                class Examples(ast.NodeTransformer):
                    def visit_Subscript(self, item):
                        if isinstance(item.value, ast.Name) and item.value.id == 'SQLS':
                            return ast.Constant(examples[ast.literal_eval(item.slice)])
                        raise ValueError('Unsupported business-rule expression')
                rules[name] = ast.literal_eval(Examples().visit(node.value))
    rules['EXPOSED_TABLES']['FIMS.TB_DYN_FLIGHT'] = '航班计划'
    rules['ontology'] = (source / 'configs/acdm_ontology.yaml').read_text(encoding='utf-8')
    rules['source'] = str(source)
    return rules


def export(source, days):
    import oracledb
    from dotenv import dotenv_values
    env = dotenv_values(source / '.env')
    def option(key, default=None):
        return os.environ.get(key) or env.get(key) or default
    rules = business_rules(source)
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / 'business_rules.json').write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding='utf-8')
    staging = DEST / ('snapshot-' + uuid.uuid4().hex)
    staging.mkdir()
    today = datetime.now(timezone(timedelta(hours=8))).date()
    start, end = today - timedelta(days=days - 1), today + timedelta(days=1)
    binds = {'start_date': datetime.combine(start, datetime.min.time()),
             'end_date': datetime.combine(end, datetime.min.time())}
    manifest = {'start': str(start), 'end_exclusive': str(end), 'tables': {},
                'exported_at': datetime.now().isoformat(), 'source_read_only': True}
    conn = None
    try:
        conn = oracledb.connect(user=option('ORACLE_USER'), password=option('ORACLE_PASSWORD'),
            dsn=oracledb.makedsn(option('ORACLE_HOST', 'localhost'), int(option('ORACLE_PORT', '1521')),
                                 service_name=option('ORACLE_DATABASE', 'orcl')))
        conn.call_timeout = 60000
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            selection = """SELECT p.FLIGHT_ID FROM FIMS.TB_DYN_FLIGHT p
                WHERE (p.PLAN_TIME >= :start_date AND p.PLAN_TIME < :end_date)
                OR EXISTS (SELECT 1 FROM ACDM.TB_DYN_FLIGHT a WHERE a.FLIGHT_ID=p.FLIGHT_ID AND
                    ((a.REAL_TAKEOFF_TIME >= :start_date AND a.REAL_TAKEOFF_TIME < :end_date)
                    OR (a.REAL_LANDIN_TIME >= :start_date AND a.REAL_LANDIN_TIME < :end_date)))"""
            for qualified, label in rules['EXPOSED_TABLES'].items():
                owner, table = qualified.split('.')
                cur.execute('''SELECT c.COLUMN_NAME,c.DATA_TYPE,c.DATA_LENGTH,c.DATA_PRECISION,
                    c.DATA_SCALE,c.NULLABLE,m.COMMENTS FROM ALL_TAB_COLUMNS c
                    LEFT JOIN ALL_COL_COMMENTS m ON m.OWNER=c.OWNER AND m.TABLE_NAME=c.TABLE_NAME
                    AND m.COLUMN_NAME=c.COLUMN_NAME WHERE c.OWNER=:owner AND c.TABLE_NAME=:tab
                    ORDER BY c.COLUMN_ID''', {'owner': owner, 'tab': table})
                columns = [dict(zip(('name','oracle_type','length','precision','scale','nullable','comment'), row)) for row in cur]
                if not columns:
                    raise ValueError('Missing table or metadata permission: ' + qualified)
                names = {c['name'] for c in columns}
                where, params = '', {}
                if table in {'TB_DYN_FLIGHT', 'TBH_FIMS_DYN_CALC_TOTAL'}:
                    if 'FLIGHT_ID' not in names:
                        raise ValueError('No FLIGHT_ID date linkage: ' + qualified)
                    where, params = ' WHERE FLIGHT_ID IN (' + selection + ')', binds
                elif owner == 'FORMFILL':
                    if 'EXEC_DATE' not in names:
                        raise ValueError('No EXEC_DATE: ' + qualified)
                    where, params = ' WHERE EXEC_DATE >= :start_date AND EXEC_DATE < :end_date', binds
                local = sqlite3.connect(staging / (owner + '.sqlite3'))
                try:
                    definitions = []
                    for col in columns:
                        numeric = col['oracle_type'] in {'NUMBER','FLOAT','BINARY_FLOAT','BINARY_DOUBLE'}
                        kind = 'INTEGER' if numeric and col['scale'] == 0 and (col['precision'] or 38) <= 18 else 'TEXT'
                        definitions.append(identifier(col['name']) + ' ' + kind)
                    local.execute('CREATE TABLE ' + identifier(table) + '(' + ','.join(definitions) + ')')
                    cur.arraysize = 500
                    cur.execute('SELECT ' + ','.join(identifier(c['name']) for c in columns) +
                                ' FROM ' + identifier(owner) + '.' + identifier(table) + where, params)
                    count = 0
                    while rows := cur.fetchmany(500):
                        local.executemany('INSERT INTO ' + identifier(table) + ' VALUES (' +
                            ','.join('?' for _ in columns) + ')', [tuple(local_value(v) for v in row) for row in rows])
                        count += len(rows)
                    for key in ('FLIGHT_ID','PLAN_TIME','EXEC_DATE'):
                        if key in names:
                            local.execute('CREATE INDEX ' + identifier('IDX_' + table + '_' + key) +
                                          ' ON ' + identifier(table) + '(' + identifier(key) + ')')
                    local.commit()
                    manifest['tables'][qualified] = {'label': label, 'columns': columns, 'rows': count}
                    print(qualified + ': ' + str(count), flush=True)
                finally:
                    local.close()
        (staging / 'schema.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        # Switch only after every table is complete; a failed copy preserves the previous snapshot.
        pointer = DEST / 'current.tmp'
        pointer.write_text(staging.name, encoding='utf-8')
        pointer.replace(DEST / 'current.txt')
        print('Snapshot complete: ' + str(staging))
    except BaseException:
        shutil.rmtree(staging)
        raise
    finally:
        if conn is not None:
            conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path('D:/home/airport-images-20260901/airport-server'))
    parser.add_argument('--days', type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.days <= 31:
        parser.error('--days must be between 1 and 31')
    export(args.source, args.days)
