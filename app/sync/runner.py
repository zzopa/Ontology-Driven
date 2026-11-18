"""Execution runners for Oracle-to-PostgreSQL sync jobs (ported from airport-ops-data-sync)."""
from datetime import datetime, timedelta, timezone
import json
from time import monotonic

from app.sync import oracle_source, pg_sink
from app.sync.transforms import (
    DEFAULT_ARR_VALUES, DEFAULT_DEP_VALUES,
    map_air_bridge_draft, map_calc_total_draft, map_codeshare_draft,
    map_flight_draft, map_production_draft, map_safeguard_node_draft,
    split_csv_tokens,
)


def _mapping_sets(sync_cfg):
    dep = split_csv_tokens(sync_cfg.get('dep_off_in_values')) or DEFAULT_DEP_VALUES
    arr = split_csv_tokens(sync_cfg.get('arr_off_in_values')) or DEFAULT_ARR_VALUES
    return dep, arr


def _flight_job_name(source_type):
    low = str(source_type or '').lower()
    if 'fims' in low:
        return 'flight_dyn_sync_fims'
    if 'acdm' in low:
        return 'flight_dyn_sync_acdm'
    return 'flight_dyn_sync'


def run_master_data_sync(pg_conn, oracle_conn, oracle_cfg, *, on_progress=None, cancel_event=None):
    """Sync all FIMS master dimension tables into PG dim_*."""
    started = monotonic()
    pg_sink.ensure_acdm_schema_extensions(pg_conn)
    airport = str(oracle_cfg.get('airport_code') or 'WUH').strip().upper()
    steps_spec = [
        ('airline', '航司', lambda: pg_sink.upsert_airlines(pg_conn, oracle_source.fetch_master_rows(
            oracle_conn,
            'SELECT TWO_CHAR_CD, THREE_CHAR_CD, ATTRIBUTE, CHINESE_NAME, ENGLISH_NAME, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_AIRLINE',
        ))),
        ('airport', '机场', lambda: pg_sink.upsert_airports(
            pg_conn,
            oracle_source.fetch_master_rows(
                oracle_conn,
                'SELECT AIRPORT_IATA, AIRPORT_ICAO, CHINESE_NAME, ENGLISH_NAME, PURPOSE, IS_OPEN, ATTRIBUTE, '
                'IN_COUNTRY_CD, IN_COUNTRY_NAME, IN_CITY_CD, IN_CITY_NAME, IN_PROVINCE_CD, IN_PROVINCE_NAME, '
                'LONGITUDE, LATITUDE, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_AIRPORT_POSITION',
            ),
            oracle_source.fetch_master_rows(
                oracle_conn,
                'SELECT AIRPORT_IATA, AIRPORT_ICAO, ATTRIBUTE, CHINESE_NAME, ENGLISH_NAME, IS_OPEN, SHORT_NAME, '
                'CREATE_TIME, UPDATE_TIME, IS_DELETE, IN_CITY FROM FIMS.TB_AIRPORT',
            ),
        )),
        ('agency', '代理', lambda: pg_sink.upsert_agencies(pg_conn, oracle_source.fetch_master_rows(
            oracle_conn,
            'SELECT CODE, CHINESE_NAME, ENGLISH_NAME, DESCRIPTION, AGENT_SORT, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_AGENCY',
        ))),
        ('abnormalReason', '不正常原因', lambda: pg_sink.upsert_abnormal_reasons(pg_conn, oracle_source.fetch_master_rows(
            oracle_conn,
            'SELECT CODE, CHINESE_NAME, ENGLISH_NAME, TYPE, IS_SUPER, SUPER_CODE, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_ABNORMAL_REASON',
        ))),
        ('aircraft', '飞机', lambda: pg_sink.upsert_aircraft(pg_conn, oracle_source.fetch_master_rows(
            oracle_conn,
            'SELECT CODE, CRAFT_TYPE_ICAO_CD, AIRLINE_TWO_CHAR_CD, SEAT_MAX_NUMBER, SUPPORT_SEAT_NUMBER, '
            'AIRLINE_BRANCH, MAX_TAKEOFF_WEIGHT, MAX_LOAD_WEIGHT, SUPPORT_LOAD_WEIGHT, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_AIR_CRAFT',
        ))),
        ('stand', '机位', lambda: pg_sink.upsert_stands(pg_conn, oracle_source.fetch_master_rows(
            oracle_conn,
            'SELECT CODE, CHINESE_NAME, ENGLISH_NAME, IS_REMOTE, TERMINAL_CD, STATUS, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_CRAFT_SITE',
        ), airport)),
        ('gate', '登机口', lambda: pg_sink.upsert_gates(pg_conn, oracle_source.fetch_master_rows(
            oracle_conn,
            'SELECT ID, CODE, CHINESE_NAME, ENGLISH_NAME, ATTRIBUTE, TERMINAL_CD, STATUS, AGENCY, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_GATE',
        ), airport)),
        ('airBridge', '登机桥', lambda: pg_sink.upsert_air_bridges(pg_conn, oracle_source.fetch_master_rows(
            oracle_conn,
            'SELECT ID, CODE, CHINESE_NAME, ENGLISH_NAME, ATTRIBUTE, TERMINAL_CD, STATUS, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_AIR_BIRDGE',
        ), airport)),
        ('safeguardNode', '保障节点定义', lambda: pg_sink.upsert_safeguard_nodes(pg_conn, oracle_source.fetch_master_rows(
            oracle_conn,
            'SELECT CODE, NAME, IS_OFF_IN, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_SAFEGUARD_NODE',
        ))),
        ('runway', '跑道基础', lambda: pg_sink.upsert_runways(pg_conn, oracle_source.fetch_master_rows(
            oracle_conn,
            'SELECT RUNWAY_NO AS CODE, MEMO AS NAME, STATE AS ATTRIBUTE, DIRECTION, LENGTH, WIDTH, CREATE_TIME, UPDATE_TIME, IS_DELETE FROM FIMS.TB_RUNWAY',
        ))),
    ]

    dimensions = {}
    upserted_total = 0
    failed = 0
    for idx, (key, label, fn) in enumerate(steps_spec, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise oracle_source.SyncCancelledError('同步任务已被管理员中止')
        try:
            res = fn()
            dimensions[key] = res
            upserted_total += int(res.get('upserted') or 0)
        except Exception as exc:
            failed += 1
            dimensions[key] = {'error': str(exc)[:200]}
        if on_progress:
            on_progress({
                'fetched': upserted_total, 'inserted': 0, 'updated': upserted_total,
                'skipped': 0, 'failed': failed,
                'detail_msg': f'基础资料 [{idx}/{len(steps_spec)}] 已更新 {label}（累计 {upserted_total:,} 条）',
            })

    elapsed_ms = int((monotonic() - started) * 1000)
    watermark_now = datetime.now(timezone.utc)
    payload = {**dimensions, 'elapsedMs': elapsed_ms}
    pg_sink.save_watermark(pg_conn, 'master_data_sync', watermark_now, json.dumps(payload, ensure_ascii=False))
    return {
        'mode': 'FULL',
        'fetched': upserted_total,
        'inserted': 0,
        'updated': upserted_total,
        'skipped': 0,
        'failed': failed,
        'elapsed_ms': elapsed_ms,
        'watermark_before': '',
        'watermark_after': watermark_now.isoformat(),
        'dimensions': dimensions,
        'message': f'基础资料更新 {upserted_total} 条（{len(steps_spec)} 类维表）',
    }


def _refresh_fims_codeshares(pg_conn, oracle_conn, oracle_cfg, sync_cfg, fims_source_ids, cancel_event=None):
    if not fims_source_ids:
        return 0
    ids = list(dict.fromkeys(fims_source_ids))
    prefer_sys = sync_cfg.get('source_system_fims', 'FIMS_ORACLE')
    alt_sys = sync_cfg.get('source_system_acdm', 'ACDM_ORACLE')
    chunk_size = int(sync_cfg.get('batch_size', 500))
    total_written = 0
    for idx in range(0, len(ids), chunk_size):
        if cancel_event is not None and cancel_event.is_set():
            raise oracle_source.SyncCancelledError('同步任务已被管理员中止')
        chunk = ids[idx:idx + chunk_size]
        raw_rows = oracle_source.fetch_share_by_source_flight_ids(oracle_conn, oracle_cfg, chunk)
        by_source = {sid: set() for sid in chunk}
        for row in raw_rows:
            draft = map_codeshare_draft(row)
            if draft:
                by_source.setdefault(draft['source_flight_id'], set()).add(draft['share_flight_identity'])
        counts = pg_sink.replace_codeshares_by_source(
            pg_conn, by_source, prefer_system=prefer_sys, alt_system=alt_sys,
        )
        total_written += counts['links_written']
    return total_written


def run_flight_sync(pg_conn, oracle_conn, oracle_cfg, sync_cfg, source_type, *,
                    mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                    updated_since=None, on_progress=None, cancel_event=None):
    """Stream FIMS or ACDM flights from Oracle and upsert into PG standard tables."""
    started = monotonic()
    pg_sink.ensure_acdm_schema_extensions(pg_conn)
    acdm = 'acdm' in str(source_type).lower()
    job_name = _flight_job_name(source_type)
    airport = str(oracle_cfg.get('airport_code') or 'WUH').strip().upper()
    source_system = sync_cfg.get('source_system_acdm' if acdm else 'source_system_fims')
    dep_vals, arr_vals = _mapping_sets(sync_cfg)

    wm_before = pg_sink.get_watermark(pg_conn, job_name)
    effective_since = updated_since
    if mode == 'INCREMENTAL' and effective_since is None:
        effective_since = wm_before or (datetime.now(timezone.utc) - timedelta(minutes=int(sync_cfg.get('lookback_minutes', 15))))

    gate_terminals, stand_terminals = pg_sink.load_terminal_lookups(pg_conn, airport)
    fetch_size = int(sync_cfg.get('fetch_size', 1000))
    batch_size = int(sync_cfg.get('batch_size', 500))
    sync_raw_acdm = bool(acdm and pg_sink.table_exists(pg_conn, 'acdm', 'tb_dyn_flight'))

    fetched = inserted = updated = skipped = failed = 0
    max_source_updated = None
    fims_source_ids = []

    for raw_rows in oracle_source.iter_flight_batches(
        oracle_conn, oracle_cfg, source_type, mode=mode,
        exec_date_from=exec_date_from, exec_date_to=exec_date_to,
        updated_since=effective_since, fetch_size=fetch_size,
        batch_size=batch_size, cancel_event=cancel_event,
    ):
        drafts = []
        for row in raw_rows:
            draft = map_flight_draft(row, source_system, acdm, airport_code=airport, dep_values=dep_vals, arr_values=arr_vals)
            if draft is None:
                raise ValueError('航班源记录缺少身份、日期或方向，水位未推进')
            drafts.append(draft)
            src_upd = draft['source_updated_at']
            if src_upd and (max_source_updated is None or src_upd > max_source_updated):
                max_source_updated = src_upd
            if not acdm and draft.get('primary_source_id'):
                fims_source_ids.append(draft['primary_source_id'])

        fetched += len(drafts)
        atomic = pg_sink.AtomicConnection(pg_conn)
        try:
            counts = pg_sink.upsert_flight_batch(
                atomic, drafts, gate_terminals=gate_terminals, stand_terminals=stand_terminals,
            )
            if counts['failed'] or counts['skipped']:
                raise ValueError('航班批次未完整写入，已回滚该批次，水位未推进')
            if sync_raw_acdm and raw_rows:
                if pg_sink.upsert_acdm_raw_batch(atomic, raw_rows) != len({r['FLIGHT_ID'] for r in raw_rows}):
                    raise ValueError('ACDM原始记录未完整写入，已回滚该批次')
            pg_conn.commit()
        except Exception:
            pg_conn.rollback()
            raise
        inserted += counts['inserted']
        updated += counts['updated']
        skipped += counts['skipped']
        failed += counts['failed']
        if on_progress:
            on_progress({
                'fetched': fetched, 'inserted': inserted, 'updated': updated,
                'skipped': skipped, 'failed': failed,
                'detail_msg': f'{source_type} 已处理 {fetched:,} 条（新增 {inserted:,} / 更新 {updated:,}）',
            })

    codeshare_links = 0
    turnaround_links = 0
    if not acdm and fims_source_ids:
        codeshare_links = _refresh_fims_codeshares(
            pg_conn, oracle_conn, oracle_cfg, sync_cfg, fims_source_ids, cancel_event=cancel_event,
        )
    if acdm:
        t_res = pg_sink.sync_turnaround_links(pg_conn, exec_date_from=exec_date_from, exec_date_to=exec_date_to)
        turnaround_links = t_res.get('turnaround_links', 0)
        pg_sink.refresh_ops_views(pg_conn)

    wm_after = max_source_updated or wm_before
    if mode == 'INCREMENTAL' and wm_after is not None:
        wm_after = wm_after - timedelta(seconds=1)

    elapsed_ms = int((monotonic() - started) * 1000)
    message = (
        f'ok:{source_type}'
        + (f';codeshareLinks={codeshare_links}' if codeshare_links > 0 else '')
        + (f';turnaroundLinks={turnaround_links}' if turnaround_links > 0 else '')
    )
    wm_before_iso = wm_before.isoformat() if wm_before else None
    wm_after_iso = wm_after.isoformat() if wm_after else None

    result_obj = {
        'mode': mode,
        'fetched': fetched,
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
        'failed': failed,
        'watermarkBefore': wm_before_iso,
        'watermarkAfter': wm_after_iso,
        'message': message,
        'elapsedMs': elapsed_ms,
    }
    pg_sink.save_watermark(
        pg_conn, job_name, wm_after if mode == 'INCREMENTAL' else wm_before, result_obj,
    )
    return {
        'mode': mode,
        'fetched': fetched,
        'inserted': inserted,
        'updated': updated,
        'skipped': skipped,
        'failed': failed,
        'watermark_before': wm_before_iso or '',
        'watermark_after': wm_after_iso or '',
        'elapsed_ms': elapsed_ms,
        'message': message,
    }


def run_codeshare_sync(pg_conn, oracle_conn, oracle_cfg, sync_cfg, *,
                       mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                       on_progress=None, cancel_event=None):
    """Stream FIMS codeshares and replace CODESHARE links per operational flight."""
    started = monotonic()
    fetch_size = int(sync_cfg.get('fetch_size', 1000))
    batch_size = int(sync_cfg.get('batch_size', 500))
    prefer_sys = sync_cfg.get('source_system_fims', 'FIMS_ORACLE')
    alt_sys = sync_cfg.get('source_system_acdm', 'ACDM_ORACLE')

    by_source = {}
    fetched = 0
    for raw_rows in oracle_source.iter_share_by_exec_date(
        oracle_conn, oracle_cfg, mode=mode,
        exec_date_from=exec_date_from, exec_date_to=exec_date_to,
        fetch_size=fetch_size, batch_size=batch_size, cancel_event=cancel_event,
    ):
        for row in raw_rows:
            draft = map_codeshare_draft(row)
            if not draft:
                continue
            by_source.setdefault(draft['source_flight_id'], set()).add(draft['share_flight_identity'])
            fetched += 1
        if on_progress:
            on_progress({
                'fetched': fetched, 'inserted': 0, 'updated': 0, 'skipped': 0, 'failed': 0,
                'detail_msg': f'共享航班已抽取 {fetched:,} 条（{len(by_source):,} 个运营航班）',
            })

    written = touched = skipped_no_flight = failed = 0
    source_keys = list(by_source.keys())
    for idx in range(0, len(source_keys), batch_size):
        if cancel_event is not None and cancel_event.is_set():
            raise oracle_source.SyncCancelledError('同步任务已被管理员中止')
        chunk_map = {k: by_source[k] for k in source_keys[idx:idx + batch_size]}
        counts = pg_sink.replace_codeshares_by_source(pg_conn, chunk_map, prefer_system=prefer_sys, alt_system=alt_sys)
        written += counts['links_written']
        touched += counts['flights_touched']
        skipped_no_flight += counts['skipped_no_flight']
        failed += counts['failed']
        if on_progress:
            on_progress({
                'fetched': fetched, 'inserted': written, 'updated': touched,
                'skipped': skipped_no_flight, 'failed': failed,
                'detail_msg': f'共享航班已写入 {written:,} 条关系（跳过未入库主航班 {skipped_no_flight:,}）',
            })

    elapsed_ms = int((monotonic() - started) * 1000)
    result_obj = {
        'mode': mode,
        'fetched': fetched,
        'inserted': written,
        'updated': touched,
        'skipped': skipped_no_flight,
        'failed': failed,
        'watermarkBefore': None,
        'watermarkAfter': None,
        'message': 'ok:codeshare',
        'elapsedMs': elapsed_ms,
    }
    pg_sink.save_watermark(pg_conn, 'flight_codeshare_sync_fims', None, result_obj)
    return {
        'mode': mode,
        'fetched': fetched,
        'inserted': written,
        'updated': touched,
        'skipped': skipped_no_flight,
        'failed': failed,
        'watermark_before': '',
        'watermark_after': '',
        'elapsed_ms': elapsed_ms,
        'message': 'ok:codeshare',
    }


def run_production_sync(pg_conn, oracle_conn, oracle_cfg, sync_cfg, *,
                        mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                        updated_since=None, on_progress=None, cancel_event=None):
    """Stream FORMFILL production analysis rows and upsert into std_flight_production."""
    started = monotonic()
    airport = str(oracle_cfg.get('airport_code') or 'WUH').strip().upper()
    source_system = sync_cfg.get('source_system_production', 'FORMFILL_ORACLE')
    dep_vals, arr_vals = _mapping_sets(sync_cfg)
    wm_before = pg_sink.get_watermark(pg_conn, 'flight_production_sync')

    effective_since = updated_since
    if mode == 'INCREMENTAL' and effective_since is None:
        effective_since = wm_before or (datetime.now(timezone.utc) - timedelta(minutes=int(sync_cfg.get('lookback_minutes', 15))))

    fetch_size = int(sync_cfg.get('fetch_size', 1000))
    batch_size = int(sync_cfg.get('batch_size', 500))
    fetched = inserted = updated = failed = 0
    max_source_updated = None
    source_ids = set()
    atomic = pg_sink.AtomicConnection(pg_conn)

    try:
        for raw_rows in oracle_source.iter_production_batches(
            oracle_conn, oracle_cfg, mode=mode,
            exec_date_from=exec_date_from, exec_date_to=exec_date_to,
            updated_since=effective_since, fetch_size=fetch_size,
            batch_size=batch_size, cancel_event=cancel_event,
        ):
            drafts = []
            for row in raw_rows:
                draft = map_production_draft(
                    row, airport_code=airport, source_system=source_system,
                    dep_values=dep_vals, arr_values=arr_vals,
                )
                if draft is None:
                    raise ValueError('生产源记录缺少身份、日期或方向，已取消发布该批次')
                drafts.append(draft)
                source_ids.add(draft['primary_source_id'])
                src_upd = draft['source_updated_at']
                if src_upd and (max_source_updated is None or src_upd > max_source_updated):
                    max_source_updated = src_upd

            fetched += len(drafts)
            counts = pg_sink.upsert_production_batch(atomic, drafts)
            inserted += counts['inserted']
            updated += counts['updated']
            failed += counts['failed']
            if counts['failed']:
                raise ValueError('生产记录写入失败，已回滚整轮源数据更新')
            if on_progress:
                on_progress({
                    'fetched': fetched, 'inserted': inserted, 'updated': updated,
                    'skipped': 0, 'failed': failed,
                    'detail_msg': f'生产量已处理 {fetched:,} 条（新增 {inserted:,} / 更新 {updated:,}）',
                })

        archived = 0
        if mode in ('BY_EXEC_DATE', 'FULL') and exec_date_from is not None and exec_date_to is not None:
            archived = pg_sink.reconcile_production_scope(atomic, airport, source_system, exec_date_from, exec_date_to, source_ids)

        wm_after = max_source_updated or wm_before
        if mode == 'INCREMENTAL' and wm_after is not None:
            wm_after = wm_after - timedelta(seconds=1)

        elapsed_ms = int((monotonic() - started) * 1000)
        wm_before_iso = wm_before.isoformat() if wm_before else None
        wm_after_iso = wm_after.isoformat() if wm_after else None
        result_obj = {
            'mode': mode,
            'fetched': fetched,
            'inserted': inserted,
            'updated': updated,
            'skipped': 0,
            'failed': failed,
            'watermarkBefore': wm_before_iso,
            'watermarkAfter': wm_after_iso,
            'message': f'production ok;source=FORMFILL.FLIGHT_PRODUCTION_ANALYSIS;archived={archived}',
            'elapsedMs': elapsed_ms,
        }
        pg_sink.save_watermark(
            atomic, 'flight_production_sync', wm_after if mode == 'INCREMENTAL' or wm_before is None else wm_before, result_obj,
        )
        pg_sink.refresh_ops_views(atomic)
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    return {
        'mode': mode,
        'fetched': fetched,
        'inserted': inserted,
        'updated': updated,
        'skipped': 0,
        'failed': failed,
        'watermark_before': wm_before_iso or '',
        'watermark_after': wm_after_iso or '',
        'elapsed_ms': elapsed_ms,
        'message': f'production ok;source=FORMFILL.FLIGHT_PRODUCTION_ANALYSIS;archived={archived}',
    }


def run_calc_total_sync(pg_conn, oracle_conn, oracle_cfg, sync_cfg, *,
                        mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                        updated_since=None, on_progress=None, cancel_event=None):
    """Stream FIMS.TBH_FIMS_DYN_CALC_TOTAL into public.std_flight_calc_total."""
    started = monotonic()
    pg_sink.ensure_acdm_schema_extensions(pg_conn)
    dep_vals, arr_vals = _mapping_sets(sync_cfg)
    wm_before = pg_sink.get_watermark(pg_conn, 'flight_calc_total_sync')
    effective_since = updated_since
    if mode == 'INCREMENTAL' and effective_since is None:
        effective_since = wm_before or (datetime.now(timezone.utc) - timedelta(minutes=int(sync_cfg.get('lookback_minutes', 15))))

    fetch_size = int(sync_cfg.get('fetch_size', 1000))
    batch_size = int(sync_cfg.get('batch_size', 500))
    fetched = inserted = updated = failed = 0
    max_source_updated = None

    for raw_rows in oracle_source.iter_calc_total_batches(
        oracle_conn, oracle_cfg, mode=mode,
        exec_date_from=exec_date_from, exec_date_to=exec_date_to,
        updated_since=effective_since, fetch_size=fetch_size,
        batch_size=batch_size, cancel_event=cancel_event,
    ):
        drafts = []
        for row in raw_rows:
            draft = map_calc_total_draft(row, dep_values=dep_vals, arr_values=arr_vals)
            if draft is None:
                continue
            drafts.append(draft)
            src_upd = draft.get('source_updated_at')
            if src_upd and (max_source_updated is None or src_upd > max_source_updated):
                max_source_updated = src_upd

        fetched += len(drafts)
        counts = pg_sink.upsert_calc_total_batch(pg_conn, drafts)
        inserted += counts['inserted']
        updated += counts['updated']
        failed += counts['failed']
        if on_progress:
            on_progress({
                'fetched': fetched, 'inserted': inserted, 'updated': updated,
                'skipped': 0, 'failed': failed,
                'detail_msg': f'动态汇总旅客已处理 {fetched:,} 条（新增 {inserted:,} / 更新 {updated:,}）',
            })

    wm_after = max_source_updated or wm_before
    if mode == 'INCREMENTAL' and wm_after is not None:
        wm_after = wm_after - timedelta(seconds=1)
    elapsed_ms = int((monotonic() - started) * 1000)
    wm_before_iso = wm_before.isoformat() if wm_before else None
    wm_after_iso = wm_after.isoformat() if wm_after else None
    result_obj = {
        'mode': mode, 'fetched': fetched, 'inserted': inserted, 'updated': updated,
        'skipped': 0, 'failed': failed, 'watermarkBefore': wm_before_iso,
        'watermarkAfter': wm_after_iso, 'message': 'calc_total ok', 'elapsedMs': elapsed_ms,
    }
    pg_sink.save_watermark(pg_conn, 'flight_calc_total_sync', wm_after if mode == 'INCREMENTAL' else wm_before, result_obj)
    pg_sink.refresh_ops_views(pg_conn)
    return {
        'mode': mode, 'fetched': fetched, 'inserted': inserted, 'updated': updated,
        'skipped': 0, 'failed': failed, 'watermark_before': wm_before_iso or '',
        'watermark_after': wm_after_iso or '', 'elapsed_ms': elapsed_ms, 'message': 'calc_total ok',
    }


def run_safeguard_sync(pg_conn, oracle_conn, oracle_cfg, sync_cfg, *,
                       mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                       updated_since=None, on_progress=None, cancel_event=None):
    """Stream FIMS.TB_DYN_AIR_BRIDGE and FIMS.TB_DYN_SAFEGUARD_NODE into PG."""
    started = monotonic()
    pg_sink.ensure_acdm_schema_extensions(pg_conn)
    wm_before = pg_sink.get_watermark(pg_conn, 'flight_safeguard_sync')
    effective_since = updated_since
    if mode == 'INCREMENTAL' and effective_since is None:
        effective_since = wm_before or (datetime.now(timezone.utc) - timedelta(minutes=int(sync_cfg.get('lookback_minutes', 15))))

    fetch_size = int(sync_cfg.get('fetch_size', 1000))
    batch_size = int(sync_cfg.get('batch_size', 500))
    fetched = inserted = updated = failed = 0
    max_source_updated = None

    for raw_rows in oracle_source.iter_air_bridge_batches(
        oracle_conn, oracle_cfg, mode=mode,
        exec_date_from=exec_date_from, exec_date_to=exec_date_to,
        updated_since=effective_since, fetch_size=fetch_size,
        batch_size=batch_size, cancel_event=cancel_event,
    ):
        drafts = [d for d in (map_air_bridge_draft(r) for r in raw_rows) if d is not None]
        for d in drafts:
            su = d.get('source_updated_at')
            if su and (max_source_updated is None or su > max_source_updated):
                max_source_updated = su
        fetched += len(drafts)
        c = pg_sink.upsert_flight_air_bridge_batch(pg_conn, drafts)
        inserted += c['inserted']
        updated += c['updated']
        failed += c['failed']
        if on_progress:
            on_progress({
                'fetched': fetched, 'inserted': inserted, 'updated': updated,
                'skipped': 0, 'failed': failed,
                'detail_msg': f'登机桥动态已处理 {fetched:,} 条',
            })

    for raw_rows in oracle_source.iter_safeguard_node_batches(
        oracle_conn, oracle_cfg, mode=mode,
        exec_date_from=exec_date_from, exec_date_to=exec_date_to,
        updated_since=effective_since, fetch_size=fetch_size,
        batch_size=batch_size, cancel_event=cancel_event,
    ):
        drafts = [d for d in (map_safeguard_node_draft(r) for r in raw_rows) if d is not None]
        for d in drafts:
            su = d.get('source_updated_at')
            if su and (max_source_updated is None or su > max_source_updated):
                max_source_updated = su
        fetched += len(drafts)
        c = pg_sink.upsert_safeguard_node_batch(pg_conn, drafts)
        inserted += c['inserted']
        updated += c['updated']
        failed += c['failed']
        if on_progress:
            on_progress({
                'fetched': fetched, 'inserted': inserted, 'updated': updated,
                'skipped': 0, 'failed': failed,
                'detail_msg': f'保障节点与登机桥已累计 {fetched:,} 条',
            })

    wm_after = max_source_updated or wm_before
    if mode == 'INCREMENTAL' and wm_after is not None:
        wm_after = wm_after - timedelta(seconds=1)
    elapsed_ms = int((monotonic() - started) * 1000)
    wm_before_iso = wm_before.isoformat() if wm_before else None
    wm_after_iso = wm_after.isoformat() if wm_after else None
    result_obj = {
        'mode': mode, 'fetched': fetched, 'inserted': inserted, 'updated': updated,
        'skipped': 0, 'failed': failed, 'watermarkBefore': wm_before_iso,
        'watermarkAfter': wm_after_iso, 'message': 'safeguard ok', 'elapsedMs': elapsed_ms,
    }
    pg_sink.save_watermark(pg_conn, 'flight_safeguard_sync', wm_after if mode == 'INCREMENTAL' else wm_before, result_obj)
    return {
        'mode': mode, 'fetched': fetched, 'inserted': inserted, 'updated': updated,
        'skipped': 0, 'failed': failed, 'watermark_before': wm_before_iso or '',
        'watermark_after': wm_after_iso or '', 'elapsed_ms': elapsed_ms, 'message': 'safeguard ok',
    }
