"""Domain mappers and normalizers ported directly from airport-ops-data-sync."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
import zlib

SHANGHAI_TZ = timezone(timedelta(hours=8))
DEFAULT_DEP_VALUES = frozenset({'1', 'D', 'O', '出'})
DEFAULT_ARR_VALUES = frozenset({'0', 'A', 'I', '进'})

NATURE_MAP = {
    'W/Z': '正班', 'WZ': '正班', 'Z/P': '正班', 'ZP': '正班', 'J/B': '正班', 'JB': '正班',
    'C/B': '补班', 'CB': '补班', '补班': '补班',
    'F/E': '调机', 'FE': '调机', '调机': '调机',
    'H/G': '货正', 'HG': '货正', '货班': '货正', '货正': '货正',
    'B/J': '备降', 'BJ': '备降', '备降': '备降',
}


def has_text(value):
    return isinstance(value, str) and bool(value.strip())


def trim(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def upper(value):
    text = trim(value)
    return text.upper() if text else None


def clip(value, max_len):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


def first_non_blank(*values):
    for val in values:
        text = trim(val)
        if text:
            return text
    return None


def first_ts(*values):
    for val in values:
        if val is not None:
            return val
    return None


def yes(value):
    text = trim(value)
    if not text:
        return False
    return text.upper() in ('Y', '1', 'TRUE') or text == '是'


def yn(value):
    if not trim(value):
        return None
    return yes(value)


def yn_flag(value):
    """Y/1/是 -> True; N/0/否 -> False; otherwise None."""
    text = trim(value)
    if not text:
        return None
    up = text.upper()
    if up in ('1', 'Y') or text == '是':
        return True
    if up in ('0', 'N') or text == '否':
        return False
    return None


def is_deleted(value):
    if value is None:
        return False
    if isinstance(value, (int, float, Decimal)):
        return int(value) == 1
    text = str(value).strip().upper()
    return text in ('1', 'Y')


def to_shanghai_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI_TZ) if value.tzinfo else value.replace(tzinfo=SHANGHAI_TZ)
    return None


def _opt_int(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(Decimal(text))
    except Exception:
        return None


def _opt_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(Decimal(text))
    except Exception:
        return None


def _assoc_id_text(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(int(Decimal(text)))
    except Exception:
        return clip(text, 64)


_ATTR_COUNTRY_CODE_MAP = {
    '2401': 'I',  # 国际
    '2402': 'R',  # 地区
    '2403': 'D',  # 国内
    '2404': 'M',  # 混合
}


def to_utc_dt(value):
    dt = to_shanghai_dt(value)
    return dt.astimezone(timezone.utc) if dt else None


def to_local_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_shanghai_dt(value).date()
    if isinstance(value, date):
        return value
    return None


def split_csv_tokens(csv):
    if not csv:
        return set()
    return {part.strip().upper() for part in str(csv).split(',') if part.strip()}


def normalize_flight_no(airline, flight_no):
    """航司二字码 + 航班号 -> 标准航班标识（如 CZ9301）."""
    raw = trim(flight_no)
    if not raw:
        return None
    no = raw.upper().replace(' ', '')
    al = upper(airline)
    if al and not no.startswith(al):
        return al + no
    return no


def map_direction(is_off_in, dep_values=DEFAULT_DEP_VALUES, arr_values=DEFAULT_ARR_VALUES, *, production=False):
    val = trim(is_off_in)
    if not val:
        return None
    up = val.upper()
    dep_set = {x.upper() for x in dep_values}
    arr_set = {x.upper() for x in arr_values}
    if production:
        dep_set |= {'O', '出', '1', 'D'}
        arr_set |= {'I', '进', '0', 'A'}
    if val in dep_set or up in dep_set:
        return 'DEP'
    if val in arr_set or up in arr_set:
        return 'ARR'
    if val == '1':
        return 'DEP'
    if val == '0':
        return 'ARR'
    return None


def map_status(abnormal, normal):
    ab = trim(abnormal)
    if ab:
        up = ab.upper()
        if 'CNL' in up or 'CAN' in up or up == 'X' or ab == '取消':
            return '取消'
        if 'DIV' in up or 'RET' in up or ab == '备降':
            return '备降'
        return ab
    norm = trim(normal)
    return norm if norm else None


def map_nature(task_cd, flight_rp, attribute):
    if yes(flight_rp):
        return '补班'
    tc = trim(task_cd)
    if not tc:
        return trim(attribute)
    return NATURE_MAP.get(tc.upper(), tc)


def split_leg(leg):
    text = trim(leg)
    if not text:
        return None
    normalized = text.upper().replace('—', '-').replace('–', '-')
    parts = re.split(r'[-/]', normalized)
    if len(parts) >= 2 and len(parts[0]) == 3 and len(parts[1]) == 3:
        return parts[0], parts[1]
    return None


def resolve_leg_seq(leg, airport, direction):
    text = trim(leg)
    if not text:
        return 1
    parts = split_leg(text)
    if not parts:
        return (zlib.crc32(text.encode('utf-8')) % 1000) + 1
    if direction == 'DEP' and airport == parts[0]:
        return 1
    if direction == 'ARR' and airport == parts[1]:
        return 1
    return 2


def map_flight_draft(row, source_system, acdm_profile, *, airport_code='WUH',
                     dep_values=DEFAULT_DEP_VALUES, arr_values=DEFAULT_ARR_VALUES):
    """Map an Oracle FIMS/ACDM TB_DYN_FLIGHT row dict into a standard flight draft dict."""
    direction = map_direction(row.get('IS_OFF_IN'), dep_values, arr_values)
    if direction is None:
        return None

    airline_raw = row.get('AIRLINE_TWO_CHAR_CD')
    flight_no = normalize_flight_no(airline_raw, row.get('FLIGHT_NO'))
    if not flight_no:
        return None

    flight_date = to_local_date(row.get('EXEC_DATE'))
    if flight_date is None and row.get('PLAN_TIME') is not None:
        flight_date = to_local_date(row.get('PLAN_TIME'))
    if flight_date is None:
        return None

    airline = upper(airline_raw)
    airport = upper(airport_code) or 'WUH'
    biz_key = f'{airport}:{flight_date.isoformat()}:{direction}:{flight_no}'

    if acdm_profile:
        sched_dep = to_shanghai_dt(row.get('SCHEME_TAKEOFF_TIME'))
        est_dep = to_shanghai_dt(row.get('ALTERATE_TAKEOFF_TIME'))
        act_dep = to_shanghai_dt(row.get('REAL_TAKEOFF_TIME'))
        sched_arr = to_shanghai_dt(row.get('SCHEME_LANDIN_TIME'))
        est_arr = to_shanghai_dt(row.get('ALTERATE_LANDIN_TIME'))
        act_arr = to_shanghai_dt(row.get('REAL_LANDIN_TIME'))
        origin_airport = upper(first_non_blank(row.get('TAKEOFF_AIRPORT_IATA'), row.get('START_STATION')))
        dest_airport = upper(first_non_blank(row.get('ARRIVAL_AIRPORT_IATA'), row.get('TERMINAL_STATION')))
        board_start = to_shanghai_dt(row.get('REAL_BEGIN_BOARD_TIME'))
        board_end = to_shanghai_dt(row.get('REAL_END_BOARD_TIME'))
        last_call = to_shanghai_dt(row.get('URGE_BOARD_TIME'))
    else:
        if direction == 'DEP':
            sched_dep = to_shanghai_dt(first_ts(row.get('NEAREST_SCHEME_TAKEOFF_TIME'), row.get('START_SCHEME_TAKEOFF_TIME'), row.get('PLAN_TIME')))
            est_dep = to_shanghai_dt(first_ts(row.get('NEAREST_ALTERATE_TAKEOFF_TIME'), row.get('START_ALTERATE_TAKEOFF_TIME')))
            act_dep = to_shanghai_dt(first_ts(row.get('NEAREST_REAL_TAKEOFF_TIME'), row.get('START_REAL_TAKEOFF_TIME')))
            sched_arr = to_shanghai_dt(row.get('TERMINAL_SCHEME_LANDIN_TIME'))
            est_arr = to_shanghai_dt(row.get('TERMINAL_ALTERATE_LANDIN_TIME'))
            act_arr = to_shanghai_dt(row.get('TERMINAL_REAL_LANDIN_TIME'))
        else:
            sched_arr = to_shanghai_dt(first_ts(row.get('NEAREST_SCHEME_LANDIN_TIME'), row.get('TERMINAL_SCHEME_LANDIN_TIME'), row.get('PLAN_TIME')))
            est_arr = to_shanghai_dt(first_ts(row.get('NEAREST_ALTERATE_LANDIN_TIME'), row.get('TERMINAL_ALTERATE_LANDIN_TIME')))
            act_arr = to_shanghai_dt(first_ts(row.get('NEAREST_REAL_LANDIN_TIME'), row.get('TERMINAL_REAL_LANDIN_TIME')))
            sched_dep = to_shanghai_dt(row.get('START_SCHEME_TAKEOFF_TIME'))
            est_dep = to_shanghai_dt(row.get('START_ALTERATE_TAKEOFF_TIME'))
            act_dep = to_shanghai_dt(row.get('START_REAL_TAKEOFF_TIME'))
        origin_airport = upper(row.get('START_STATION'))
        dest_airport = upper(row.get('TERMINAL_STATION'))
        board_start = to_shanghai_dt(row.get('BORDING_START_REALTIME'))
        board_end = to_shanghai_dt(row.get('BORDING_END_REALTIME'))
        last_call = to_shanghai_dt(row.get('LAST_CALL_REALTIME'))

    if direction == 'DEP' and not origin_airport:
        origin_airport = airport
    if direction == 'ARR' and not dest_airport:
        dest_airport = airport

    status = map_status(row.get('ABNORMAL_FLIGHT_STAT_CD'), row.get('NORMAL_FLIGHT_STAT_CD'))
    nature = map_nature(row.get('FLIGHT_TASK_CD'), row.get('FLIGHT_RP'), row.get('ATTRIBUTE'))
    flight_task_cd = clip(row.get('FLIGHT_TASK_CD'), 16)
    flight_attribute = clip(row.get('ATTRIBUTE'), 16)
    flight_country_type = _ATTR_COUNTRY_CODE_MAP.get(flight_attribute)
    cancel_period = clip(row.get('S_CNL_STAUTS'), 32)
    plan_time = to_shanghai_dt(row.get('PLAN_TIME'))
    link_id = _assoc_id_text(row.get('ASSOCIATED_FLIGHT_ID'))
    divert = yes(row.get('ALT')) or bool(trim(row.get('DIVERT_EDSTATION'))) or nature in ('备降', '返航飞行')
    ferry = nature == '调机'
    ori_act = yn(row.get('ORI_ACT'))
    flight_first_on_time = yn(row.get('FLIGHT_FIRST_ON_TIME'))
    origin_flight = yes(row.get('ORI_ACT')) if row.get('ORI_ACT') is not None else yes(row.get('FLIGHT_FIRST_ON_TIME'))

    release_on_time = yn(row.get('RELEASE_ON_TIME'))
    takeoff_on_time = yn(row.get('TAKEOFF_ON_TIME'))
    dep_on_time = yn(row.get('DEP_ON_TIME'))
    land_on_time = yn(row.get('LAND_ON_TIME'))
    arr_on_time = yn(row.get('ARR_ON_TIME'))
    flight_on_time = yn(row.get('FLIGHT_ON_TIME'))

    release_delay = _opt_int(row.get('CLEARANCE_DELAY_TIME'))
    flight_delay = _opt_int(row.get('FLIGHT_DELAY_TIME'))
    dep_delay = _opt_int(row.get('DEP_DELAY_TIME'))
    takeoff_delay = _opt_int(row.get('TAKEOFF_DELAY_TIME'))
    land_delay = _opt_int(row.get('LAND_DELAY_TIME'))
    arr_delay = _opt_int(row.get('ARR_DELAY_TIME'))

    mins = None
    if direction == 'DEP' and sched_dep is not None and act_dep is not None:
        mins = int((act_dep - sched_dep).total_seconds() / 60)
    elif direction == 'ARR' and sched_arr is not None and act_arr is not None:
        mins = int((act_arr - sched_arr).total_seconds() / 60)

    if takeoff_on_time is None and direction == 'DEP' and mins is not None:
        takeoff_on_time = mins <= 15
    if dep_on_time is None:
        dep_on_time = takeoff_on_time
    if takeoff_delay is None and direction == 'DEP' and mins is not None:
        takeoff_delay = mins
    if dep_delay is None and direction == 'DEP' and mins is not None:
        dep_delay = mins

    if flight_delay is not None:
        delayed = flight_delay > 0
        delay_minutes = flight_delay if flight_delay > 0 else 0
    elif mins is not None:
        delayed = mins > 15
        delay_minutes = mins if delayed else 0
    else:
        delayed = None
        delay_minutes = None

    terminal = first_non_blank(row.get('DOMESTIC_TERMINAL_CD'), row.get('INTERNATION_TERMINAL_CD'))
    source_updated = to_utc_dt(row.get('UPDATE_TIME')) or datetime.now(timezone.utc)

    checkin_act_begin = first_ts(to_shanghai_dt(row.get('CHECKIN_START_REALTIME')), to_shanghai_dt(row.get('CHECKIN_START_TIME')))
    checkin_act_end = first_ts(to_shanghai_dt(row.get('CHECKIN_END_REALTIME')), to_shanghai_dt(row.get('CHECKIN_END_TIME')))
    checkin_sched_begin = to_shanghai_dt(row.get('CHECKIN_ALTERATE_STARTTIME'))
    checkin_sched_end = to_shanghai_dt(row.get('CHECKIN_ALTERATE_ENDTIME'))

    cobt = to_shanghai_dt(row.get('COUNT_BLOCK_OFF_TIME'))
    tobt = to_shanghai_dt(row.get('TARGT_BLOCK_OFF_TIME'))
    tsat = to_shanghai_dt(row.get('TARGT_DRIVE_TIME'))
    ttot = to_shanghai_dt(row.get('TARGT_TAKE_OFF_TIME'))
    ctot = to_shanghai_dt(row.get('COUNT_TAKE_OFF_TIME'))

    process_ext = {}
    for k in (
        'AIRPORT_TARGT_BLOCK_OFF_TIME', 'COORDINATE_BLOCK_OFF_TIME',
        'PLAN_CLOSE_DOOR_TIME', 'LOAD_START_TIME', 'LOAD_END_TIME',
        'ALTERATE_START_CLEAN_TIME', 'ALTERATE_END_CLEAN_TIME',
        'ALTERATE_MEAL_START_TIME', 'ALTERATE_MEAL_END_TIME',
        'ALTERATE_START_SUPPLY_TIME', 'ALTERATE_END_SUPPLY_TIME',
        'ALTERATE_START_DEICE_TIME', 'ALTERATE_END_DEICIE_TIME',
        'ALTERATE_BEGIN_BOARD_TIME', 'ALTERATE_END_BOARD_TIME',
        'ALTERATE_TAXI_IN_TIME', 'ALTERATE_TAXI_OUT_TIME',
    ):
        val = row.get(k)
        if val is not None:
            dt = to_shanghai_dt(val)
            process_ext[k] = dt.isoformat() if dt is not None else val
    if row.get('IS_VIP') is not None:
        process_ext['IS_VIP'] = row.get('IS_VIP')
    if row.get('VIP_NUMBER') is not None:
        process_ext['VIP_NUMBER'] = row.get('VIP_NUMBER')

    return {
        'biz_key': biz_key,
        'airport_code': airport,
        'flight_date': flight_date,
        'flight_date_utc': datetime(flight_date.year, flight_date.month, flight_date.day, tzinfo=timezone.utc),
        'direction': direction,
        'flight_no': flight_no,
        'flight_identity': flight_no,
        'airline_code': airline,
        'flight_nature': nature,
        'flight_task_cd': flight_task_cd,
        'flight_attribute': flight_attribute,
        'flight_country_type': flight_country_type,
        'cancel_period': cancel_period,
        'plan_time': plan_time,
        'link_id': link_id,
        'flight_status': status,
        'operation_status': upper(row.get('NORMAL_FLIGHT_STAT_CD')),
        'delay_code': upper(row.get('ABNORMAL_REASON_CD')),
        'delay_reason_text': trim(row.get('ABNORMAL_REASON_CD')),
        'aircraft_type': upper(row.get('CRAFT_TYPE_ICAO_CD')),
        'aircraft_reg': upper(row.get('CRAFT_NO')),
        'aircraft_icao': upper(row.get('CRAFT_TYPE_ICAO_CD')),
        'stand_code': upper(row.get('CRAFT_SITE_CD')),
        'gate_code': upper(row.get('GATE_CD')),
        'terminal': upper(terminal),
        'runway': upper(row.get('RUNWAY_NO')),
        'agency': upper(row.get('AGENCY')),
        'origin_airport': origin_airport,
        'dest_airport': dest_airport,
        'previous_airport': upper(row.get('NEAREST_STATION')) if direction == 'ARR' else None,
        'next_airport': upper(row.get('NEAREST_STATION')) if direction == 'DEP' else None,
        'diversion_airport': upper(row.get('DIVERT_EDSTATION')),
        'origin_city': origin_airport,
        'dest_city': dest_airport,
        'scheduled_departure': sched_dep,
        'estimated_departure': est_dep,
        'actual_departure': act_dep,
        'scheduled_arrival': sched_arr,
        'estimated_arrival': est_arr,
        'actual_arrival': act_arr,
        'flight_scheduled_datetime': sched_dep if direction == 'DEP' else sched_arr,
        'board_time': board_start,
        'cobt': cobt,
        'tobt': tobt,
        'tsat': tsat,
        'ttot': ttot,
        'ctot': ctot,
        'estimated_on_block': to_shanghai_dt(row.get('ALTERATE_BLOCK_ON_TIME')),
        'estimated_off_block': to_shanghai_dt(row.get('ALTERATE_BLOCK_OFF_TIME')),
        'actual_on_block': to_shanghai_dt(row.get('REAL_BLOCK_ON_TIME')),
        'actual_off_block': to_shanghai_dt(row.get('REAL_BLOCK_OFF_TIME')),
        'boarding_start': board_start,
        'boarding_end': board_end,
        'last_call': last_call,
        'checkin_sched_begin': checkin_sched_begin,
        'checkin_sched_end': checkin_sched_end,
        'checkin_act_begin': checkin_act_begin,
        'checkin_act_end': checkin_act_end,
        'first_baggage': to_shanghai_dt(row.get('FIRST_BAGGAGE_TIME')),
        'last_baggage': to_shanghai_dt(row.get('LAST_BAGGAGE_TIME')),
        'alterate_by_bridge_time': to_shanghai_dt(row.get('ALTERATE_BY_BRIDGE_TIME')),
        'real_by_bridge_time': to_shanghai_dt(row.get('REAL_BY_BRIDGE_TIME')),
        'plan_off_bridge_time': to_shanghai_dt(row.get('PLAN_OFF_BRIDGE_TIME')),
        'real_off_bridge_time': to_shanghai_dt(row.get('REAL_OFF_BRIDGE_TIME')),
        'departure_start_time': to_shanghai_dt(row.get('DEPARTURE_START_TIME')),
        'departure_end_time': to_shanghai_dt(row.get('DEPARTURE_END_TIME')),
        'alterate_open_cabin_time': to_shanghai_dt(row.get('ALTERATE_OPEN_CABIN_TIME')),
        'real_open_cabin_time': to_shanghai_dt(row.get('REAL_OPEN_CABIN_TIME')),
        'alterate_close_cabin_time': to_shanghai_dt(row.get('ALTERATE_CLOSE_CABIN_TIME')),
        'real_close_cabin_time': to_shanghai_dt(row.get('REAL_CLOSE_CABIN_TIME')),
        'real_start_clean_time': to_shanghai_dt(row.get('REAL_START_CLEAN_TIME')),
        'real_end_clean_time': to_shanghai_dt(row.get('REAL_END_CLEAN_TIME')),
        'real_meal_start_time': to_shanghai_dt(row.get('REAL_MEAL_START_TIME')),
        'real_meal_end_time': to_shanghai_dt(row.get('REAL_MEAL_END_TIME')),
        'real_start_supply_time': to_shanghai_dt(row.get('REAL_START_SUPPLY_TIME')),
        'real_end_supply_time': to_shanghai_dt(row.get('REAL_END_SUPPLY_TIME')),
        'real_start_deice_time': to_shanghai_dt(row.get('REAL_START_DEICE_TIME')),
        'real_end_deicie_time': to_shanghai_dt(row.get('REAL_END_DEICIE_TIME')),
        'request_launch_time': to_shanghai_dt(row.get('REQUEST_LAUNCH_TIME')),
        'real_launch_time': to_shanghai_dt(row.get('REAL_LAUNCH_TIME')),
        'real_drive_time': to_shanghai_dt(row.get('REAL_DRIVE_TIME')),
        'real_taxi_in_time': _opt_float(row.get('REAL_TAXI_IN_TIME')),
        'real_taxi_out_time': _opt_float(row.get('REAL_TAXI_OUT_TIME')),
        'process_ext': process_ext,
        'is_virtual': yn(row.get('IS_VIRTUAL')),
        'is_divert': True if divert else None,
        'is_ferry': True if ferry else None,
        'is_origin': True if origin_flight else None,
        'associated_flight_id': row.get('ASSOCIATED_FLIGHT_ID'),
        'is_delayed': delayed,
        'delay_minutes': delay_minutes,
        'is_origin_flight': True if origin_flight else None,
        'ori_act': ori_act,
        'flight_first_on_time': flight_first_on_time,
        'release_on_time': release_on_time,
        'takeoff_on_time': takeoff_on_time,
        'dep_on_time': dep_on_time,
        'land_on_time': land_on_time,
        'arr_on_time': arr_on_time,
        'flight_on_time': flight_on_time,
        'release_delay_minutes': release_delay,
        'dep_delay_minutes': dep_delay,
        'takeoff_delay_minutes': takeoff_delay,
        'land_delay_minutes': land_delay,
        'arr_delay_minutes': arr_delay,
        'flight_delay_minutes': flight_delay,
        'delay_reason': trim(row.get('ABNORMAL_REASON_CD')),
        'partner_flight_no': None,
        'source_system': source_system,
        'primary_source_id': str(row['FLIGHT_ID']),
        'source_ffid': trim(row.get('FFID')),
        'vendor_code': airport.lower() + '_oracle',
        'source_updated_at': source_updated,
        'last_event_time': source_updated,
    }


def map_production_draft(row, *, airport_code='WUH', source_system='FORMFILL_ORACLE',
                         dep_values=DEFAULT_DEP_VALUES, arr_values=DEFAULT_ARR_VALUES):
    """Map an Oracle FORMFILL.SUM_FLIGHT_PRODUCTION_ANALYSIS row dict."""
    direction = map_direction(row.get('IS_OFF_IN'), dep_values, arr_values, production=True)
    exec_date = to_local_date(row.get('EXEC_DATE'))
    raw_fno = trim(row.get('FLIGHT_NO'))
    if direction is None or exec_date is None or not raw_fno:
        return None

    airport = (first_non_blank(row.get('AIRPORT_IATA'), airport_code) or 'WUH').upper()
    airline = upper(row.get('AIRLINE_TWO_CHAR_CD'))
    flight_no = normalize_flight_no(airline, raw_fno)
    leg = trim(row.get('LEG'))
    leg_seq = resolve_leg_seq(leg, airport, direction)
    leg_key = leg.replace(' ', '_') if leg else str(leg_seq)
    biz_key = f'{airport}:{exec_date.isoformat()}:{direction}:{flight_no}:L{leg_key}'

    parts = split_leg(leg)
    if parts:
        origin, dest = parts
    elif direction == 'DEP':
        origin, dest = airport, None
    else:
        origin, dest = None, airport

    local_leg = bool(leg and airport in leg.upper())
    updated = to_utc_dt(row.get('UPDATE_TIME')) or to_utc_dt(row.get('CREATE_TIME')) or datetime.now(timezone.utc)

    pe_payload = row.get('PE_PAYLOAD')
    pe_seat = row.get('PE_SEAT')

    return {
        'biz_key': biz_key,
        'airport_code': airport,
        'flight_date': exec_date,
        'direction': direction,
        'airline_code': airline,
        'flight_no': flight_no,
        'flight_identity': flight_no,
        'aircraft_type': upper(row.get('CRAFT_TYPE_ICAO_CD')),
        'aircraft_reg': upper(row.get('CRAFT_NO')),
        'flight_task_cd': trim(row.get('FLIGHT_TASK_CD')),
        'area_code': trim(row.get('AREA_CODE')),
        'route_name_zh': trim(row.get('AIR_LINE_CHINESE_NAME')),
        'route_name_en': trim(row.get('AIR_LINE_ENGLISH_NAME')),
        'route_type': trim(row.get('AIR_LINE_TYPE')),
        'custom_route_type': trim(row.get('CUSTOM_AIR_LINE_TYPE')),
        'leg': leg,
        'leg_seq': leg_seq,
        'leg_type': trim(row.get('LEG_TYPE')),
        'is_local_leg': local_leg,
        'origin_airport': origin,
        'dest_airport': dest,
        'qty_scope': 'LEG',
        'max_payload': row.get('MAX_PAYLOAD'),
        'max_seat': int(row['MAX_SEAT']) if row.get('MAX_SEAT') is not None else None,
        'available_payload': row.get('AVAILABLE_PAYLOAD'),
        'available_seat': int(row['AVAILABLE_SEAT']) if row.get('AVAILABLE_SEAT') is not None else None,
        'movement_count': int(row['TAKEOFF_LANDIN_COUNT']) if row.get('TAKEOFF_LANDIN_COUNT') is not None else None,
        'movement_time_text': trim(row.get('TAKEOFF_LANDIN_TIME')),
        'adult_count': int(row['ADULT_COUNT']) if row.get('ADULT_COUNT') is not None else None,
        'child_count': int(row['CHILDREN_COUNT']) if row.get('CHILDREN_COUNT') is not None else None,
        'infant_count': int(row['BABY_COUNT']) if row.get('BABY_COUNT') is not None else None,
        'transit_adult_count': int(row['OVER_STATION_ADULT_COUNT']) if row.get('OVER_STATION_ADULT_COUNT') is not None else None,
        'transit_child_count': int(row['OVER_STATION_CHILDREN_COUNT']) if row.get('OVER_STATION_CHILDREN_COUNT') is not None else None,
        'transit_infant_count': int(row['OVER_STATION_BABY_COUNT']) if row.get('OVER_STATION_BABY_COUNT') is not None else None,
        'baggage_weight': row.get('LUGGAGE'),
        'mail_weight': row.get('MAIL'),
        'cargo_weight': row.get('GOODS'),
        'pe_payload': str(pe_payload) if pe_payload is not None else None,
        'pe_seat': str(int(pe_seat)) if pe_seat is not None else None,
        'source_system': source_system,
        'primary_source_id': trim(row.get('UUID')),
        'source_updated_at': updated,
        'last_event_time': updated,
    }


def map_codeshare_draft(row):
    flight_id = row.get('FLIGHT_ID')
    if flight_id is None:
        return None
    identity = normalize_flight_no(row.get('AIRLINE_TWO_CHAR_CD'), row.get('FLIGHT_NO'))
    if not identity:
        return None
    return {
        'source_flight_id': str(int(flight_id)),
        'share_airline_code': upper(row.get('AIRLINE_TWO_CHAR_CD')),
        'share_flight_no_raw': trim(row.get('FLIGHT_NO')),
        'share_flight_identity': identity,
    }


def map_calc_total_draft(row, *, dep_values=DEFAULT_DEP_VALUES, arr_values=DEFAULT_ARR_VALUES):
    """Map a FIMS.TBH_FIMS_DYN_CALC_TOTAL row dict."""
    fid = row.get('FLIGHT_ID')
    if fid is None:
        return None
    airline = upper(row.get('AIRLINE_TWO_CHAR_CD'))
    return {
        'source_flight_id': int(Decimal(str(fid))),
        'ffid': trim(row.get('FFID')),
        'exec_date': to_local_date(row.get('EXEC_DATE')),
        'direction': map_direction(row.get('IS_OFF_IN'), dep_values, arr_values),
        'airline_code': airline,
        'flight_no': normalize_flight_no(airline, row.get('FLIGHT_NO')),
        'dep_dpsr_depe_num': _opt_int(row.get('DEP_DPSR_DEPE_NUM')),
        'psg_arrival_num': _opt_int(row.get('PSG_ARRIVAL_NUM')),
        'dep_dpsr_checkin_num': _opt_int(row.get('DEP_DPSR_CHECKIN_NUM')),
        'dep_dpsr_security_num': _opt_int(row.get('DEP_DPSR_SECURITY_NUM')),
        'dep_dpsr_boarded_num': _opt_int(row.get('DEP_DPSR_BOARDED_NUM')),
        'dep_dpsr_stop_num': _opt_int(row.get('DEP_DPSR_STOP_NUM')),
        'dep_dpsr_baby_num': _opt_int(row.get('DEP_DPSR_BABY_NUM')),
        'dep_dpsr_transfer_num': _opt_int(row.get('DEP_DPSR_TRANSFER_NUM')),
        'dep_dpsr_vip_num': _opt_int(row.get('DEP_DPSR_VIP_NUM')),
        'dep_dpsr_first_class_num': _opt_int(row.get('DEP_DPSR_FIRST_CLASS_NUM')),
        'crew_number': _opt_int(row.get('CREW_NUMBER')),
        'bag_num': _opt_int(row.get('BAG_NUM')),
        'bag_weight': row.get('BAG_WEIGHT'),
        'delay_duration': _opt_int(row.get('DELAY_DURATION')),
        'real_open_cabin_time': to_utc_dt(row.get('REAL_OPEN_CABIN_TIME')),
        'real_close_cabin_time': to_utc_dt(row.get('REAL_CLOSE_CABIN_TIME')),
        'first_baggage_time': to_utc_dt(row.get('FIRST_BAGGAGE_TIME')),
        'last_baggage_time': to_utc_dt(row.get('LAST_BAGGAGE_TIME')),
        'source_updated_at': to_utc_dt(row.get('UPDATE_TIME')) or to_utc_dt(row.get('CREATE_TIME')),
    }


def map_air_bridge_draft(row):
    """Map a FIMS.TB_DYN_AIR_BRIDGE row dict."""
    fid = row.get('FLIGHT_ID')
    if fid is None:
        return None
    return {
        'source_flight_id': int(Decimal(str(fid))),
        'order_cd': _opt_int(row.get('ORDER_CD')) or 1,
        'air_bridge_id': clip(row.get('AIR_BRIDGE_ID'), 64),
        'air_bridge_cd': clip(row.get('AIR_BRIDGE_CD'), 32),
        'bridge_start_time': to_utc_dt(row.get('AIR_BRIDGE_START_TIME')),
        'bridge_end_time': to_utc_dt(row.get('AIR_BRIDGE_END_TIME')),
        'bridge_duration': _opt_float(row.get('AIR_BRIDGE_DURATION')),
        'power_start_time': to_utc_dt(row.get('POWER_SUPPLY_START_TIME')),
        'power_end_time': to_utc_dt(row.get('POWER_SUPPLY_END_TIME')),
        'ac_start_time': to_utc_dt(row.get('AIR_CONDITIONING_START_TIME')),
        'ac_end_time': to_utc_dt(row.get('AIR_CONDITIONING_END_TIME')),
        'source_updated_at': to_utc_dt(row.get('UPDATE_TIME')) or to_utc_dt(row.get('CREATE_TIME')),
    }


def map_safeguard_node_draft(row):
    """Map a FIMS.TB_DYN_SAFEGUARD_NODE row dict."""
    fid = row.get('FLIGHT_ID')
    code = clip(row.get('SAFEGUARD_CODE'), 16)
    if fid is None or not code:
        return None
    return {
        'source_flight_id': int(Decimal(str(fid))),
        'safeguard_order_cd': _opt_int(row.get('SAFEGUARD_ORDER_CD')) or 1,
        'safeguard_code': code,
        'ffid': clip(row.get('FFID'), 128),
        'flight_task_cd': clip(row.get('FLIGHT_TASK_CD'), 16),
        'safeguard_alterate_time': to_utc_dt(row.get('SAFEGUARD_ALTERATE_TIME')),
        'safeguard_real_time': to_utc_dt(row.get('SAFEGUARD_REAL_TIME')),
        'abnormal_start_time': to_utc_dt(row.get('ABNORMAL_START_TIME')),
        'abnormal_end_time': to_utc_dt(row.get('ABNORMAL_END_TIME')),
        'abnormal_reason': clip(row.get('ABNORMAL_REASON'), 256),
        'source_updated_at': to_utc_dt(row.get('UPDATE_TIME')) or to_utc_dt(row.get('CREATE_TIME')),
    }
