"""Split merged Oracle flights and reload their source records without rebuilding the database."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json

from app import core, ops_governance
from app.admin.security import Vault
from app.config import settings
from app.sync import oracle_source, pg_sink, runner, transforms
from scripts.ops_ask.seed_acdm_handbook import METRICS

COUNT_METRICS = {'operated_flight_count', 'dep_flight_count', 'arr_flight_count'}


class Transaction:
    """Reuse sync sinks while committing the entire repair only after verification."""
    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return self.conn.cursor()

    def commit(self):
        pass

    def rollback(self):
        self.conn.rollback()


def rows(cur, sql, params=()):
    cur.execute(sql, params)
    return [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]


def publish_metrics():
    core.initialize()
    current = core.ontology_document()['document']['metrics']
    for definition in METRICS:
        code = definition['code']
        if code in COUNT_METRICS:
            metric = deepcopy(current[code])
            for key in ('date_field', 'required_fields', 'description'):
                metric[key] = definition[key]
            ops_governance.save_metric({'code': code, **metric})
    ops_governance.publish_catalog_to_ontology('system-flight-repair', '实际执行架次按实际起降自然日统计，保留同号多次运行')


def repair(apply=False):
    if settings.data_source != 'ops_ask':
        raise ValueError('仅支持ops_ask数据源')
    pg = pg_sink.connect_pg_write()
    oracle = None
    try:
        q = pg.cursor()
        q.execute("SET LOCAL lock_timeout='15s'")
        q.execute("SELECT pg_advisory_xact_lock(hashtext('airport:oracle-flight-ingestion'))")
        groups = rows(q, """SELECT c.id,c.airport_code,c.primary_source_id,
                   array_agg(DISTINCT sr.source_flight_id ORDER BY sr.source_flight_id) AS source_ids
            FROM public.std_flight_core c JOIN public.std_flight_source_ref sr ON sr.flight_id=c.id
            WHERE c.deleted_at IS NULL AND sr.source_system IN ('FIMS_ORACLE','ACDM_ORACLE')
              AND c.airport_code=%s
            GROUP BY c.id HAVING COUNT(DISTINCT sr.source_flight_id)>1 ORDER BY c.id""",
            (settings.oracle.get('airport_code') or 'WUH',))
        print(json.dumps({'merged_groups': len(groups), 'additional_occurrences': sum(len(g['source_ids']) - 1 for g in groups), 'apply': apply}), flush=True)
        if not apply:
            return
        if not groups:
            pg.rollback()
            publish_metrics()
            return
        ids = [g['id'] for g in groups]
        source_ids = sorted({sid for g in groups for sid in g['source_ids']})
        oracle = oracle_source.connect_oracle(settings.oracle)
        oracle.call_timeout = 60000
        oq = oracle.cursor()
        oq.execute('SET TRANSACTION READ ONLY')
        drafts = {}
        for source, acdm in [('fims', False), ('acdm', True)]:
            system = settings.sync_service.get('source_system_' + source) or source.upper() + '_ORACLE'
            raw = [r for batch in oracle_source.iter_flight_batches(oracle, settings.oracle, source,
                   mode='BY_SOURCE_ID', source_flight_ids=source_ids) for r in batch]
            mapped = [transforms.map_flight_draft(r, system, acdm,
                      airport_code=settings.oracle.get('airport_code') or 'WUH') for r in raw]
            got = {d['primary_source_id'] for d in mapped if d is not None}
            if any(d is None for d in mapped) or len(got) != len(mapped) or (acdm and got != set(source_ids)):
                raise ValueError('Oracle源记录缺失、重复或无法转换，已取消修复')
            drafts[source] = mapped

        snapshot = {'captured_at': datetime.now(timezone.utc).isoformat(), 'groups': groups, 'tables': {}}
        for table in ['std_flight_core', 'std_flight_source_ref', 'std_flight_indicator', 'std_flight_process',
                      'std_flight_production', 'std_flight_calc_total', 'std_flight_air_bridge',
                      'std_flight_safeguard_node', 'std_flight_resource', 'std_flight_link']:
            condition = 'id=ANY(%s)' if table == 'std_flight_core' else 'flight_id=ANY(%s)'
            params = (ids,)
            if table == 'std_flight_link':
                condition += ' OR linked_flight_id=ANY(%s)'
                params = (ids, ids)
            snapshot['tables'][table] = rows(q, f'SELECT * FROM public.{table} WHERE {condition}', params)
        vault = Vault(settings.admin_directory)
        directory = settings.admin_directory / 'backups'
        directory.mkdir(exist_ok=True)
        backup = directory / ('flight-occurrences-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json.enc')
        content = json.dumps(snapshot, ensure_ascii=False, default=str).encode()
        backup.write_bytes(vault.cipher.encrypt(content))
        if vault.cipher.decrypt(backup.read_bytes()) != content:
            raise ValueError('备份校验失败，已取消修复')
        print('verified_backup ' + str(backup), flush=True)

        fims = {d['primary_source_id']: d for d in drafts['fims']}
        acdm = {d['primary_source_id']: d for d in drafts['acdm']}
        columns = [c.strip() for c in pg_sink._CORE_INSERT_COLS.split(',')][:-2]
        for group in groups:
            if group['primary_source_id'] not in group['source_ids']:
                raise ValueError('原主源ID不在关联源记录中，已取消自动修复')
            draft = fims.get(group['primary_source_id']) or acdm[group['primary_source_id']]
            values = pg_sink._flight_core_insert_row(draft, draft['terminal'])
            q.execute('UPDATE public.std_flight_core SET ' + ','.join(c + '=%s' for c in columns)
                      + ',is_link_turn=false,updated_at=now() WHERE id=%s', (*values, group['id']))
        for table in ['std_flight_source_ref', 'std_flight_indicator', 'std_flight_process']:
            q.execute(f'DELETE FROM public.{table} WHERE flight_id=ANY(%s)', (ids,))
        transaction = Transaction(pg)
        for source in ('fims', 'acdm'):
            counts = pg_sink.upsert_flight_batch(transaction, drafts[source])
            if counts['failed'] or counts['skipped']:
                raise ValueError('源记录重新入库失败：' + str(counts))
            print(source + ' ' + json.dumps(counts), flush=True)
        for table in ['std_flight_calc_total', 'std_flight_air_bridge', 'std_flight_safeguard_node']:
            q.execute(f"""UPDATE public.{table} t SET flight_id=sr.flight_id
                FROM public.std_flight_source_ref sr
                WHERE sr.source_system='ACDM_ORACLE' AND sr.source_flight_id=t.source_flight_id::text
                  AND sr.source_flight_id=ANY(%s)""", (source_ids,))
        lookup = {}
        candidates = rows(q, """SELECT c.id,c.airport_code,(c.flight_date AT TIME ZONE 'Asia/Shanghai')::date AS flight_date,
                  c.direction,c.flight_no,c.aircraft_reg,to_char((CASE WHEN c.direction='DEP' THEN c.actual_departure ELSE c.actual_arrival END)
                  AT TIME ZONE 'Asia/Shanghai','HH24:MI') AS clock,c.origin_airport || '-' || c.dest_airport AS leg
            FROM public.std_flight_core c WHERE c.deleted_at IS NULL AND c.primary_source_id=ANY(%s)""", (source_ids,))
        for r in candidates:
            key = (r['airport_code'], r['flight_date'], r['direction'], r['flight_no'])
            lookup.setdefault(key, []).append((r['id'], r['aircraft_reg'], r['clock'], r['leg']))
        for production in snapshot['tables']['std_flight_production']:
            fid = pg_sink._production_flight_id(production, lookup)
            if fid is None:
                raise ValueError('生产记录无法唯一归属，已回滚：' + str(production['id']))
            q.execute('UPDATE public.std_flight_production SET flight_id=%s,updated_at=now() WHERE id=%s', (fid, production['id']))
        runner._refresh_fims_codeshares(transaction, oracle, settings.oracle, settings.sync_service, source_ids)
        pg_sink.sync_turnaround_links(transaction)
        q.execute("""SELECT COUNT(*) FROM public.std_flight_source_ref sr JOIN public.std_flight_core c ON c.id=sr.flight_id
                  WHERE sr.source_flight_id=ANY(%s) AND c.primary_source_id<>sr.source_flight_id""", (source_ids,))
        if q.fetchone()[0]:
            raise ValueError('仍有跨源航班ID合并，已回滚')
        q.execute("SELECT is_off_in,COUNT(*) FROM public.mv_acdm_dyn_flight_detail WHERE real_exec_day=DATE '2026-11-09' AND is_operated_flight='Y' GROUP BY is_off_in ORDER BY is_off_in")
        result = dict(q.fetchall())
        if result != {'A': 256, 'D': 250}:
            raise ValueError('11月9日执行量核验未通过，已回滚：' + str(result))
        pg.commit()
        print('committed ' + json.dumps(result), flush=True)
        publish_metrics()
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()
        if oracle:
            oracle.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='备份后执行修复；默认只查看受影响范围')
    repair(parser.parse_args().apply)
