"""PostgreSQL write sinks and watermark store ported from airport-ops-data-sync."""
from datetime import datetime, timezone
import json
import re

import pg8000.dbapi as pg

from app.config import settings
from app.sync.transforms import (
    clip, first_non_blank, first_ts, is_deleted, to_utc_dt, trim, upper, yn_flag,
)


class AtomicConnection:
    """Let existing sinks share one commit for a consistent source refresh."""
    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return self.connection.cursor()

    def commit(self):
        pass

    def rollback(self):
        self.connection.rollback()


def connect_pg_write():
    """Open a read-write PostgreSQL connection strictly for data sync ingestion."""
    options = settings.db.copy()
    options['timeout'] = max(options.get('timeout', 8), 30)
    return pg.connect(**options)


def ensure_watermark_table(pg_conn):
    cur = pg_conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_job_state (
                job_name         VARCHAR(64) PRIMARY KEY,
                watermark        TIMESTAMPTZ,
                last_success_at  TIMESTAMPTZ,
                last_result_json TEXT,
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        pg_conn.commit()
    finally:
        cur.close()


def get_watermark(pg_conn, job_name):
    ensure_watermark_table(pg_conn)
    cur = pg_conn.cursor()
    try:
        cur.execute('SELECT watermark FROM sync_job_state WHERE job_name = %s', (job_name,))
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None
    finally:
        cur.close()


def save_watermark(pg_conn, job_name, watermark, result_payload):
    ensure_watermark_table(pg_conn)
    payload_text = result_payload if isinstance(result_payload, str) else json.dumps(result_payload, ensure_ascii=False, default=str)
    cur = pg_conn.cursor()
    try:
        cur.execute("""
            INSERT INTO sync_job_state (job_name, watermark, last_success_at, last_result_json, updated_at)
            VALUES (%s, %s, now(), %s, now())
            ON CONFLICT (job_name) DO UPDATE SET
                watermark = COALESCE(EXCLUDED.watermark, sync_job_state.watermark),
                last_success_at = now(),
                last_result_json = EXCLUDED.last_result_json,
                updated_at = now()
        """, (job_name, watermark, payload_text))
        pg_conn.commit()
    finally:
        cur.close()


def load_terminal_lookups(pg_conn, airport_code):
    """Pre-load active gate -> terminal and stand -> terminal lookups once per sync batch."""
    gate_terminals, stand_terminals = {}, {}
    cur = pg_conn.cursor()
    try:
        cur.execute("""
            SELECT TRIM(gate_code), NULLIF(TRIM(terminal_code), '')
            FROM dim_gate
            WHERE deleted_at IS NULL AND airport_code = %s
              AND NULLIF(TRIM(gate_code), '') IS NOT NULL
              AND NULLIF(TRIM(terminal_code), '') IS NOT NULL
        """, (airport_code,))
        for gate_code, term in cur.fetchall():
            gate_terminals.setdefault(gate_code, term)

        cur.execute("""
            SELECT TRIM(stand_code), NULLIF(TRIM(terminal_code), '')
            FROM dim_stand
            WHERE deleted_at IS NULL AND airport_code = %s
              AND NULLIF(TRIM(stand_code), '') IS NOT NULL
              AND NULLIF(TRIM(terminal_code), '') IS NOT NULL
        """, (airport_code,))
        for stand_code, term in cur.fetchall():
            stand_terminals.setdefault(stand_code, term)
        return gate_terminals, stand_terminals
    finally:
        cur.close()


def _resolve_terminal(d, gate_terminals, stand_terminals):
    if d.get('terminal'):
        return d['terminal']
    gate = trim(d.get('gate_code'))
    if gate and gate in gate_terminals:
        return gate_terminals[gate]
    stand = trim(d.get('stand_code'))
    if stand and stand in stand_terminals:
        return stand_terminals[stand]
    return None


def upsert_airlines(pg_conn, rows):
    now_utc = datetime.now(timezone.utc)
    cur = pg_conn.cursor()
    count = 0
    try:
        for r in rows:
            iata = upper(r.get('TWO_CHAR_CD'))
            if not iata:
                continue
            cur.execute("""
                INSERT INTO dim_airline (iata_code, icao_code, attribute, name_zh, name_en, created_at, updated_at, deleted_at)
                VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()), %s)
                ON CONFLICT (iata_code) DO UPDATE SET
                    icao_code = EXCLUDED.icao_code,
                    attribute = EXCLUDED.attribute,
                    name_zh = EXCLUDED.name_zh,
                    name_en = EXCLUDED.name_en,
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                iata, upper(r.get('THREE_CHAR_CD')), trim(r.get('ATTRIBUTE')),
                trim(r.get('CHINESE_NAME')), trim(r.get('ENGLISH_NAME')),
                to_utc_dt(r.get('CREATE_TIME')), to_utc_dt(r.get('UPDATE_TIME')),
                now_utc if is_deleted(r.get('IS_DELETE')) else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_airports(pg_conn, position_rows, airport_rows):
    now_utc = datetime.now(timezone.utc)
    positions = {}
    for r in position_rows:
        iata = upper(r.get('AIRPORT_IATA'))
        if not iata:
            continue
        positions[iata] = {
            'icao': upper(r.get('AIRPORT_ICAO')),
            'name_zh': trim(r.get('CHINESE_NAME')),
            'name_en': trim(r.get('ENGLISH_NAME')),
            'purpose': trim(r.get('PURPOSE')),
            'is_open': yn_flag(r.get('IS_OPEN')),
            'attribute': trim(r.get('ATTRIBUTE')),
            'country_code': trim(r.get('IN_COUNTRY_CD')),
            'country_name': trim(r.get('IN_COUNTRY_NAME')),
            'city_code': trim(r.get('IN_CITY_CD')),
            'city_name': trim(r.get('IN_CITY_NAME')),
            'province_code': trim(r.get('IN_PROVINCE_CD')),
            'province_name': trim(r.get('IN_PROVINCE_NAME')),
            'longitude': clip(r.get('LONGITUDE'), 100),
            'latitude': clip(r.get('LATITUDE'), 100),
            'create_time': to_utc_dt(r.get('CREATE_TIME')),
            'update_time': to_utc_dt(r.get('UPDATE_TIME')),
            'deleted': is_deleted(r.get('IS_DELETE')),
        }
    position_loaded = len(positions)
    count = 0
    cur = pg_conn.cursor()
    try:
        for r in airport_rows:
            iata = upper(r.get('AIRPORT_IATA'))
            if not iata:
                continue
            p = positions.pop(iata, None)
            deleted = is_deleted(r.get('IS_DELETE')) or bool(p and p['deleted'])
            city_name = p['city_name'] if (p and p['city_name']) else trim(r.get('IN_CITY'))
            cur.execute("""
                INSERT INTO dim_airport (
                    iata_code, icao_code, attribute, name_zh, name_en, short_name,
                    city_name, city_code, province_code, province_name, country_code, country_name,
                    is_open, purpose, longitude, latitude, created_at, updated_at, deleted_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()), %s
                )
                ON CONFLICT (iata_code) DO UPDATE SET
                    icao_code = COALESCE(EXCLUDED.icao_code, dim_airport.icao_code),
                    attribute = COALESCE(EXCLUDED.attribute, dim_airport.attribute),
                    name_zh = COALESCE(EXCLUDED.name_zh, dim_airport.name_zh),
                    name_en = COALESCE(EXCLUDED.name_en, dim_airport.name_en),
                    short_name = COALESCE(EXCLUDED.short_name, dim_airport.short_name),
                    city_name = COALESCE(EXCLUDED.city_name, dim_airport.city_name),
                    city_code = COALESCE(EXCLUDED.city_code, dim_airport.city_code),
                    province_code = COALESCE(EXCLUDED.province_code, dim_airport.province_code),
                    province_name = COALESCE(EXCLUDED.province_name, dim_airport.province_name),
                    country_code = COALESCE(EXCLUDED.country_code, dim_airport.country_code),
                    country_name = COALESCE(EXCLUDED.country_name, dim_airport.country_name),
                    is_open = COALESCE(EXCLUDED.is_open, dim_airport.is_open),
                    purpose = COALESCE(EXCLUDED.purpose, dim_airport.purpose),
                    longitude = COALESCE(EXCLUDED.longitude, dim_airport.longitude),
                    latitude = COALESCE(EXCLUDED.latitude, dim_airport.latitude),
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                iata,
                first_non_blank(upper(r.get('AIRPORT_ICAO')), p['icao'] if p else None),
                first_non_blank(trim(r.get('ATTRIBUTE')), p['attribute'] if p else None),
                first_non_blank(trim(r.get('CHINESE_NAME')), p['name_zh'] if p else None),
                first_non_blank(trim(r.get('ENGLISH_NAME')), p['name_en'] if p else None),
                trim(r.get('SHORT_NAME')),
                city_name,
                p['city_code'] if p else None,
                p['province_code'] if p else None,
                p['province_name'] if p else None,
                p['country_code'] if p else None,
                p['country_name'] if p else None,
                p['is_open'] if (p and p['is_open'] is not None) else yn_flag(r.get('IS_OPEN')),
                p['purpose'] if p else None,
                p['longitude'] if p else None,
                p['latitude'] if p else None,
                to_utc_dt(r.get('CREATE_TIME')),
                first_ts(to_utc_dt(r.get('UPDATE_TIME')), p['update_time'] if p else None),
                now_utc if deleted else None,
            ))
            count += 1

        for iata, p in positions.items():
            cur.execute("""
                INSERT INTO dim_airport (
                    iata_code, icao_code, attribute, name_zh, name_en,
                    city_name, city_code, province_code, province_name, country_code, country_name,
                    is_open, purpose, longitude, latitude, created_at, updated_at, deleted_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()), %s
                )
                ON CONFLICT (iata_code) DO UPDATE SET
                    icao_code = COALESCE(EXCLUDED.icao_code, dim_airport.icao_code),
                    name_zh = COALESCE(EXCLUDED.name_zh, dim_airport.name_zh),
                    name_en = COALESCE(EXCLUDED.name_en, dim_airport.name_en),
                    city_name = COALESCE(EXCLUDED.city_name, dim_airport.city_name),
                    city_code = COALESCE(EXCLUDED.city_code, dim_airport.city_code),
                    province_code = COALESCE(EXCLUDED.province_code, dim_airport.province_code),
                    province_name = COALESCE(EXCLUDED.province_name, dim_airport.province_name),
                    country_code = COALESCE(EXCLUDED.country_code, dim_airport.country_code),
                    country_name = COALESCE(EXCLUDED.country_name, dim_airport.country_name),
                    is_open = COALESCE(EXCLUDED.is_open, dim_airport.is_open),
                    purpose = COALESCE(EXCLUDED.purpose, dim_airport.purpose),
                    longitude = COALESCE(EXCLUDED.longitude, dim_airport.longitude),
                    latitude = COALESCE(EXCLUDED.latitude, dim_airport.latitude),
                    updated_at = now(),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                iata, p['icao'], p['attribute'], p['name_zh'], p['name_en'],
                p['city_name'], p['city_code'], p['province_code'], p['province_name'],
                p['country_code'], p['country_name'], p['is_open'], p['purpose'],
                p['longitude'], p['latitude'], p['create_time'], p['update_time'],
                now_utc if p['deleted'] else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count, 'positionLoaded': position_loaded}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_agencies(pg_conn, rows):
    now_utc = datetime.now(timezone.utc)
    cur = pg_conn.cursor()
    count = 0
    try:
        for r in rows:
            code = upper(r.get('CODE'))
            if not code:
                continue
            cur.execute("""
                INSERT INTO dim_agency (code, name_zh, name_en, description, agent_sort, created_at, updated_at, deleted_at)
                VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()), %s)
                ON CONFLICT (code) DO UPDATE SET
                    name_zh = EXCLUDED.name_zh,
                    name_en = EXCLUDED.name_en,
                    description = EXCLUDED.description,
                    agent_sort = EXCLUDED.agent_sort,
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                code, trim(r.get('CHINESE_NAME')), trim(r.get('ENGLISH_NAME')),
                trim(r.get('DESCRIPTION')), trim(r.get('AGENT_SORT')),
                to_utc_dt(r.get('CREATE_TIME')), to_utc_dt(r.get('UPDATE_TIME')),
                now_utc if is_deleted(r.get('IS_DELETE')) else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_abnormal_reasons(pg_conn, rows):
    now_utc = datetime.now(timezone.utc)
    cur = pg_conn.cursor()
    count = 0
    try:
        for r in rows:
            code = upper(r.get('CODE'))
            if not code:
                continue
            is_super = trim(r.get('IS_SUPER'))
            super_flag = (is_super.upper() == 'Y' or is_super == '1') if is_super else None
            cur.execute("""
                INSERT INTO dim_abnormal_reason (
                    code, name_zh, name_en, reason_type, is_super, parent_code,
                    created_at, updated_at, deleted_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    COALESCE(%s, now()), COALESCE(%s, now()), %s
                )
                ON CONFLICT (code) DO UPDATE SET
                    name_zh = EXCLUDED.name_zh,
                    name_en = EXCLUDED.name_en,
                    reason_type = EXCLUDED.reason_type,
                    is_super = EXCLUDED.is_super,
                    parent_code = EXCLUDED.parent_code,
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                code, trim(r.get('CHINESE_NAME')), trim(r.get('ENGLISH_NAME')),
                trim(r.get('TYPE')), super_flag, upper(r.get('SUPER_CODE')),
                to_utc_dt(r.get('CREATE_TIME')), to_utc_dt(r.get('UPDATE_TIME')),
                now_utc if is_deleted(r.get('IS_DELETE')) else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_aircraft(pg_conn, rows):
    now_utc = datetime.now(timezone.utc)
    cur = pg_conn.cursor()
    count = 0
    try:
        for r in rows:
            reg = upper(r.get('CODE'))
            if not reg:
                continue
            cur.execute("""
                INSERT INTO dim_aircraft (
                    reg_no, aircraft_icao, airline_iata, airline_branch,
                    seat_max, seat_ops, mtow, max_payload, ops_payload,
                    created_at, updated_at, deleted_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    COALESCE(%s, now()), COALESCE(%s, now()), %s
                )
                ON CONFLICT (reg_no) DO UPDATE SET
                    aircraft_icao = EXCLUDED.aircraft_icao,
                    airline_iata = EXCLUDED.airline_iata,
                    airline_branch = EXCLUDED.airline_branch,
                    seat_max = EXCLUDED.seat_max,
                    seat_ops = EXCLUDED.seat_ops,
                    mtow = EXCLUDED.mtow,
                    max_payload = EXCLUDED.max_payload,
                    ops_payload = EXCLUDED.ops_payload,
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                reg, upper(r.get('CRAFT_TYPE_ICAO_CD')), upper(r.get('AIRLINE_TWO_CHAR_CD')),
                trim(r.get('AIRLINE_BRANCH')),
                int(r['SEAT_MAX_NUMBER']) if r.get('SEAT_MAX_NUMBER') is not None else None,
                int(r['SUPPORT_SEAT_NUMBER']) if r.get('SUPPORT_SEAT_NUMBER') is not None else None,
                r.get('MAX_TAKEOFF_WEIGHT'), r.get('MAX_LOAD_WEIGHT'), r.get('SUPPORT_LOAD_WEIGHT'),
                to_utc_dt(r.get('CREATE_TIME')), to_utc_dt(r.get('UPDATE_TIME')),
                now_utc if is_deleted(r.get('IS_DELETE')) else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_stands(pg_conn, rows, airport_code):
    now_utc = datetime.now(timezone.utc)
    cur = pg_conn.cursor()
    count = 0
    bridge = 0
    try:
        for r in rows:
            code = upper(r.get('CODE'))
            if not code:
                continue
            remote = yn_flag(r.get('IS_REMOTE'))
            bridge_stand = (not remote) if remote is not None else None
            if bridge_stand:
                bridge += 1
            cur.execute("""
                INSERT INTO dim_stand (
                    airport_code, stand_code, name_zh, name_en,
                    is_remote, is_bridge_stand, terminal_code, status,
                    created_at, updated_at, deleted_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    COALESCE(%s, now()), COALESCE(%s, now()), %s
                )
                ON CONFLICT (airport_code, stand_code) DO UPDATE SET
                    name_zh = EXCLUDED.name_zh,
                    name_en = EXCLUDED.name_en,
                    is_remote = EXCLUDED.is_remote,
                    is_bridge_stand = EXCLUDED.is_bridge_stand,
                    terminal_code = EXCLUDED.terminal_code,
                    status = EXCLUDED.status,
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                airport_code, code, trim(r.get('CHINESE_NAME')), trim(r.get('ENGLISH_NAME')),
                remote, bridge_stand, upper(r.get('TERMINAL_CD')), trim(r.get('STATUS')),
                to_utc_dt(r.get('CREATE_TIME')), to_utc_dt(r.get('UPDATE_TIME')),
                now_utc if is_deleted(r.get('IS_DELETE')) else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count, 'bridgeStands': bridge}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_gates(pg_conn, rows, airport_code):
    now_utc = datetime.now(timezone.utc)
    cur = pg_conn.cursor()
    count = 0
    try:
        for r in rows:
            code = upper(r.get('CODE'))
            if not code:
                continue
            cur.execute("""
                INSERT INTO dim_gate (
                    airport_code, gate_code, source_id, name_zh, name_en,
                    attribute, terminal_code, status, agency,
                    created_at, updated_at, deleted_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    COALESCE(%s, now()), COALESCE(%s, now()), %s
                )
                ON CONFLICT (airport_code, gate_code) DO UPDATE SET
                    source_id = EXCLUDED.source_id,
                    name_zh = EXCLUDED.name_zh,
                    name_en = EXCLUDED.name_en,
                    attribute = EXCLUDED.attribute,
                    terminal_code = EXCLUDED.terminal_code,
                    status = EXCLUDED.status,
                    agency = EXCLUDED.agency,
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                airport_code, code, trim(str(r['ID'])) if r.get('ID') is not None else None,
                trim(r.get('CHINESE_NAME')), trim(r.get('ENGLISH_NAME')),
                trim(r.get('ATTRIBUTE')), upper(r.get('TERMINAL_CD')), trim(r.get('STATUS')),
                upper(r.get('AGENCY')),
                to_utc_dt(r.get('CREATE_TIME')), to_utc_dt(r.get('UPDATE_TIME')),
                now_utc if is_deleted(r.get('IS_DELETE')) else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_air_bridges(pg_conn, rows, airport_code):
    """Sync FIMS.TB_AIR_BIRDGE into public.dim_air_bridge."""
    now_utc = datetime.now(timezone.utc)
    cur = pg_conn.cursor()
    count = 0
    try:
        for r in rows:
            code = upper(r.get('CODE'))
            if not code:
                continue
            cur.execute("""
                INSERT INTO dim_air_bridge (
                    airport_code, bridge_code, source_id, name_zh, name_en,
                    attribute, terminal_code, status,
                    created_at, updated_at, deleted_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    COALESCE(%s, now()), COALESCE(%s, now()), %s
                )
                ON CONFLICT (airport_code, bridge_code) DO UPDATE SET
                    source_id = EXCLUDED.source_id,
                    name_zh = EXCLUDED.name_zh,
                    name_en = EXCLUDED.name_en,
                    attribute = EXCLUDED.attribute,
                    terminal_code = EXCLUDED.terminal_code,
                    status = EXCLUDED.status,
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                airport_code, code, trim(str(r['ID'])) if r.get('ID') is not None else None,
                trim(r.get('CHINESE_NAME')), trim(r.get('ENGLISH_NAME')),
                trim(r.get('ATTRIBUTE')), upper(r.get('TERMINAL_CD')), trim(r.get('STATUS')),
                to_utc_dt(r.get('CREATE_TIME')), to_utc_dt(r.get('UPDATE_TIME')),
                now_utc if is_deleted(r.get('IS_DELETE')) else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_safeguard_nodes(pg_conn, rows):
    """Sync FIMS.TB_SAFEGUARD_NODE dictionary into public.dim_safeguard_node."""
    now_utc = datetime.now(timezone.utc)
    cur = pg_conn.cursor()
    count = 0
    try:
        for r in rows:
            code = trim(r.get('CODE'))
            if not code:
                continue
            cur.execute("""
                INSERT INTO dim_safeguard_node (
                    code, name, is_off_in, created_at, updated_at, deleted_at
                ) VALUES (
                    %s, %s, %s, COALESCE(%s, now()), COALESCE(%s, now()), %s
                )
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    is_off_in = EXCLUDED.is_off_in,
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                code, trim(r.get('NAME')), upper(r.get('IS_OFF_IN')),
                to_utc_dt(r.get('CREATE_TIME')), to_utc_dt(r.get('UPDATE_TIME')),
                now_utc if is_deleted(r.get('IS_DELETE')) else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_runways(pg_conn, rows):
    """Sync FIMS.TB_RUNWAY master table into public.dim_runway."""
    now_utc = datetime.now(timezone.utc)
    cur = pg_conn.cursor()
    count = 0
    try:
        for r in rows:
            code = upper(r.get('CODE'))
            if not code:
                continue
            cur.execute("""
                INSERT INTO dim_runway (
                    code, name, attribute, direction, length_m, width_m,
                    created_at, updated_at, deleted_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    COALESCE(%s, now()), COALESCE(%s, now()), %s
                )
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    attribute = EXCLUDED.attribute,
                    direction = EXCLUDED.direction,
                    length_m = EXCLUDED.length_m,
                    width_m = EXCLUDED.width_m,
                    updated_at = COALESCE(EXCLUDED.updated_at, now()),
                    deleted_at = EXCLUDED.deleted_at
            """, (
                code, trim(r.get('NAME')), trim(r.get('ATTRIBUTE')), trim(r.get('DIRECTION')),
                r.get('LENGTH'), r.get('WIDTH'),
                to_utc_dt(r.get('CREATE_TIME')), to_utc_dt(r.get('UPDATE_TIME')),
                now_utc if is_deleted(r.get('IS_DELETE')) else None,
            ))
            count += 1
        pg_conn.commit()
        return {'upserted': count}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def ensure_acdm_schema_extensions(pg_conn):
    """Idempotently add missing A-CDM columns/tables and views on PostgreSQL ops_ask."""
    cur = pg_conn.cursor()
    try:
        stmts = [
            "ALTER TABLE public.std_flight_core ADD COLUMN IF NOT EXISTS flight_task_cd VARCHAR(16)",
            "ALTER TABLE public.std_flight_core ADD COLUMN IF NOT EXISTS flight_attribute VARCHAR(16)",
            "ALTER TABLE public.std_flight_core ADD COLUMN IF NOT EXISTS cancel_period VARCHAR(32)",
            "ALTER TABLE public.std_flight_core ADD COLUMN IF NOT EXISTS plan_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_indicator ADD COLUMN IF NOT EXISTS ori_act BOOLEAN",
            "ALTER TABLE public.std_flight_indicator ADD COLUMN IF NOT EXISTS flight_first_on_time BOOLEAN",
            "ALTER TABLE public.std_flight_indicator ADD COLUMN IF NOT EXISTS flight_delay_minutes INTEGER",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS ctot TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS alterate_by_bridge_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_by_bridge_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS plan_off_bridge_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_off_bridge_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS departure_start_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS departure_end_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS alterate_open_cabin_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_open_cabin_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS alterate_close_cabin_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_close_cabin_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_start_clean_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_end_clean_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_meal_start_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_meal_end_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_start_supply_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_end_supply_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_start_deice_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_end_deicie_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS request_launch_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_launch_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_drive_time TIMESTAMPTZ",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_taxi_in_time NUMERIC(10,2)",
            "ALTER TABLE public.std_flight_process ADD COLUMN IF NOT EXISTS real_taxi_out_time NUMERIC(10,2)",
            """CREATE TABLE IF NOT EXISTS public.dim_safeguard_node (
                code VARCHAR(16) PRIMARY KEY,
                name VARCHAR(128),
                is_off_in VARCHAR(8),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                deleted_at TIMESTAMPTZ
            )""",
            """CREATE TABLE IF NOT EXISTS public.dim_runway (
                code VARCHAR(16) PRIMARY KEY,
                name VARCHAR(128),
                attribute VARCHAR(16),
                direction VARCHAR(32),
                length_m NUMERIC(10,2),
                width_m NUMERIC(10,2),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                deleted_at TIMESTAMPTZ
            )""",
            """CREATE TABLE IF NOT EXISTS public.std_flight_calc_total (
                source_flight_id BIGINT PRIMARY KEY,
                flight_id BIGINT REFERENCES public.std_flight_core(id) ON DELETE SET NULL,
                ffid VARCHAR(128),
                exec_date DATE,
                direction VARCHAR(8),
                airline_code VARCHAR(8),
                flight_no VARCHAR(32),
                dep_dpsr_depe_num INTEGER,
                psg_arrival_num INTEGER,
                dep_dpsr_checkin_num INTEGER,
                dep_dpsr_security_num INTEGER,
                dep_dpsr_boarded_num INTEGER,
                dep_dpsr_stop_num INTEGER,
                dep_dpsr_baby_num INTEGER,
                dep_dpsr_transfer_num INTEGER,
                dep_dpsr_vip_num INTEGER,
                dep_dpsr_first_class_num INTEGER,
                crew_number INTEGER,
                bag_num INTEGER,
                bag_weight NUMERIC(12,2),
                delay_duration INTEGER,
                real_open_cabin_time TIMESTAMPTZ,
                real_close_cabin_time TIMESTAMPTZ,
                first_baggage_time TIMESTAMPTZ,
                last_baggage_time TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_std_flight_calc_total_flight_id ON public.std_flight_calc_total(flight_id)",
            "CREATE INDEX IF NOT EXISTS idx_std_flight_calc_total_exec_date ON public.std_flight_calc_total(exec_date)",
            """CREATE TABLE IF NOT EXISTS public.std_flight_air_bridge (
                id BIGSERIAL PRIMARY KEY,
                source_flight_id BIGINT NOT NULL,
                flight_id BIGINT REFERENCES public.std_flight_core(id) ON DELETE SET NULL,
                order_cd INTEGER NOT NULL DEFAULT 1,
                air_bridge_id VARCHAR(64),
                air_bridge_cd VARCHAR(32),
                bridge_start_time TIMESTAMPTZ,
                bridge_end_time TIMESTAMPTZ,
                bridge_duration NUMERIC(10,2),
                power_start_time TIMESTAMPTZ,
                power_end_time TIMESTAMPTZ,
                ac_start_time TIMESTAMPTZ,
                ac_end_time TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (source_flight_id, order_cd, air_bridge_id, air_bridge_cd)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_std_flight_air_bridge_flight_id ON public.std_flight_air_bridge(flight_id)",
            "CREATE INDEX IF NOT EXISTS idx_std_flight_air_bridge_cd ON public.std_flight_air_bridge(air_bridge_cd)",
            """CREATE TABLE IF NOT EXISTS public.std_flight_safeguard_node (
                id BIGSERIAL PRIMARY KEY,
                source_flight_id BIGINT NOT NULL,
                flight_id BIGINT REFERENCES public.std_flight_core(id) ON DELETE SET NULL,
                safeguard_order_cd INTEGER NOT NULL DEFAULT 1,
                safeguard_code VARCHAR(16) NOT NULL,
                ffid VARCHAR(128),
                flight_task_cd VARCHAR(16),
                safeguard_alterate_time TIMESTAMPTZ,
                safeguard_real_time TIMESTAMPTZ,
                abnormal_start_time TIMESTAMPTZ,
                abnormal_end_time TIMESTAMPTZ,
                abnormal_reason VARCHAR(256),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (source_flight_id, safeguard_order_cd, safeguard_code)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_std_flight_safeguard_flight_id ON public.std_flight_safeguard_node(flight_id)",
            "CREATE INDEX IF NOT EXISTS idx_std_flight_safeguard_code ON public.std_flight_safeguard_node(safeguard_code)",
            "CREATE INDEX IF NOT EXISTS idx_std_flight_core_task_cd ON public.std_flight_core(flight_task_cd)",
            "ALTER TABLE public.std_flight_air_bridge DROP CONSTRAINT IF EXISTS std_flight_air_bridge_source_flight_id_order_cd_key",
            "CREATE UNIQUE INDEX IF NOT EXISTS uk_air_bridge_source_identity ON public.std_flight_air_bridge(source_flight_id,order_cd,air_bridge_id,air_bridge_cd)",
        ]
        for s in stmts:
            cur.execute(s)
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def refresh_ops_views(pg_conn):
    """Ensure ops_ask_views.flight_execution (excl. A/N) and mv_acdm_dyn_flight_detail stay up to date."""
    cur = pg_conn.cursor()
    try:
        cur.execute("""
            CREATE OR REPLACE VIEW public.mv_acdm_dyn_flight_detail AS
            WITH fpa_agg AS (
                SELECT
                    airport_code,flight_date,aircraft_reg,flight_no,leg,
                    SUM(CASE WHEN COALESCE(available_seat, 0) <> 0 THEN available_seat ELSE 0 END) AS fpa_available_seat,
                    SUM(CASE WHEN COALESCE(available_seat, 0) <> 0 THEN adult_count ELSE 0 END) AS fpa_adult_count,
                    SUM(CASE WHEN COALESCE(available_seat, 0) <> 0 THEN child_count ELSE 0 END) AS fpa_children_count,
                    SUM(CASE WHEN COALESCE(available_seat, 0) <> 0 THEN infant_count ELSE 0 END) AS fpa_baby_count,
                    SUM(CASE WHEN COALESCE(available_seat, 0) <> 0 THEN COALESCE(cargo_weight, 0) ELSE 0 END) AS fpa_goods,
                    SUM(CASE WHEN COALESCE(available_seat, 0) <> 0 THEN COALESCE(mail_weight, 0) ELSE 0 END) AS fpa_mail,
                    SUM(CASE WHEN COALESCE(available_seat, 0) <> 0 THEN COALESCE(baggage_weight, 0) ELSE 0 END) AS fpa_luggage
                FROM public.std_flight_production goods_weight_alias
                WHERE deleted_at IS NULL
                GROUP BY airport_code,flight_date,aircraft_reg,flight_no,leg
            ),
            turn_agg AS (
                SELECT DISTINCT ON (flight_id)
                    flight_id,
                    linked_flight_id,
                    linked_flight_identity
                FROM public.std_flight_link
                WHERE link_type = 'TURNAROUND'
                ORDER BY flight_id, id
            ),
            ct_agg AS (
                SELECT DISTINCT ON (flight_id)
                    flight_id,
                    dep_dpsr_depe_num,
                    psg_arrival_num,
                    bag_num,
                    bag_weight
                FROM public.std_flight_calc_total
                WHERE flight_id IS NOT NULL
                ORDER BY flight_id, dep_dpsr_depe_num DESC NULLS LAST, updated_at DESC, source_flight_id DESC
            )
            SELECT
                c.id AS pg_flight_id,
                CAST(NULLIF(c.primary_source_id, '') AS BIGINT) AS flight_id,
                raw_acdm."ASSOCIATED_FLIGHT_ID"::bigint AS associated_flight_id,
                turn.linked_flight_id AS linked_pg_flight_id,
                c.airport_code,
                (c.flight_date AT TIME ZONE 'Asia/Shanghai')::date AS exec_date,
                c.plan_time,
                CASE c.direction WHEN 'DEP' THEN 'D' WHEN 'ARR' THEN 'A' ELSE c.direction END AS is_off_in,
                c.direction,
                c.airline_code AS airline_two_char_cd,
                c.flight_no,
                c.flight_task_cd,
                c.flight_nature,
                c.flight_attribute AS attribute,
                c.flight_country_type,
                c.agency,
                c.aircraft_type AS craft_type_icao_cd,
                c.aircraft_reg AS craft_no,
                (raw_acdm."CRAFT_SITE_CD"::varchar(16)) AS craft_site_cd,
                CASE WHEN st.is_remote IS TRUE THEN 'Y' WHEN st.is_remote IS FALSE THEN 'N' ELSE NULL END AS craft_site_is_remote,
                (raw_acdm."GATE_CD"::varchar(16)) AS gate_cd,
                c.terminal,
                (raw_acdm."TAKEOFF_AIRPORT_IATA"::varchar(8)) AS takeoff_airport_iata,
                (raw_acdm."ARRIVAL_AIRPORT_IATA"::varchar(8)) AS arrival_airport_iata,
                (COALESCE(raw_acdm."ABNORMAL_FLIGHT_STAT_CD",raw_acdm."NORMAL_FLIGHT_STAT_CD")::varchar(32)) AS flight_status,
                c.operation_status AS normal_flight_stat_cd,
                raw_acdm."ABNORMAL_FLIGHT_STAT_CD"::varchar AS abnormal_flight_stat_cd,
                c.cancel_period AS s_cnl_stauts,
                c.delay_code AS abnormal_reason_cd,
                (raw_acdm."SCHEME_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS scheme_takeoff_time,
                (raw_acdm."ALTERATE_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS alterate_takeoff_time,
                (raw_acdm."REAL_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_takeoff_time,
                (raw_acdm."SCHEME_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') AS scheme_landin_time,
                (raw_acdm."ALTERATE_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') AS alterate_landin_time,
                (raw_acdm."REAL_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_landin_time,
                CASE WHEN c.direction = 'DEP' THEN (raw_acdm."REAL_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') WHEN c.direction = 'ARR' THEN (raw_acdm."REAL_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') END AS real_takeoff_landin_time,
                (((CASE WHEN c.direction = 'DEP' THEN (raw_acdm."REAL_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') WHEN c.direction = 'ARR' THEN (raw_acdm."REAL_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') END) AT TIME ZONE 'Asia/Shanghai') - INTERVAL '5 hours')::date AS operation_real_takeoff_landin_time,
                ((COALESCE(c.plan_time, c.flight_scheduled_datetime) AT TIME ZONE 'Asia/Shanghai') - INTERVAL '5 hours')::date AS operation_plan_time,
                CASE
                    WHEN c.deleted_at IS NULL
                     AND COALESCE(raw_acdm."ABNORMAL_FLIGHT_STAT_CD", '') <> 'CAN'
                     AND COALESCE(c.flight_task_cd, '') <> 'A/N'
                     AND NOT COALESCE(c.is_virtual, false)
                     AND ((c.direction = 'DEP' AND (raw_acdm."REAL_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') IS NOT NULL)
                       OR (c.direction = 'ARR' AND (raw_acdm."REAL_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') IS NOT NULL))
                    THEN 'Y' ELSE 'N'
                END AS is_operated_flight,
                CASE WHEN EXISTS (
                    SELECT 1 FROM public.std_flight_source_ref sr
                    WHERE sr.flight_id = c.id AND sr.source_system = 'FIMS_ORACLE'
                ) THEN 'Y' ELSE 'N' END AS has_fims_source,
                raw_acdm."ORI_ACT"::text AS ori_act,
                raw_acdm."FLIGHT_FIRST_ON_TIME"::text AS flight_first_on_time,
                raw_acdm."RELEASE_ON_TIME"::text AS release_on_time,
                raw_acdm."TAKEOFF_ON_TIME"::text AS takeoff_on_time,
                raw_acdm."DEP_ON_TIME"::text AS dep_on_time,
                raw_acdm."LAND_ON_TIME"::text AS land_on_time,
                raw_acdm."ARR_ON_TIME"::text AS arr_on_time,
                raw_acdm."FLIGHT_ON_TIME"::text AS flight_on_time,
                raw_acdm."CLEARANCE_DELAY_TIME"::integer AS clearance_delay_time,
                raw_acdm."FLIGHT_DELAY_TIME"::integer AS flight_delay_time,
                raw_acdm."TAKEOFF_DELAY_TIME"::integer AS takeoff_delay_time,
                raw_acdm."DEP_DELAY_TIME"::integer AS dep_delay_time,
                raw_acdm."LAND_DELAY_TIME"::integer AS land_delay_time,
                raw_acdm."ARR_DELAY_TIME"::integer AS arr_delay_time,
                (raw_acdm."REAL_BLOCK_ON_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_block_on_time,
                (raw_acdm."REAL_BLOCK_OFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_block_off_time,
                (raw_acdm."ALTERATE_BLOCK_OFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS alterate_block_off_time,
                (raw_acdm."TARGT_BLOCK_OFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS targt_block_off_time,
                (raw_acdm."TARGT_DRIVE_TIME" AT TIME ZONE 'Asia/Shanghai') AS targt_drive_time,
                (raw_acdm."TARGT_TAKE_OFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS targt_take_off_time,
                (raw_acdm."COUNT_BLOCK_OFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS count_block_off_time,
                (raw_acdm."COUNT_TAKE_OFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS count_take_off_time,
                (raw_acdm."REAL_BEGIN_BOARD_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_begin_board_time,
                (raw_acdm."REAL_END_BOARD_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_end_board_time,
                (raw_acdm."URGE_BOARD_TIME" AT TIME ZONE 'Asia/Shanghai') AS urge_board_time,
                (raw_acdm."CHECKIN_START_TIME" AT TIME ZONE 'Asia/Shanghai') AS checkin_start_realtime,
                (raw_acdm."CHECKIN_END_TIME" AT TIME ZONE 'Asia/Shanghai') AS checkin_end_realtime,
                (raw_acdm."REAL_BY_BRIDGE_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_by_bridge_time,
                (raw_acdm."REAL_OFF_BRIDGE_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_off_bridge_time,
                (raw_acdm."DEPARTURE_START_TIME" AT TIME ZONE 'Asia/Shanghai') AS departure_start_time,
                (raw_acdm."DEPARTURE_END_TIME" AT TIME ZONE 'Asia/Shanghai') AS departure_end_time,
                (raw_acdm."ALTERATE_OPEN_CABIN_TIME" AT TIME ZONE 'Asia/Shanghai') AS alterate_open_cabin_time,
                (raw_acdm."REAL_OPEN_CABIN_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_open_cabin_time,
                (raw_acdm."ALTERATE_CLOSE_CABIN_TIME" AT TIME ZONE 'Asia/Shanghai') AS alterate_close_cabin_time,
                (raw_acdm."REAL_CLOSE_CABIN_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_close_cabin_time,
                (raw_acdm."REAL_START_CLEAN_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_start_clean_time,
                (raw_acdm."REAL_END_CLEAN_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_end_clean_time,
                (raw_acdm."REAL_MEAL_START_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_meal_start_time,
                (raw_acdm."REAL_MEAL_END_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_meal_end_time,
                (raw_acdm."REAL_START_SUPPLY_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_start_supply_time,
                (raw_acdm."REAL_END_SUPPLY_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_end_supply_time,
                (raw_acdm."REAL_START_DEICE_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_start_deice_time,
                (raw_acdm."REAL_END_DEICIE_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_end_deicie_time,
                (raw_acdm."REQUEST_LAUNCH_TIME" AT TIME ZONE 'Asia/Shanghai') AS request_launch_time,
                (raw_acdm."REAL_LAUNCH_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_launch_time,
                (raw_acdm."REAL_DRIVE_TIME" AT TIME ZONE 'Asia/Shanghai') AS real_drive_time,
                raw_acdm."REAL_TAXI_IN_TIME"::numeric(10,2) AS real_taxi_in_time,
                raw_acdm."REAL_TAXI_OUT_TIME"::numeric(10,2) AS real_taxi_out_time,
                ct.dep_dpsr_depe_num,
                ct.psg_arrival_num,
                ct.bag_num,
                ct.bag_weight,
                fpa.fpa_available_seat,
                fpa.fpa_adult_count,
                fpa.fpa_children_count,
                fpa.fpa_baby_count,
                fpa.fpa_goods,
                fpa.fpa_mail,
                fpa.fpa_luggage,
                c.flight_date,
                (COALESCE(c.plan_time, c.flight_scheduled_datetime) AT TIME ZONE 'Asia/Shanghai')::date AS plan_day,
                ((raw_acdm."REAL_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')::date AS real_takeoff_day,
                ((raw_acdm."REAL_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')::date AS real_landin_day,
                ((CASE WHEN c.direction = 'DEP' THEN (raw_acdm."REAL_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') ELSE (raw_acdm."REAL_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') END) AT TIME ZONE 'Asia/Shanghai')::date AS real_exec_day,
                c.airline_code,
                c.aircraft_type AS aircraft_type,
                (raw_acdm."CRAFT_SITE_CD"::varchar(16)) AS stand_code,
                (raw_acdm."GATE_CD"::varchar(16)) AS gate_code,
                (raw_acdm."TAKEOFF_AIRPORT_IATA"::varchar(8)) AS origin_airport,
                c.origin_city,
                (raw_acdm."ARRIVAL_AIRPORT_IATA"::varchar(8)) AS dest_airport,
                c.dest_city,
                (raw_acdm."SCHEME_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS scheduled_departure,
                (raw_acdm."SCHEME_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') AS scheduled_arrival,
                (raw_acdm."REAL_TAKEOFF_TIME" AT TIME ZONE 'Asia/Shanghai') AS actual_departure,
                (raw_acdm."REAL_LANDIN_TIME" AT TIME ZONE 'Asia/Shanghai') AS actual_arrival,
                ind.is_delayed,
                ind.delay_reason,
                c.deleted_at,
                c.is_virtual
            FROM public.std_flight_core c
            LEFT JOIN acdm.tb_dyn_flight raw_acdm ON raw_acdm."FLIGHT_ID" = CAST(NULLIF(c.primary_source_id, '') AS numeric)
            LEFT JOIN public.std_flight_indicator ind ON ind.flight_id = c.id
            LEFT JOIN public.std_flight_process p ON p.flight_id = c.id
            LEFT JOIN public.dim_stand st ON st.airport_code = c.airport_code AND st.stand_code = (raw_acdm."CRAFT_SITE_CD"::varchar(16)) AND st.deleted_at IS NULL
            -- Production has no FLIGHT_ID; retain the handbook's native date/aircraft/number/segment join.
            LEFT JOIN fpa_agg fpa ON fpa.airport_code=c.airport_code AND fpa.flight_date=raw_acdm."EXEC_DATE"::date
                AND fpa.aircraft_reg=c.aircraft_reg AND fpa.flight_no=c.flight_no
                AND fpa.leg=raw_acdm."TAKEOFF_AIRPORT_IATA"||'-'||raw_acdm."ARRIVAL_AIRPORT_IATA"
            LEFT JOIN ct_agg ct ON ct.flight_id = c.id
            LEFT JOIN turn_agg turn ON turn.flight_id = c.id
            WHERE c.deleted_at IS NULL AND EXISTS (
                SELECT 1 FROM public.std_flight_source_ref fims_source
                WHERE fims_source.flight_id = c.id AND fims_source.source_system = 'FIMS_ORACLE'
            )
        """)
        cur.execute("""
            CREATE OR REPLACE VIEW ops_ask_views.flight_execution AS
            SELECT f.id,
                   f.flight_no,
                   f.flight_date,
                   f.direction,
                   m.scheduled_departure,
                   m.actual_departure,
                   m.scheduled_arrival,
                   m.actual_arrival,
                   m.flight_status,
                   f.source_system,
                   f.vendor_code,
                   f.created_at,
                   f.updated_at,
                   m.aircraft_type,
                   m.craft_no AS aircraft_reg,
                   m.stand_code,
                   f.link_id,
                   f.partner_flight_no,
                   f.is_link_turn,
                   f.origin_city,
                   f.flight_nature,
                   f.airline_code,
                   f.terminal,
                   f.dest_city,
                   f.via_city,
                   f.estimated_departure,
                   f.estimated_arrival,
                   f.board_time,
                   f.cobt,
                   f.airport_code,
                   f.biz_key,
                   f.flight_identity,
                   f.airline_icao,
                   f.primary_source_id,
                   f.flight_country_type,
                   f.caac_service_type,
                   f.iata_service_type,
                   f.aircraft_iata,
                   f.aircraft_icao,
                   f.call_sign,
                   f.agency,
                   f.is_transit,
                   f.is_overnight,
                   f.is_origin,
                   f.is_divert,
                   f.is_virtual,
                   f.is_ferry,
                   m.origin_airport,
                   f.previous_airport,
                   f.next_airport,
                   m.dest_airport,
                   f.full_route,
                   f.aircraft_terminal,
                   m.gate_code,
                   f.runway,
                   f.baggage_reclaim,
                   f.flight_scheduled_datetime,
                   f.operation_status,
                   f.delay_code,
                   f.delay_reason_text,
                   f.diversion_airport,
                   f.change_landing_airport,
                   f.display_code,
                   f.public_comment,
                   f.deleted_at,
                   f.row_version,
                   f.last_event_time,
                   m.real_takeoff_landin_time AS execution_time
            FROM public.std_flight_core f JOIN public.mv_acdm_dyn_flight_detail m ON m.pg_flight_id=f.id
            WHERE m.is_operated_flight='Y'
        """)
        cur.execute("""
            CREATE OR REPLACE VIEW public.mv_acdm_flight_calendar AS
            SELECT f.*, (event.event_time AT TIME ZONE 'Asia/Shanghai')::date AS calendar_day,
                   event.planned_measure, event.operated_measure, event.event_time,
                   EXTRACT(HOUR FROM (event.event_time AT TIME ZONE 'Asia/Shanghai'))::integer AS hour_of_day
            FROM public.mv_acdm_dyn_flight_detail f
            CROSS JOIN LATERAL (VALUES
                (COALESCE(f.plan_time,CASE WHEN f.is_off_in='D' THEN f.scheme_takeoff_time ELSE f.scheme_landin_time END),
                 CASE WHEN f.flight_task_cd<>'A/N' THEN 1 ELSE 0 END,0),
                (f.real_takeoff_landin_time,0,CASE WHEN f.is_operated_flight='Y' THEN 1 ELSE 0 END)
            ) event(event_time,planned_measure,operated_measure)
            WHERE event.event_time IS NOT NULL AND (event.planned_measure>0 OR event.operated_measure>0)
        """)
        cur.execute("DROP VIEW IF EXISTS public.mv_acdm_turnaround_pair")
        cur.execute("""
            CREATE VIEW public.mv_acdm_turnaround_pair AS
            SELECT
                a.pg_flight_id AS pair_id,
                a.pg_flight_id AS arr_pg_flight_id,
                d.pg_flight_id AS dep_pg_flight_id,
                a.flight_no AS arr_flight_no,
                d.flight_no AS dep_flight_no,
                d.flight_no AS flight_no,
                (a.flight_date AT TIME ZONE 'Asia/Shanghai')::date AS arr_exec_date,
                (d.flight_date AT TIME ZONE 'Asia/Shanghai')::date AS dep_exec_date,
                ((a.flight_date AT TIME ZONE 'Asia/Shanghai')::date = (d.flight_date AT TIME ZONE 'Asia/Shanghai')::date) AS same_exec_date,
                (a.actual_arrival AT TIME ZONE 'Asia/Shanghai')::date AS arr_landin_day,
                (d.actual_departure AT TIME ZONE 'Asia/Shanghai')::date AS dep_takeoff_day,
                a.flight_date AS flight_date,
                a.airport_code AS airport_code,
                'DEP'::text AS direction,
                CASE WHEN a.is_operated_flight='Y' AND d.is_operated_flight='Y' THEN 'Y' ELSE 'N' END AS is_both_operated,
                COALESCE(d.airline_code, a.airline_code) AS airline_two_char_cd,
                COALESCE(d.airline_code, a.airline_code) AS airline_code,
                COALESCE(d.aircraft_type, a.aircraft_type) AS craft_type_icao_cd,
                COALESCE(d.aircraft_type, a.aircraft_type) AS aircraft_type,
                COALESCE(d.craft_no, a.craft_no) AS craft_no,
                COALESCE(d.terminal, a.terminal) AS terminal,
                COALESCE(d.stand_code, a.stand_code) AS craft_site_cd,
                COALESCE(d.stand_code, a.stand_code) AS stand_code,
                COALESCE(d.gate_code, a.gate_code) AS gate_cd,
                COALESCE(d.gate_code, a.gate_code) AS gate_code,
                d.agency,
                d.attribute AS attribute,
                d.flight_task_cd,
                d.origin_airport AS takeoff_airport_iata,
                d.origin_airport AS origin_airport,
                d.origin_city AS origin_city,
                d.dest_airport AS arrival_airport_iata,
                d.dest_airport AS dest_airport,
                d.dest_city AS dest_city,
                d.scheduled_departure AS scheduled_departure,
                a.scheduled_arrival AS scheduled_arrival,
                d.release_on_time AS pair_release_on_time,
                a.scheduled_arrival AS arr_scheme_landin_time,
                d.scheduled_departure AS dep_scheme_takeoff_time,
                a.real_block_on_time AS real_block_on_time,
                d.real_block_off_time AS real_block_off_time,
                (a.real_block_on_time IS NOT NULL AND d.real_block_off_time IS NOT NULL AND d.real_block_off_time > a.real_block_on_time) AS valid_block_sequence,
                CASE
                    WHEN d.real_block_off_time > a.real_block_on_time
                    THEN ROUND((EXTRACT(EPOCH FROM (d.real_block_off_time - a.real_block_on_time)) / 60.0)::numeric, 2)
                END AS service_minutes,
                CASE
                    WHEN a.real_off_bridge_time > a.real_by_bridge_time
                    THEN ROUND((EXTRACT(EPOCH FROM (a.real_off_bridge_time - a.real_by_bridge_time)) / 60.0)::numeric, 2)
                END AS bridge_minutes,
                CASE
                    WHEN a.departure_end_time > a.departure_start_time
                    THEN ROUND((EXTRACT(EPOCH FROM (a.departure_end_time - a.departure_start_time)) / 60.0)::numeric, 2)
                END AS departure_minutes,
                CASE
                    WHEN d.real_end_board_time > d.real_begin_board_time
                    THEN ROUND((EXTRACT(EPOCH FROM (d.real_end_board_time - d.real_begin_board_time)) / 60.0)::numeric, 2)
                END AS board_minutes,
                CASE
                    WHEN d.real_end_clean_time > d.real_start_clean_time
                    THEN ROUND((EXTRACT(EPOCH FROM (d.real_end_clean_time - d.real_start_clean_time)) / 60.0)::numeric, 2)
                END AS clean_minutes,
                CASE
                    WHEN d.real_meal_end_time > d.real_meal_start_time
                    THEN ROUND((EXTRACT(EPOCH FROM (d.real_meal_end_time - d.real_meal_start_time)) / 60.0)::numeric, 2)
                END AS meal_minutes,
                CASE
                    WHEN d.real_end_supply_time > d.real_start_supply_time
                    THEN ROUND((EXTRACT(EPOCH FROM (d.real_end_supply_time - d.real_start_supply_time)) / 60.0)::numeric, 2)
                END AS supply_minutes,
                CASE
                    WHEN d.real_end_deicie_time > d.real_start_deice_time
                    THEN ROUND((EXTRACT(EPOCH FROM (d.real_end_deicie_time - d.real_start_deice_time)) / 60.0)::numeric, 2)
                END AS deicie_minutes,
                CASE
                    WHEN d.scheduled_departure IS NOT NULL AND a.scheduled_arrival IS NOT NULL
                    THEN ROUND((EXTRACT(EPOCH FROM (d.scheduled_departure - a.scheduled_arrival)) / 60.0)::numeric, 2)
                END AS sched_turnaround_minutes,
                (UPPER(COALESCE(d.aircraft_type, a.aircraft_type, '')) IN ('B738', 'B788', 'A320', 'A321', 'A320/1', 'C919')) AS is_key_turnaround_aircraft,
                a.actual_arrival AS actual_arrival
            FROM public.mv_acdm_dyn_flight_detail a
            JOIN public.mv_acdm_dyn_flight_detail d
              ON d.flight_id = a.associated_flight_id AND d.direction = 'DEP'
            WHERE a.direction = 'ARR'
              AND a.craft_no = d.craft_no
              AND a.flight_task_cd<>'A/N' AND d.flight_task_cd<>'A/N'
              AND COALESCE(a.abnormal_flight_stat_cd,'')<>'CAN' AND COALESCE(d.abnormal_flight_stat_cd,'')<>'CAN'
        """)
        cur.execute("DROP VIEW IF EXISTS ops_ask_views.flight_hourly_movements")
        cur.execute("""
            CREATE VIEW ops_ask_views.flight_hourly_movements AS
            SELECT
                c.pg_flight_id AS id,
                c.flight_no,
                c.airport_code,
                c.flight_date,
                (c.flight_date AT TIME ZONE 'Asia/Shanghai')::date AS exec_date,
                ((CASE WHEN c.direction = 'DEP' THEN c.actual_departure ELSE c.actual_arrival END) AT TIME ZONE 'Asia/Shanghai')::date AS movement_date,
                DATE_TRUNC('hour', (CASE WHEN c.direction = 'DEP' THEN c.actual_departure ELSE c.actual_arrival END) AT TIME ZONE 'Asia/Shanghai') AS peak_hour,
                EXTRACT(HOUR FROM ((CASE WHEN c.direction = 'DEP' THEN c.actual_departure ELSE c.actual_arrival END) AT TIME ZONE 'Asia/Shanghai'))::integer AS hour_of_day,
                (CASE WHEN c.direction = 'DEP' THEN c.actual_departure ELSE c.actual_arrival END) AS scheduled_departure,
                (CASE WHEN c.direction = 'DEP' THEN c.actual_departure ELSE c.actual_arrival END) AS scheduled_arrival,
                c.direction,
                c.airline_code AS airline_two_char_cd,
                c.airline_code,
                c.agency,
                c.attribute AS attribute,
                c.flight_task_cd,
                c.aircraft_type AS craft_type_icao_cd,
                c.aircraft_type,
                c.stand_code AS craft_site_cd,
                c.stand_code,
                c.gate_code AS gate_cd,
                c.gate_code,
                c.terminal,
                c.origin_airport AS takeoff_airport_iata,
                c.origin_airport,
                c.origin_city,
                c.dest_airport AS arrival_airport_iata,
                c.dest_airport,
                c.dest_city
            FROM public.mv_acdm_dyn_flight_detail c
            WHERE c.is_operated_flight='Y'
        """)
        pg_conn.commit()
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def _table_exists(pg_conn, schema, table):
    cur = pg_conn.cursor()
    try:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f'{schema}.{table}',))
        row = cur.fetchone()
        return bool(row and row[0])
    finally:
        cur.close()


def table_exists(pg_conn, schema, table):
    return _table_exists(pg_conn, schema, table)


_BULK_ROW_CHUNK = 200


def _chunked(items, size=_BULK_ROW_CHUNK):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _flatten_params(rows):
    params = []
    for row in rows:
        params.extend(row)
    return tuple(params)


def _values_placeholders(row_tpl, count):
    return ', '.join([row_tpl] * count)


_CORE_INSERT_COLS = """
    biz_key, airport_code, flight_date, direction, flight_no, flight_identity,
    airline_code, flight_nature, flight_status, operation_status,
    delay_code, delay_reason_text, aircraft_type, aircraft_reg, aircraft_icao,
    stand_code, gate_code, terminal, runway, agency,
    origin_airport, dest_airport, previous_airport, next_airport, diversion_airport,
    origin_city, dest_city,
    scheduled_departure, estimated_departure, actual_departure,
    scheduled_arrival, estimated_arrival, actual_arrival,
    flight_scheduled_datetime, board_time, cobt,
    is_virtual, is_divert, is_ferry, is_origin,
    partner_flight_no, source_system, primary_source_id, vendor_code,
    last_event_time,
    flight_task_cd, flight_attribute, flight_country_type, link_id, cancel_period, plan_time,
    created_at, updated_at
"""

_CORE_INSERT_ROW_TPL = (
    '(' + ', '.join(['%s'] * 51) + ', now(), now())'
)

_CORE_UPDATE_ROW_TPL = (
    '(' + ', '.join([
        'CAST(%s AS bigint)',
        *(['CAST(%s AS text)'] * 23),
        *(['CAST(%s AS timestamptz)'] * 9),
        *(['CAST(%s AS boolean)'] * 4),
        *(['CAST(%s AS text)'] * 3),
        'CAST(%s AS timestamptz)',
        *(['CAST(%s AS text)'] * 5),
        'CAST(%s AS timestamptz)',
    ]) + ')'
)

_CORE_UPDATE_VAL_COLS = """
    id, biz_key, flight_identity,
    airline_code, flight_nature, flight_status, operation_status,
    delay_code, delay_reason_text, aircraft_type, aircraft_reg, aircraft_icao,
    stand_code, gate_code, terminal, runway, agency,
    origin_airport, dest_airport, previous_airport, next_airport, diversion_airport,
    origin_city, dest_city,
    scheduled_departure, estimated_departure, actual_departure,
    scheduled_arrival, estimated_arrival, actual_arrival,
    flight_scheduled_datetime, board_time, cobt,
    is_virtual, is_divert, is_ferry, is_origin,
    partner_flight_no, source_system, primary_source_id,
    last_event_time,
    flight_task_cd, flight_attribute, flight_country_type, link_id, cancel_period, plan_time
"""

_IND_UPDATE_ROW_TPL = (
    '(' + ', '.join([
        'CAST(%s AS bigint)',
        'CAST(%s AS boolean)',
        'CAST(%s AS integer)',
        *(['CAST(%s AS boolean)'] * 9),
        *(['CAST(%s AS integer)'] * 6),
        'CAST(%s AS text)',
        'CAST(%s AS text)',
    ]) + ')'
)

_PROC_UPDATE_ROW_TPL = (
    '(' + ', '.join([
        'CAST(%s AS bigint)',
        *(['CAST(%s AS timestamptz)'] * 39),
        'CAST(%s AS numeric)',
        'CAST(%s AS numeric)',
        'CAST(%s AS jsonb)',
    ]) + ')'
)


def _flight_core_insert_row(d, terminal):
    return (
        d['biz_key'], d['airport_code'], d['flight_date_utc'], d['direction'], d['flight_no'], d['flight_identity'],
        d['airline_code'], d['flight_nature'], d['flight_status'], d['operation_status'],
        d['delay_code'], d['delay_reason_text'], d['aircraft_type'], d['aircraft_reg'], d['aircraft_icao'],
        d['stand_code'], d['gate_code'], terminal, d['runway'], d['agency'],
        d['origin_airport'], d['dest_airport'], d['previous_airport'], d['next_airport'], d['diversion_airport'],
        d['origin_city'], d['dest_city'],
        d['scheduled_departure'], d['estimated_departure'], d['actual_departure'],
        d['scheduled_arrival'], d['estimated_arrival'], d['actual_arrival'],
        d['flight_scheduled_datetime'], d['board_time'], d['cobt'],
        d['is_virtual'], d['is_divert'], d['is_ferry'], d['is_origin'],
        d['partner_flight_no'], d['source_system'], d['primary_source_id'], d['vendor_code'],
        d['last_event_time'],
        d.get('flight_task_cd'), d.get('flight_attribute'), d.get('flight_country_type'),
        d.get('link_id'), d.get('cancel_period'), d.get('plan_time'),
    )


def _flight_core_update_row(d, terminal, flight_id):
    return (
        flight_id, d['biz_key'], d['flight_identity'],
        d['airline_code'], d['flight_nature'], d['flight_status'], d['operation_status'],
        d['delay_code'], d['delay_reason_text'], d['aircraft_type'], d['aircraft_reg'], d['aircraft_icao'],
        d['stand_code'], d['gate_code'], terminal, d['runway'], d['agency'],
        d['origin_airport'], d['dest_airport'], d['previous_airport'], d['next_airport'], d['diversion_airport'],
        d['origin_city'], d['dest_city'],
        d['scheduled_departure'], d['estimated_departure'], d['actual_departure'],
        d['scheduled_arrival'], d['estimated_arrival'], d['actual_arrival'],
        d['flight_scheduled_datetime'], d['board_time'], d['cobt'],
        d['is_virtual'], d['is_divert'], d['is_ferry'], d['is_origin'],
        d['partner_flight_no'], d['source_system'], d['primary_source_id'],
        d['last_event_time'],
        d.get('flight_task_cd'), d.get('flight_attribute'), d.get('flight_country_type'),
        d.get('link_id'), d.get('cancel_period'), d.get('plan_time'),
    )


def _flight_ind_row(d, flight_id):
    return (
        flight_id, d.get('is_delayed'), d.get('delay_minutes'), d.get('is_origin_flight'),
        d.get('ori_act'), d.get('flight_first_on_time'),
        d.get('release_on_time'), d.get('takeoff_on_time'), d.get('dep_on_time'),
        d.get('land_on_time'), d.get('arr_on_time'), d.get('flight_on_time'),
        d.get('release_delay_minutes'), d.get('takeoff_delay_minutes'), d.get('dep_delay_minutes'),
        d.get('land_delay_minutes'), d.get('arr_delay_minutes'), d.get('flight_delay_minutes'),
        d.get('delay_reason'), d.get('source_system'),
    )


def _flight_proc_row(d, flight_id):
    ext = json.dumps(d.get('process_ext') or {}, ensure_ascii=False)
    return (
        flight_id,
        d.get('estimated_on_block'), d.get('actual_on_block'),
        d.get('estimated_off_block'), d.get('actual_off_block'),
        d.get('tobt'), d.get('tsat'), d.get('ttot'), d.get('cobt'), d.get('ctot'),
        d.get('boarding_start'), d.get('boarding_end'), d.get('last_call'),
        d.get('checkin_sched_begin'), d.get('checkin_sched_end'),
        d.get('checkin_act_begin'), d.get('checkin_act_end'),
        d.get('first_baggage'), d.get('last_baggage'),
        d.get('alterate_by_bridge_time'), d.get('real_by_bridge_time'),
        d.get('plan_off_bridge_time'), d.get('real_off_bridge_time'),
        d.get('departure_start_time'), d.get('departure_end_time'),
        d.get('alterate_open_cabin_time'), d.get('real_open_cabin_time'),
        d.get('alterate_close_cabin_time'), d.get('real_close_cabin_time'),
        d.get('real_start_clean_time'), d.get('real_end_clean_time'),
        d.get('real_meal_start_time'), d.get('real_meal_end_time'),
        d.get('real_start_supply_time'), d.get('real_end_supply_time'),
        d.get('real_start_deice_time'), d.get('real_end_deicie_time'),
        d.get('request_launch_time'), d.get('real_launch_time'), d.get('real_drive_time'),
        d.get('real_taxi_in_time'), d.get('real_taxi_out_time'),
        ext,
    )


def _partition_flight_pass(drafts, by_source, by_occurrence, gate_terminals, stand_terminals):
    """Split a batch into a conflict-free bulk pass plus remaining items for later passes."""
    pass_items = []
    next_remaining = []
    seen_ids = set()
    seen_occurrences = set()
    seen_sources = set()
    seen_biz = set()
    for d in drafts:
        occurrence_key = (d['airport_code'], d['primary_source_id'])
        source_key = (d['source_system'], d['primary_source_id'])
        existing_id = by_source.get(source_key) or by_occurrence.get(occurrence_key)
        biz_key = d.get('biz_key')
        src_valid = bool(source_key[0] and source_key[1])
        conflict = (
            (existing_id is not None and existing_id in seen_ids)
            or (occurrence_key in seen_occurrences)
            or (src_valid and source_key in seen_sources)
            or (bool(biz_key) and biz_key in seen_biz)
        )
        if conflict:
            next_remaining.append(d)
            continue
        if existing_id is not None:
            seen_ids.add(existing_id)
        seen_occurrences.add(occurrence_key)
        if src_valid:
            seen_sources.add(source_key)
        if biz_key:
            seen_biz.add(biz_key)
        pass_items.append((d, occurrence_key, source_key, _resolve_terminal(d, gate_terminals, stand_terminals), existing_id))
    return pass_items, next_remaining


def _upsert_flight_pass_bulk(cur, pass_items, *, by_source, by_occurrence, has_indicator, has_process):
    """Execute one conflict-free pass of flight core + satellites using multi-row SQL."""
    inserted = updated = 0
    resolved_ids = []
    insert_entries = [(idx, item) for idx, item in enumerate(pass_items) if item[4] is None]
    update_entries = [(idx, item) for idx, item in enumerate(pass_items) if item[4] is not None]
    fid_by_idx = {}

    for chunk in _chunked(insert_entries):
        rows = [_flight_core_insert_row(item[0], item[3]) for _, item in chunk]
        cur.execute(
            f"INSERT INTO std_flight_core ({_CORE_INSERT_COLS}) VALUES {_values_placeholders(_CORE_INSERT_ROW_TPL, len(chunk))} RETURNING id",
            _flatten_params(rows),
        )
        returned = cur.fetchall()
        if len(returned) != len(chunk):
            raise RuntimeError(f"Expected {len(chunk)} inserted flight ids, got {len(returned)}")
        for (idx, item), fid_row in zip(chunk, returned):
            flight_id = int(fid_row[0])
            fid_by_idx[idx] = flight_id
            by_occurrence[item[1]] = flight_id
            by_source[item[2]] = flight_id
            inserted += 1

    for chunk in _chunked(update_entries):
        rows = [_flight_core_update_row(item[0], item[3], item[4]) for _, item in chunk]
        cur.execute(f"""
            UPDATE std_flight_core AS t SET
                biz_key = COALESCE(NULLIF(TRIM(v.biz_key), ''), t.biz_key),
                flight_identity = COALESCE(NULLIF(TRIM(v.flight_identity), ''), t.flight_identity),
                airline_code = COALESCE(NULLIF(TRIM(v.airline_code), ''), t.airline_code),
                flight_nature = COALESCE(NULLIF(TRIM(v.flight_nature), ''), t.flight_nature),
                flight_status = COALESCE(NULLIF(TRIM(v.flight_status), ''), t.flight_status),
                operation_status = COALESCE(NULLIF(TRIM(v.operation_status), ''), t.operation_status),
                delay_code = COALESCE(NULLIF(TRIM(v.delay_code), ''), t.delay_code),
                delay_reason_text = COALESCE(NULLIF(TRIM(v.delay_reason_text), ''), t.delay_reason_text),
                aircraft_type = COALESCE(NULLIF(TRIM(v.aircraft_type), ''), t.aircraft_type),
                aircraft_reg = COALESCE(NULLIF(TRIM(v.aircraft_reg), ''), t.aircraft_reg),
                aircraft_icao = COALESCE(NULLIF(TRIM(v.aircraft_icao), ''), t.aircraft_icao),
                stand_code = COALESCE(NULLIF(TRIM(v.stand_code), ''), t.stand_code),
                gate_code = COALESCE(NULLIF(TRIM(v.gate_code), ''), t.gate_code),
                terminal = COALESCE(v.terminal, t.terminal),
                runway = COALESCE(NULLIF(TRIM(v.runway), ''), t.runway),
                agency = COALESCE(NULLIF(TRIM(v.agency), ''), t.agency),
                origin_airport = COALESCE(NULLIF(TRIM(v.origin_airport), ''), t.origin_airport),
                dest_airport = COALESCE(NULLIF(TRIM(v.dest_airport), ''), t.dest_airport),
                previous_airport = COALESCE(NULLIF(TRIM(v.previous_airport), ''), t.previous_airport),
                next_airport = COALESCE(NULLIF(TRIM(v.next_airport), ''), t.next_airport),
                diversion_airport = COALESCE(NULLIF(TRIM(v.diversion_airport), ''), t.diversion_airport),
                origin_city = COALESCE(NULLIF(TRIM(v.origin_city), ''), t.origin_city),
                dest_city = COALESCE(NULLIF(TRIM(v.dest_city), ''), t.dest_city),
                scheduled_departure = COALESCE(v.scheduled_departure, t.scheduled_departure),
                estimated_departure = COALESCE(v.estimated_departure, t.estimated_departure),
                actual_departure = COALESCE(v.actual_departure, t.actual_departure),
                scheduled_arrival = COALESCE(v.scheduled_arrival, t.scheduled_arrival),
                estimated_arrival = COALESCE(v.estimated_arrival, t.estimated_arrival),
                actual_arrival = COALESCE(v.actual_arrival, t.actual_arrival),
                flight_scheduled_datetime = COALESCE(v.flight_scheduled_datetime, t.flight_scheduled_datetime),
                board_time = COALESCE(v.board_time, t.board_time),
                cobt = COALESCE(v.cobt, t.cobt),
                is_virtual = COALESCE(v.is_virtual, t.is_virtual),
                is_divert = COALESCE(v.is_divert, t.is_divert),
                is_ferry = COALESCE(v.is_ferry, t.is_ferry),
                is_origin = COALESCE(v.is_origin, t.is_origin),
                partner_flight_no = COALESCE(NULLIF(TRIM(v.partner_flight_no), ''), t.partner_flight_no),
                source_system = COALESCE(NULLIF(TRIM(v.source_system), ''), t.source_system),
                primary_source_id = COALESCE(NULLIF(TRIM(v.primary_source_id), ''), t.primary_source_id),
                last_event_time = COALESCE(v.last_event_time, t.last_event_time),
                flight_task_cd = COALESCE(NULLIF(TRIM(v.flight_task_cd), ''), t.flight_task_cd),
                flight_attribute = COALESCE(NULLIF(TRIM(v.flight_attribute), ''), t.flight_attribute),
                flight_country_type = COALESCE(CAST(NULLIF(TRIM(v.flight_country_type), '') AS character(1)), t.flight_country_type),
                link_id = COALESCE(NULLIF(TRIM(v.link_id), ''), t.link_id),
                cancel_period = COALESCE(NULLIF(TRIM(v.cancel_period), ''), t.cancel_period),
                plan_time = COALESCE(v.plan_time, t.plan_time),
                row_version = COALESCE(t.row_version, 0) + 1,
                updated_at = now()
            FROM (VALUES {_values_placeholders(_CORE_UPDATE_ROW_TPL, len(chunk))}) AS v({_CORE_UPDATE_VAL_COLS})
            WHERE t.id = v.id
        """, _flatten_params(rows))
        for idx, item in chunk:
            flight_id = item[4]
            fid_by_idx[idx] = flight_id
            by_source[item[2]] = flight_id
            updated += 1

    for idx, item in enumerate(pass_items):
        resolved_ids.append((item[0], fid_by_idx[idx]))

    ind_inserts = [_flight_ind_row(d, fid) for d, fid in resolved_ids if fid not in has_indicator]
    ind_updates = [_flight_ind_row(d, fid) for d, fid in resolved_ids if fid in has_indicator]
    ind_row_tpl = '(' + ', '.join(['%s'] * 20) + ', now(), now())'
    for chunk in _chunked(ind_inserts, 400):
        cur.execute(
            f"""INSERT INTO std_flight_indicator (
                    flight_id, is_delayed, delay_minutes, is_origin_flight,
                    ori_act, flight_first_on_time,
                    release_on_time, takeoff_on_time, dep_on_time,
                    land_on_time, arr_on_time, flight_on_time,
                    release_delay_minutes, takeoff_delay_minutes, dep_delay_minutes,
                    land_delay_minutes, arr_delay_minutes, flight_delay_minutes,
                    delay_reason, source_system, computed_at, updated_at
                ) VALUES {_values_placeholders(ind_row_tpl, len(chunk))}""",
            _flatten_params(chunk),
        )
        for row in chunk:
            has_indicator.add(row[0])
    for chunk in _chunked(ind_updates, 400):
        cur.execute(
            f"""UPDATE std_flight_indicator AS t SET
                    is_delayed = COALESCE(v.is_delayed, t.is_delayed),
                    delay_minutes = COALESCE(v.delay_minutes, t.delay_minutes),
                    is_origin_flight = COALESCE(v.is_origin_flight, t.is_origin_flight),
                    ori_act = COALESCE(v.ori_act, t.ori_act),
                    flight_first_on_time = COALESCE(v.flight_first_on_time, t.flight_first_on_time),
                    release_on_time = COALESCE(v.release_on_time, t.release_on_time),
                    takeoff_on_time = COALESCE(v.takeoff_on_time, t.takeoff_on_time),
                    dep_on_time = COALESCE(v.dep_on_time, t.dep_on_time),
                    land_on_time = COALESCE(v.land_on_time, t.land_on_time),
                    arr_on_time = COALESCE(v.arr_on_time, t.arr_on_time),
                    flight_on_time = COALESCE(v.flight_on_time, t.flight_on_time),
                    release_delay_minutes = COALESCE(v.release_delay_minutes, t.release_delay_minutes),
                    takeoff_delay_minutes = COALESCE(v.takeoff_delay_minutes, t.takeoff_delay_minutes),
                    dep_delay_minutes = COALESCE(v.dep_delay_minutes, t.dep_delay_minutes),
                    land_delay_minutes = COALESCE(v.land_delay_minutes, t.land_delay_minutes),
                    arr_delay_minutes = COALESCE(v.arr_delay_minutes, t.arr_delay_minutes),
                    flight_delay_minutes = COALESCE(v.flight_delay_minutes, t.flight_delay_minutes),
                    delay_reason = COALESCE(NULLIF(TRIM(v.delay_reason), ''), t.delay_reason),
                    source_system = COALESCE(NULLIF(TRIM(v.source_system), ''), t.source_system),
                    updated_at = now()
                FROM (VALUES {_values_placeholders(_IND_UPDATE_ROW_TPL, len(chunk))}) AS v(
                    flight_id, is_delayed, delay_minutes, is_origin_flight,
                    ori_act, flight_first_on_time,
                    release_on_time, takeoff_on_time, dep_on_time,
                    land_on_time, arr_on_time, flight_on_time,
                    release_delay_minutes, takeoff_delay_minutes, dep_delay_minutes,
                    land_delay_minutes, arr_delay_minutes, flight_delay_minutes,
                    delay_reason, source_system
                )
                WHERE t.flight_id = v.flight_id""",
            _flatten_params(chunk),
        )

    proc_inserts = [_flight_proc_row(d, fid) for d, fid in resolved_ids if fid not in has_process]
    proc_updates = [_flight_proc_row(d, fid) for d, fid in resolved_ids if fid in has_process]
    proc_row_tpl = '(' + ', '.join(['%s'] * 42) + ', CAST(%s AS jsonb), now())'
    for chunk in _chunked(proc_inserts, 200):
        cur.execute(
            f"""INSERT INTO std_flight_process (
                    flight_id,
                    estimated_on_block, actual_on_block, estimated_off_block, actual_off_block,
                    tobt, tsat, ttot, cobt, ctot,
                    boarding_start, boarding_end, last_call,
                    checkin_sched_begin, checkin_sched_end, checkin_act_begin, checkin_act_end,
                    first_baggage, last_baggage,
                    alterate_by_bridge_time, real_by_bridge_time, plan_off_bridge_time, real_off_bridge_time,
                    departure_start_time, departure_end_time,
                    alterate_open_cabin_time, real_open_cabin_time, alterate_close_cabin_time, real_close_cabin_time,
                    real_start_clean_time, real_end_clean_time, real_meal_start_time, real_meal_end_time,
                    real_start_supply_time, real_end_supply_time, real_start_deice_time, real_end_deicie_time,
                    request_launch_time, real_launch_time, real_drive_time,
                    real_taxi_in_time, real_taxi_out_time, ext_json, updated_at
                ) VALUES {_values_placeholders(proc_row_tpl, len(chunk))}""",
            _flatten_params(chunk),
        )
        for row in chunk:
            has_process.add(row[0])
    for chunk in _chunked(proc_updates, 200):
        cur.execute(
            f"""UPDATE std_flight_process AS t SET
                    estimated_on_block = COALESCE(v.estimated_on_block, t.estimated_on_block),
                    actual_on_block = COALESCE(v.actual_on_block, t.actual_on_block),
                    estimated_off_block = COALESCE(v.estimated_off_block, t.estimated_off_block),
                    actual_off_block = COALESCE(v.actual_off_block, t.actual_off_block),
                    tobt = COALESCE(v.tobt, t.tobt),
                    tsat = COALESCE(v.tsat, t.tsat),
                    ttot = COALESCE(v.ttot, t.ttot),
                    cobt = COALESCE(v.cobt, t.cobt),
                    ctot = COALESCE(v.ctot, t.ctot),
                    boarding_start = COALESCE(v.boarding_start, t.boarding_start),
                    boarding_end = COALESCE(v.boarding_end, t.boarding_end),
                    last_call = COALESCE(v.last_call, t.last_call),
                    checkin_sched_begin = COALESCE(v.checkin_sched_begin, t.checkin_sched_begin),
                    checkin_sched_end = COALESCE(v.checkin_sched_end, t.checkin_sched_end),
                    checkin_act_begin = COALESCE(v.checkin_act_begin, t.checkin_act_begin),
                    checkin_act_end = COALESCE(v.checkin_act_end, t.checkin_act_end),
                    first_baggage = COALESCE(v.first_baggage, t.first_baggage),
                    last_baggage = COALESCE(v.last_baggage, t.last_baggage),
                    alterate_by_bridge_time = COALESCE(v.alterate_by_bridge_time, t.alterate_by_bridge_time),
                    real_by_bridge_time = COALESCE(v.real_by_bridge_time, t.real_by_bridge_time),
                    plan_off_bridge_time = COALESCE(v.plan_off_bridge_time, t.plan_off_bridge_time),
                    real_off_bridge_time = COALESCE(v.real_off_bridge_time, t.real_off_bridge_time),
                    departure_start_time = COALESCE(v.departure_start_time, t.departure_start_time),
                    departure_end_time = COALESCE(v.departure_end_time, t.departure_end_time),
                    alterate_open_cabin_time = COALESCE(v.alterate_open_cabin_time, t.alterate_open_cabin_time),
                    real_open_cabin_time = COALESCE(v.real_open_cabin_time, t.real_open_cabin_time),
                    alterate_close_cabin_time = COALESCE(v.alterate_close_cabin_time, t.alterate_close_cabin_time),
                    real_close_cabin_time = COALESCE(v.real_close_cabin_time, t.real_close_cabin_time),
                    real_start_clean_time = COALESCE(v.real_start_clean_time, t.real_start_clean_time),
                    real_end_clean_time = COALESCE(v.real_end_clean_time, t.real_end_clean_time),
                    real_meal_start_time = COALESCE(v.real_meal_start_time, t.real_meal_start_time),
                    real_meal_end_time = COALESCE(v.real_meal_end_time, t.real_meal_end_time),
                    real_start_supply_time = COALESCE(v.real_start_supply_time, t.real_start_supply_time),
                    real_end_supply_time = COALESCE(v.real_end_supply_time, t.real_end_supply_time),
                    real_start_deice_time = COALESCE(v.real_start_deice_time, t.real_start_deice_time),
                    real_end_deicie_time = COALESCE(v.real_end_deicie_time, t.real_end_deicie_time),
                    request_launch_time = COALESCE(v.request_launch_time, t.request_launch_time),
                    real_launch_time = COALESCE(v.real_launch_time, t.real_launch_time),
                    real_drive_time = COALESCE(v.real_drive_time, t.real_drive_time),
                    real_taxi_in_time = COALESCE(v.real_taxi_in_time, t.real_taxi_in_time),
                    real_taxi_out_time = COALESCE(v.real_taxi_out_time, t.real_taxi_out_time),
                    ext_json = COALESCE(t.ext_json, '{{}}'::jsonb) || COALESCE(v.ext_json, '{{}}'::jsonb),
                    updated_at = now()
                FROM (VALUES {_values_placeholders(_PROC_UPDATE_ROW_TPL, len(chunk))}) AS v(
                    flight_id,
                    estimated_on_block, actual_on_block, estimated_off_block, actual_off_block,
                    tobt, tsat, ttot, cobt, ctot,
                    boarding_start, boarding_end, last_call,
                    checkin_sched_begin, checkin_sched_end, checkin_act_begin, checkin_act_end,
                    first_baggage, last_baggage,
                    alterate_by_bridge_time, real_by_bridge_time, plan_off_bridge_time, real_off_bridge_time,
                    departure_start_time, departure_end_time,
                    alterate_open_cabin_time, real_open_cabin_time, alterate_close_cabin_time, real_close_cabin_time,
                    real_start_clean_time, real_end_clean_time, real_meal_start_time, real_meal_end_time,
                    real_start_supply_time, real_end_supply_time, real_start_deice_time, real_end_deicie_time,
                    request_launch_time, real_launch_time, real_drive_time,
                    real_taxi_in_time, real_taxi_out_time, ext_json
                )
                WHERE t.flight_id = v.flight_id""",
            _flatten_params(chunk),
        )

    src_rows = [(fid, d['source_system'], d['primary_source_id'], d['source_ffid']) for d, fid in resolved_ids]
    src_row_tpl = '(%s, %s, %s, %s, now(), now())'
    for chunk in _chunked(src_rows, 400):
        cur.execute(
            f"""INSERT INTO std_flight_source_ref (flight_id, source_system, source_flight_id, source_ffid, last_seen_at, created_at)
                VALUES {_values_placeholders(src_row_tpl, len(chunk))}
                ON CONFLICT (source_system, source_flight_id) DO UPDATE SET
                    flight_id = EXCLUDED.flight_id,
                    source_ffid = COALESCE(EXCLUDED.source_ffid, std_flight_source_ref.source_ffid),
                    last_seen_at = now()""",
            _flatten_params(chunk),
        )
    return inserted, updated


def _upsert_flight_batch_rowwise(cur, drafts, *, by_source, by_occurrence, has_indicator, has_process,
                                 gate_terminals, stand_terminals):
    """Fallback row-by-row flight upsert with per-row savepoints so bad rows never abort a batch."""
    inserted = updated = skipped = failed = 0
    for d in drafts:
        cur.execute('SAVEPOINT row_sp')
        try:
            occurrence_key = (d['airport_code'], d['primary_source_id'])
            source_key = (d['source_system'], d['primary_source_id'])
            existing_id = by_source.get(source_key) or by_occurrence.get(occurrence_key)
            terminal = _resolve_terminal(d, gate_terminals, stand_terminals)

            if existing_id is None:
                cur.execute(
                    f"INSERT INTO std_flight_core ({_CORE_INSERT_COLS}) VALUES {_CORE_INSERT_ROW_TPL} RETURNING id",
                    _flight_core_insert_row(d, terminal),
                )
                flight_id = int(cur.fetchone()[0])
                by_occurrence[occurrence_key] = flight_id
                by_source[source_key] = flight_id
                inserted += 1
            else:
                flight_id = existing_id
                cur.execute("""
                    UPDATE std_flight_core SET
                        biz_key = COALESCE(NULLIF(TRIM(%s), ''), biz_key),
                        flight_identity = COALESCE(NULLIF(TRIM(%s), ''), flight_identity),
                        airline_code = COALESCE(NULLIF(TRIM(%s), ''), airline_code),
                        flight_nature = COALESCE(NULLIF(TRIM(%s), ''), flight_nature),
                        flight_status = COALESCE(NULLIF(TRIM(%s), ''), flight_status),
                        operation_status = COALESCE(NULLIF(TRIM(%s), ''), operation_status),
                        delay_code = COALESCE(NULLIF(TRIM(%s), ''), delay_code),
                        delay_reason_text = COALESCE(NULLIF(TRIM(%s), ''), delay_reason_text),
                        aircraft_type = COALESCE(NULLIF(TRIM(%s), ''), aircraft_type),
                        aircraft_reg = COALESCE(NULLIF(TRIM(%s), ''), aircraft_reg),
                        aircraft_icao = COALESCE(NULLIF(TRIM(%s), ''), aircraft_icao),
                        stand_code = COALESCE(NULLIF(TRIM(%s), ''), stand_code),
                        gate_code = COALESCE(NULLIF(TRIM(%s), ''), gate_code),
                        terminal = COALESCE(%s, terminal),
                        runway = COALESCE(NULLIF(TRIM(%s), ''), runway),
                        agency = COALESCE(NULLIF(TRIM(%s), ''), agency),
                        origin_airport = COALESCE(NULLIF(TRIM(%s), ''), origin_airport),
                        dest_airport = COALESCE(NULLIF(TRIM(%s), ''), dest_airport),
                        previous_airport = COALESCE(NULLIF(TRIM(%s), ''), previous_airport),
                        next_airport = COALESCE(NULLIF(TRIM(%s), ''), next_airport),
                        diversion_airport = COALESCE(NULLIF(TRIM(%s), ''), diversion_airport),
                        origin_city = COALESCE(NULLIF(TRIM(%s), ''), origin_city),
                        dest_city = COALESCE(NULLIF(TRIM(%s), ''), dest_city),
                        scheduled_departure = COALESCE(%s, scheduled_departure),
                        estimated_departure = COALESCE(%s, estimated_departure),
                        actual_departure = COALESCE(%s, actual_departure),
                        scheduled_arrival = COALESCE(%s, scheduled_arrival),
                        estimated_arrival = COALESCE(%s, estimated_arrival),
                        actual_arrival = COALESCE(%s, actual_arrival),
                        flight_scheduled_datetime = COALESCE(%s, flight_scheduled_datetime),
                        board_time = COALESCE(%s, board_time),
                        cobt = COALESCE(%s, cobt),
                        is_virtual = COALESCE(%s, is_virtual),
                        is_divert = COALESCE(%s, is_divert),
                        is_ferry = COALESCE(%s, is_ferry),
                        is_origin = COALESCE(%s, is_origin),
                        partner_flight_no = COALESCE(NULLIF(TRIM(%s), ''), partner_flight_no),
                        source_system = COALESCE(NULLIF(TRIM(%s), ''), source_system),
                        primary_source_id = COALESCE(NULLIF(TRIM(%s), ''), primary_source_id),
                        last_event_time = COALESCE(%s, last_event_time),
                        flight_task_cd = COALESCE(NULLIF(TRIM(%s), ''), flight_task_cd),
                        flight_attribute = COALESCE(NULLIF(TRIM(%s), ''), flight_attribute),
                        flight_country_type = COALESCE(CAST(NULLIF(TRIM(%s), '') AS character(1)), flight_country_type),
                        link_id = COALESCE(NULLIF(TRIM(%s), ''), link_id),
                        cancel_period = COALESCE(NULLIF(TRIM(%s), ''), cancel_period),
                        plan_time = COALESCE(%s, plan_time),
                        row_version = COALESCE(row_version, 0) + 1,
                        updated_at = now()
                    WHERE id = %s
                """, _flight_core_update_row(d, terminal, flight_id)[1:] + (flight_id,))
                by_source[source_key] = flight_id
                updated += 1

            if flight_id not in has_indicator:
                cur.execute("""
                    INSERT INTO std_flight_indicator (
                        flight_id, is_delayed, delay_minutes, is_origin_flight,
                        ori_act, flight_first_on_time,
                        release_on_time, takeoff_on_time, dep_on_time,
                        land_on_time, arr_on_time, flight_on_time,
                        release_delay_minutes, takeoff_delay_minutes, dep_delay_minutes,
                        land_delay_minutes, arr_delay_minutes, flight_delay_minutes,
                        delay_reason, source_system, computed_at, updated_at
                    ) VALUES (""" + ', '.join(['%s'] * 20) + """, now(), now())
                """, _flight_ind_row(d, flight_id))
                has_indicator.add(flight_id)
            else:
                cur.execute("""
                    UPDATE std_flight_indicator SET
                        is_delayed = COALESCE(%s, is_delayed),
                        delay_minutes = COALESCE(%s, delay_minutes),
                        is_origin_flight = COALESCE(%s, is_origin_flight),
                        ori_act = COALESCE(%s, ori_act),
                        flight_first_on_time = COALESCE(%s, flight_first_on_time),
                        release_on_time = COALESCE(%s, release_on_time),
                        takeoff_on_time = COALESCE(%s, takeoff_on_time),
                        dep_on_time = COALESCE(%s, dep_on_time),
                        land_on_time = COALESCE(%s, land_on_time),
                        arr_on_time = COALESCE(%s, arr_on_time),
                        flight_on_time = COALESCE(%s, flight_on_time),
                        release_delay_minutes = COALESCE(%s, release_delay_minutes),
                        takeoff_delay_minutes = COALESCE(%s, takeoff_delay_minutes),
                        dep_delay_minutes = COALESCE(%s, dep_delay_minutes),
                        land_delay_minutes = COALESCE(%s, land_delay_minutes),
                        arr_delay_minutes = COALESCE(%s, arr_delay_minutes),
                        flight_delay_minutes = COALESCE(%s, flight_delay_minutes),
                        delay_reason = COALESCE(NULLIF(TRIM(%s), ''), delay_reason),
                        source_system = COALESCE(NULLIF(TRIM(%s), ''), source_system),
                        updated_at = now()
                    WHERE flight_id = %s
                """, _flight_ind_row(d, flight_id)[1:] + (flight_id,))

            if flight_id not in has_process:
                cur.execute("""
                    INSERT INTO std_flight_process (
                        flight_id,
                        estimated_on_block, actual_on_block, estimated_off_block, actual_off_block,
                        tobt, tsat, ttot, cobt, ctot,
                        boarding_start, boarding_end, last_call,
                        checkin_sched_begin, checkin_sched_end, checkin_act_begin, checkin_act_end,
                        first_baggage, last_baggage,
                        alterate_by_bridge_time, real_by_bridge_time, plan_off_bridge_time, real_off_bridge_time,
                        departure_start_time, departure_end_time,
                        alterate_open_cabin_time, real_open_cabin_time, alterate_close_cabin_time, real_close_cabin_time,
                        real_start_clean_time, real_end_clean_time, real_meal_start_time, real_meal_end_time,
                        real_start_supply_time, real_end_supply_time, real_start_deice_time, real_end_deicie_time,
                        request_launch_time, real_launch_time, real_drive_time,
                        real_taxi_in_time, real_taxi_out_time, ext_json, updated_at
                    ) VALUES (""" + ', '.join(['%s'] * 42) + """, CAST(%s AS jsonb), now())
                """, _flight_proc_row(d, flight_id))
                has_process.add(flight_id)
            else:
                cur.execute("""
                    UPDATE std_flight_process SET
                        estimated_on_block = COALESCE(%s, estimated_on_block),
                        actual_on_block = COALESCE(%s, actual_on_block),
                        estimated_off_block = COALESCE(%s, estimated_off_block),
                        actual_off_block = COALESCE(%s, actual_off_block),
                        tobt = COALESCE(%s, tobt),
                        tsat = COALESCE(%s, tsat),
                        ttot = COALESCE(%s, ttot),
                        cobt = COALESCE(%s, cobt),
                        ctot = COALESCE(%s, ctot),
                        boarding_start = COALESCE(%s, boarding_start),
                        boarding_end = COALESCE(%s, boarding_end),
                        last_call = COALESCE(%s, last_call),
                        checkin_sched_begin = COALESCE(%s, checkin_sched_begin),
                        checkin_sched_end = COALESCE(%s, checkin_sched_end),
                        checkin_act_begin = COALESCE(%s, checkin_act_begin),
                        checkin_act_end = COALESCE(%s, checkin_act_end),
                        first_baggage = COALESCE(%s, first_baggage),
                        last_baggage = COALESCE(%s, last_baggage),
                        alterate_by_bridge_time = COALESCE(%s, alterate_by_bridge_time),
                        real_by_bridge_time = COALESCE(%s, real_by_bridge_time),
                        plan_off_bridge_time = COALESCE(%s, plan_off_bridge_time),
                        real_off_bridge_time = COALESCE(%s, real_off_bridge_time),
                        departure_start_time = COALESCE(%s, departure_start_time),
                        departure_end_time = COALESCE(%s, departure_end_time),
                        alterate_open_cabin_time = COALESCE(%s, alterate_open_cabin_time),
                        real_open_cabin_time = COALESCE(%s, real_open_cabin_time),
                        alterate_close_cabin_time = COALESCE(%s, alterate_close_cabin_time),
                        real_close_cabin_time = COALESCE(%s, real_close_cabin_time),
                        real_start_clean_time = COALESCE(%s, real_start_clean_time),
                        real_end_clean_time = COALESCE(%s, real_end_clean_time),
                        real_meal_start_time = COALESCE(%s, real_meal_start_time),
                        real_meal_end_time = COALESCE(%s, real_meal_end_time),
                        real_start_supply_time = COALESCE(%s, real_start_supply_time),
                        real_end_supply_time = COALESCE(%s, real_end_supply_time),
                        real_start_deice_time = COALESCE(%s, real_start_deice_time),
                        real_end_deicie_time = COALESCE(%s, real_end_deicie_time),
                        request_launch_time = COALESCE(%s, request_launch_time),
                        real_launch_time = COALESCE(%s, real_launch_time),
                        real_drive_time = COALESCE(%s, real_drive_time),
                        real_taxi_in_time = COALESCE(%s, real_taxi_in_time),
                        real_taxi_out_time = COALESCE(%s, real_taxi_out_time),
                        ext_json = COALESCE(ext_json, '{}'::jsonb) || COALESCE(CAST(%s AS jsonb), '{}'::jsonb),
                        updated_at = now()
                    WHERE flight_id = %s
                """, _flight_proc_row(d, flight_id)[1:] + (flight_id,))

            cur.execute("""
                INSERT INTO std_flight_source_ref (flight_id, source_system, source_flight_id, source_ffid, last_seen_at, created_at)
                VALUES (%s, %s, %s, %s, now(), now())
                ON CONFLICT (source_system, source_flight_id) DO UPDATE SET
                    flight_id = EXCLUDED.flight_id,
                    source_ffid = COALESCE(EXCLUDED.source_ffid, std_flight_source_ref.source_ffid),
                    last_seen_at = now()
            """, (flight_id, d['source_system'], d['primary_source_id'], d['source_ffid']))
            cur.execute('RELEASE SAVEPOINT row_sp')
        except Exception:
            cur.execute('ROLLBACK TO SAVEPOINT row_sp')
            cur.execute('RELEASE SAVEPOINT row_sp')
            failed += 1
    return inserted, updated, skipped, failed


def upsert_flight_batch(pg_conn, drafts, *, gate_terminals=None, stand_terminals=None):
    """Upsert a batch of standard flight drafts in one transaction using bulk SQL and row-wise fallback."""
    if not drafts:
        return {'inserted': 0, 'updated': 0, 'skipped': 0, 'failed': 0}

    gate_terminals = gate_terminals or {}
    stand_terminals = stand_terminals or {}
    cur = pg_conn.cursor()
    inserted = updated = skipped = failed = 0
    try:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('airport:oracle-flight-ingestion'))")
        source_systems = sorted({d['source_system'] for d in drafts if d.get('source_system')} | {'FIMS_ORACLE', 'ACDM_ORACLE'})
        source_ids = sorted({d['primary_source_id'] for d in drafts if d.get('primary_source_id')})
        by_source = {}
        if source_systems and source_ids:
            cur.execute("""
                SELECT sr.source_system, sr.source_flight_id, sr.flight_id
                FROM std_flight_source_ref sr
                JOIN std_flight_core c ON c.id = sr.flight_id
                WHERE sr.source_system = ANY(%s) AND sr.source_flight_id = ANY(%s)
                  AND c.deleted_at IS NULL AND c.primary_source_id = sr.source_flight_id
            """, (source_systems, source_ids))
            for ss, sid, fid in cur.fetchall():
                by_source[(ss, sid)] = int(fid)

        # Each source owns its fields; a later FIMS poll must not overwrite ACDM observations.
        prepared = []
        acdm_fields = {'scheduled_departure', 'estimated_departure', 'actual_departure',
                       'scheduled_arrival', 'estimated_arrival', 'actual_arrival', 'flight_scheduled_datetime',
                       'stand_code', 'gate_code',
                       'flight_status', 'operation_status', 'delay_code', 'delay_reason_text', 'cancel_period',
                       'is_origin', 'is_origin_flight', 'ori_act', 'flight_first_on_time', 'release_on_time',
                       'takeoff_on_time', 'dep_on_time', 'land_on_time', 'arr_on_time', 'flight_on_time',
                       'is_delayed', 'delay_minutes', 'release_delay_minutes', 'takeoff_delay_minutes',
                       'dep_delay_minutes', 'land_delay_minutes', 'arr_delay_minutes', 'flight_delay_minutes'}
        fims_fields = {'airline_code', 'flight_task_cd', 'flight_attribute', 'flight_country_type',
                       'plan_time', 'is_virtual', 'aircraft_type', 'aircraft_reg', 'aircraft_icao',
                       'flight_nature', 'is_ferry'}
        for original in drafts:
            d = dict(original)
            sid = d['primary_source_id']
            masked = acdm_fields if d['source_system'] == 'FIMS_ORACLE' and ('ACDM_ORACLE', sid) in by_source else (
                fims_fields if d['source_system'] == 'ACDM_ORACLE' and ('FIMS_ORACLE', sid) in by_source else ())
            for field in masked:
                d[field] = None
            prepared.append(d)
        drafts = prepared

        airports = sorted({d['airport_code'] for d in drafts})
        by_occurrence = {}
        if airports and source_ids:
            cur.execute("""
                SELECT id, airport_code, primary_source_id
                FROM std_flight_core
                WHERE deleted_at IS NULL
                  AND airport_code = ANY(%s) AND primary_source_id = ANY(%s)
                  AND source_system = ANY(%s)
                ORDER BY id
            """, (airports, source_ids, sorted(set(source_systems) | {'FIMS_ORACLE', 'ACDM_ORACLE'})))
            for fid, ap, sid in cur.fetchall():
                key = (ap, sid)
                if key in by_occurrence:
                    raise ValueError('同一Oracle FLIGHT_ID对应多条本地航班，请先修复源记录关联')
                by_occurrence[key] = int(fid)

        candidate_ids = sorted(set(by_source.values()) | set(by_occurrence.values()))
        has_indicator = set()
        has_process = set()
        if candidate_ids:
            cur.execute('SELECT DISTINCT flight_id FROM std_flight_indicator WHERE flight_id = ANY(%s)', (candidate_ids,))
            has_indicator = {int(r[0]) for r in cur.fetchall()}
            cur.execute('SELECT DISTINCT flight_id FROM std_flight_process WHERE flight_id = ANY(%s)', (candidate_ids,))
            has_process = {int(r[0]) for r in cur.fetchall()}

        remaining = list(drafts)
        while remaining:
            pass_items, remaining = _partition_flight_pass(
                remaining, by_source, by_occurrence, gate_terminals, stand_terminals
            )
            snap_by_source = dict(by_source)
            snap_by_occurrence = dict(by_occurrence)
            snap_ind = set(has_indicator)
            snap_proc = set(has_process)
            cur.execute('SAVEPOINT batch_sp')
            try:
                b_ins, b_upd = _upsert_flight_pass_bulk(
                    cur,
                    pass_items,
                    by_source=by_source,
                    by_occurrence=by_occurrence,
                    has_indicator=has_indicator,
                    has_process=has_process,
                )
                cur.execute('RELEASE SAVEPOINT batch_sp')
                inserted += b_ins
                updated += b_upd
            except Exception:
                cur.execute('ROLLBACK TO SAVEPOINT batch_sp')
                cur.execute('RELEASE SAVEPOINT batch_sp')
                by_source = snap_by_source
                by_occurrence = snap_by_occurrence
                has_indicator = snap_ind
                has_process = snap_proc
                r_ins, r_upd, r_skip, r_fail = _upsert_flight_batch_rowwise(
                    cur,
                    [item[0] for item in pass_items],
                    by_source=by_source,
                    by_occurrence=by_occurrence,
                    has_indicator=has_indicator,
                    has_process=has_process,
                    gate_terminals=gate_terminals,
                    stand_terminals=stand_terminals,
                )
                inserted += r_ins
                updated += r_upd
                skipped += r_skip
                failed += r_fail

        pg_conn.commit()
        return {'inserted': inserted, 'updated': updated, 'skipped': skipped, 'failed': failed}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def _replace_codeshares_rowwise(cur, chunk):
    touched = written = failed = 0
    for flight_id, identities in chunk:
        cur.execute('SAVEPOINT cs_sp')
        try:
            cur.execute("DELETE FROM std_flight_link WHERE flight_id = %s AND link_type = 'CODESHARE'", (flight_id,))
            for identity in identities:
                cur.execute("""
                    INSERT INTO std_flight_link (flight_id, linked_flight_identity, link_type, created_at, updated_at)
                    VALUES (%s, %s, 'CODESHARE', now(), now())
                """, (flight_id, identity))
                written += 1
            touched += 1
            cur.execute('RELEASE SAVEPOINT cs_sp')
        except Exception:
            cur.execute('ROLLBACK TO SAVEPOINT cs_sp')
            cur.execute('RELEASE SAVEPOINT cs_sp')
            failed += 1
    return touched, written, failed


def replace_codeshares_by_source(pg_conn, identities_by_source_id, *,
                                 prefer_system='FIMS_ORACLE', alt_system='ACDM_ORACLE'):
    """Replace CODESHARE links per operational flight_id using bulk delete+insert with fallback."""
    if not identities_by_source_id:
        return {'flights_touched': 0, 'links_written': 0, 'skipped_no_flight': 0, 'failed': 0}

    systems = [s for s in dict.fromkeys([prefer_system, alt_system, 'FIMS_ORACLE']) if s]
    source_ids = list(identities_by_source_id.keys())
    flight_id_by_source = {}
    cur = pg_conn.cursor()
    try:
        for idx in range(0, len(source_ids), 500):
            chunk = source_ids[idx:idx + 500]
            cur.execute("""
                SELECT DISTINCT ON (source_flight_id) source_flight_id, flight_id
                FROM std_flight_source_ref
                WHERE source_system = ANY(%s) AND source_flight_id = ANY(%s)
                ORDER BY source_flight_id,
                         CASE source_system WHEN %s THEN 0 ELSE 1 END
            """, (systems, chunk, prefer_system))
            for sid, fid in cur.fetchall():
                flight_id_by_source[sid] = int(fid)

        touched = written = skipped_no_flight = failed = 0
        pending = []
        for sid, identities in identities_by_source_id.items():
            flight_id = flight_id_by_source.get(sid)
            if flight_id is None:
                skipped_no_flight += 1
                continue
            pending.append((flight_id, list(identities)))

        while pending:
            pass_chunk = []
            next_pending = []
            seen_fids = set()
            for flight_id, identities in pending:
                if flight_id in seen_fids:
                    next_pending.append((flight_id, identities))
                    continue
                seen_fids.add(flight_id)
                pass_chunk.append((flight_id, identities))
            pending = next_pending

            for chunk in _chunked(pass_chunk, _BULK_ROW_CHUNK):
                cur.execute('SAVEPOINT cs_batch_sp')
                try:
                    fids = [fid for fid, _ in chunk]
                    cur.execute(
                        "DELETE FROM std_flight_link WHERE flight_id = ANY(%s) AND link_type = 'CODESHARE'",
                        (fids,),
                    )
                    pairs = [(fid, ident) for fid, idents in chunk for ident in idents]
                    for pair_chunk in _chunked(pairs, 500):
                        cur.execute(
                            f"""INSERT INTO std_flight_link (flight_id, linked_flight_identity, link_type, created_at, updated_at)
                                VALUES {_values_placeholders("(%s, %s, 'CODESHARE', now(), now())", len(pair_chunk))}""",
                            _flatten_params(pair_chunk),
                        )
                    cur.execute('RELEASE SAVEPOINT cs_batch_sp')
                    touched += len(chunk)
                    written += len(pairs)
                except Exception:
                    cur.execute('ROLLBACK TO SAVEPOINT cs_batch_sp')
                    cur.execute('RELEASE SAVEPOINT cs_batch_sp')
                    r_t, r_w, r_f = _replace_codeshares_rowwise(cur, chunk)
                    touched += r_t
                    written += r_w
                    failed += r_f

        pg_conn.commit()
        return {
            'flights_touched': touched,
            'links_written': written,
            'skipped_no_flight': skipped_no_flight,
            'failed': failed,
        }
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


_PROD_INSERT_COLS = """
    biz_key, flight_id, airport_code, flight_date, airline_code, flight_no, flight_identity,
    direction, aircraft_type, aircraft_reg, flight_task_cd, area_code, source_system, primary_source_id,
    route_name_zh, route_name_en, route_type, custom_route_type,
    leg, leg_seq, leg_type, is_local_leg, origin_airport, dest_airport, qty_scope,
    max_payload, max_seat, available_payload, available_seat,
    movement_count, movement_time_text,
    adult_count, child_count, infant_count,
    transit_adult_count, transit_child_count, transit_infant_count,
    baggage_weight, mail_weight, cargo_weight,
    ext_json, last_event_time, created_at, updated_at
"""

_PROD_INSERT_ROW_TPL = '(' + ', '.join(['%s'] * 40) + ', CAST(%s AS jsonb), %s, now(), now())'

_PROD_UPDATE_ROW_TPL = (
    '(' + ', '.join([
        'CAST(%s AS bigint)',
        'CAST(%s AS bigint)',
        *(['CAST(%s AS text)'] * 11),
        'CAST(%s AS integer)',
        'CAST(%s AS text)',
        'CAST(%s AS boolean)',
        *(['CAST(%s AS text)'] * 3),
        'CAST(%s AS numeric)',
        'CAST(%s AS integer)',
        'CAST(%s AS numeric)',
        'CAST(%s AS integer)',
        'CAST(%s AS integer)',
        'CAST(%s AS text)',
        *(['CAST(%s AS integer)'] * 6),
        *(['CAST(%s AS numeric)'] * 3),
        'CAST(%s AS jsonb)',
        'CAST(%s AS timestamptz)',
    ]) + ')'
)

_PROD_UPDATE_VAL_COLS = """
    id, flight_id, airline_code, flight_identity,
    aircraft_type, aircraft_reg, flight_task_cd, area_code,
    route_name_zh, route_name_en, route_type, custom_route_type,
    leg, leg_seq, leg_type, is_local_leg,
    origin_airport, dest_airport, qty_scope,
    max_payload, max_seat, available_payload, available_seat,
    movement_count, movement_time_text,
    adult_count, child_count, infant_count,
    transit_adult_count, transit_child_count, transit_infant_count,
    baggage_weight, mail_weight, cargo_weight,
    ext_json, last_event_time
"""


def _prod_ext_json(d):
    ext = {}
    if d.get('pe_payload'):
        ext['pe_payload'] = d['pe_payload']
    if d.get('pe_seat'):
        ext['pe_seat'] = d['pe_seat']
    return json.dumps(ext, ensure_ascii=False)


def _prod_insert_row(d, flight_id, ext_json):
    return (
        d['biz_key'], flight_id, d['airport_code'], d['flight_date'], d['airline_code'], d['flight_no'], d['flight_identity'],
        d['direction'], d['aircraft_type'], d['aircraft_reg'], d['flight_task_cd'], d['area_code'], d['source_system'], d['primary_source_id'],
        d['route_name_zh'], d['route_name_en'], d['route_type'], d['custom_route_type'],
        d['leg'], d['leg_seq'], d['leg_type'], d['is_local_leg'], d['origin_airport'], d['dest_airport'], d['qty_scope'],
        d['max_payload'], d['max_seat'], d['available_payload'], d['available_seat'],
        d['movement_count'], d['movement_time_text'],
        d['adult_count'], d['child_count'], d['infant_count'],
        d['transit_adult_count'], d['transit_child_count'], d['transit_infant_count'],
        d['baggage_weight'], d['mail_weight'], d['cargo_weight'],
        ext_json, d['last_event_time'],
    )


def _prod_update_fields(d, flight_id, ext_json):
    return (
        flight_id, d['airline_code'], d['flight_identity'],
        d['aircraft_type'], d['aircraft_reg'], d['flight_task_cd'], d['area_code'],
        d['route_name_zh'], d['route_name_en'], d['route_type'], d['custom_route_type'],
        d['leg'], d['leg_seq'], d['leg_type'], d['is_local_leg'],
        d['origin_airport'], d['dest_airport'], d['qty_scope'],
        d['max_payload'], d['max_seat'], d['available_payload'], d['available_seat'],
        d['movement_count'], d['movement_time_text'],
        d['adult_count'], d['child_count'], d['infant_count'],
        d['transit_adult_count'], d['transit_child_count'], d['transit_infant_count'],
        d['baggage_weight'], d['mail_weight'], d['cargo_weight'],
        ext_json, d['last_event_time'],
    )


def _production_flight_id(draft, flight_lookup):
    key = (draft['airport_code'], draft['flight_date'], draft['direction'], draft['flight_no'])
    candidates = flight_lookup.get(key, [])
    leg = (draft.get('leg') or '').strip().upper()
    if not leg or not draft.get('aircraft_reg'):
        return None
    if leg:
        candidates = [c for c in candidates if len(c) > 3 and c[3] == leg]
    aircraft = (draft.get('aircraft_reg') or '').replace('-', '').upper()
    if aircraft:
        candidates = [c for c in candidates if (c[1] or '').replace('-', '').upper() == aircraft]
    if len(candidates) == 1:
        return candidates[0][0]
    clock = re.search(r'(?<!\d)([0-2]\d:[0-5]\d)', str(draft.get('movement_time_text') or ''))
    if clock:
        candidates = [c for c in candidates if c[2] == clock[1]]
    return candidates[0][0] if len(candidates) == 1 else None


def reconcile_production_scope(pg_conn, airport, source_system, start, end, source_ids):
    """Archive superseded source rows only after a nonempty complete read succeeds."""
    if not source_ids:
        return 0
    cur = pg_conn.cursor()
    try:
        cur.execute("""UPDATE public.std_flight_production SET deleted_at=now(),updated_at=now()
            WHERE deleted_at IS NULL AND airport_code=%s AND source_system=%s
              AND flight_date BETWEEN %s AND %s AND NOT (COALESCE(primary_source_id,'')=ANY(%s))""",
            (airport, source_system, start, end, list(source_ids)))
        archived = cur.rowcount
        pg_conn.commit()
        return archived
    finally:
        cur.close()


def _partition_production_pass(drafts, by_source, by_biz, flight_lookup):
    pass_items = []
    next_remaining = []
    seen_ids = set()
    seen_sources = set()
    seen_biz = set()
    for d in drafts:
        src_key = (d['source_system'], d['primary_source_id']) if d.get('primary_source_id') else None
        existing_id = (by_source.get(src_key) if src_key else None) or by_biz.get(d['biz_key'])
        biz_key = d.get('biz_key')
        conflict = (
            (existing_id is not None and existing_id in seen_ids)
            or (src_key is not None and src_key in seen_sources)
            or (bool(biz_key) and biz_key in seen_biz)
        )
        if conflict:
            next_remaining.append(d)
            continue
        if existing_id is not None:
            seen_ids.add(existing_id)
        if src_key is not None:
            seen_sources.add(src_key)
        if biz_key:
            seen_biz.add(biz_key)
        flight_id = _production_flight_id(d, flight_lookup)
        pass_items.append((d, src_key, existing_id, flight_id, _prod_ext_json(d)))
    return pass_items, next_remaining


def _upsert_production_pass_bulk(cur, pass_items, *, by_source, by_biz):
    inserted = updated = 0
    inserts = [item for item in pass_items if item[2] is None]
    updates = [item for item in pass_items if item[2] is not None]

    for chunk in _chunked(inserts):
        rows = [_prod_insert_row(item[0], item[3], item[4]) for item in chunk]
        cur.execute(
            f"INSERT INTO std_flight_production ({_PROD_INSERT_COLS}) VALUES {_values_placeholders(_PROD_INSERT_ROW_TPL, len(chunk))} RETURNING id",
            _flatten_params(rows),
        )
        returned = cur.fetchall()
        if len(returned) != len(chunk):
            raise RuntimeError(f"Expected {len(chunk)} inserted production ids, got {len(returned)}")
        for (d, src_key, _, _, _), pid_row in zip(chunk, returned):
            new_id = int(pid_row[0])
            if src_key:
                by_source[src_key] = new_id
            by_biz[d['biz_key']] = new_id
            inserted += 1

    for chunk in _chunked(updates):
        rows = [(item[2],) + _prod_update_fields(item[0], item[3], item[4]) for item in chunk]
        cur.execute(f"""
            UPDATE std_flight_production AS t SET
                flight_id = v.flight_id,
                airline_code = v.airline_code,
                flight_identity = v.flight_identity,
                aircraft_type = v.aircraft_type,
                aircraft_reg = v.aircraft_reg,
                flight_task_cd = v.flight_task_cd,
                area_code = v.area_code,
                route_name_zh = v.route_name_zh,
                route_name_en = v.route_name_en,
                route_type = v.route_type,
                custom_route_type = v.custom_route_type,
                leg = v.leg,
                leg_seq = v.leg_seq,
                leg_type = v.leg_type,
                is_local_leg = v.is_local_leg,
                origin_airport = v.origin_airport,
                dest_airport = v.dest_airport,
                qty_scope = v.qty_scope,
                max_payload = v.max_payload,
                max_seat = v.max_seat,
                available_payload = v.available_payload,
                available_seat = v.available_seat,
                movement_count = v.movement_count,
                movement_time_text = v.movement_time_text,
                adult_count = v.adult_count,
                child_count = v.child_count,
                infant_count = v.infant_count,
                transit_adult_count = v.transit_adult_count,
                transit_child_count = v.transit_child_count,
                transit_infant_count = v.transit_infant_count,
                baggage_weight = v.baggage_weight,
                mail_weight = v.mail_weight,
                cargo_weight = v.cargo_weight,
                ext_json = v.ext_json,
                last_event_time = v.last_event_time,
                row_version = COALESCE(t.row_version, 0) + 1,
                updated_at = now()
            FROM (VALUES {_values_placeholders(_PROD_UPDATE_ROW_TPL, len(chunk))}) AS v({_PROD_UPDATE_VAL_COLS})
            WHERE t.id = v.id
        """, _flatten_params(rows))
        updated += len(chunk)

    return inserted, updated


def _upsert_production_batch_rowwise(cur, drafts, *, by_source, by_biz, flight_lookup):
    inserted = updated = failed = 0
    for d in drafts:
        cur.execute('SAVEPOINT prod_sp')
        try:
            src_key = (d['source_system'], d['primary_source_id']) if d.get('primary_source_id') else None
            existing_id = (by_source.get(src_key) if src_key else None) or by_biz.get(d['biz_key'])
            flight_id = _production_flight_id(d, flight_lookup)
            ext_json = _prod_ext_json(d)

            if existing_id is None:
                cur.execute(
                    f"INSERT INTO std_flight_production ({_PROD_INSERT_COLS}) VALUES {_PROD_INSERT_ROW_TPL} RETURNING id",
                    _prod_insert_row(d, flight_id, ext_json),
                )
                new_id = int(cur.fetchone()[0])
                if src_key:
                    by_source[src_key] = new_id
                by_biz[d['biz_key']] = new_id
                inserted += 1
            else:
                cur.execute("""
                    UPDATE std_flight_production SET
                        flight_id = %s,
                        airline_code = %s,
                        flight_identity = %s,
                        aircraft_type = %s,
                        aircraft_reg = %s,
                        flight_task_cd = %s,
                        area_code = %s,
                        route_name_zh = %s,
                        route_name_en = %s,
                        route_type = %s,
                        custom_route_type = %s,
                        leg = %s,
                        leg_seq = %s,
                        leg_type = %s,
                        is_local_leg = %s,
                        origin_airport = %s,
                        dest_airport = %s,
                        qty_scope = %s,
                        max_payload = %s,
                        max_seat = %s,
                        available_payload = %s,
                        available_seat = %s,
                        movement_count = %s,
                        movement_time_text = %s,
                        adult_count = %s,
                        child_count = %s,
                        infant_count = %s,
                        transit_adult_count = %s,
                        transit_child_count = %s,
                        transit_infant_count = %s,
                        baggage_weight = %s,
                        mail_weight = %s,
                        cargo_weight = %s,
                        ext_json = CAST(%s AS jsonb),
                        last_event_time = %s,
                        row_version = COALESCE(row_version, 0) + 1,
                        updated_at = now()
                    WHERE id = %s
                """, _prod_update_fields(d, flight_id, ext_json) + (existing_id,))
                updated += 1
            cur.execute('RELEASE SAVEPOINT prod_sp')
        except Exception:
            cur.execute('ROLLBACK TO SAVEPOINT prod_sp')
            cur.execute('RELEASE SAVEPOINT prod_sp')
            failed += 1
    return inserted, updated, failed


def upsert_production_batch(pg_conn, drafts):
    """Upsert a batch of standard flight production rows in one transaction using bulk SQL with fallback."""
    if not drafts:
        return {'inserted': 0, 'updated': 0, 'failed': 0}

    cur = pg_conn.cursor()
    inserted = updated = failed = 0
    try:
        source_systems = sorted({d['source_system'] for d in drafts if d.get('source_system')})
        source_ids = sorted({d['primary_source_id'] for d in drafts if d.get('primary_source_id')})
        by_source = {}
        if source_systems and source_ids:
            cur.execute("""
                SELECT source_system, primary_source_id, id
                FROM std_flight_production
                WHERE source_system = ANY(%s) AND primary_source_id = ANY(%s) AND deleted_at IS NULL
            """, (source_systems, source_ids))
            for ss, sid, pid in cur.fetchall():
                by_source[(ss, sid)] = int(pid)

        biz_keys = sorted({d['biz_key'] for d in drafts if d.get('biz_key')})
        by_biz = {}
        if biz_keys:
            cur.execute("""
                SELECT biz_key, id
                FROM std_flight_production
                WHERE deleted_at IS NULL AND biz_key = ANY(%s)
            """, (biz_keys,))
            for bk, pid in cur.fetchall():
                by_biz[bk] = int(pid)

        airports = sorted({d['airport_code'] for d in drafts})
        dates = sorted({d['flight_date'] for d in drafts})
        flight_lookup = {}
        if airports and dates:
            cur.execute("""
                SELECT id, airport_code, (flight_date AT TIME ZONE 'Asia/Shanghai')::date, direction, flight_no,
                       aircraft_reg, to_char((CASE WHEN direction='DEP' THEN actual_departure ELSE actual_arrival END)
                                             AT TIME ZONE 'Asia/Shanghai', 'HH24:MI'),
                       a."TAKEOFF_AIRPORT_IATA" || '-' || a."ARRIVAL_AIRPORT_IATA"
                FROM std_flight_core c
                LEFT JOIN acdm.tb_dyn_flight a ON a."FLIGHT_ID"=CAST(NULLIF(c.primary_source_id,'') AS numeric)
                WHERE c.deleted_at IS NULL
                  AND airport_code = ANY(%s)
                  AND (flight_date AT TIME ZONE 'Asia/Shanghai')::date = ANY(%s)
                ORDER BY id
            """, (airports, dates))
            for fid, ap, fd, dir_val, fno, aircraft, clock, leg in cur.fetchall():
                flight_lookup.setdefault((ap, fd, dir_val, fno), []).append((int(fid), aircraft, clock, leg))

        remaining = list(drafts)
        while remaining:
            pass_items, remaining = _partition_production_pass(remaining, by_source, by_biz, flight_lookup)
            snap_by_source = dict(by_source)
            snap_by_biz = dict(by_biz)
            cur.execute('SAVEPOINT prod_batch_sp')
            try:
                b_ins, b_upd = _upsert_production_pass_bulk(cur, pass_items, by_source=by_source, by_biz=by_biz)
                cur.execute('RELEASE SAVEPOINT prod_batch_sp')
                inserted += b_ins
                updated += b_upd
            except Exception:
                cur.execute('ROLLBACK TO SAVEPOINT prod_batch_sp')
                cur.execute('RELEASE SAVEPOINT prod_batch_sp')
                by_source = snap_by_source
                by_biz = snap_by_biz
                r_ins, r_upd, r_fail = _upsert_production_batch_rowwise(
                    cur,
                    [item[0] for item in pass_items],
                    by_source=by_source,
                    by_biz=by_biz,
                    flight_lookup=flight_lookup,
                )
                inserted += r_ins
                updated += r_upd
                failed += r_fail

        pg_conn.commit()
        return {'inserted': inserted, 'updated': updated, 'failed': failed}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def _resolve_pg_flight_ids_by_source(cur, source_ids):
    """Map Oracle numeric FLIGHT_ID strings -> local PG std_flight_core.id."""
    str_ids = sorted({str(s).strip() for s in source_ids if s is not None})
    if not str_ids:
        return {}
    mapping = {}
    for chunk in _chunked(str_ids, 1000):
        cur.execute("""
            SELECT DISTINCT ON (source_flight_id) source_flight_id, flight_id
            FROM std_flight_source_ref
            WHERE source_flight_id = ANY(%s)
            ORDER BY source_flight_id,
                     CASE source_system WHEN 'ACDM_ORACLE' THEN 0 WHEN 'FIMS_ORACLE' THEN 1 ELSE 2 END
        """, (chunk,))
        for sid, fid in cur.fetchall():
            mapping[int(sid)] = int(fid)
    return mapping


def sync_turnaround_links(pg_conn, *, exec_date_from=None, exec_date_to=None):
    """Materialize ARR -> DEP turnaround pairs in std_flight_link and partner flags on std_flight_core."""
    cur = pg_conn.cursor()
    try:
        date_filter = ''
        params = []
        if exec_date_from is not None and exec_date_to is not None:
            date_filter = " AND (a.flight_date AT TIME ZONE 'Asia/Shanghai')::date BETWEEN %s AND %s"
            params = [exec_date_from, exec_date_to]

        cur.execute(f"""
            DELETE FROM std_flight_link l
            USING std_flight_core a
            WHERE l.flight_id = a.id
              AND l.link_type = 'TURNAROUND'
              {date_filter}
        """, tuple(params))

        cur.execute(f"""
            INSERT INTO std_flight_link (
                flight_id, linked_flight_id, linked_biz_key, linked_flight_identity,
                linked_flight_date, linked_direction, link_type, created_at, updated_at
            )
            SELECT DISTINCT ON (a.id, d.biz_key)
                a.id,
                d.id,
                d.biz_key,
                d.flight_identity,
                (d.flight_date AT TIME ZONE 'Asia/Shanghai')::date,
                'DEP',
                'TURNAROUND',
                now(),
                now()
            FROM std_flight_core a
            JOIN std_flight_source_ref sr
              ON sr.source_flight_id = a.link_id
            JOIN std_flight_core d
              ON d.id = sr.flight_id
            WHERE a.deleted_at IS NULL
              AND d.deleted_at IS NULL
              AND a.direction = 'ARR'
              AND d.direction = 'DEP'
              AND NULLIF(TRIM(a.link_id), '') IS NOT NULL
              AND (a.aircraft_reg IS NULL OR d.aircraft_reg IS NULL OR a.aircraft_reg = d.aircraft_reg)
              {date_filter}
            ORDER BY a.id, d.biz_key,
                     CASE sr.source_system WHEN 'ACDM_ORACLE' THEN 0 WHEN 'FIMS_ORACLE' THEN 1 ELSE 2 END
            ON CONFLICT (flight_id, link_type, linked_biz_key) WHERE (linked_biz_key IS NOT NULL)
            DO UPDATE SET
                linked_flight_id = EXCLUDED.linked_flight_id,
                linked_flight_identity = EXCLUDED.linked_flight_identity,
                linked_flight_date = EXCLUDED.linked_flight_date,
                updated_at = now()
        """, tuple(params))

        cur.execute(f"""
            UPDATE std_flight_core a SET
                is_link_turn = true,
                partner_flight_no = d.flight_no
            FROM std_flight_link l
            JOIN std_flight_core d ON d.id = l.linked_flight_id
            WHERE l.flight_id = a.id
              AND l.link_type = 'TURNAROUND'
              {date_filter}
        """, tuple(params))

        cur.execute("""
            SELECT COUNT(*) FROM std_flight_link WHERE link_type = 'TURNAROUND'
        """)
        total = int(cur.fetchone()[0] or 0)
        pg_conn.commit()
        return {'turnaround_links': total}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_calc_total_batch(pg_conn, drafts):
    """Bulk upsert TBH_FIMS_DYN_CALC_TOTAL summary drafts into public.std_flight_calc_total."""
    if not drafts:
        return {'inserted': 0, 'updated': 0, 'failed': 0}
    cur = pg_conn.cursor()
    try:
        # Deduplicate intra-batch on source_flight_id
        dedup = {}
        for d in drafts:
            dedup[d['source_flight_id']] = d
        items = list(dedup.values())
        fid_map = _resolve_pg_flight_ids_by_source(cur, [d['source_flight_id'] for d in items])

        source_ids = [d['source_flight_id'] for d in items]
        cur.execute("SELECT source_flight_id FROM std_flight_calc_total WHERE source_flight_id = ANY(%s)", (source_ids,))
        existing = {int(r[0]) for r in cur.fetchall()}

        tpl = '(' + ', '.join(['%s'] * 25) + ', now())'
        for chunk in _chunked(items, 200):
            rows = [
                (
                    d['source_flight_id'], fid_map.get(d['source_flight_id']), d.get('ffid'),
                    d.get('exec_date'), d.get('direction'), d.get('airline_code'), d.get('flight_no'),
                    d.get('dep_dpsr_depe_num'), d.get('psg_arrival_num'),
                    d.get('dep_dpsr_checkin_num'), d.get('dep_dpsr_security_num'), d.get('dep_dpsr_boarded_num'),
                    d.get('dep_dpsr_stop_num'), d.get('dep_dpsr_baby_num'), d.get('dep_dpsr_transfer_num'),
                    d.get('dep_dpsr_vip_num'), d.get('dep_dpsr_first_class_num'),
                    d.get('crew_number'), d.get('bag_num'), d.get('bag_weight'), d.get('delay_duration'),
                    d.get('real_open_cabin_time'), d.get('real_close_cabin_time'),
                    d.get('first_baggage_time'), d.get('last_baggage_time'),
                )
                for d in chunk
            ]
            cur.execute(f"""
                INSERT INTO std_flight_calc_total (
                    source_flight_id, flight_id, ffid, exec_date, direction, airline_code, flight_no,
                    dep_dpsr_depe_num, psg_arrival_num, dep_dpsr_checkin_num, dep_dpsr_security_num,
                    dep_dpsr_boarded_num, dep_dpsr_stop_num, dep_dpsr_baby_num, dep_dpsr_transfer_num,
                    dep_dpsr_vip_num, dep_dpsr_first_class_num, crew_number, bag_num, bag_weight,
                    delay_duration, real_open_cabin_time, real_close_cabin_time,
                    first_baggage_time, last_baggage_time, updated_at
                ) VALUES {_values_placeholders(tpl, len(chunk))}
                ON CONFLICT (source_flight_id) DO UPDATE SET
                    flight_id = COALESCE(EXCLUDED.flight_id, std_flight_calc_total.flight_id),
                    dep_dpsr_depe_num = EXCLUDED.dep_dpsr_depe_num,
                    psg_arrival_num = EXCLUDED.psg_arrival_num,
                    dep_dpsr_checkin_num = EXCLUDED.dep_dpsr_checkin_num,
                    dep_dpsr_security_num = EXCLUDED.dep_dpsr_security_num,
                    dep_dpsr_boarded_num = EXCLUDED.dep_dpsr_boarded_num,
                    dep_dpsr_stop_num = EXCLUDED.dep_dpsr_stop_num,
                    dep_dpsr_baby_num = EXCLUDED.dep_dpsr_baby_num,
                    dep_dpsr_transfer_num = EXCLUDED.dep_dpsr_transfer_num,
                    dep_dpsr_vip_num = EXCLUDED.dep_dpsr_vip_num,
                    dep_dpsr_first_class_num = EXCLUDED.dep_dpsr_first_class_num,
                    crew_number = EXCLUDED.crew_number,
                    bag_num = EXCLUDED.bag_num,
                    bag_weight = EXCLUDED.bag_weight,
                    delay_duration = EXCLUDED.delay_duration,
                    real_open_cabin_time = COALESCE(EXCLUDED.real_open_cabin_time, std_flight_calc_total.real_open_cabin_time),
                    real_close_cabin_time = COALESCE(EXCLUDED.real_close_cabin_time, std_flight_calc_total.real_close_cabin_time),
                    first_baggage_time = COALESCE(EXCLUDED.first_baggage_time, std_flight_calc_total.first_baggage_time),
                    last_baggage_time = COALESCE(EXCLUDED.last_baggage_time, std_flight_calc_total.last_baggage_time),
                    updated_at = now()
            """, _flatten_params(rows))
        pg_conn.commit()
        upd = sum(1 for sid in source_ids if sid in existing)
        return {'inserted': len(source_ids) - upd, 'updated': upd, 'failed': 0}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_flight_air_bridge_batch(pg_conn, drafts):
    """Bulk upsert FIMS.TB_DYN_AIR_BRIDGE rows into public.std_flight_air_bridge."""
    if not drafts:
        return {'inserted': 0, 'updated': 0, 'failed': 0}
    cur = pg_conn.cursor()
    try:
        dedup = {(d['source_flight_id'], d['order_cd'], d.get('air_bridge_id'), d.get('air_bridge_cd')): d for d in drafts}
        items = list(dedup.values())
        fid_map = _resolve_pg_flight_ids_by_source(cur, [d['source_flight_id'] for d in items])
        tpl = '(' + ', '.join(['%s'] * 12) + ', now())'
        for chunk in _chunked(items, 200):
            rows = [
                (
                    d['source_flight_id'], fid_map.get(d['source_flight_id']), d['order_cd'],
                    d.get('air_bridge_id'), d.get('air_bridge_cd'),
                    d.get('bridge_start_time'), d.get('bridge_end_time'), d.get('bridge_duration'),
                    d.get('power_start_time'), d.get('power_end_time'),
                    d.get('ac_start_time'), d.get('ac_end_time'),
                )
                for d in chunk
            ]
            cur.execute(f"""
                INSERT INTO std_flight_air_bridge (
                    source_flight_id, flight_id, order_cd, air_bridge_id, air_bridge_cd,
                    bridge_start_time, bridge_end_time, bridge_duration,
                    power_start_time, power_end_time, ac_start_time, ac_end_time, updated_at
                ) VALUES {_values_placeholders(tpl, len(chunk))}
                ON CONFLICT (source_flight_id, order_cd, air_bridge_id, air_bridge_cd) DO UPDATE SET
                    flight_id = COALESCE(EXCLUDED.flight_id, std_flight_air_bridge.flight_id),
                    air_bridge_id = EXCLUDED.air_bridge_id,
                    air_bridge_cd = EXCLUDED.air_bridge_cd,
                    bridge_start_time = EXCLUDED.bridge_start_time,
                    bridge_end_time = EXCLUDED.bridge_end_time,
                    bridge_duration = EXCLUDED.bridge_duration,
                    power_start_time = EXCLUDED.power_start_time,
                    power_end_time = EXCLUDED.power_end_time,
                    ac_start_time = EXCLUDED.ac_start_time,
                    ac_end_time = EXCLUDED.ac_end_time,
                    updated_at = now()
            """, _flatten_params(rows))
        pg_conn.commit()
        return {'inserted': len(items), 'updated': 0, 'failed': 0}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def upsert_safeguard_node_batch(pg_conn, drafts):
    """Bulk upsert FIMS.TB_DYN_SAFEGUARD_NODE rows into public.std_flight_safeguard_node."""
    if not drafts:
        return {'inserted': 0, 'updated': 0, 'failed': 0}
    cur = pg_conn.cursor()
    try:
        dedup = {(d['source_flight_id'], d['safeguard_order_cd'], d['safeguard_code']): d for d in drafts}
        items = list(dedup.values())
        fid_map = _resolve_pg_flight_ids_by_source(cur, [d['source_flight_id'] for d in items])
        tpl = '(' + ', '.join(['%s'] * 11) + ', now())'
        for chunk in _chunked(items, 200):
            rows = [
                (
                    d['source_flight_id'], fid_map.get(d['source_flight_id']),
                    d['safeguard_order_cd'], d['safeguard_code'],
                    d.get('ffid'), d.get('flight_task_cd'),
                    d.get('safeguard_alterate_time'), d.get('safeguard_real_time'),
                    d.get('abnormal_start_time'), d.get('abnormal_end_time'), d.get('abnormal_reason'),
                )
                for d in chunk
            ]
            cur.execute(f"""
                INSERT INTO std_flight_safeguard_node (
                    source_flight_id, flight_id, safeguard_order_cd, safeguard_code,
                    ffid, flight_task_cd, safeguard_alterate_time, safeguard_real_time,
                    abnormal_start_time, abnormal_end_time, abnormal_reason, updated_at
                ) VALUES {_values_placeholders(tpl, len(chunk))}
                ON CONFLICT (source_flight_id, safeguard_order_cd, safeguard_code) DO UPDATE SET
                    flight_id = COALESCE(EXCLUDED.flight_id, std_flight_safeguard_node.flight_id),
                    ffid = EXCLUDED.ffid,
                    flight_task_cd = EXCLUDED.flight_task_cd,
                    safeguard_alterate_time = EXCLUDED.safeguard_alterate_time,
                    safeguard_real_time = EXCLUDED.safeguard_real_time,
                    abnormal_start_time = EXCLUDED.abnormal_start_time,
                    abnormal_end_time = EXCLUDED.abnormal_end_time,
                    abnormal_reason = EXCLUDED.abnormal_reason,
                    updated_at = now()
            """, _flatten_params(rows))
        pg_conn.commit()
        return {'inserted': len(items), 'updated': 0, 'failed': 0}
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def _normalize_acdm_raw_val(val):
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    return val


def upsert_acdm_raw_batch(pg_conn, raw_rows):
    """Refresh acdm.tb_dyn_flight when present in PG so legacy raw ACDM queries stay fresh."""
    if not raw_rows or not _table_exists(pg_conn, 'acdm', 'tb_dyn_flight'):
        return 0
    cur = pg_conn.cursor()
    try:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'acdm' AND table_name = 'tb_dyn_flight'
            ORDER BY ordinal_position
        """)
        pg_cols = cur.fetchall()
        sample_keys = {str(k).upper() for k in raw_rows[0].keys()}
        cols = [(c, dt) for c, dt in pg_cols if c in sample_keys]
        if not cols or ('FLIGHT_ID' not in [c for c, _ in cols]):
            return 0

        dedup = {}
        for r in raw_rows:
            fid = r.get('FLIGHT_ID')
            if fid is not None:
                dedup[int(fid)] = r
        items = list(dedup.values())
        if not items:
            return 0

        fids = list(dedup.keys())
        for id_chunk in _chunked(fids, 500):
            cur.execute('DELETE FROM acdm.tb_dyn_flight WHERE "FLIGHT_ID" = ANY(%s)', (id_chunk,))

        col_list = ', '.join(f'"{c}"' for c, _ in cols)
        tpl = '(' + ', '.join(['%s'] * len(cols)) + ')'
        for chunk in _chunked(items, 100):
            flat = []
            for r in chunk:
                for c, dt in cols:
                    val = _normalize_acdm_raw_val(r.get(c))
                    if val is not None and dt in ('text', 'character varying') and not isinstance(val, str):
                        val = str(val)
                    flat.append(val)
            cur.execute(
                f'INSERT INTO acdm.tb_dyn_flight ({col_list}) VALUES {_values_placeholders(tpl, len(chunk))}',
                tuple(flat),
            )
        pg_conn.commit()
        return len(items)
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()


def prune_stale_window(pg_conn, keep_from_date):
    """Prune flight & satellite rows strictly older than keep_from_date when syncing a sliding window."""
    if keep_from_date is None:
        return {}
    cur = pg_conn.cursor()
    try:
        cur.execute("""
            SELECT id FROM public.std_flight_core
            WHERE (flight_date AT TIME ZONE 'Asia/Shanghai')::date < %s
        """, (keep_from_date,))
        stale_ids = [int(r[0]) for r in cur.fetchall()]
        deleted = {'std_flight_core': len(stale_ids)}

        if stale_ids:
            for id_chunk in _chunked(stale_ids, 2000):
                for child_tbl, col in (
                    ('public.std_flight_indicator', 'flight_id'),
                    ('public.std_flight_process', 'flight_id'),
                    ('public.std_flight_source_ref', 'flight_id'),
                    ('public.std_flight_resource', 'flight_id'),
                    ('public.std_flight_safeguard_node', 'flight_id'),
                    ('public.std_flight_air_bridge', 'flight_id'),
                    ('public.std_flight_calc_total', 'flight_id'),
                ):
                    if _table_exists(pg_conn, child_tbl.split('.')[0], child_tbl.split('.')[1]):
                        cur.execute(f'DELETE FROM {child_tbl} WHERE {col} = ANY(%s)', (id_chunk,))
                cur.execute('DELETE FROM public.std_flight_link WHERE flight_id = ANY(%s) OR linked_flight_id = ANY(%s)', (id_chunk, id_chunk))
                cur.execute('DELETE FROM public.std_flight_production WHERE flight_id = ANY(%s)', (id_chunk,))
            for id_chunk in _chunked(stale_ids, 2000):
                cur.execute('DELETE FROM public.std_flight_core WHERE id = ANY(%s)', (id_chunk,))

        cur.execute('DELETE FROM public.std_flight_production WHERE flight_date < %s', (keep_from_date,))
        deleted['std_flight_production_extra'] = cur.rowcount

        if _table_exists(pg_conn, 'public', 'std_flight_calc_total'):
            cur.execute('DELETE FROM public.std_flight_calc_total WHERE exec_date < %s', (keep_from_date,))
            deleted['std_flight_calc_total_extra'] = cur.rowcount
        if _table_exists(pg_conn, 'public', 'std_flight_air_bridge'):
            cur.execute("""
                DELETE FROM public.std_flight_air_bridge
                WHERE flight_id IS NULL
                  AND COALESCE(bridge_start_time, power_start_time, ac_start_time, bridge_end_time) IS NOT NULL
                  AND (COALESCE(bridge_start_time, power_start_time, ac_start_time, bridge_end_time) AT TIME ZONE 'Asia/Shanghai')::date < %s
            """, (keep_from_date,))
            deleted['std_flight_air_bridge_extra'] = cur.rowcount
        if _table_exists(pg_conn, 'public', 'std_flight_safeguard_node'):
            cur.execute("""
                DELETE FROM public.std_flight_safeguard_node
                WHERE flight_id IS NULL
                  AND COALESCE(safeguard_real_time, safeguard_alterate_time, abnormal_start_time) IS NOT NULL
                  AND (COALESCE(safeguard_real_time, safeguard_alterate_time, abnormal_start_time) AT TIME ZONE 'Asia/Shanghai')::date < %s
            """, (keep_from_date,))
            deleted['std_flight_safeguard_node_extra'] = cur.rowcount
        if _table_exists(pg_conn, 'acdm', 'tb_dyn_flight'):
            cur.execute('DELETE FROM acdm.tb_dyn_flight WHERE "EXEC_DATE"::date < %s', (keep_from_date,))
            deleted['acdm_tb_dyn_flight'] = cur.rowcount
        pg_conn.commit()
        return deleted
    except Exception:
        pg_conn.rollback()
        raise
    finally:
        cur.close()
