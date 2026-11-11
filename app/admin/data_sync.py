"""Pure-Python Oracle-to-PostgreSQL data sync orchestration, status probes, and backup/ledger integration."""
from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
from threading import Event, Lock, Thread
from time import monotonic, perf_counter
from zoneinfo import ZoneInfo

from app import core
from app.config import settings
from app.sync import oracle_source, pg_sink, runner

logger = logging.getLogger(__name__)
SHANGHAI_TZ = timezone(timedelta(hours=8))
_CANCEL_LOCK = Lock()
_CANCEL_EVENTS: dict[str, Event] = {}
_SOURCE_CACHE_LOCK = Lock()
_SOURCE_COUNTS_CACHE: dict | None = None

JOB_DEFINITIONS = {
    'full_sync': {'title': '全量数据导入（近 1 个月全表）'},
    'master_data': {'title': '基础资料全量（dim_*）'},
    'flights_fims': {'title': 'FIMS 航班动态'},
    'flights_acdm': {'title': 'ACDM 航班动态'},
    'codeshares': {'title': 'FIMS 共享航班'},
    'production': {'title': '货邮行生产量（FORMFILL）'},
    'calc_total': {'title': '动态汇总旅客（FIMS_CALC_TOTAL）'},
    'safeguards': {'title': '登机桥与保障节点（FIMS）'},
    'flights_scheduled': {'title': '航班动态增量（FIMS+ACDM）'},
}

PG_JOB_MAP = {
    'flight_dyn_sync_fims': ('flights_fims', 'FIMS 航班动态'),
    'flight_dyn_sync_acdm': ('flights_acdm', 'ACDM 航班动态'),
    'flight_dyn_sync': ('flights_scheduled', '航班动态增量'),
    'flight_production_sync': ('production', '货邮行生产量（FORMFILL）'),
    'flight_calc_total_sync': ('calc_total', '动态汇总旅客（FIMS_CALC_TOTAL）'),
    'flight_safeguard_sync': ('safeguards', '登机桥与保障节点（FIMS）'),
    'master_data_sync': ('master_data', '基础资料全量（dim_*）'),
    'flight_codeshare_sync_fims': ('codeshares', 'FIMS 共享航班'),
}

FULL_LOOKBACK_DAYS = 30
FULL_FUTURE_DAYS = 7


def oracle_config():
    return (settings.oracle or {
        'host': '172.26.78.133',
        'port': 1521,
        'service_name': 'datacache',
        'user': '',
        'password': '',
        'airport_code': 'WUH',
        'acdm_schema': 'ACDM',
        'acdm_table': 'TB_DYN_FLIGHT',
        'fims_schema': 'FIMS',
        'fims_table': 'TB_DYN_FLIGHT',
        'fims_share_table': 'TB_DYN_SHARE_FLIGHTS',
    }).copy()


def sync_config():
    return (settings.sync_service or {
        'oracle_host': oracle_config().get('host', '172.26.78.133'),
        'oracle_port': int(oracle_config().get('port', 1521)),
        'fetch_size': 1000,
        'batch_size': 500,
        'lookback_minutes': 15,
        'dep_off_in_values': '1,D,O,出',
        'arr_off_in_values': '0,A,I,进',
        'source_system_acdm': 'ACDM_ORACLE',
        'source_system_fims': 'FIMS_ORACLE',
        'source_system_production': 'FORMFILL_ORACLE',
        'backup_container': 'lightrag-pgvector',
        'backup_dir': str(Path(__file__).resolve().parents[2] / 'artifacts' / 'backups'),
    }).copy()


def _tcp_probe(host, port, timeout=1.5):
    started = perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {'reachable': True, 'host': host, 'port': int(port), 'latency_ms': round((perf_counter() - started) * 1000, 1)}
    except OSError as exc:
        return {'reachable': False, 'host': host, 'port': int(port), 'error': str(exc)[:120]}


def probe_oracle_status():
    """Return safe Oracle connection config summary, TCP reachability, and cached table stats."""
    ora = oracle_config()
    configured = bool(ora.get('host') and ora.get('service_name') and ora.get('user') and ora.get('password'))
    tcp = _tcp_probe(ora.get('host', ''), ora.get('port', 1521), timeout=1.5) if ora.get('host') else {'reachable': False, 'host': '', 'port': 1521}
    with _SOURCE_CACHE_LOCK:
        cached = _SOURCE_COUNTS_CACHE.copy() if _SOURCE_COUNTS_CACHE else None
    return {
        **tcp,
        'configured': configured,
        'service_name': ora.get('service_name', ''),
        'airport_code': ora.get('airport_code', 'WUH'),
        'source_stats': cached,
    }


def probe_source_counts():
    """Run an explicit Oracle COUNT/MIN/MAX probe and cache non-secret metrics."""
    global _SOURCE_COUNTS_CACHE
    ora = oracle_config()
    try:
        stats = oracle_source.probe_oracle_source(ora)
    except ValueError as exc:
        raise
    except Exception as exc:
        raise RuntimeError(f'连接 Oracle 源库失败：{str(exc)[:180]}') from exc
    payload = {**stats, 'checked_at': datetime.now(timezone.utc).isoformat()}
    with _SOURCE_CACHE_LOCK:
        _SOURCE_COUNTS_CACHE = payload
    return payload


def parse_master_data_result(raw):
    """Parse MasterDataSyncService dict or legacy Java Map.toString() output."""
    if isinstance(raw, dict):
        parsed = raw
    else:
        text_raw = str(raw or '').strip()
        if not text_raw:
            return {'fetched': 0, 'inserted': 0, 'updated': 0, 'skipped': 0, 'failed': 0, 'elapsed_ms': 0, 'dimensions': {}}
        try:
            parsed = json.loads(text_raw)
        except ValueError:
            parsed = {}
            for key, inner in re.findall(r'([a-zA-Z0-9_]+)=\{([^}]*)\}', text_raw):
                parsed[key] = {k: int(v) for k, v in re.findall(r'([a-zA-Z0-9_]+)=(-?\d+)', inner)}
            elapsed_match = re.search(r'\belapsedMs=(-?\d+)', text_raw)
            if elapsed_match:
                parsed['elapsedMs'] = int(elapsed_match.group(1))
    dimensions = {k: v for k, v in parsed.items() if isinstance(v, dict)}
    upserted = sum(int(item.get('upserted') or 0) for item in dimensions.values())
    return {
        'mode': 'FULL',
        'fetched': upserted,
        'inserted': 0,
        'updated': upserted,
        'skipped': 0,
        'failed': 0,
        'elapsed_ms': int(parsed.get('elapsedMs') or 0),
        'dimensions': dimensions,
        'message': f'基础资料更新 {upserted} 条（覆盖 {len(dimensions)} 类维表）',
    }


def parse_sync_result(raw, job_key=''):
    """Normalize sync_job_state result payloads into uniform metrics."""
    if job_key in ('master_data', 'master_data_sync'):
        return parse_master_data_result(raw)
    if isinstance(raw, dict):
        parsed = raw
    else:
        text_raw = str(raw or '').strip()
        if not text_raw:
            return {'fetched': 0, 'inserted': 0, 'updated': 0, 'skipped': 0, 'failed': 0, 'elapsed_ms': 0, 'message': ''}
        try:
            parsed = json.loads(text_raw)
        except ValueError:
            return parse_master_data_result(text_raw)
    return {
        'mode': str(parsed.get('mode') or 'INCREMENTAL'),
        'fetched': int(parsed.get('fetched') or 0),
        'inserted': int(parsed.get('inserted') or 0),
        'updated': int(parsed.get('updated') or 0),
        'skipped': int(parsed.get('skipped') or 0),
        'failed': int(parsed.get('failed') or 0),
        'watermark_before': parsed.get('watermarkBefore') or '',
        'watermark_after': parsed.get('watermarkAfter') or '',
        'elapsed_ms': int(parsed.get('elapsedMs') or 0),
        'message': str(parsed.get('message') or ''),
    }


def list_backups():
    backup_dir = Path(sync_config()['backup_dir'])
    if not backup_dir.is_dir():
        return []
    files = sorted(backup_dir.glob('*.dump'), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for path in files[:20]:
        stat = path.stat()
        result.append({
            'name': path.name,
            'size_bytes': stat.st_size,
            'modified_at': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return result


def create_backup(store, actor, actor_name):
    """Create a compressed pg_dump backup in artifacts/backups before sync."""
    cfg = sync_config()
    backup_dir = Path(cfg['backup_dir'])
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(SHANGHAI_TZ).strftime('%Y%m%d-%H%M%S')
    target = backup_dir / f"{settings.db['database']}-before-{stamp}.dump"
    temp = backup_dir / f".{target.name}.tmp"

    env = os.environ.copy()
    env['PGPASSWORD'] = settings.db.get('password', '')
    local_pg_dump = shutil.which('pg_dump')
    if local_pg_dump:
        cmd = [
            local_pg_dump, '-h', str(settings.db['host']), '-p', str(settings.db['port']),
            '-U', str(settings.db['user']), '-Fc', str(settings.db['database']),
        ]
    else:
        docker_exe = shutil.which('docker')
        container = cfg.get('backup_container') or ''
        if not docker_exe or not container:
            raise ValueError('未找到本地 pg_dump，且未配置可用的 Docker 容器用于备份')
        cmd = [
            docker_exe, 'exec', '-i', '-e', 'PGPASSWORD', container,
            'pg_dump', '-U', str(settings.db['user']), '-Fc', str(settings.db['database']),
        ]

    try:
        with temp.open('wb') as out:
            proc = subprocess.run(cmd, env=env, stdout=out, stderr=subprocess.PIPE, timeout=300)
        if proc.returncode != 0:
            temp.unlink(missing_ok=True)
            raise RuntimeError('数据库备份失败，请确认 PostgreSQL 容器与权限正常')
        if temp.stat().st_size == 0:
            temp.unlink(missing_ok=True)
            raise RuntimeError('数据库备份输出为空')
        temp.replace(target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise

    stat = target.stat()
    info = {
        'name': target.name,
        'size_bytes': stat.st_size,
        'modified_at': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }
    store.audit(actor, 'data_sync.backup', target.name, f'{stat.st_size} bytes')
    return info


def _scalar_row(cur, sql):
    cur.execute(sql)
    row = cur.fetchone()
    return row if row else None


def read_pg_snapshot():
    """Inspect PG sync_job_state watermarks and target table coverage read-only."""
    empty = {'available': False, 'database': settings.db.get('database', ''), 'job_states': [], 'tables': {}, 'dimensions': {}}
    conn = core.connect_db()
    try:
        if not hasattr(conn, 'cursor'):
            return empty
        cur = conn.cursor()
        try:
            row = _scalar_row(cur, "SELECT to_regclass('public.sync_job_state') IS NOT NULL")
            has_state_table = bool(row and row[0])
            job_states = []
            if has_state_table:
                cur.execute(
                    'SELECT job_name, watermark, last_success_at, last_result_json, updated_at '
                    'FROM public.sync_job_state ORDER BY job_name'
                )
                for job_name, watermark, last_success_at, last_result_json, updated_at in cur.fetchall():
                    mapped_key, title = PG_JOB_MAP.get(job_name, (job_name, job_name))
                    parsed = parse_sync_result(last_result_json, mapped_key)
                    finished = last_success_at or updated_at
                    finished_iso = finished.astimezone(timezone.utc).isoformat() if isinstance(finished, datetime) else (str(finished) if finished else '')
                    wm_iso = watermark.astimezone(timezone.utc).isoformat() if isinstance(watermark, datetime) else (str(watermark) if watermark else '')
                    started_iso = finished_iso
                    if isinstance(finished, datetime) and parsed.get('elapsed_ms', 0) > 0:
                        started_iso = (finished - timedelta(milliseconds=int(parsed['elapsed_ms']))).astimezone(timezone.utc).isoformat()
                    job_states.append({
                        'job_name': job_name,
                        'job_key': mapped_key,
                        'title': title,
                        'watermark': wm_iso,
                        'last_success_at': finished_iso,
                        'started_at': started_iso,
                        'parsed_result': parsed,
                    })

            tables = {}
            table_queries = {
                'std_flight_core': (
                    'public.std_flight_core',
                    "SELECT COUNT(*), to_char(MIN(flight_date AT TIME ZONE 'Asia/Shanghai'), 'YYYY-MM-DD HH24:MI'), "
                    "to_char(MAX(flight_date AT TIME ZONE 'Asia/Shanghai'), 'YYYY-MM-DD HH24:MI') FROM public.std_flight_core",
                ),
                'std_flight_production': (
                    'public.std_flight_production',
                    "SELECT COUNT(*), to_char(MIN(flight_date), 'YYYY-MM-DD'), "
                    "to_char(MAX(flight_date), 'YYYY-MM-DD') FROM public.std_flight_production WHERE deleted_at IS NULL",
                ),
                'std_flight_link': (
                    'public.std_flight_link',
                    'SELECT COUNT(*), NULL, NULL FROM public.std_flight_link',
                ),
                'std_flight_calc_total': (
                    'public.std_flight_calc_total',
                    "SELECT COUNT(*), to_char(MIN(exec_date), 'YYYY-MM-DD'), "
                    "to_char(MAX(exec_date), 'YYYY-MM-DD') FROM public.std_flight_calc_total",
                ),
                'std_flight_air_bridge': (
                    'public.std_flight_air_bridge',
                    'SELECT COUNT(*), NULL, NULL FROM public.std_flight_air_bridge',
                ),
                'std_flight_safeguard_node': (
                    'public.std_flight_safeguard_node',
                    'SELECT COUNT(*), NULL, NULL FROM public.std_flight_safeguard_node',
                ),
                'flight_execution_view': (
                    'ops_ask_views.flight_execution',
                    'SELECT COUNT(*), NULL, NULL FROM ops_ask_views.flight_execution',
                ),
                'mv_acdm_dyn_flight_detail': (
                    'public.mv_acdm_dyn_flight_detail',
                    'SELECT COUNT(*), NULL, NULL FROM public.mv_acdm_dyn_flight_detail',
                ),
            }
            for key, (regname, query) in table_queries.items():
                exists_row = _scalar_row(cur, f"SELECT to_regclass('{regname}') IS NOT NULL")
                if exists_row and exists_row[0]:
                    res = _scalar_row(cur, query)
                    tables[key] = {'count': int(res[0] or 0), 'min_date': res[1], 'max_date': res[2]}

            dimensions = {}
            for dim in ('dim_airline', 'dim_airport', 'dim_aircraft', 'dim_stand', 'dim_gate', 'dim_air_bridge', 'dim_safeguard_node', 'dim_runway', 'dim_agency', 'dim_abnormal_reason'):
                exists_row = _scalar_row(cur, f"SELECT to_regclass('public.{dim}') IS NOT NULL")
                if exists_row and exists_row[0]:
                    res = _scalar_row(cur, f'SELECT COUNT(*) FROM public.{dim} WHERE deleted_at IS NULL')
                    dimensions[dim] = int(res[0] or 0)

            return {
                'available': True,
                'database': settings.db.get('database', ''),
                'job_states': job_states,
                'tables': tables,
                'dimensions': dimensions,
            }
        finally:
            cur.close()
    except Exception:
        logger.debug('读取 ops_ask 覆盖统计失败', exc_info=True)
        return empty
    finally:
        conn.close()


def reconcile_pg_states(store, pg_snapshot):
    """Sync PG sync_job_state latest runs into local AdminStore sync_runs ledger."""
    entries = []
    for item in pg_snapshot.get('job_states', []):
        finished = item.get('last_success_at')
        if not finished:
            continue
        parsed = item.get('parsed_result') or {}
        entries.append({
            'external_key': f"{item['job_name']}:{finished}",
            'job_key': item['job_key'],
            'title': item['title'],
            'mode': parsed.get('mode') or ('FULL' if item['job_key'] == 'master_data' else 'INCREMENTAL'),
            'started_at': item.get('started_at') or finished,
            'finished_at': finished,
            'elapsed_ms': parsed.get('elapsed_ms', 0),
            'fetched': parsed.get('fetched', 0),
            'inserted': parsed.get('inserted', 0),
            'updated': parsed.get('updated', 0),
            'skipped': parsed.get('skipped', 0),
            'failed': parsed.get('failed', 0),
            'watermark_before': parsed.get('watermark_before', ''),
            'watermark_after': parsed.get('watermark_after') or item.get('watermark', ''),
            'detail': parsed.get('dimensions') or parsed,
            'message': parsed.get('message', ''),
        })
    return store.reconcile_external_sync_runs(entries) if entries else 0


def ensure_reconciled(store):
    if store.sync_runs_count() == 0:
        return reconcile_pg_states(store, read_pg_snapshot())
    return 0


def get_sync_overview(store):
    """Aggregate native Python sync engine readiness, Oracle reachability, PG coverage, backups, and run summary."""
    oracle = probe_oracle_status()
    pg_snapshot = read_pg_snapshot()
    reconciled_added = reconcile_pg_states(store, pg_snapshot)
    return {
        'ok': True,
        'data_source': settings.data_source,
        'oracle': oracle,
        'engine': {
            'mode': 'python-native',
            'ready': bool(oracle['configured']),
            'airport_code': oracle.get('airport_code', 'WUH'),
        },
        'pg': pg_snapshot,
        'backups': list_backups(),
        'schema_status': core.status(),
        'active_run': store.active_sync_run(),
        'summary': store.sync_summary(),
        'reconciled_added': reconciled_added,
    }


def _validate_iso_date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value.strip()):
        raise ValueError(f'{label}必须为 YYYY-MM-DD 格式')
    return date.fromisoformat(value.strip())


def _full_date_bounds():
    today = datetime.now(SHANGHAI_TZ).date()
    return today - timedelta(days=FULL_LOOKBACK_DAYS), today + timedelta(days=FULL_FUTURE_DAYS)


def _parse_run_payload(data):
    job_type = str(data.get('job_type') or 'full_sync').strip()
    if job_type not in JOB_DEFINITIONS:
        raise ValueError('不支持的同步任务类型')

    default_mode = 'INCREMENTAL' if job_type == 'flights_scheduled' else 'FULL'
    mode = str(data.get('mode') or default_mode).strip().upper()
    if mode not in ('FULL', 'BY_EXEC_DATE', 'INCREMENTAL'):
        raise ValueError('同步模式无效')

    exec_from_str = data.get('exec_date_from')
    exec_to_str = data.get('exec_date_to')
    if mode == 'FULL':
        d_from, d_to = _full_date_bounds()
        if exec_from_str:
            d_from = _validate_iso_date(exec_from_str, '开始执行日')
        if exec_to_str:
            d_to = _validate_iso_date(exec_to_str, '结束执行日')
        if d_from > d_to:
            raise ValueError('开始执行日不能晚于结束执行日')
    elif mode == 'BY_EXEC_DATE' or job_type == 'codeshares':
        today = datetime.now(SHANGHAI_TZ).date()
        d_from = _validate_iso_date(exec_from_str or (today - timedelta(days=7)).isoformat(), '开始执行日')
        d_to = _validate_iso_date(exec_to_str or (today + timedelta(days=1)).isoformat(), '结束执行日')
        if d_from > d_to:
            raise ValueError('开始执行日不能晚于结束执行日')
    else:
        d_from = d_to = None

    updated_since = None
    if data.get('updated_since'):
        raw_us = str(data['updated_since']).strip()
        try:
            updated_since = datetime.fromisoformat(raw_us)
        except ValueError as exc:
            raise ValueError('updated_since 时间格式无效') from exc

    return {
        'job_type': job_type,
        'mode': mode,
        'exec_date_from': d_from.isoformat() if d_from else None,
        'exec_date_to': d_to.isoformat() if d_to else None,
        'updated_since': updated_since.isoformat() if updated_since else None,
        'with_backup': bool(data.get('with_backup', True)),
        'include_master': bool(data.get('include_master', True)),
        'include_flights': bool(data.get('include_flights', True)),
        'include_codeshares': bool(data.get('include_codeshares', True)),
        'include_production': bool(data.get('include_production', True)),
        'include_calc_total': bool(data.get('include_calc_total', True)),
        'include_safeguards': bool(data.get('include_safeguards', True)),
        'prune_outside': bool(data.get('prune_outside', job_type == 'full_sync' and mode == 'FULL')),
    }


def _step_totals(steps, current=None):
    all_items = list(steps) + ([current] if current else [])
    return {
        'fetched': sum(int(s.get('fetched') or 0) for s in all_items),
        'inserted': sum(int(s.get('inserted') or 0) for s in all_items),
        'updated': sum(int(s.get('updated') or 0) for s in all_items),
        'skipped': sum(int(s.get('skipped') or 0) for s in all_items),
        'failed': sum(int(s.get('failed') or 0) for s in all_items),
    }


def _build_steps(options):
    d_from = date.fromisoformat(options['exec_date_from']) if options['exec_date_from'] else None
    d_to = date.fromisoformat(options['exec_date_to']) if options['exec_date_to'] else None
    upd = datetime.fromisoformat(options['updated_since']) if options['updated_since'] else None
    mode = options['mode']
    job = options['job_type']

    def run_master(ora_conn, pg_conn, ora_cfg, s_cfg, on_prog, cancel):
        return runner.run_master_data_sync(pg_conn, ora_conn, ora_cfg, on_progress=on_prog, cancel_event=cancel)

    def run_fims(ora_conn, pg_conn, ora_cfg, s_cfg, on_prog, cancel):
        return runner.run_flight_sync(
            pg_conn, ora_conn, ora_cfg, s_cfg, 'oracle-fims', mode=mode,
            exec_date_from=d_from, exec_date_to=d_to, updated_since=upd,
            on_progress=on_prog, cancel_event=cancel,
        )

    def run_acdm(ora_conn, pg_conn, ora_cfg, s_cfg, on_prog, cancel):
        return runner.run_flight_sync(
            pg_conn, ora_conn, ora_cfg, s_cfg, 'oracle-acdm', mode=mode,
            exec_date_from=d_from, exec_date_to=d_to, updated_since=upd,
            on_progress=on_prog, cancel_event=cancel,
        )

    def run_codeshares(ora_conn, pg_conn, ora_cfg, s_cfg, on_prog, cancel):
        return runner.run_codeshare_sync(
            pg_conn, ora_conn, ora_cfg, s_cfg, mode=mode,
            exec_date_from=d_from, exec_date_to=d_to,
            on_progress=on_prog, cancel_event=cancel,
        )

    def run_prod(ora_conn, pg_conn, ora_cfg, s_cfg, on_prog, cancel):
        return runner.run_production_sync(
            pg_conn, ora_conn, ora_cfg, s_cfg, mode=mode,
            exec_date_from=d_from, exec_date_to=d_to, updated_since=upd,
            on_progress=on_prog, cancel_event=cancel,
        )

    def run_calc_total(ora_conn, pg_conn, ora_cfg, s_cfg, on_prog, cancel):
        return runner.run_calc_total_sync(
            pg_conn, ora_conn, ora_cfg, s_cfg, mode=mode,
            exec_date_from=d_from, exec_date_to=d_to, updated_since=upd,
            on_progress=on_prog, cancel_event=cancel,
        )

    def run_safeguards(ora_conn, pg_conn, ora_cfg, s_cfg, on_prog, cancel):
        return runner.run_safeguard_sync(
            pg_conn, ora_conn, ora_cfg, s_cfg, mode=mode,
            exec_date_from=d_from, exec_date_to=d_to, updated_since=upd,
            on_progress=on_prog, cancel_event=cancel,
        )

    if job == 'full_sync':
        plan = []
        if options['include_master']:
            plan.append(('master_data', '基础资料全量（航司/机场/机型/机位/登机口/登机桥/保障节点/跑道）', run_master))
        if options['include_flights']:
            plan.append(('flights_fims', 'FIMS 航班动态全量', run_fims))
            plan.append(('flights_acdm', 'ACDM 航班动态全量（含过站串联与宽表视图）', run_acdm))
        if options['include_codeshares']:
            plan.append(('codeshares', 'FIMS 共享航班全量', run_codeshares))
        if options['include_production']:
            plan.append(('production', '货邮行生产量全量（FORMFILL）', run_prod))
        if options.get('include_calc_total', True):
            plan.append(('calc_total', '动态汇总旅客全量（TBH_FIMS_DYN_CALC_TOTAL）', run_calc_total))
        if options.get('include_safeguards', True):
            plan.append(('safeguards', '登机桥与保障节点全量（FIMS）', run_safeguards))
        return plan
    if job == 'flights_scheduled':
        return [
            ('flights_fims', 'FIMS 航班动态增量', run_fims),
            ('flights_acdm', 'ACDM 航班动态增量', run_acdm),
        ]
    single = {
        'master_data': ('master_data', '基础资料全量（dim_*）', run_master),
        'flights_fims': ('flights_fims', 'FIMS 航班动态', run_fims),
        'flights_acdm': ('flights_acdm', 'ACDM 航班动态', run_acdm),
        'codeshares': ('codeshares', 'FIMS 共享航班', run_codeshares),
        'production': ('production', '货邮行生产量（FORMFILL）', run_prod),
        'calc_total': ('calc_total', '动态汇总旅客（TBH_FIMS_DYN_CALC_TOTAL）', run_calc_total),
        'safeguards': ('safeguards', '登机桥与保障节点（FIMS）', run_safeguards),
    }
    return [single[job]]


def _execute_sync_worker(store, run_id, actor, actor_name, options, cancel_event):
    started = monotonic()
    ora_cfg = oracle_config()
    s_cfg = sync_config()
    backup_name = ''
    steps = []
    oracle_conn = None
    pg_conn = None

    try:
        if options['with_backup']:
            store.update_sync_run_progress(run_id, message='正在备份当前 ops_ask 数据库…')
            backup_name = create_backup(store, actor, actor_name)['name']

        if cancel_event.is_set():
            raise oracle_source.SyncCancelledError('同步任务已被管理员中止')

        store.update_sync_run_progress(run_id, backup_file=backup_name, message='正在建立 Oracle 与 PostgreSQL 直连…')
        oracle_conn = oracle_source.connect_oracle(ora_cfg)
        pg_conn = pg_sink.connect_pg_write()
        if hasattr(pg_sink, 'ensure_acdm_schema_extensions') and callable(getattr(pg_conn, 'cursor', None)):
            pg_sink.ensure_acdm_schema_extensions(pg_conn)
        if options.get('prune_outside') and options.get('exec_date_from') and callable(getattr(pg_conn, 'cursor', None)):
            keep_from = date.fromisoformat(options['exec_date_from'])
            store.update_sync_run_progress(run_id, backup_file=backup_name, message=f'正在清理 {keep_from.isoformat()} 之前的历史超出窗口数据…')
            pg_sink.prune_stale_window(pg_conn, keep_from)

        plan = _build_steps(options)
        if not plan:
            raise ValueError('同步任务未包含任何有效数据模块')
        total_steps = len(plan)

        for idx, (s_key, s_label, fn) in enumerate(plan, start=1):
            if cancel_event.is_set():
                raise oracle_source.SyncCancelledError('同步任务已被管理员中止')
            last_report = [0.0]

            def on_progress(p):
                now_ts = monotonic()
                if now_ts - last_report[0] < 1.2:
                    return
                last_report[0] = now_ts
                totals = _step_totals(steps, p)
                msg = f'[{idx}/{total_steps}] {p.get("detail_msg") or s_label}'
                store.update_sync_run_progress(
                    run_id,
                    elapsed_ms=int((now_ts - started) * 1000),
                    **totals,
                    detail={'steps': steps},
                    message=msg,
                    backup_file=backup_name,
                )

            oracle_conn = oracle_source.ensure_oracle_conn(oracle_conn, ora_cfg)
            store.update_sync_run_progress(
                run_id,
                elapsed_ms=int((monotonic() - started) * 1000),
                **_step_totals(steps),
                detail={'steps': steps},
                message=f'正在执行 [{idx}/{total_steps}]：{s_label}…',
                backup_file=backup_name,
            )
            step_res = fn(oracle_conn, pg_conn, ora_cfg, s_cfg, on_progress, cancel_event)
            steps.append({'step': s_key, 'label': s_label, **step_res})
            store.update_sync_run_progress(
                run_id,
                elapsed_ms=int((monotonic() - started) * 1000),
                **_step_totals(steps),
                detail={'steps': steps},
                message=f'已完成 [{idx}/{total_steps}]：{s_label}（拉取 {step_res.get("fetched", 0):,} 条）',
                backup_file=backup_name,
            )

        totals = _step_totals(steps)
        wm_before = next((s.get('watermark_before') for s in steps if s.get('watermark_before')), '')
        wm_after = next((s.get('watermark_after') for s in reversed(steps) if s.get('watermark_after')), '')
        status = 'partial' if totals['failed'] > 0 else 'completed'
        elapsed_ms = int((monotonic() - started) * 1000)
        summary_msg = '；'.join(filter(None, [s.get('message') for s in steps])) or f'完成 {len(steps)} 个步骤'
        store.complete_sync_run(
            run_id, status=status, elapsed_ms=elapsed_ms, **totals, watermark_before=wm_before,
            watermark_after=wm_after, detail={'steps': steps}, message=summary_msg, backup_file=backup_name,
        )
        store.audit(actor, 'data_sync.run', run_id, f"{options['job_type']} {status} fetched={totals['fetched']} elapsed={elapsed_ms}ms")
    except oracle_source.SyncCancelledError as exc:
        elapsed_ms = int((monotonic() - started) * 1000)
        totals = _step_totals(steps)
        store.complete_sync_run(
            run_id, status='interrupted', elapsed_ms=elapsed_ms, **totals,
            detail={'steps': steps, 'cancelled': True}, message=str(exc), backup_file=backup_name,
        )
        store.audit(actor, 'data_sync.run', run_id, f"{options['job_type']} cancelled fetched={totals['fetched']}")
    except Exception as exc:
        elapsed_ms = int((monotonic() - started) * 1000)
        totals = _step_totals(steps)
        msg = str(exc)[:300] or '同步执行失败'
        store.complete_sync_run(
            run_id, status='failed', elapsed_ms=elapsed_ms,
            fetched=totals['fetched'], inserted=totals['inserted'], updated=totals['updated'],
            skipped=totals['skipped'], failed=max(1, totals['failed']),
            detail={'steps': steps, 'error': msg}, message=msg, backup_file=backup_name,
        )
        store.audit(actor, 'data_sync.run', run_id, f"{options['job_type']} failed: {msg[:120]}")
    finally:
        with _CANCEL_LOCK:
            _CANCEL_EVENTS.pop(run_id, None)
        for conn in (oracle_conn, pg_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def trigger_sync(store, user, payload):
    """Validate parameters, record a running sync run, and launch the Python sync worker."""
    options = _parse_run_payload(payload)
    run = store.begin_sync_run(
        options['job_type'], JOB_DEFINITIONS[options['job_type']]['title'], 'ui',
        options['mode'], user['id'], user['display_name'], params=options,
    )
    cancel_event = Event()
    with _CANCEL_LOCK:
        _CANCEL_EVENTS[run['id']] = cancel_event
    Thread(
        target=_execute_sync_worker,
        args=(store, run['id'], user['id'], user['display_name'], options, cancel_event),
        daemon=True,
    ).start()
    return run


def cancel_active_sync(store, actor, actor_name):
    """Request cancellation of the currently running Python sync job."""
    active = store.active_sync_run()
    if not active:
        raise ValueError('当前没有正在执行的同步任务')
    with _CANCEL_LOCK:
        ev = _CANCEL_EVENTS.get(active['id'])
    if ev is None:
        raise ValueError('当前任务不支持在线中止或已进入收尾阶段')
    ev.set()
    store.audit(actor, 'data_sync.cancel', active['id'], f'由 {actor_name} 发起中止请求')
    return {'cancelled': True, 'run_id': active['id']}
