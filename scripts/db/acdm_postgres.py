"""Import the existing SQLite copy into an isolated local PostgreSQL database."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import uuid
import re

import pg8000.dbapi as pg
from app.config import ROOT, settings
from app.query import identifier, literal
from app.acdm import snapshot, DATA


def prepare():
    path, manifest = snapshot()
    rules = json.loads((DATA / 'business_rules.json').read_text(encoding='utf-8'))
    database = 'acdm_test_' + datetime.now().strftime('%Y%m%d') + '_' + uuid.uuid4().hex[:8]
    options = settings.identity_db.copy()
    # Only a new database is created; no DDL or inserts target the original business database.
    options['database'] = 'postgres'
    maintenance = pg.connect(**options)
    maintenance.autocommit = True
    try:
        maintenance.cursor().execute('CREATE DATABASE ' + identifier(database))
    finally:
        maintenance.close()
    options['database'] = database
    conn = pg.connect(**options)
    ontology = {'entities': {}, 'logical_links': [], 'relations': {}, 'sensitive_fields': [],
                'coverage': {k:manifest[k] for k in ('start','end_exclusive','exported_at')}, 'offline_queries': []}
    try:
        cur = conn.cursor()
        for schema in ('FIMS','ACDM','FORMFILL','TEST'):
            cur.execute('CREATE SCHEMA ' + identifier(schema))
        for qualified, table in manifest['tables'].items():
            owner, name = qualified.split('.')
            target = identifier(owner) + '.' + identifier(name)
            fields = table['columns']
            types = {}
            for col in fields:
                typ = col['oracle_type']
                types[col['name']] = ('numeric' if typ in {'NUMBER','FLOAT','BINARY_FLOAT','BINARY_DOUBLE'}
                    else 'timestamp' if typ == 'DATE' or typ.startswith('TIMESTAMP')
                    else 'bytea' if typ in {'BLOB','RAW','LONG RAW'} else 'text')
            cur.execute('CREATE TABLE ' + target + '(' + ','.join(identifier(c['name']) + ' ' + types[c['name']] for c in fields) + ')')
            cur.execute('COMMENT ON TABLE ' + target + ' IS ' + literal(table['label']))
            for col in fields:
                if col.get('comment'):
                    cur.execute('COMMENT ON COLUMN ' + target + '.' + identifier(col['name']) + ' IS ' + literal(col['comment']))
            local = sqlite3.connect((path / (owner + '.sqlite3')).as_uri() + '?mode=ro', uri=True)
            try:
                source = local.execute('SELECT * FROM ' + identifier(name))
                while rows := source.fetchmany(250):
                    cur.executemany('INSERT INTO ' + target + ' VALUES (' + ','.join('%s' for _ in fields) + ')', rows)
            finally:
                local.close()
            cur.execute('SELECT count(*) FROM ' + target)
            if cur.fetchone()[0] != table['rows']:
                raise ValueError('Copy row count mismatch: ' + qualified)
            columns = {c['name'] for c in fields}
            if 'FLIGHT_ID' in columns:
                cur.execute('CREATE INDEX ON ' + target + '(' + identifier('FLIGHT_ID') + ')')
            attributes = {field: {'label':spec['label'],'enums':spec.get('enum',{})}
                          for field,spec in rules['COLUMN_METADATA'].get(qualified,{}).items() if field in columns}
            ontology['entities'][qualified] = {'name':table['label'],'aliases':[table['label']],
                'attributes':attributes,'bindings':{},'identity_fields':[],'queryable':True}
            if name.startswith('TB_') and name != 'TB_DYN_FLIGHT':
                ontology['offline_queries'].append({'subject':qualified,'intent':'detail','aliases':[table['label']]})
            print(qualified + ': ' + str(table['rows']), flush=True)
        plan_columns = {c['name'] for c in manifest['tables']['FIMS.TB_DYN_FLIGHT']['columns']}
        actual_columns = {c['name'] for c in manifest['tables']['ACDM.TB_DYN_FLIGHT']['columns']}
        extras = ','.join('f2.' + identifier(c) for c in sorted(actual_columns-plan_columns))
        extras = ',' + extras if extras else ''
        for table, executed in [('flight_plan',False),('flight_execution',True)]:
            event = '''CASE WHEN f1."IS_OFF_IN"='D' THEN f2."REAL_TAKEOFF_TIME" ELSE f2."REAL_LANDIN_TIME" END''' if executed else 'f1."PLAN_TIME"'
            filt = '''AND coalesce(f2."ABNORMAL_FLIGHT_STAT_CD",'null') <> 'CAN' ''' if executed else ''
            if executed:
                filt += ' AND ' + event + ' >= ' + literal(manifest['start']) + '::timestamp AND ' + event + ' < ' + literal(manifest['end_exclusive']) + '::timestamp'
            cur.execute('CREATE TABLE "TEST".' + identifier(table) + ' AS SELECT * FROM (SELECT f1.*' + extras +
                ', f1."AIRLINE_TWO_CHAR_CD" || f1."FLIGHT_NO" AS "FLIGHT_KEY",' + event + ' AS "EVENT_TIME",'
                'ROW_NUMBER() OVER(PARTITION BY f1."AIRLINE_TWO_CHAR_CD" || f1."FLIGHT_NO",f1."PLAN_TIME",f1."IS_OFF_IN" ORDER BY f1."FLIGHT_ID") AS "DEDUP_RN" '
                'FROM "FIMS"."TB_DYN_FLIGHT" f1 LEFT JOIN "ACDM"."TB_DYN_FLIGHT" f2 ON f1."FLIGHT_ID"=f2."FLIGHT_ID" '
                'WHERE f1."IS_OFF_IN" IN (\'A\',\'D\') AND f1."FLIGHT_TASK_CD" <> \'A/N\' ' + filt + ') d WHERE "DEDUP_RN"=1')
            cur.execute('ALTER TABLE "TEST".' + identifier(table) + ' ADD PRIMARY KEY ("FLIGHT_ID")')
            qualified = 'TEST.' + table
            label = '执行航班' if executed else '计划航班'
            ontology['entities'][qualified] = {'name':label,'aliases':([label,'实际执行','实际航班','航班吞吐量','起飞航班','落地航班','进港航班','出港航班'] if executed else [label,'航班计划']),
                'description':('执行航班本地语义表，已按航空公司+航班号、计划时间、进出港方向去重，排除取消和A/N备降。架次直接COUNT本表，EVENT_TIME按进港落地/出港起飞时间，不再重复添加默认排除条件。' if executed else
                               '计划航班本地语义表，已按航空公司+航班号、计划时间、进出港方向去重并排除A/N备降。架次直接COUNT本表，EVENT_TIME是计划时间，不再重复添加默认排除条件。'),
                'identity_fields':['FLIGHT_KEY','PLAN_TIME','IS_OFF_IN'],
                'bindings':{'code':'FLIGHT_KEY','time':'EVENT_TIME','direction':'IS_OFF_IN'},
                'attributes':{'FLIGHT_KEY':{'label':'航班号'},'EVENT_TIME':{'label':'实际起降时间' if executed else '计划时间'},
                              'IS_OFF_IN':{'label':'进出港','enums':{'A':'进港','D':'出港'}}}}
            ontology['offline_queries'] += [
                {'subject':qualified,'intent':'count','aliases':[label+'量',label+'数量',label+'数'] + (['航班吞吐量'] if executed else [])},
                {'subject':qualified,'intent':'detail','aliases':[label+'明细'] + (['航班明细','航班信息'] if not executed else [])}]
            ontology['logical_links'].append({'source':qualified+'.FLIGHT_ID','target':'ACDM.TB_DYN_FLIGHT.FLIGHT_ID','note':'按原系统 FLIGHT_ID 关联实际动态'})
            ontology['relations'][qualified] = {'ACDM.TB_DYN_FLIGHT':{'name':'实际航班','aliases':['实际航班']}}
        for key, rule in rules['_LEFT_JOIN_TEMPLATE'].items():
            for match in re.finditer(r'(f[12])\.([A-Z_0-9]+)\s*=\s*(\w+)\.([A-Z_0-9]+)', rule):
                parent = 'FIMS.TB_DYN_FLIGHT' if match[1] == 'f1' else 'ACDM.TB_DYN_FLIGHT'
                table_match = re.search(r'JOIN\s+(\w+\.\w+)\s+' + match[3] + r'\b', rule)
                if table_match and table_match[1] in ontology['entities']:
                    ontology['logical_links'].append({'source':parent+'.'+match[2],'target':table_match[1]+'.'+match[4],'note':key})
        conn.commit()
    finally:
        conn.close()
    directory = DATA / database
    directory.mkdir()
    (directory / 'ontology.json').write_text(json.dumps(ontology,ensure_ascii=False,indent=2),encoding='utf-8')
    profile = {'database':{'database':database},'server':{'host':'127.0.0.1'},
               'files':{'ontology':str((directory/'ontology.json').relative_to(ROOT)),
                        'memory':str((directory/'memory.json').relative_to(ROOT)),
                        'schema':str((directory/'runtime/schema.json').relative_to(ROOT)),
                        'graph':str((directory/'runtime/graph.json').relative_to(ROOT))},
               'metadata':{'schemas':['FIMS','ACDM','FORMFILL','TEST'],'excluded_prefixes':[],'excluded_suffixes':[]},
               'admin':{'directory':str((directory/'admin').relative_to(ROOT))}}
    previous = DATA/'profile.json'
    if previous.exists():
        old_profile = json.loads(previous.read_text(encoding='utf-8'))
        if 'llm' in old_profile:
            profile['llm'] = old_profile['llm']
    (directory/'memory.json').write_text('{"entries":[]}',encoding='utf-8')
    (DATA/'profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Ready: ' + database)


if __name__ == '__main__':
    prepare()
