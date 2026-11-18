"""Oracle streaming source adapters ported from airport-ops-data-sync."""
from datetime import datetime, time, timedelta
import re
from time import perf_counter

import oracledb

from app.sync.transforms import SHANGHAI_TZ, to_shanghai_dt

_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_$#]*$')


class SyncCancelledError(RuntimeError):
    """Raised when a running sync job is cancelled by an administrator."""


def safe_ident(name, label='Oracle identifier'):
    text = str(name or '').strip()
    if not _IDENT_RE.fullmatch(text):
        raise ValueError(f'{label} 格式无效: {text}')
    return text


def connect_oracle(cfg):
    """Open a thin-mode Oracle connection using configured host/port/service credentials."""
    user = str(cfg.get('user') or '').strip()
    password = str(cfg.get('password') or '')
    host = str(cfg.get('host') or '').strip()
    port = int(cfg.get('port') or 1521)
    service = str(cfg.get('service_name') or '').strip()
    if not (host and service and user and password):
        raise ValueError('Oracle 连接配置不完整（需在 config.local.json 或环境变量中配置地址、服务名与凭据）')
    dsn = oracledb.makedsn(host, port, service_name=service)
    return oracledb.connect(
        user=user,
        password=password,
        dsn=dsn,
        expire_time=1,
        tcp_connect_timeout=15,
    )


def ensure_oracle_conn(conn, cfg):
    """Keep or transparently reopen an Oracle connection when idle TCP sockets are dropped."""
    if conn is not None:
        try:
            ping = getattr(conn, 'ping', None)
            if callable(ping):
                ping()
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    return connect_oracle(cfg)


def probe_oracle_source(cfg):
    """Verify Oracle authentication and read lightweight table bounds."""
    started = perf_counter()
    fims_schema = safe_ident(cfg.get('fims_schema') or 'FIMS')
    fims_table = safe_ident(cfg.get('fims_table') or 'TB_DYN_FLIGHT')
    acdm_schema = safe_ident(cfg.get('acdm_schema') or 'ACDM')
    acdm_table = safe_ident(cfg.get('acdm_table') or 'TB_DYN_FLIGHT')
    share_table = safe_ident(cfg.get('fims_share_table') or 'TB_DYN_SHARE_FLIGHTS')
    airport = str(cfg.get('airport_code') or 'WUH').strip().upper()

    conn = connect_oracle(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute('SELECT 1 FROM DUAL')
            cur.fetchone()
            stats = {}
            queries = {
                'fims_flights': f'SELECT COUNT(*), MIN(EXEC_DATE), MAX(EXEC_DATE) FROM {fims_schema}.{fims_table}',
                'acdm_flights': f'SELECT COUNT(*), MIN(EXEC_DATE), MAX(EXEC_DATE) FROM {acdm_schema}.{acdm_table}',
                'codeshares': f'SELECT COUNT(*), NULL, NULL FROM {fims_schema}.{share_table}',
                'production': f'SELECT COUNT(*), MIN(EXEC_DATE), MAX(EXEC_DATE) FROM FORMFILL.FLIGHT_PRODUCTION_ANALYSIS WHERE AIRPORT_IATA = :airport',
                'calc_total': f'SELECT COUNT(*), MIN(EXEC_DATE), MAX(EXEC_DATE) FROM {fims_schema}.TBH_FIMS_DYN_CALC_TOTAL',
                'air_bridges': f'SELECT COUNT(*), MIN(CREATE_TIME), MAX(CREATE_TIME) FROM {fims_schema}.TB_DYN_AIR_BRIDGE',
                'safeguard_nodes': f'SELECT COUNT(*), MIN(CREATE_TIME), MAX(CREATE_TIME) FROM {fims_schema}.TB_DYN_SAFEGUARD_NODE',
            }
            for key, sql in queries.items():
                if ':airport' in sql:
                    cur.execute(sql, airport=airport)
                else:
                    cur.execute(sql)
                cnt, min_dt, max_dt = cur.fetchone()
                stats[key] = {
                    'count': int(cnt or 0),
                    'min_date': min_dt.strftime('%Y-%m-%d') if hasattr(min_dt, 'strftime') else None,
                    'max_date': max_dt.strftime('%Y-%m-%d') if hasattr(max_dt, 'strftime') else None,
                }
            return {
                'ok': True,
                'latency_ms': round((perf_counter() - started) * 1000, 1),
                'tables': stats,
            }
        finally:
            cur.close()
    finally:
        conn.close()


def _iter_oracle_dicts(conn, sql, binds=None, *, fetch_size=1000, batch_size=500, cancel_event=None):
    cur = conn.cursor()
    cur.arraysize = max(100, int(fetch_size))
    try:
        cur.execute(sql, binds or {})
        cols = [col[0].upper() for col in cur.description]
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise SyncCancelledError('同步任务已被管理员中止')
            rows = cur.fetchmany(max(50, int(batch_size)))
            if not rows:
                break
            yield [dict(zip(cols, row)) for row in rows]
    finally:
        cur.close()


def _to_oracle_naive_ts(dt):
    """Convert aware/naive datetime into naive Asia/Shanghai wall-clock datetime for Oracle TIMESTAMP binds."""
    sh = to_shanghai_dt(dt)
    return sh.replace(tzinfo=None) if sh else None


def iter_flight_batches(conn, cfg, source_type, *, mode='BY_EXEC_DATE',
                        exec_date_from=None, exec_date_to=None, updated_since=None,
                        fetch_size=1000, batch_size=500, cancel_event=None, source_flight_ids=None):
    """Stream FIMS or ACDM TB_DYN_FLIGHT rows in batches."""
    acdm = 'acdm' in str(source_type).lower()
    if acdm:
        schema = safe_ident(cfg.get('acdm_schema') or 'ACDM')
        table = safe_ident(cfg.get('acdm_table') or 'TB_DYN_FLIGHT')
        sql = f"""
            SELECT FLIGHT_ID, ASSOCIATED_FLIGHT_ID, FFID, AIRLINE_TWO_CHAR_CD, FLIGHT_NO,
                   EXEC_DATE, IS_OFF_IN, FLIGHT_TASK_CD, ATTRIBUTE, AGENCY,
                   CRAFT_TYPE_ICAO_CD, CRAFT_NO, GATE_CD, CRAFT_SITE_CD,
                   TAKEOFF_AIRPORT_IATA, ARRIVAL_AIRPORT_IATA,
                   NORMAL_FLIGHT_STAT_CD, ABNORMAL_FLIGHT_STAT_CD, ABNORMAL_REASON_CD,
                   SCHEME_TAKEOFF_TIME, ALTERATE_TAKEOFF_TIME, COUNT_TAKE_OFF_TIME, TARGT_TAKE_OFF_TIME, REAL_TAKEOFF_TIME,
                   SCHEME_LANDIN_TIME, ALTERATE_LANDIN_TIME, REAL_LANDIN_TIME,
                   START_SCHEME_TAKEOFF_TIME,
                   ALTERATE_BLOCK_ON_TIME, REAL_BLOCK_ON_TIME,
                   COUNT_BLOCK_OFF_TIME, COORDINATE_BLOCK_OFF_TIME, AIRPORT_TARGT_BLOCK_OFF_TIME,
                   ALTERATE_BLOCK_OFF_TIME, TARGT_BLOCK_OFF_TIME, REAL_BLOCK_OFF_TIME,
                   ALTERATE_BY_BRIDGE_TIME, REAL_BY_BRIDGE_TIME, PLAN_OFF_BRIDGE_TIME, REAL_OFF_BRIDGE_TIME,
                   CHECKIN_START_TIME, CHECKIN_END_TIME,
                   DEPARTURE_START_TIME, DEPARTURE_END_TIME, LOAD_START_TIME, LOAD_END_TIME,
                   ALTERATE_OPEN_CABIN_TIME, REAL_OPEN_CABIN_TIME,
                   ALTERATE_CLOSE_CABIN_TIME, REAL_CLOSE_CABIN_TIME, PLAN_CLOSE_DOOR_TIME,
                   ALTERATE_BEGIN_BOARD_TIME, REAL_BEGIN_BOARD_TIME, URGE_BOARD_TIME,
                   ALTERATE_END_BOARD_TIME, REAL_END_BOARD_TIME,
                   ALTERATE_START_CLEAN_TIME, REAL_START_CLEAN_TIME, ALTERATE_END_CLEAN_TIME, REAL_END_CLEAN_TIME,
                   ALTERATE_MEAL_START_TIME, REAL_MEAL_START_TIME, ALTERATE_MEAL_END_TIME, REAL_MEAL_END_TIME,
                   ALTERATE_START_SUPPLY_TIME, REAL_START_SUPPLY_TIME, ALTERATE_END_SUPPLY_TIME, REAL_END_SUPPLY_TIME,
                   ALTERATE_START_DEICE_TIME, REAL_START_DEICE_TIME, ALTERATE_END_DEICIE_TIME, REAL_END_DEICIE_TIME,
                   REQUEST_LAUNCH_TIME, TARGT_DRIVE_TIME, REAL_DRIVE_TIME, REAL_LAUNCH_TIME,
                   ALTERATE_TAXI_IN_TIME, REAL_TAXI_IN_TIME, ALTERATE_TAXI_OUT_TIME, REAL_TAXI_OUT_TIME,
                   FLIGHT_RP, ORI_ACT, ALT,
                   FLIGHT_FIRST_ON_TIME, RELEASE_ON_TIME, FLIGHT_ON_TIME, DEP_ON_TIME,
                   TAKEOFF_ON_TIME, LAND_ON_TIME, ARR_ON_TIME,
                   CLEARANCE_DELAY_TIME, FLIGHT_DELAY_TIME, DEP_DELAY_TIME,
                   TAKEOFF_DELAY_TIME, LAND_DELAY_TIME, ARR_DELAY_TIME,
                   S_CNL_STAUTS, UPDATE_TIME, CREATE_TIME
            FROM {schema}.{table}
            WHERE 1 = 1
        """
    else:
        schema = safe_ident(cfg.get('fims_schema') or 'FIMS')
        table = safe_ident(cfg.get('fims_table') or 'TB_DYN_FLIGHT')
        sql = f"""
            SELECT FLIGHT_ID, ASSOCIATED_FLIGHT_ID, FFID, AIRLINE_TWO_CHAR_CD, FLIGHT_NO,
                   EXEC_DATE, IS_OFF_IN, FLIGHT_TASK_CD, ATTRIBUTE, IS_VIRTUAL, AGENCY,
                   CRAFT_TYPE_ICAO_CD, CRAFT_NO, START_STATION, TERMINAL_STATION,
                   NEAREST_STATION, DIVERT_EDSTATION, IS_VIP, VIP_NUMBER,
                   NORMAL_FLIGHT_STAT_CD, ABNORMAL_FLIGHT_STAT_CD, ABNORMAL_REASON_CD,
                   DOMESTIC_TERMINAL_CD, INTERNATION_TERMINAL_CD, RUNWAY_NO, PLAN_TIME,
                   START_SCHEME_TAKEOFF_TIME, START_ALTERATE_TAKEOFF_TIME, START_REAL_TAKEOFF_TIME,
                   TERMINAL_SCHEME_LANDIN_TIME, TERMINAL_ALTERATE_LANDIN_TIME, TERMINAL_REAL_LANDIN_TIME,
                   NEAREST_SCHEME_LANDIN_TIME, NEAREST_ALTERATE_LANDIN_TIME, NEAREST_REAL_LANDIN_TIME,
                   NEAREST_SCHEME_TAKEOFF_TIME, NEAREST_ALTERATE_TAKEOFF_TIME, NEAREST_REAL_TAKEOFF_TIME,
                   BORDING_START_REALTIME, BORDING_END_REALTIME, LAST_CALL_REALTIME,
                   CHECKIN_ALTERATE_STARTTIME, CHECKIN_ALTERATE_ENDTIME, CHECKIN_START_REALTIME, CHECKIN_END_REALTIME,
                   FIRST_BAGGAGE_TIME, LAST_BAGGAGE_TIME, UPDATE_TIME, CREATE_TIME
            FROM {schema}.{table}
            WHERE 1 = 1
        """

    binds = {}
    if mode in ('BY_EXEC_DATE', 'FULL'):
        if exec_date_from is not None and exec_date_to is not None:
            sql += ' AND EXEC_DATE >= :fromDate AND EXEC_DATE < :toDateExclusive'
            binds['fromDate'] = datetime.combine(exec_date_from, time.min)
            binds['toDateExclusive'] = datetime.combine(exec_date_to + timedelta(days=1), time.min)
    elif updated_since is not None:
        sql += ' AND UPDATE_TIME >= :updatedSince'
        binds['updatedSince'] = _to_oracle_naive_ts(updated_since)
    if source_flight_ids is not None:
        ids = list(dict.fromkeys(int(str(value)) for value in source_flight_ids))
        if not ids:
            return iter(())
        if len(ids) > 1000 or any(value <= 0 for value in ids):
            raise ValueError('按源航班ID读取限1至1000个正整数')
        names = [f'flightId{i}' for i in range(len(ids))]
        sql += ' AND FLIGHT_ID IN (' + ','.join(':' + name for name in names) + ')'
        binds.update(zip(names, ids))
    sql += ' ORDER BY UPDATE_TIME NULLS LAST, FLIGHT_ID'

    return _iter_oracle_dicts(conn, sql, binds, fetch_size=fetch_size, batch_size=batch_size, cancel_event=cancel_event)


def iter_share_by_exec_date(conn, cfg, *, mode='BY_EXEC_DATE', exec_date_from=None, exec_date_to=None,
                            fetch_size=1000, batch_size=500, cancel_event=None):
    """Stream FIMS.TB_DYN_SHARE_FLIGHTS joined to FIMS dyn flights."""
    schema = safe_ident(cfg.get('fims_schema') or 'FIMS')
    dyn_table = safe_ident(cfg.get('fims_table') or 'TB_DYN_FLIGHT')
    share_table = safe_ident(cfg.get('fims_share_table') or 'TB_DYN_SHARE_FLIGHTS')
    binds = {}
    if mode in ('BY_EXEC_DATE', 'FULL') and exec_date_from is not None and exec_date_to is not None:
        sql = f"""
            SELECT s.FLIGHT_ID, s.AIRLINE_TWO_CHAR_CD, s.FLIGHT_NO
            FROM {schema}.{share_table} s
            JOIN {schema}.{dyn_table} d ON d.FLIGHT_ID = s.FLIGHT_ID
            WHERE d.EXEC_DATE >= :fromDate AND d.EXEC_DATE < :toDateExclusive
        """
        binds['fromDate'] = datetime.combine(exec_date_from, time.min)
        binds['toDateExclusive'] = datetime.combine(exec_date_to + timedelta(days=1), time.min)
    else:
        sql = f'SELECT FLIGHT_ID, AIRLINE_TWO_CHAR_CD, FLIGHT_NO FROM {schema}.{share_table}'

    return _iter_oracle_dicts(conn, sql, binds, fetch_size=fetch_size, batch_size=batch_size, cancel_event=cancel_event)


def fetch_share_by_source_flight_ids(conn, cfg, source_flight_ids, *, chunk_size=200):
    """Fetch codeshare rows for an explicit list of FIMS FLIGHT_IDs."""
    schema = safe_ident(cfg.get('fims_schema') or 'FIMS')
    share_table = safe_ident(cfg.get('fims_share_table') or 'TB_DYN_SHARE_FLIGHTS')
    numeric_ids = []
    seen = set()
    for raw in source_flight_ids or ():
        try:
            val = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if val not in seen:
            seen.add(val)
            numeric_ids.append(val)
    if not numeric_ids:
        return []

    rows = []
    cur = conn.cursor()
    try:
        step = max(50, min(900, int(chunk_size)))
        for idx in range(0, len(numeric_ids), step):
            chunk = numeric_ids[idx:idx + step]
            placeholders = ', '.join(f':id{i}' for i in range(len(chunk)))
            binds = {f'id{i}': val for i, val in enumerate(chunk)}
            cur.execute(
                f'SELECT FLIGHT_ID, AIRLINE_TWO_CHAR_CD, FLIGHT_NO FROM {schema}.{share_table} WHERE FLIGHT_ID IN ({placeholders})',
                binds,
            )
            cols = [col[0].upper() for col in cur.description]
            rows.extend(dict(zip(cols, rec)) for rec in cur.fetchall())
        return rows
    finally:
        cur.close()


def iter_production_batches(conn, cfg, *, mode='BY_EXEC_DATE',
                            exec_date_from=None, exec_date_to=None, updated_since=None,
                            fetch_size=1000, batch_size=500, cancel_event=None):
    """Stream FORMFILL.FLIGHT_PRODUCTION_ANALYSIS rows for the configured airport."""
    airport = str(cfg.get('airport_code') or 'WUH').strip().upper()
    sql = """
        SELECT UUID, EXEC_DATE, AIRLINE_TWO_CHAR_CD, AIRPORT_IATA, AREA_CODE, FLIGHT_NO,
               CRAFT_TYPE_ICAO_CD, FLIGHT_TASK_CD, AIR_LINE_ENGLISH_NAME, AIR_LINE_CHINESE_NAME,
               AIR_LINE_TYPE, LEG, LEG_TYPE, MAX_PAYLOAD, MAX_SEAT, PE_PAYLOAD, PE_SEAT,
               AVAILABLE_PAYLOAD, AVAILABLE_SEAT, IS_OFF_IN, TAKEOFF_LANDIN_COUNT, TAKEOFF_LANDIN_TIME,
               ADULT_COUNT, CHILDREN_COUNT, BABY_COUNT,
               OVER_STATION_ADULT_COUNT, OVER_STATION_CHILDREN_COUNT, OVER_STATION_BABY_COUNT,
               LUGGAGE, MAIL, GOODS, CRAFT_NO, CUSTOM_AIR_LINE_TYPE, CREATE_TIME, UPDATE_TIME
        FROM FORMFILL.FLIGHT_PRODUCTION_ANALYSIS
        WHERE AIRPORT_IATA = :airport
    """
    binds = {'airport': airport}
    if mode in ('BY_EXEC_DATE', 'FULL'):
        if exec_date_from is not None and exec_date_to is not None:
            sql += ' AND EXEC_DATE >= :fromDate AND EXEC_DATE < :toDateExclusive'
            binds['fromDate'] = datetime.combine(exec_date_from, time.min)
            binds['toDateExclusive'] = datetime.combine(exec_date_to + timedelta(days=1), time.min)
    elif updated_since is not None:
        sql += ' AND NVL(UPDATE_TIME, CREATE_TIME) >= :updatedSince'
        binds['updatedSince'] = _to_oracle_naive_ts(updated_since)
    sql += ' ORDER BY NVL(UPDATE_TIME, CREATE_TIME) NULLS LAST, UUID'

    return _iter_oracle_dicts(conn, sql, binds, fetch_size=fetch_size, batch_size=batch_size, cancel_event=cancel_event)


def iter_calc_total_batches(conn, cfg, *, mode='BY_EXEC_DATE',
                            exec_date_from=None, exec_date_to=None, updated_since=None,
                            fetch_size=1000, batch_size=500, cancel_event=None):
    """Stream FIMS.TBH_FIMS_DYN_CALC_TOTAL passenger/baggage summary rows."""
    schema = safe_ident(cfg.get('fims_schema') or 'FIMS')
    sql = f"""
        SELECT FLIGHT_ID, ASSOCIATED_FLIGHT_ID, FFID, AIRLINE_TWO_CHAR_CD, FLIGHT_NO,
               EXEC_DATE, IS_OFF_IN, CREW_NUMBER, PSG_ARRIVAL_NUM,
               DEP_DPSR_CHECKIN_NUM, DEP_DPSR_SECURITY_NUM, DEP_DPSR_BOARDED_NUM,
               DEP_DPSR_STOP_NUM, DEP_DPSR_BABY_NUM, DEP_DPSR_DEPE_NUM,
               DEP_DPSR_TRANSFER_NUM, DEP_DPSR_VIP_NUM, DEP_DPSR_FIRST_CLASS_NUM,
               BAG_NUM, BAG_WEIGHT, DELAY_DURATION,
               REAL_OPEN_CABIN_TIME, REAL_CLOSE_CABIN_TIME,
               FIRST_BAGGAGE_TIME, LAST_BAGGAGE_TIME, CREATE_TIME, UPDATE_TIME
        FROM {schema}.TBH_FIMS_DYN_CALC_TOTAL
        WHERE 1 = 1
    """
    binds = {}
    if mode in ('BY_EXEC_DATE', 'FULL'):
        if exec_date_from is not None and exec_date_to is not None:
            sql += ' AND EXEC_DATE >= :fromDate AND EXEC_DATE < :toDateExclusive'
            binds['fromDate'] = datetime.combine(exec_date_from, time.min)
            binds['toDateExclusive'] = datetime.combine(exec_date_to + timedelta(days=1), time.min)
    elif updated_since is not None:
        sql += ' AND NVL(UPDATE_TIME, CREATE_TIME) >= :updatedSince'
        binds['updatedSince'] = _to_oracle_naive_ts(updated_since)
    sql += ' ORDER BY NVL(UPDATE_TIME, CREATE_TIME) NULLS LAST, FLIGHT_ID'
    return _iter_oracle_dicts(conn, sql, binds, fetch_size=fetch_size, batch_size=batch_size, cancel_event=cancel_event)


def iter_air_bridge_batches(conn, cfg, *, mode='BY_EXEC_DATE',
                            exec_date_from=None, exec_date_to=None, updated_since=None,
                            fetch_size=1000, batch_size=500, cancel_event=None):
    """Stream FIMS.TB_DYN_AIR_BRIDGE rows joined with FIMS dyn flights for date filtering."""
    schema = safe_ident(cfg.get('fims_schema') or 'FIMS')
    dyn_table = safe_ident(cfg.get('fims_table') or 'TB_DYN_FLIGHT')
    sql = f"""
        SELECT b.FLIGHT_ID, b.FFID, b.ORDER_CD, b.AIR_BRIDGE_ID, b.AIR_BRIDGE_CD,
               b.AIR_BRIDGE_START_TIME, b.AIR_BRIDGE_END_TIME, b.AIR_BRIDGE_DURATION,
               b.POWER_SUPPLY_START_TIME, b.POWER_SUPPLY_END_TIME,
               b.AIR_CONDITIONING_START_TIME, b.AIR_CONDITIONING_END_TIME,
               b.CREATE_TIME, b.UPDATE_TIME
        FROM {schema}.TB_DYN_AIR_BRIDGE b
        JOIN {schema}.{dyn_table} d ON d.FLIGHT_ID = b.FLIGHT_ID
        WHERE 1 = 1
    """
    binds = {}
    if mode in ('BY_EXEC_DATE', 'FULL'):
        if exec_date_from is not None and exec_date_to is not None:
            sql += ' AND d.EXEC_DATE >= :fromDate AND d.EXEC_DATE < :toDateExclusive'
            binds['fromDate'] = datetime.combine(exec_date_from, time.min)
            binds['toDateExclusive'] = datetime.combine(exec_date_to + timedelta(days=1), time.min)
    elif updated_since is not None:
        sql += ' AND NVL(b.UPDATE_TIME, b.CREATE_TIME) >= :updatedSince'
        binds['updatedSince'] = _to_oracle_naive_ts(updated_since)
    sql += ' ORDER BY NVL(b.UPDATE_TIME, b.CREATE_TIME) NULLS LAST, b.FLIGHT_ID, b.ORDER_CD'
    return _iter_oracle_dicts(conn, sql, binds, fetch_size=fetch_size, batch_size=batch_size, cancel_event=cancel_event)


def iter_safeguard_node_batches(conn, cfg, *, mode='BY_EXEC_DATE',
                                exec_date_from=None, exec_date_to=None, updated_since=None,
                                fetch_size=1000, batch_size=500, cancel_event=None):
    """Stream FIMS.TB_DYN_SAFEGUARD_NODE rows joined with FIMS dyn flights for date filtering."""
    schema = safe_ident(cfg.get('fims_schema') or 'FIMS')
    dyn_table = safe_ident(cfg.get('fims_table') or 'TB_DYN_FLIGHT')
    sql = f"""
        SELECT n.FLIGHT_ID, n.SAFEGUARD_ORDER_CD, n.SAFEGUARD_CODE, n.FFID,
               n.FLIGHT_TASK_CD, n.SAFEGUARD_ALTERATE_TIME, n.SAFEGUARD_REAL_TIME,
               n.ABNORMAL_START_TIME, n.ABNORMAL_END_TIME, n.ABNORMAL_REASON,
               n.CREATE_TIME, n.UPDATE_TIME
        FROM {schema}.TB_DYN_SAFEGUARD_NODE n
        JOIN {schema}.{dyn_table} d ON d.FLIGHT_ID = n.FLIGHT_ID
        WHERE 1 = 1
    """
    binds = {}
    if mode in ('BY_EXEC_DATE', 'FULL'):
        if exec_date_from is not None and exec_date_to is not None:
            sql += ' AND d.EXEC_DATE >= :fromDate AND d.EXEC_DATE < :toDateExclusive'
            binds['fromDate'] = datetime.combine(exec_date_from, time.min)
            binds['toDateExclusive'] = datetime.combine(exec_date_to + timedelta(days=1), time.min)
    elif updated_since is not None:
        sql += ' AND NVL(n.UPDATE_TIME, n.CREATE_TIME) >= :updatedSince'
        binds['updatedSince'] = _to_oracle_naive_ts(updated_since)
    sql += ' ORDER BY NVL(n.UPDATE_TIME, n.CREATE_TIME) NULLS LAST, n.FLIGHT_ID, n.SAFEGUARD_ORDER_CD'
    return _iter_oracle_dicts(conn, sql, binds, fetch_size=fetch_size, batch_size=batch_size, cancel_event=cancel_event)


def fetch_master_rows(conn, sql):
    """Load dimension master rows cleanly from Oracle."""
    cur = conn.cursor()
    cur.arraysize = 1000
    try:
        cur.execute(sql)
        cols = [col[0].upper() for col in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()
