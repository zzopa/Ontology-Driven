"""Adaptive visualization metadata builder for business queries.

Generates structured chart and KPI specifications (visual_meta) for:
- stats: Big number KPI cards for totals, counts, averages
- donut: Proportional distribution rings for multi-category breakdowns
- bar: Ranked horizontal comparison bars for dimensions
- trend: Time-series sequences for monthly/quarterly/daily temporal groups
"""
from decimal import Decimal
import json
import re
from typing import Any, Dict, List, Optional, Tuple


TEMPORAL_KEYS: Tuple[str, ...] = (
    '__period', 'hour', 'hour_of_day', 'peak_hour',
    'movement_date', 'flight_date', 'exec_date', 'plan_day', 'real_exec_day', 'arr_landin_day',
    'date', 'day', 'month', 'week', 'quarter', 'year',
    'operation_plan_day', 'operation_exec_day',
)

TEMPORAL_DATE_FIELDS: Tuple[str, ...] = (
    'movement_date', 'flight_date', 'exec_date', 'plan_day', 'real_exec_day', 'arr_landin_day',
    'operation_plan_day', 'operation_exec_day', 'date', 'day',
)

NON_TEMPORAL_DIMENSION_KEYS: Tuple[str, ...] = (
    'airline_code', 'aircraft_type', 'agency', 'attribute', 'flight_task_cd',
    'terminal', 'craft_site_cd', 'stand_code', 'gate_cd', 'gate_code',
    'stand_type', 'delay_reason', 'abnormal_reason', 'origin_airport', 'dest_airport',
)


def parse_temporal_sort_key(raw_value: Any) -> Optional[Tuple[int, int, int, int, int, int, int]]:
    """Parse common date/datetime/month/week/quarter/hour labels into a chronologically sortable tuple."""
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, int):
        if 0 <= raw_value <= 23:
            return (8, 0, 0, 0, raw_value, 0, 0)
        if 1900 <= raw_value <= 2100:
            return (5, raw_value, 0, 0, 0, 0, 0)
        return None
    if isinstance(raw_value, float) and raw_value.is_integer():
        return parse_temporal_sort_key(int(raw_value))

    text = str(raw_value).strip()
    if not text or text in ('未标注', '—', '-'):
        return None

    # Full datetime: 2026-11-09 12:00(:00) / 2026-11-09T12:00:00+08:00 / 2026-11-09 12时
    m = re.match(
        r'^(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})[T\s]+(\d{1,2})(?::(\d{1,2})(?::(\d{1,2}))?|时|点)',
        text,
    )
    if m:
        return (0, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5) or 0), int(m.group(6) or 0))

    # Date: 2026-11-09 / 2026年11月9日
    m = re.match(r'^(\d{4})[-/\.年](\d{1,2})[-/\.月](\d{1,2})[日号]?$', text)
    if m:
        return (1, int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, 0, 0)

    # Year-Month: 2026-11 / 2026年11月
    m = re.match(r'^(\d{4})[-/\.年](\d{1,2})月?$', text)
    if m:
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            return (2, int(m.group(1)), mo, 0, 0, 0, 0)

    # Year-Week: 2026-W45 / 2026年第45周
    m = re.match(r'^(\d{4})(?:[-\s]?W|年第?)(\d{1,2})周?$', text, re.I)
    if m:
        return (3, int(m.group(1)), int(m.group(2)), 0, 0, 0, 0)

    # Year-Quarter: 2026-Q1 / 2026年第1季度
    m = re.match(r'^(\d{4})(?:[-\s]?Q|年第?)([1-4])(?:季度|季)?$', text, re.I)
    if m:
        return (4, int(m.group(1)), int(m.group(2)), 0, 0, 0, 0)

    # Year: 2026 / 2026年
    m = re.match(r'^(\d{4})年?$', text)
    if m:
        yr = int(m.group(1))
        if 1900 <= yr <= 2100:
            return (5, yr, 0, 0, 0, 0, 0)

    # Month-Day: 11-09 / 11月9日
    m = re.match(r'^(\d{1,2})[-/\.月](\d{1,2})[日号]?$', text)
    if m:
        mo, da = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= da <= 31:
            return (6, 0, mo, da, 0, 0, 0)

    # Month only: 11月
    m = re.match(r'^(\d{1,2})月$', text)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            return (7, 0, mo, 0, 0, 0, 0)

    # Time / Hour: 12:00 / 12:00:00 / 12:00-13:00 / 12时 / 12点 / pure 0..23
    m = re.match(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*[-~～]\s*\d{1,2}:\d{2})?$', text)
    if m:
        hr, mn, sc = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if 0 <= hr <= 23 and 0 <= mn <= 59:
            return (8, 0, 0, 0, hr, mn, sc)
    m = re.match(r'^(\d{1,2})\s*[时点](?:\s*[-~～]\s*\d{1,2}\s*[时点])?$', text)
    if m:
        hr = int(m.group(1))
        if 0 <= hr <= 23:
            return (8, 0, 0, 0, hr, 0, 0)
    if re.fullmatch(r'\d{1,2}', text):
        hr = int(text)
        if 0 <= hr <= 23:
            return (8, 0, 0, 0, hr, 0, 0)

    return None


PALETTE = [
    '#2563eb',  # Blue
    '#10b981',  # Emerald
    '#f59e0b',  # Amber
    '#8b5cf6',  # Violet
    '#ec4899',  # Pink
    '#06b6d4',  # Cyan
    '#f97316',  # Orange
    '#64748b',  # Slate
]


def safe_num(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(Decimal(str(val).replace(',', '')))
    except Exception:
        return 0.0


FLIGHT_STATUS_DICT: Dict[str, str] = {
    'DEP': '已起飞',
    'ARR': '已到达',
    'DLY': '延误',
    'CNL': '取消',
    'CAN': '取消',
    'CANCEL': '取消',
    'CANCELLED': '取消',
    'CANCELED': '取消',
    'SCH': '计划',
    'PLN': '计划',
    'SCHEDULED': '计划',
    'PLANNED': '计划',
    'BOR': '登机中',
    'BDG': '登机中',
    'BOARDING': '登机中',
    'LBD': '催促登机',
    'CKI': '值机中',
    'CHECKIN': '值机中',
    'POK': '下机中',
    'TXI': '滑行中',
    'TAXIING': '滑行中',
    'ONR': '飞行中',
    'ENROUTE': '飞行中',
    'ALT': '备降',
    'DIV': '备降',
    'DIVERTED': '备降',
    'RTN': '返航',
    'RET': '返航',
    'RETURNED': '返航',
    'POF': '客齐',
    'CLS': '关舱门',
    'RDY': '就绪',
}

_DEFAULT_AIRPORT_MAP: Dict[str, str] = {
    'WUH': '武汉', 'PKX': '北京大兴', 'PEK': '北京首都',
    'PVG': '上海浦东', 'SHA': '上海虹桥', 'CAN': '广州白云',
    'SZX': '深圳宝安', 'CTU': '成都双流', 'TFU': '成都天府',
    'CKG': '重庆江北', 'KMG': '昆明长水', 'XIY': '西安咸阳',
    'HGH': '杭州萧山', 'NKG': '南京禄口', 'CGO': '郑州新郑',
    'CSX': '长沙黄花', 'XMN': '厦门高崎', 'TAO': '青岛胶东',
    'DLC': '大连周水子', 'SHE': '沈阳桃仙', 'URC': '乌鲁木齐地窝堡',
    'HAK': '海口美兰', 'SYX': '三亚凤凰', 'TSN': '天津滨海',
    'FOC': '福州长乐', 'NNG': '南宁吴圩', 'KWE': '贵阳龙洞堡',
    'HRB': '哈尔滨太平', 'CGQ': '长春龙嘉', 'TYN': '太原武宿',
    'HET': '呼和浩特白塔', 'LHW': '兰州中川', 'INC': '银川河东',
    'XNN': '西宁曹家堡', 'LXA': '拉萨贡嘎', 'HKG': '香港国际',
    'MFM': '澳门国际', 'TPE': '台北桃园', 'KHH': '高雄国际',
    'WNZ': '温州龙湾', 'NGB': '宁波栎社', 'SJW': '石家庄正定',
    'JJN': '泉州晋江', 'ZUH': '珠海金湾', 'YNT': '烟台蓬莱',
    'KHN': '南昌昌北', 'XUZ': '徐州观音', 'LYA': '洛阳北郊',
    'LJG': '丽江三义', 'JHG': '西双版纳', 'DAT': '大同云冈',
    'YTY': '扬州泰州', 'NTG': '南通兴东', 'YNZ': '盐城南洋',
    'LYG': '连云港花果山', 'CZX': '常州奔牛', 'WUX': '无锡硕放',
    'HDG': '邯郸', 'YNJ': '延吉朝阳川', 'MDG': '牡丹江海浪',
    'JMU': '佳木斯', 'NDG': '齐齐哈尔', 'CIH': '长治王村',
    'YIC': '宜春明月山', 'JIU': '九江庐山', 'JDZ': '景德镇罗家',
    'GYS': '广元盘龙', 'MIG': '绵阳南郊', 'YBP': '宜宾五粮液',
    'LUZ': '泸州云龙', 'NAO': '南充高坪', 'DAX': '达州金垭',
    'BAX': '巴中恩阳', 'WXN': '万州五桥', 'LZO': '泸州',
    'ENH': '恩施许家坪', 'XFN': '襄阳刘集', 'YIH': '宜昌三峡',
    'SHS': '沙市', 'WDS': '十堰武当山', 'HPG': '神农架红坪',
}

_PREFERRED_AIRLINE_NAMES: Dict[str, str] = {
    'CZ': '南方航空', 'MU': '东方航空', 'CA': '中国国航',
    'HU': '海南航空', 'ZH': '深圳航空', '3U': '四川航空',
    'FM': '上海航空', 'MF': '厦门航空', 'SC': '山东航空',
    '9C': '春秋航空', 'HO': '吉祥航空', 'JD': '首都航空',
    'GS': '天津航空', '8L': '祥鹏航空', 'KN': '中国联合航空',
    'BK': '奥凯航空', 'EU': '成都航空', 'G5': '华夏航空',
    'GJ': '长龙航空', 'QW': '青岛航空', 'KY': '昆明航空',
    'DZ': '东海航空', 'RY': '江西航空', 'FU': '福州航空',
    'GX': '北部湾航空', 'DR': '瑞丽航空', 'GT': '桂林航空',
    'A6': '湖南航空', 'Y8': '金鹏航空', 'O3': '顺丰航空',
    'YG': '圆通航空', 'CF': '邮政航空', 'CX': '国泰航空',
    'KA': '国泰港龙航空', 'HX': '香港航空', 'NX': '澳门航空',
    'AK': '亚洲航空', 'AQ': '九元航空', '9H': '长安航空',
    'NS': '河北航空', 'TV': '西藏航空', 'JR': '幸福航空',
    'LT': '龙江航空', 'PN': '西部航空', 'UQ': '乌鲁木齐航空',
    'OQ': '重庆航空', 'GY': '多彩贵州航空',
}

_AIRPORT_MAP: Optional[Dict[str, str]] = None
_AIRLINE_MAP: Optional[Dict[str, str]] = None
_DELAY_REASON_MAP: Optional[Dict[str, str]] = None


def get_airport_name(code: Any) -> str:
    global _AIRPORT_MAP
    if not code:
        return ''
    raw = str(code).strip()
    if not raw:
        return ''
    c = raw.upper()
    if _AIRPORT_MAP is None:
        _AIRPORT_MAP = dict(_DEFAULT_AIRPORT_MAP)
        conn = None
        try:
            from app.core import connect_db
            conn = connect_db()
            if conn:
                cur = conn.cursor()
                cur.execute("SELECT iata_code, name_zh, short_name, city_name FROM dim_airport WHERE iata_code IS NOT NULL AND iata_code <> '' AND deleted_at IS NULL")
                for icode, name_zh, short_name, city_name in cur.fetchall():
                    ic = (icode or '').strip().upper()
                    if ic:
                        _AIRPORT_MAP[ic] = name_zh or city_name or short_name or ic
                cur.close()
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return _AIRPORT_MAP.get(c, raw)


def get_airline_name(code: Any) -> str:
    global _AIRLINE_MAP
    if not code:
        return ''
    raw = str(code).strip()
    if not raw:
        return ''
    c = raw.upper()
    if _AIRLINE_MAP is None:
        _AIRLINE_MAP = dict(_PREFERRED_AIRLINE_NAMES)
        conn = None
        try:
            from app.core import connect_db
            conn = connect_db()
            if conn:
                cur = conn.cursor()
                cur.execute("SELECT iata_code, name_zh FROM dim_airline WHERE iata_code IS NOT NULL AND iata_code <> '' AND deleted_at IS NULL")
                for icode, name_zh in cur.fetchall():
                    ic = (icode or '').strip().upper()
                    if ic and name_zh:
                        if ic not in _PREFERRED_AIRLINE_NAMES:
                            _AIRLINE_MAP[ic] = name_zh.strip()
                cur.close()
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return _AIRLINE_MAP.get(c, raw)


def get_delay_reason_name(code: Any) -> str:
    global _DELAY_REASON_MAP
    if not code:
        return ''
    raw = str(code).strip()
    if not raw:
        return ''
    if _DELAY_REASON_MAP is None:
        _DELAY_REASON_MAP = {}
        conn = None
        try:
            from app.core import connect_db
            conn = connect_db()
            if conn:
                cur = conn.cursor()
                cur.execute("SELECT code, name_zh FROM dim_abnormal_reason WHERE code IS NOT NULL AND deleted_at IS NULL")
                for c, name in cur.fetchall():
                    ck = (c or '').strip()
                    if ck and name:
                        _DELAY_REASON_MAP[ck] = name.strip()
                cur.close()
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return _DELAY_REASON_MAP.get(raw, raw)


def format_flight_status(raw_status: Any, act_dt: Any = None, direction: str = 'DEP') -> str:
    s = str(raw_status or '').strip()
    if not s:
        if act_dt:
            return '已起飞' if direction == 'DEP' else '已到达'
        return '计划'
    u = s.upper()
    if u in FLIGHT_STATUS_DICT:
        if u == 'DEP':
            return '已起飞' if act_dt else '出港'
        if u == 'ARR':
            return '已到达' if act_dt else '进港'
        return FLIGHT_STATUS_DICT[u]
    return s


def _first_val(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        val = row.get(key)
        if val not in (None, ''):
            return val
    return None


def _build_detail_table_meta(
    plan: Dict[str, Any],
    main_rows: List[Dict[str, Any]],
    main_total: int,
    subject: str,
    subject_name: str,
    catalog: Any,
    *,
    is_flight_row: Any,
    parse_dt: Any,
    format_node_time: Any,
    resolve_direction: Any,
    safe_str: Any,
) -> Dict[str, Any]:
    is_flight = any(is_flight_row(r) for r in main_rows[:3])
    if is_flight:
        rows: List[Dict[str, Any]] = []
        dep_count = 0
        arr_count = 0
        delayed_count = 0
        ontime_count = 0
        for idx, r in enumerate(main_rows):
            direction = resolve_direction(r)
            if direction == 'DEP':
                dep_count += 1
            elif direction == 'ARR':
                arr_count += 1
            airline_cd = safe_str(_first_val(r, 'AIRLINE_TWO_CHAR_CD', 'airline_code'))
            airline_name = get_airline_name(airline_cd) or airline_cd
            raw_fno = safe_str(_first_val(r, 'FLIGHT_KEY', 'flight_no', 'FLIGHT_NO'))
            if airline_cd and raw_fno and not raw_fno.startswith(airline_cd) and raw_fno.isdigit():
                flight_no = f'{airline_cd}{raw_fno}'
            else:
                flight_no = raw_fno or f'航班 {idx + 1}'
            flight_date = safe_str(_first_val(r, 'flight_date', 'EXEC_DATE'))[:10]
            origin_raw = safe_str(_first_val(r, 'origin_city', 'origin_airport', 'TAKEOFF_AIRPORT_IATA'))
            dest_raw = safe_str(_first_val(r, 'dest_city', 'dest_airport', 'ARRIVAL_AIRPORT_IATA'))
            origin = get_airport_name(origin_raw) or origin_raw
            dest = get_airport_name(dest_raw) or dest_raw
            route = f'{origin} → {dest}' if origin and dest else (origin or dest or '—')

            if direction == 'ARR':
                plan_raw = _first_val(r, 'scheduled_arrival', 'SCHEME_LANDIN_TIME')
                act_raw = _first_val(r, 'actual_arrival', 'REAL_LANDIN_TIME', 'execution_time')
            else:
                plan_raw = _first_val(r, 'scheduled_departure', 'SCHEME_TAKEOFF_TIME')
                act_raw = _first_val(r, 'actual_departure', 'REAL_TAKEOFF_TIME', 'execution_time')

            plan_dt = parse_dt(plan_raw)
            act_dt = parse_dt(act_raw)
            delay_min: Optional[int] = None
            if plan_dt and act_dt:
                delay_min = int((act_dt - plan_dt).total_seconds() // 60)
                if delay_min > 15:
                    delayed_count += 1
                else:
                    ontime_count += 1

            stand = safe_str(_first_val(r, 'stand_code', 'CRAFT_SITE_CD'))
            gate = safe_str(_first_val(r, 'gate_code', 'GATE_CD'))
            terminal = safe_str(_first_val(r, 'terminal', 'TERMINAL'))
            craft_type = safe_str(_first_val(r, 'aircraft_type', 'CRAFT_TYPE_ICAO_CD'))
            craft_no = safe_str(_first_val(r, 'aircraft_reg', 'CRAFT_NO'))
            raw_status = safe_str(_first_val(r, 'flight_status', 'operation_status'))
            status = format_flight_status(raw_status, act_dt=act_dt, direction=direction)
            raw_delay_reason = safe_str(_first_val(r, 'delay_reason_text', 'delay_reason', 'abnormal_reason'))
            delay_reason = get_delay_reason_name(raw_delay_reason) or raw_delay_reason

            # 客运、货运、航线属性扩展
            raw_attr = safe_str(_first_val(r, 'attribute', 'flight_task_cd'))
            raw_country = safe_str(_first_val(r, 'flight_country_type', 'route_type'))
            attr_display = ' / '.join(filter(None, [raw_attr, raw_country]))
            pax_val = _first_val(r, 'dep_dpsr_depe_num', 'psg_arrival_num', 'passenger_volume', 'adult_count')
            pax_num = str(int(pax_val)) if pax_val not in (None, '') and str(pax_val).replace('.', '', 1).isdigit() else None
            cargo_val = _first_val(r, 'fpa_goods', 'cargo_weight')
            cargo_num = str(round(float(cargo_val), 1)) if cargo_val not in (None, '') and str(cargo_val).replace('.', '', 1).isdigit() else None
            mail_val = _first_val(r, 'fpa_mail', 'mail_weight')
            mail_num = str(round(float(mail_val), 1)) if mail_val not in (None, '') and str(mail_val).replace('.', '', 1).isdigit() else None
            seat_val = _first_val(r, 'fpa_available_seat', 'available_seat', 'seat_capacity')
            seat_num = str(int(seat_val)) if seat_val not in (None, '') and str(seat_val).replace('.', '', 1).isdigit() else None

            if status == '延误' and (delay_min is None or delay_min <= 15):
                delayed_count += 1

            rows.append({
                'flight_no': flight_no,
                'airline': airline_cd,
                'airline_name': airline_name,
                'direction': direction,
                'direction_label': '出港' if direction == 'DEP' else '进港',
                'flight_date': flight_date,
                'origin': origin,
                'origin_code': origin_raw,
                'destination': dest,
                'destination_code': dest_raw,
                'route': route,
                'attribute': raw_attr,
                'flight_country_type': raw_country,
                'attribute_display': attr_display,
                'passenger_count': pax_num,
                'seat_count': seat_num,
                'cargo_weight': cargo_num,
                'mail_weight': mail_num,
                'plan_time': format_node_time(plan_dt or plan_raw, flight_date) or '—',
                'actual_time': format_node_time(act_dt or act_raw, flight_date) or '—',
                'stand': stand,
                'gate': gate,
                'terminal': terminal,
                'aircraft_type': craft_type,
                'aircraft_reg': craft_no,
                'status': status,
                'delay_minutes': delay_min,
                'delay_reason': delay_reason,
                'drill_question': f'{flight_date} {flight_no} 时序图'.strip() if raw_fno else '',
            })

        hourly_counts: Dict[int, int] = {}
        for r in rows:
            act_t = str(r.get('actual_time') or '').strip()
            plan_t = str(r.get('plan_time') or '').strip()
            t_str = act_t if (act_t and act_t != '—') else (plan_t if (plan_t and plan_t != '—') else '')
            if ':' in t_str:
                try:
                    time_part = t_str.split(' ')[-1]
                    h = int(time_part.split(':')[0])
                    if 0 <= h <= 23:
                        hourly_counts[h] = hourly_counts.get(h, 0) + 1
                except Exception:
                    pass
        hourly_distribution = [
            {'hour': h, 'label': f'{h:02d}时', 'count': hourly_counts.get(h, 0)}
            for h in range(24)
        ] if hourly_counts else []

        planned_count = sum(1 for r in rows if r.get('status') == '计划' or not r.get('actual_time') or r.get('actual_time') == '—')

        cols = [
            {'key': 'flight_no', 'label': '航班号'},
            {'key': 'route', 'label': '航线'},
        ]
        if any(r.get('attribute_display') for r in rows):
            cols.append({'key': 'attribute_display', 'label': '属性/性质'})
        if any(r.get('passenger_count') for r in rows):
            cols.append({'key': 'passenger_count', 'label': '旅客数(人)'})
        if any(r.get('seat_count') for r in rows):
            cols.append({'key': 'seat_count', 'label': '可用座位'})
        if any(r.get('cargo_weight') for r in rows):
            cols.append({'key': 'cargo_weight', 'label': '货运重量(kg)'})
        if any(r.get('mail_weight') for r in rows):
            cols.append({'key': 'mail_weight', 'label': '邮件重量(kg)'})

        cols.extend([
            {'key': 'plan_time', 'label': '计划时刻'},
            {'key': 'actual_time', 'label': '实际时刻'},
            {'key': 'stand', 'label': '机位 / 登机口'},
            {'key': 'aircraft', 'label': '机型 / 机号'},
            {'key': 'status', 'label': '状态'},
        ])

        return {
            'kind': 'detail_table',
            'is_flight': True,
            'title': f'{subject_name}明细清单',
            'total': main_total,
            'shown': len(rows),
            'summary': {
                'dep_count': dep_count,
                'arr_count': arr_count,
                'ontime_count': ontime_count,
                'delayed_count': delayed_count,
                'planned_count': planned_count,
                'hourly_distribution': hourly_distribution,
            },
            'columns': cols,
            'rows': rows,
        }

    # Generic non-flight detail table
    spec = catalog.entities.get(subject, {}) if catalog and hasattr(catalog, 'entities') else {}
    attrs = spec.get('attributes', {})
    identity_fields = list(spec.get('identity_fields') or [])
    sample_keys = list(dict.fromkeys([*identity_fields, *main_rows[0].keys()]))
    skip_cols = {
        'id', 'deleted_at', 'row_version', 'ext_json', 'raw_json', 'created_at', 'updated_at',
        'biz_key', 'link_id', 'record_key', 'source_system', 'primary_source_id',
        'pg_flight_id', 'associated_flight_id', 'source_flight_id', 'source_ffid',
        'sync_run_id', 'sync_checkpoint', 'sync_error'
    }
    picked: List[str] = []
    for key in sample_keys:
        if key in skip_cols or key.endswith('_json'):
            continue
        if any(r.get(key) not in (None, '') and not isinstance(r.get(key), (dict, list)) for r in main_rows):
            picked.append(key)
        if len(picked) >= 7:
            break
    if not picked:
        picked = [k for k in main_rows[0].keys() if not isinstance(main_rows[0].get(k), (dict, list))][:7]

    columns = [
        {
            'key': key,
            'label': attrs.get(key, {}).get('label') or key,
            'unit': attrs.get(key, {}).get('unit') or '',
        }
        for key in picked
    ]
    rows = [
        {col['key']: r.get(col['key']) for col in columns}
        for r in main_rows
    ]
    return {
        'kind': 'detail_table',
        'is_flight': False,
        'title': f'{subject_name}明细清单',
        'total': main_total,
        'shown': len(rows),
        'columns': columns,
        'rows': rows,
    }


def build_visual_meta(
    plan: Dict[str, Any],
    main_rows: List[Dict[str, Any]],
    main_total: int,
    metric: Optional[Dict[str, Any]] = None,
    catalog: Any = None,
    related: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Inspects query plan and results to build structured visual metadata."""
    if not main_rows or main_total == 0:
        return {'kind': 'none'}

    intent = plan.get('intent', 'detail')
    subject = plan.get('subject', '')
    target = plan.get('aggregate_target') or subject
    subject_name = catalog.entities[subject]['name'] if catalog and subject in catalog.entities else subject

    # Case -1: ACDM Turnaround Gantt
    if plan.get('intent') == 'turnaround' or plan.get('turnaround_data'):
        turnaround_data = plan.get('turnaround_data')
        if not turnaround_data:
            from app.turnaround import get_turnaround_data
            fno = plan.get('query_identifier') or plan.get('flight_no')
            turnaround_data = get_turnaround_data(fno, plan.get('flight_date')) if fno else None
        if turnaround_data:
            return {'kind': 'turnaround_gantt', **{key: value for key, value in turnaround_data.items()
                    if key not in ('source_row', 'process_row', 'acdm_rows')}}

    # Case 0: Single-Flight Milestone Fishbone Diagram
    from app.agent.fishbone import is_flight_row, build_fishbone_meta, parse_dt, format_node_time, resolve_direction, safe_str
    if intent == 'detail' and main_total == 1 and len(main_rows) == 1 and is_flight_row(main_rows[0]):
        r = main_rows[0]
        node_row = r
        if subject.split('.')[-1] in ('std_flight_core', 'flight_execution') and r.get('id') is not None:
            for item in related or []:
                if item.get('table', '').split('.')[-1] != 'std_flight_process':
                    continue
                for process in item.get('rows', []):
                    if str(process.get('flight_id')) != str(r['id']):
                        continue
                    ext = process.get('ext_json') or {}
                    if isinstance(ext, str):
                        try:
                            ext = json.loads(ext)
                        except ValueError:
                            ext = {}
                    node_row = {**(ext if isinstance(ext, dict) else {}), **r, **process}
        fishbone = build_fishbone_meta(node_row, plan=plan, total_flights=main_total)
        if fishbone.get('nodes'):
            return fishbone

    # Case 0.5: Structured Detail Table (Flight List & Generic Detail Queries)
    if intent == 'detail' and main_rows:
        return _build_detail_table_meta(
            plan, main_rows, main_total, subject, subject_name, catalog,
            is_flight_row=is_flight_row, parse_dt=parse_dt,
            format_node_time=format_node_time, resolve_direction=resolve_direction, safe_str=safe_str,
        )

    # Case 1: Temporal Trend (e.g. __period in rows, hour, or date)
    val_alias = (metric or {}).get('value_alias') or (metric or {}).get('field') or ('total' if intent in ('sum', 'metric') else 'n')
    is_temporal = any(k in r for r in main_rows for k in TEMPORAL_KEYS) and not any(k in r for r in main_rows for k in NON_TEMPORAL_DIMENSION_KEYS)
    if intent in ('count', 'sum', 'metric') and len(main_rows) > 1 and is_temporal:
        series_with_keys: List[Tuple[Tuple[Any, ...], Dict[str, Any]]] = []
        unit = (metric or {}).get('unit', '架次' if intent in ('count', 'metric') else '')
        pk_dates = {str(r['peak_hour'])[:10] for r in main_rows if r.get('peak_hour') is not None and len(str(r['peak_hour'])) >= 10}
        multi_day_pk = len(pk_dates) > 1
        for idx, r in enumerate(main_rows):
            row_date = next((str(r.get(k))[:10] for k in TEMPORAL_DATE_FIELDS if r.get(k) is not None), None)
            raw_time_key: Any = None
            if r.get('__period') is not None:
                period_label = str(r['__period'])
                raw_time_key = period_label
            elif r.get('peak_hour') is not None:
                pk_str = str(r['peak_hour'])
                raw_time_key = pk_str
                if len(pk_str) >= 16 and ('T' in pk_str or ' ' in pk_str):
                    period_label = pk_str[:16].replace('T', ' ') if multi_day_pk else pk_str[11:16]
                else:
                    period_label = pk_str
            elif r.get('hour') is not None:
                hr_int = int(r['hour'])
                period_label = f"{row_date} {hr_int:02d}时" if row_date else f"{hr_int:02d}时"
                raw_time_key = f"{row_date} {hr_int:02d}:00" if row_date else f"{hr_int:02d}:00"
            elif r.get('hour_of_day') is not None:
                hr_int = int(r['hour_of_day'])
                period_label = f"{row_date} {hr_int:02d}时" if row_date else f"{hr_int:02d}时"
                raw_time_key = f"{row_date} {hr_int:02d}:00" if row_date else f"{hr_int:02d}:00"
            else:
                first_t = next((r.get(k) for k in (*TEMPORAL_DATE_FIELDS, 'month', 'week', 'quarter', 'year') if r.get(k) is not None), None)
                raw_time_key = str(first_t) if first_t is not None else None
                period_label = str(first_t)[:10] if first_t is not None else '未标注'
            val = safe_num(r.get(val_alias, r.get('total', r.get('n', 0))))
            sort_tuple = parse_temporal_sort_key(raw_time_key) or parse_temporal_sort_key(period_label)
            point: Dict[str, Any] = {'label': period_label, 'value': val}
            if raw_time_key is not None and str(raw_time_key) != period_label:
                point['time_key'] = str(raw_time_key)
            order_key = (0, sort_tuple, idx) if sort_tuple is not None else (1, (), idx)
            series_with_keys.append((order_key, point))
        series_with_keys.sort(key=lambda pair: pair[0])
        series = [point for _, point in series_with_keys]
        return {
            'kind': 'trend',
            'title': f"{(metric or {}).get('name') or subject_name}分布趋势",
            'series': series,
            'unit': unit,
            'metric_label': (metric or {}).get('name', '架次' if intent == 'count' else '统计值')
        }

    # Case 2: Multi-row Grouped Distribution (Donut / Bar)
    if intent in ('count', 'sum', 'metric') and len(main_rows) > 1:
        # Determine group key
        sample = main_rows[0]
        group_keys = [k for k in sample.keys() if k not in ('total', 'mx', 'n', 'missing', 'invalid', '__period', val_alias)]
        group_key = group_keys[0] if group_keys else None
        
        attr_label = '分组'
        if group_key and hasattr(catalog, 'dimensions') and group_key in catalog.dimensions:
            attr_label = catalog.dimensions[group_key].get('name') or group_key
        elif group_key and catalog and target in catalog.entities:
            attr = catalog.entities[target].get('attributes', {}).get(group_key, {})
            attr_label = attr.get('label') or group_key

        if group_key in ('airline', 'airline_code', 'airline_two_char_cd', 'AIRLINE_TWO_CHAR_CD') or attr_label in ('航司代码', '航司', '航空公司'):
            attr_label = '航司'

        total_sum = sum(safe_num(r.get(val_alias, r.get('total', r.get('n', 0)))) for r in main_rows)
        unit = (metric or {}).get('unit', '架次' if intent in ('count', 'metric') else '')
        agg_kind = str((metric or {}).get('aggregation') or '').upper()
        is_rate_or_avg = (unit == '%') or agg_kind in ('AVG', 'RATE', 'MAX', 'MIN')
        is_ranking = bool(plan.get('limit') or plan.get('sort_dir'))

        # Build items
        items = []
        for i, r in enumerate(main_rows):
            val = safe_num(r.get(val_alias, r.get('total', r.get('n', 0))))
            pct = 0.0 if is_rate_or_avg else (round((val / total_sum * 100), 1) if total_sum > 0 else 0.0)
            raw_group_val = r.get(group_key)
            if group_key in ('airline', 'airline_code', 'airline_two_char_cd', 'AIRLINE_TWO_CHAR_CD') or attr_label == '航司':
                al_name = get_airline_name(raw_group_val)
                if al_name and al_name != str(raw_group_val):
                    label_val = f"{al_name}（{raw_group_val}）"
                else:
                    label_val = str(raw_group_val or '未标注')
            elif group_key in ('dest_city', 'origin_city', 'dest_airport', 'origin_airport', 'airport', 'airport_code', 'takeoff_airport_iata', 'arrival_airport_iata', 'TAKEOFF_AIRPORT_IATA', 'ARRIVAL_AIRPORT_IATA') or attr_label in ('机场代码', '起飞机场', '到达机场', '目的地'):
                ap_name = get_airport_name(raw_group_val)
                if ap_name and ap_name != str(raw_group_val):
                    label_val = f"{ap_name}（{raw_group_val}）"
                else:
                    label_val = str(raw_group_val or '未标注')
            elif group_key in ('delay_reason', 'abnormal_reason', 'abnormal_reason_cd') or attr_label in ('延误原因', '不正常原因', '异常原因'):
                reason_name = str(r.get('abnormal_reason') or '').strip()
                if not reason_name or reason_name in ('正常/未标', '未标注'):
                    reason_name = get_delay_reason_name(raw_group_val)
                if reason_name and reason_name != str(raw_group_val) and reason_name not in ('正常/未标', '未标注'):
                    label_val = f"{reason_name}（{raw_group_val}）" if raw_group_val and raw_group_val != reason_name else reason_name
                elif raw_group_val is None or str(raw_group_val).strip() in ('', 'None'):
                    label_val = '未标注'
                else:
                    label_val = str(raw_group_val)
            else:
                label_val = str(raw_group_val or '未标注') if group_key else f'项目 {i+1}'
            items.append({
                'label': label_val,
                'value': val,
                'percent': pct,
                'color': PALETTE[i % len(PALETTE)],
                'count': int(r.get('n', 0))
            })

        temporal_parsed = [parse_temporal_sort_key(item['label']) for item in items]
        is_temporal_items = (
            (group_key in TEMPORAL_KEYS)
            or (
                sum(1 for k in temporal_parsed if k is not None) >= 2
                and all(k is not None or item['label'] == '未标注' for k, item in zip(temporal_parsed, items))
            )
        )
        if is_temporal_items:
            items = [
                item
                for _, item in sorted(
                    enumerate(items),
                    key=lambda pair: (
                        (0, temporal_parsed[pair[0]], pair[0])
                        if temporal_parsed[pair[0]] is not None
                        else (1, (), pair[0])
                    ),
                )
            ]
        else:
            # Sort descending by value
            items.sort(key=lambda x: -x['value'])

        if 2 <= len(items) <= 6 and not is_rate_or_avg and not is_ranking:
            return {
                'kind': 'donut',
                'title': f'按{attr_label}占比分布',
                'slices': items,
                'total': round(total_sum, 2),
                'unit': unit,
                'group_label': attr_label
            }
        else:
            return {
                'kind': 'bar',
                'title': f'按{attr_label}排名统计',
                'bars': items[:15],
                'unit': unit,
                'group_label': attr_label,
                'is_rate': unit == '%',
            }

    # Case 3: Single Aggregate KPI Stats
    if intent in ('count', 'sum', 'metric') and len(main_rows) == 1:
        row = main_rows[0]
        stats = []
        if plan.get('group_by'):
            ignore_k = {'total', 'mx', 'n', 'missing', 'invalid', val_alias, '__num', '__den',
                        (metric or {}).get('value_alias'), (metric or {}).get('field'),
                        (metric or {}).get('code'), (metric or {}).get('key')}
            for gk, gv in row.items():
                if gk in ignore_k or (isinstance(gk, str) and gk.startswith('__')):
                    continue
                if gv is None:
                    continue
                if hasattr(catalog, 'dimensions') and gk in catalog.dimensions:
                    glbl = catalog.dimensions[gk].get('name') or gk
                elif catalog and target in catalog.entities and gk in catalog.entities[target].get('attributes', {}):
                    glbl = catalog.entities[target]['attributes'][gk].get('label') or gk
                else:
                    glbl = gk
                stats.append({'label': glbl, 'value': str(gv), 'unit': ''})
        if intent == 'count':
            count_unit = '班' if ('航班' in subject_name or 'flight' in target.lower()) else '条'
            stats.append({
                'label': f'{subject_name}总数',
                'value': str(row.get('n', main_total)),
                'unit': count_unit
            })
        elif intent == 'metric':
            unit = (metric or {}).get('unit', '')
            metric_name = (metric or {}).get('name', '统计指标')
            if val_alias in row:
                raw_val = row[val_alias]
            else:
                raw_val = None
                for cand in ((metric or {}).get('code'), 'total'):
                    if cand and cand in row:
                        raw_val = row[cand]
                        break
            stats.append({
                'label': metric_name,
                'value': str(raw_val) if raw_val is not None else '无有效样本',
                'unit': unit if raw_val is not None else ''
            })
            if row.get('n') is not None:
                stats.append({
                    'label': '匹配记录数' if raw_val is not None else '扫描记录数',
                    'value': str(row['n']),
                    'unit': '条'
                })
        else:
            tot = row.get('total')
            unit = (metric or {}).get('unit', '元')
            metric_name = (metric or {}).get('name', '总金额')
            stats.append({
                'label': metric_name,
                'value': str(tot) if tot is not None else '无有效样本',
                'unit': unit if tot is not None else ''
            })
            stats.append({
                'label': '参与记录数',
                'value': str(row.get('n', main_total)),
                'unit': '笔'
            })
            if row.get('mx') is not None:
                stats.append({
                    'label': '单笔最大',
                    'value': str(row.get('mx')),
                    'unit': unit
                })
        return {
            'kind': 'stats',
            'title': f'{(metric or {}).get("name") or subject_name}统计概览',
            'stats': stats
        }

    return {'kind': 'none'}
