"""A-CDM calibrated reports and duty briefings strictly grounded in ops_ask PostgreSQL data."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import re
from typing import Any

SHANGHAI_TZ = timezone(timedelta(hours=8))
GROUND_BRIEFING_CUTOFF = time(22)
KEY_TURNAROUND_TYPES = ('B738', 'B788', 'A320', 'A321', 'A320/1', 'C919')
REGIONAL_AIRPORT_NAMES = {
    'YIH': '宜昌三峡',
    'XFN': '襄阳刘集',
    'WDS': '十堰武当山',
    'ENH': '恩施许家坪',
    'SHS': '荆州沙市',
    'EHU': '鄂州花湖',
    'HPG': '神农架红坪',
}


def _pct(num: Any, den: Any) -> float | None:
    n = float(num or 0)
    d = float(den or 0)
    if d <= 0:
        return None
    return round(100.0 * n / d, 2)


def _num(val: Any) -> float | int | None:
    if val is None:
        return None
    if isinstance(val, Decimal):
        f = float(val)
        return int(f) if f.is_integer() else round(f, 2)
    return val


def _q_dicts(cur, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    rows = []
    for rec in cur.fetchall():
        row = {}
        for k, v in zip(cols, rec):
            if isinstance(v, Decimal):
                f = float(v)
                row[k] = int(f) if f.is_integer() else round(f, 2)
            elif hasattr(v, 'isoformat'):
                row[k] = v.isoformat()
            else:
                row[k] = v
        rows.append(row)
    return rows


def _q_one(cur, sql: str, params: tuple = ()) -> dict[str, Any]:
    rows = _q_dicts(cur, sql, params)
    return rows[0] if rows else {}


def _coverage_bounds(cur) -> dict[str, Any]:
    row = _q_one(
        cur,
        'SELECT MIN(exec_date)::text AS start_date, MAX(exec_date)::text AS end_date, COUNT(*) AS total '
        'FROM public.mv_acdm_dyn_flight_detail',
    )
    return row or {'start_date': None, 'end_date': None, 'total': 0}


def build_general_report(
    conn,
    *,
    start_date: date,
    end_date: date,
    period_type: str = 'day',
) -> dict[str, Any]:
    """Generate the calibrated 8-section A-CDM General Report (Handbook Ch.7.1)."""
    if start_date > end_date:
        raise ValueError('开始日期不能晚于结束日期')
    span_days = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span_days - 1)
    yoy_start = start_date.replace(year=start_date.year - 1) if not (start_date.month == 2 and start_date.day == 29) else start_date - timedelta(days=365)
    yoy_end = yoy_start + timedelta(days=span_days - 1)

    cur = conn.cursor()
    try:
        cov = _coverage_bounds(cur)
        cov_s = cov.get('start_date')
        cov_e = cov.get('end_date')

        def fetch_span_kpis(d_from: date, d_to: date) -> dict[str, Any]:
            # Section 1 & 2 core metrics from mv_acdm_dyn_flight_detail
            summary = _q_one(cur, """
                SELECT
                    COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND plan_day BETWEEN %s AND %s) AS planned_all,
                    COUNT(*) FILTER (WHERE flight_task_cd = 'W/Z' AND attribute = '2403' AND plan_day BETWEEN %s AND %s) AS planned_dom_pax,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s) AS operated_all,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND flight_task_cd = 'W/Z' AND attribute = '2403' AND real_exec_day BETWEEN %s AND %s) AS operated_dom_pax,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s) AS dep_operated,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'A' AND real_landin_day BETWEEN %s AND %s) AS arr_operated,
                    COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND abnormal_flight_stat_cd = 'CAN' AND plan_day BETWEEN %s AND %s) AS cancel_all,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND takeoff_on_time = 'Y') AS takeoff_normal_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND agency = '210' AND attribute = '2403') AS ground_dep_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND agency = '210' AND attribute = '2403' AND takeoff_on_time = 'Y') AS ground_takeoff_normal_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND release_on_time = 'Y') AS release_normal_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND ori_act = 'Y') AS origin_dep_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND ori_act = 'Y' AND flight_first_on_time = 'Y') AS origin_normal_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'A' AND real_landin_day BETWEEN %s AND %s AND land_on_time = 'Y') AS arr_normal_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'A' AND real_landin_day BETWEEN %s AND %s AND craft_site_is_remote = 'N') AS arr_bridge_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s AND craft_site_is_remote = 'N') AS all_bridge_cnt,
                    COALESCE(SUM(CASE WHEN is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s THEN COALESCE(fpa_goods, 0) + COALESCE(fpa_mail, 0) ELSE 0 END), 0) AS cargo_mail_kg,
                    COALESCE(SUM(CASE WHEN is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s AND is_off_in = 'D' THEN COALESCE(dep_dpsr_depe_num, 0) ELSE 0 END), 0) AS dep_pax_calc,
                    COALESCE(SUM(CASE WHEN is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s AND is_off_in = 'A' THEN COALESCE(fpa_adult_count, 0) + COALESCE(fpa_children_count, 0) + COALESCE(fpa_baby_count, 0) ELSE 0 END), 0) AS arr_pax_fpa,
                    COALESCE(SUM(CASE WHEN is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s AND COALESCE(fpa_available_seat, 0) > 0 THEN fpa_available_seat ELSE 0 END), 0) AS total_seats,
                    COALESCE(SUM(CASE WHEN is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s AND COALESCE(fpa_available_seat, 0)>0
                        THEN CASE WHEN is_off_in='D' THEN COALESCE(dep_dpsr_depe_num,0)
                            ELSE COALESCE(fpa_adult_count,0)+COALESCE(fpa_children_count,0)+COALESCE(fpa_baby_count,0) END ELSE 0 END),0) AS load_factor_pax,
                    COUNT(*) FILTER(WHERE is_operated_flight='Y' AND real_exec_day BETWEEN %s AND %s AND COALESCE(fpa_available_seat,0)>0) AS load_factor_sample_flights,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND real_block_off_time IS NOT NULL AND targt_block_off_time IS NOT NULL) AS block_off_sample,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND real_block_off_time <= targt_block_off_time) AS block_off_ontime,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND real_close_cabin_time IS NOT NULL) AS close_cabin_real_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND COALESCE(clearance_delay_time, 0) > 0) AS release_delay_cnt,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND clearance_delay_time BETWEEN 1 AND 5) AS short_delay_5m_cnt,
                    ROUND(AVG(clearance_delay_time) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND clearance_delay_time > 0)::numeric, 2) AS avg_release_delay_min,
                    ROUND(AVG(takeoff_delay_time) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND takeoff_delay_time > 0)::numeric, 2) AS avg_takeoff_delay_min,
                    ROUND(AVG(arr_delay_time) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'A' AND real_landin_day BETWEEN %s AND %s AND arr_delay_time > 0)::numeric, 2) AS avg_arr_delay_min,
                    ROUND(AVG(real_taxi_out_time) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_day BETWEEN %s AND %s AND real_taxi_out_time IS NOT NULL)::numeric, 2) AS avg_taxi_out_min,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'A' AND real_landin_day BETWEEN %s AND %s AND real_taxi_in_time IS NOT NULL) AS taxi_in_samples
                FROM public.mv_acdm_dyn_flight_detail
            """, (d_from, d_to) * 32)

            summary['execution_rate'] = _pct(summary.get('operated_all'), summary.get('planned_all'))
            summary['takeoff_on_time_rate'] = _pct(summary.get('takeoff_normal_cnt'), summary.get('dep_operated'))
            summary['ground_takeoff_on_time_rate'] = _pct(summary.get('ground_takeoff_normal_cnt'), summary.get('ground_dep_cnt'))
            summary['release_on_time_rate'] = _pct(summary.get('release_normal_cnt'), summary.get('dep_operated'))
            summary['origin_dep_on_time_rate'] = _pct(summary.get('origin_normal_cnt'), summary.get('origin_dep_cnt'))
            summary['arr_on_time_rate'] = _pct(summary.get('arr_normal_cnt'), summary.get('arr_operated'))
            summary['bridge_rate_arr'] = _pct(summary.get('arr_bridge_cnt'), summary.get('arr_operated'))
            summary['bridge_rate_all'] = _pct(summary.get('all_bridge_cnt'), summary.get('operated_all'))
            summary['total_pax_sql'] = int(summary.get('dep_pax_calc') or 0) + int(summary.get('arr_pax_fpa') or 0)
            summary['total_load_factor'] = _pct(summary.get('load_factor_pax'), summary.get('total_seats'))
            summary['load_factor_excluded_pax'] = summary['total_pax_sql'] - int(summary.get('load_factor_pax') or 0)
            summary['block_off_on_time_rate'] = _pct(summary.get('block_off_ontime'), summary.get('dep_operated'))
            if summary.get('dep_operated'):
                improved_release = int(summary.get('release_normal_cnt') or 0) + int(summary.get('short_delay_5m_cnt') or 0)
                base_r = summary['release_on_time_rate'] or 0.0
                up_r = round(100.0 * improved_release / float(summary['dep_operated']), 2)
                summary['short_delay_lift_pct'] = round(up_r - base_r, 2)
            else:
                summary['short_delay_lift_pct'] = None

            pair_stats = _q_one(cur, """
                SELECT
                    COUNT(*) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s AND same_exec_date) AS pair_same_day_cnt,
                    COUNT(*) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s AND same_exec_date AND pair_release_on_time = 'Y') AS pair_release_normal_cnt,
                    COUNT(*) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s) AS turnaround_valid_cnt,
                    ROUND(AVG(service_minutes) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s)::numeric, 2) AS avg_service_min,
                    ROUND(AVG(bridge_minutes) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s)::numeric, 2) AS avg_bridge_min,
                    ROUND(AVG(departure_minutes) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s)::numeric, 2) AS avg_departure_min,
                    ROUND(AVG(board_minutes) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s)::numeric, 2) AS avg_board_min,
                    ROUND(AVG(clean_minutes) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s)::numeric, 2) AS avg_clean_min,
                    ROUND(AVG(meal_minutes) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s)::numeric, 2) AS avg_meal_min,
                    ROUND(AVG(supply_minutes) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s)::numeric, 2) AS avg_supply_min,
                    ROUND(AVG(deicie_minutes) FILTER (WHERE is_both_operated = 'Y' AND valid_block_sequence AND arr_landin_day BETWEEN %s AND %s)::numeric, 2) AS avg_deicie_min,
                    COUNT(*) FILTER (WHERE is_key_turnaround_aircraft AND arr_exec_date BETWEEN %s AND %s AND sched_turnaround_minutes <= 0) AS key_turn_le0_cnt,
                    COUNT(*) FILTER (WHERE is_key_turnaround_aircraft AND arr_exec_date BETWEEN %s AND %s AND sched_turnaround_minutes > 0 AND sched_turnaround_minutes < 45) AS key_turn_under45_cnt
                FROM public.mv_acdm_turnaround_pair
            """, (d_from, d_to) * 13)
            pair_stats['pair_release_on_time_rate'] = _pct(pair_stats.get('pair_release_normal_cnt'), pair_stats.get('pair_same_day_cnt'))
            summary.update(pair_stats)
            return summary

        current = fetch_span_kpis(start_date, end_date)
        mom_available = bool(cov_s and cov_e and prev_start.isoformat() >= cov_s and prev_end.isoformat() <= cov_e)
        prev_mom = fetch_span_kpis(prev_start, prev_end) if mom_available else None
        yoy_available = bool(cov_s and cov_e and yoy_start.isoformat() >= cov_s and yoy_end.isoformat() <= cov_e)
        prev_yoy = fetch_span_kpis(yoy_start, yoy_end) if yoy_available else None

        # Cutoff 00:00-22:00 snapshot when single day
        day_22_cutoff = None
        if span_days == 1:
            cutoff_ts = datetime.combine(start_date, time(22, 0), tzinfo=SHANGHAI_TZ)
            day_start_ts = datetime.combine(start_date, time(0, 0), tzinfo=SHANGHAI_TZ)
            day_22_cutoff = _q_one(cur, """
                SELECT
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_time >= %s AND real_takeoff_time < %s) AS dep_by_22,
                    COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_time >= %s AND real_takeoff_time < %s AND takeoff_on_time = 'Y') AS takeoff_norm_22
                FROM public.mv_acdm_dyn_flight_detail
            """, (day_start_ts, cutoff_ts, day_start_ts, cutoff_ts))
            day_22_cutoff['takeoff_rate_22'] = _pct(day_22_cutoff.get('takeoff_norm_22'), day_22_cutoff.get('dep_by_22'))

        # Section 2 breakdown: Attribute structure
        attr_rows = _q_dicts(cur, """
            SELECT
                CASE attribute
                    WHEN '2403' THEN '国内(2403)'
                    WHEN '2401' THEN '国际(2401)'
                    WHEN '2402' THEN '地区(2402)'
                    WHEN '2404' THEN '混合(2404)'
                    ELSE COALESCE(attribute, '未标注')
                END AS flight_attribute_label,
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND plan_day BETWEEN %s AND %s) AS planned_flights,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s) AS operated_flights
            FROM public.mv_acdm_dyn_flight_detail
            WHERE plan_day BETWEEN %s AND %s OR real_exec_day BETWEEN %s AND %s
            GROUP BY 1
            ORDER BY operated_flights DESC
        """, (start_date, end_date) * 4)

        # Section 3 Top/Bottom airlines (>= 3 operated dep flights)
        airline_rows = _q_dicts(cur, """
            SELECT
                c.airline_two_char_cd AS airline_code,
                COALESCE(MAX(al.name_zh), c.airline_two_char_cd) AS airline_name,
                COUNT(*) AS dep_flights,
                SUM(CASE WHEN c.takeoff_on_time = 'Y' THEN 1 ELSE 0 END) AS normal_dep,
                ROUND(100.0 * SUM(CASE WHEN c.takeoff_on_time = 'Y' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS takeoff_on_time_rate,
                ROUND(100.0 * SUM(CASE WHEN c.release_on_time = 'Y' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS release_on_time_rate
            FROM public.mv_acdm_dyn_flight_detail c
            LEFT JOIN public.dim_airline al ON al.iata_code = c.airline_two_char_cd AND al.deleted_at IS NULL
            WHERE c.is_operated_flight = 'Y' AND c.is_off_in = 'D' AND c.real_takeoff_day BETWEEN %s AND %s
            GROUP BY c.airline_two_char_cd
            ORDER BY dep_flights DESC
            LIMIT 12
        """, (start_date, end_date))

        # Section 4 Abnormal reasons Top 10
        delay_reasons = _q_dicts(cur, """
            SELECT
                COALESCE(c.abnormal_reason_cd, '未填原因') AS reason_code,
                COALESCE(MAX(r.name_zh), COALESCE(c.abnormal_reason_cd, '未填原因')) AS reason_name,
                COUNT(*) FILTER (WHERE c.is_operated_flight='Y' AND c.real_exec_day BETWEEN %s AND %s
                    AND (COALESCE(c.takeoff_delay_time, 0) > 0 OR COALESCE(c.clearance_delay_time, 0) > 0)) AS delay_flights,
                COUNT(*) FILTER (WHERE c.abnormal_flight_stat_cd = 'CAN' AND c.plan_day BETWEEN %s AND %s) AS cancel_flights
            FROM public.mv_acdm_dyn_flight_detail c
            LEFT JOIN public.dim_abnormal_reason r ON r.code = c.abnormal_reason_cd AND r.deleted_at IS NULL
            WHERE COALESCE(c.flight_task_cd, '') <> 'A/N'
              AND (c.plan_day BETWEEN %s AND %s OR c.real_exec_day BETWEEN %s AND %s)
              AND (COALESCE(c.takeoff_delay_time, 0) > 0 OR COALESCE(c.clearance_delay_time, 0) > 0 OR c.abnormal_flight_stat_cd = 'CAN')
            GROUP BY COALESCE(c.abnormal_reason_cd, '未填原因')
            ORDER BY delay_flights DESC, cancel_flights DESC
            LIMIT 10
        """, (start_date, end_date) * 4)

        # Section 6 Hourly movements & peak
        hourly_rows = _q_dicts(cur, """
            SELECT
                hour_of_day,
                COUNT(*) AS total_movements,
                COUNT(*) FILTER (WHERE direction = 'DEP') AS dep_movements,
                COUNT(*) FILTER (WHERE direction = 'ARR') AS arr_movements
            FROM ops_ask_views.flight_hourly_movements
            WHERE movement_date BETWEEN %s AND %s
            GROUP BY hour_of_day
            ORDER BY hour_of_day ASC
        """, (start_date, end_date))
        peak_rows = _q_dicts(cur, """SELECT peak_hour, COUNT(*) AS total_movements
            FROM ops_ask_views.flight_hourly_movements WHERE movement_date BETWEEN %s AND %s
            GROUP BY peak_hour ORDER BY total_movements DESC,peak_hour ASC LIMIT 1""", (start_date, end_date))
        peak_row = peak_rows[0] if peak_rows else None

        # Section 7 Key aircraft turnaround warning list + regional airport routes
        key_warnings = _q_dicts(cur, """
            SELECT arr_exec_date, arr_flight_no, dep_flight_no, craft_type_icao_cd, craft_no, sched_turnaround_minutes, craft_site_cd
            FROM public.mv_acdm_turnaround_pair
            WHERE is_key_turnaround_aircraft
              AND arr_exec_date BETWEEN %s AND %s
              AND sched_turnaround_minutes IS NOT NULL
              AND sched_turnaround_minutes > 0 AND sched_turnaround_minutes < 45
            ORDER BY sched_turnaround_minutes ASC, arr_exec_date DESC
            LIMIT 20
        """, (start_date, end_date))

        regional_routes = _q_dicts(cur, """
            SELECT
                CASE WHEN takeoff_airport_iata = ANY(%s) THEN takeoff_airport_iata ELSE arrival_airport_iata END AS regional_iata,
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND plan_day BETWEEN %s AND %s) AS planned_flights,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s) AS operated_flights
            FROM public.mv_acdm_dyn_flight_detail
            WHERE (takeoff_airport_iata = ANY(%s) OR arrival_airport_iata = ANY(%s))
              AND (plan_day BETWEEN %s AND %s OR real_exec_day BETWEEN %s AND %s)
            GROUP BY 1
            ORDER BY operated_flights DESC
        """, (list(REGIONAL_AIRPORT_NAMES.keys()), start_date, end_date, start_date, end_date,
              list(REGIONAL_AIRPORT_NAMES.keys()), list(REGIONAL_AIRPORT_NAMES.keys()), start_date, end_date, start_date, end_date))
        for rr in regional_routes:
            rr['airport_name'] = REGIONAL_AIRPORT_NAMES.get(rr['regional_iata'], rr['regional_iata'])

    finally:
        cur.close()

    span_label = start_date.isoformat() if start_date == end_date else f'{start_date.isoformat()} ~ {end_date.isoformat()}'
    period_name = {'day': '日报', 'week': '周报', 'month': '月报', 'custom': '自定义区间报告'}.get(period_type, '运行报告')
    mom_text = '上期未覆盖'
    if prev_mom:
        delta_r = (
            round((current['takeoff_on_time_rate'] or 0) - (prev_mom['takeoff_on_time_rate'] or 0), 2)
            if current.get('takeoff_on_time_rate') is not None and prev_mom.get('takeoff_on_time_rate') is not None
            else None
        )
        mom_text = f"上期（{prev_start.isoformat()}~{prev_end.isoformat()}）起飞正常率 {prev_mom.get('takeoff_on_time_rate')}%，环比变化 {delta_r:+.2f} 个百分点" if delta_r is not None else '上期起飞正常率暂无有效数据'
    yoy_text = (
        f"去年同期起飞正常率 {prev_yoy.get('takeoff_on_time_rate')}%"
        if prev_yoy else '当前本地库仅同步近 1 个月窗口，暂无法计算上年同期同比。'
    )

    peak_text = f"最高单小时为 {str(peak_row['peak_hour'])[:16]} 起的一小时（{peak_row['total_movements']} 架次）" if peak_row else '当前时段暂无小时起降记录'
    cutoff_text = f"（截至 22:00 截断起飞正常率 {day_22_cutoff['takeoff_rate_22']}%，实际 {day_22_cutoff['dep_by_22']} 班）" if day_22_cutoff and day_22_cutoff.get('takeoff_rate_22') is not None else ''
    load_factor_label = f"{current['total_load_factor']}%" if current['total_load_factor'] is not None else '无有效样本'

    sections = [
        {
            'title': '1. 总体情况与核心 KPI',
            'kind': 'facts',
            'claims': [
                {'text': f"{span_label} 全场计划航班 {current['planned_all']} 班（国内客运正班 {current['planned_dom_pax']} 班），实际执行 {current['operated_all']} 班（出港 {current['dep_operated']} / 进港 {current['arr_operated']}），取消 {current['cancel_all']} 班，执行率 {current['execution_rate']}%。{cutoff_text}"},
                {'text': f"正常性：起飞正常率 {current['takeoff_on_time_rate']}%、放行正常率(口径A单表) {current['release_on_time_rate']}%、始发正常率 {current['origin_dep_on_time_rate']}%、进港正常率(落地正常) {current['arr_on_time_rate']}%、地服起飞正常率(210+2403) {current['ground_takeoff_on_time_rate']}%。"},
                {'text': f"吞吐与资源：全量旅客吞吐量 {current['total_pax_sql']:,} 人次；综合客座率 {load_factor_label}（有效座位样本旅客 {current['load_factor_pax']:,} 人次 / 座位 {current['total_seats']:,} 个，覆盖 {current['load_factor_sample_flights']} 架次；未匹配正数座位的旅客 {current['load_factor_excluded_pax']:,} 人次不计入该比率）；货邮吞吐量(不含行李) {current['cargo_mail_kg']:,} kg；进港靠桥率 {current['bridge_rate_arr']}%（全量进出港 {current['bridge_rate_all']}%）；撤轮挡准时率 {current['block_off_on_time_rate']}%（出港执行 {current['dep_operated']} 班，其中时刻完整 {current['block_off_sample']} 班）。"},
                {'text': f"对比口径：{mom_text}；{yoy_text}"},
            ],
        },
        {
            'title': '2. 运行概况与结构',
            'kind': 'facts',
            'claims': [
                {'text': '按航班属性分布：' + ('；'.join(f"{r['flight_attribute_label']} 计划 {r['planned_flights']} / 执行 {r['operated_flights']}" for r in attr_rows) or '暂无记录') + '。'},
            ],
        },
        {
            'title': '3. 正常率与过站效率深度分析',
            'kind': 'facts',
            'claims': [
                {'text': f"放行正常率口径B(有效过站同日配对 {current['pair_same_day_cnt']} 对)：{current['pair_release_on_time_rate']}%。"},
                {'text': f"有效过站 {current['turnaround_valid_cnt']} 对平均总保障时长 {current['avg_service_min'] or '—'} 分钟；7项子节点均值：进港靠桥 {current['avg_bridge_min'] or '—'}分、下客 {current['avg_departure_min'] or '—(无记录)'}分、出港登机 {current['avg_board_min'] or '—'}分、保洁 {current['avg_clean_min'] or '—'}分、配餐 {current['avg_meal_min'] or '—'}分、加油 {current['avg_supply_min'] or '—'}分、除冰 {current['avg_deicie_min'] or '—(无记录)'}分。"},
            ],
        },
        {
            'title': '4. 不正常航班与延误分析',
            'kind': 'facts',
            'claims': [
                {'text': f"大于0分钟延误统计：放行延误 {current['release_delay_cnt']} 班（均长 {current['avg_release_delay_min'] or '—'} 分）、起飞延误均值 {current['avg_takeoff_delay_min'] or '—'} 分、到港延误均值 {current['avg_arr_delay_min'] or '—'} 分。"},
                {'text': '异常原因 Top：' + ('；'.join(f"{r['reason_name']}({r['reason_code']}) 延误 {r['delay_flights']}/取消 {r['cancel_flights']}" for r in delay_reasons[:5]) or '当前时段无延误或取消归因记录') + '。'},
            ],
        },
        {
            'title': '5–6. 靠桥分析与高峰小时',
            'kind': 'facts',
            'claims': [
                {'text': f"进港近机位 {current['arr_bridge_cnt']}/{current['arr_operated']} ({current['bridge_rate_arr']}%)，全量近机位 {current['all_bridge_cnt']}/{current['operated_all']} ({current['bridge_rate_all']}%)；{peak_text}。"},
            ],
        },
        {
            'title': '7–8. 协同运行、重点机型过站与滑行效率',
            'kind': 'facts',
            'claims': [
                {'text': f"1–5 分钟短延误：{current['short_delay_5m_cnt']} 班放行延误在 1–5 分钟区间（若全部规避，放行正常率可提效约 +{current['short_delay_lift_pct'] or 0:.2f} 个百分点）。"},
                {'text': f"重点机型(B738/B788/A320/A321/C919)计划过站预警：<=0 分钟 {current['key_turn_le0_cnt']} 对，不足45分钟 {current['key_turn_under45_cnt']} 对。"},
                {'text': '本场与省域 7 机场往来航班：' + ('；'.join(f"{r['airport_name']}({r['regional_iata']}) 执行 {r['operated_flights']}" for r in regional_routes) or '当前窗口无匹配航线') + '（注：代管省域机场内部起降与调时数据需外部机场群接口接入）。'},
                {'text': f"地面滑行：平均滑出 {current['avg_taxi_out_min'] or '—'} 分钟（提示：当前源库近1月滑出值为固定值 12），实测滑入有效样本 {current['taxi_in_samples']} 条。"},
            ],
        },
        {
            'title': '数据边界与暂未接入能力标注',
            'kind': 'limitations',
            'claims': [
                {'text': f"当前实库数据覆盖区间为 {cov_s} ~ {cov_e}；已检测到实测关舱门记录 {current['close_cabin_real_cnt']} 班，但因预计关门时间(ALTERATE_CLOSE_CABIN_TIME)全空、放行临界时间缺失及三方核定基准滑行时间表未入库，关舱门准时率、临界干预贡献度及畅通/额外滑行时间本次暂按缺口标注不输出。"},
            ],
        },
    ]

    summary_text = '\n\n'.join(f"【{s['title']}】\n" + '\n'.join(c['text'] for c in s['claims']) for s in sections)
    claims_flat = [{'text': c['text']} for s in sections for c in s['claims']]
    visual_meta = {
        'kind': 'trend' if len(hourly_rows) > 1 else 'stats',
        'title': f'{span_label} 各钟点累计起降分布' if len(hourly_rows) > 1 else f'{span_label} 核心指标概览',
        'series': [{'label': f"{r['hour_of_day']:02d}时", 'value': r['total_movements']} for r in hourly_rows] if len(hourly_rows) > 1 else [],
        'stats': [
            {'label': '执行航班', 'value': str(current['operated_all']), 'unit': '班'},
            {'label': '起飞正常率', 'value': str(current['takeoff_on_time_rate'] if current['takeoff_on_time_rate'] is not None else '—'), 'unit': '%'},
            {'label': '进港靠桥率', 'value': str(current['bridge_rate_arr'] if current['bridge_rate_arr'] is not None else '—'), 'unit': '%'},
            {'label': '旅客吞吐量', 'value': f"{current['total_pax_sql']:,}", 'unit': '人次'},
        ],
        'unit': '架次' if len(hourly_rows) > 1 else '',
        'metric_label': '每小时起降架次',
    }
    return {
        'kind': 'general',
        'title': f'《A-CDM 通用报告（{period_name}）》· {span_label}',
        'coverage': cov,
        'summary': current,
        'period_type': period_type,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'text': summary_text,
        'claims': claims_flat,
        'document': {'title': f'《A-CDM 通用报告（{period_name}）》· {span_label}', 'sections': sections},
        'rows': [current],
        'total': int(current.get('operated_all') or len(airline_rows) or 1),
        'related': [
            {'table': 'airline_ranking', 'name': '航司起飞/放行正常率排行', 'note': '出港执行航班分航司统计', 'type': 'report', 'level': 1, 'count': len(airline_rows), 'rows': airline_rows},
            {'table': 'delay_reasons', 'name': '异常延误与取消原因 Top10', 'note': '仅统计延误时长>0及CAN取消', 'type': 'report', 'level': 1, 'count': len(delay_reasons), 'rows': delay_reasons},
            {'table': 'key_turnaround_warnings', 'name': '重点机型计划过站不足预警', 'note': 'B738/B788/A320/A321/C919 <45min', 'type': 'report', 'level': 1, 'count': len(key_warnings), 'rows': key_warnings},
        ],
        'visual_meta': visual_meta,
        'sql_preview': 'SELECT ... FROM public.mv_acdm_dyn_flight_detail / public.mv_acdm_turnaround_pair / ops_ask_views.flight_hourly_movements',
        'params': [start_date.isoformat(), end_date.isoformat()],
    }


def build_ground_briefing(conn, *, target_date: date) -> dict[str, Any]:
    """Generate the calibrated 6-part Ground Handling Duty Briefing (Handbook Ch.7.2)."""
    next_day = target_date + timedelta(days=1)
    d0_00 = datetime.combine(target_date, time(0, 0), tzinfo=SHANGHAI_TZ)
    d0_22 = datetime.combine(target_date, GROUND_BRIEFING_CUTOFF, tzinfo=SHANGHAI_TZ)
    d1_00 = datetime.combine(next_day, time(0, 0), tzinfo=SHANGHAI_TZ)
    d1_05 = datetime.combine(next_day, time(5, 0), tzinfo=SHANGHAI_TZ)
    d1_06 = datetime.combine(next_day, time(6, 0), tzinfo=SHANGHAI_TZ)
    d2_00 = datetime.combine(target_date + timedelta(days=2), time(0, 0), tzinfo=SHANGHAI_TZ)

    cur = conn.cursor()
    try:
        cov = _coverage_bounds(cur)
        today_box = _q_one(cur, """
            SELECT
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND plan_time >= %s AND plan_time < %s) AS planned_today,
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND abnormal_flight_stat_cd = 'CAN' AND plan_time >= %s AND plan_time < %s) AS canceled_today,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND real_takeoff_landin_time >= %s AND real_takeoff_landin_time < %s) AS operated_today,
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND COALESCE(abnormal_flight_stat_cd, '') <> 'CAN'
                                   AND ((is_off_in = 'D' AND COALESCE(real_takeoff_time, alterate_takeoff_time, scheme_takeoff_time) >= %s AND COALESCE(real_takeoff_time, alterate_takeoff_time, scheme_takeoff_time) < %s)
                                     OR (is_off_in = 'A' AND COALESCE(real_landin_time, alterate_landin_time, scheme_landin_time) >= %s AND COALESCE(real_landin_time, alterate_landin_time, scheme_landin_time) < %s))) AS est_before_24,
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND COALESCE(abnormal_flight_stat_cd, '') <> 'CAN'
                                   AND ((is_off_in = 'D' AND COALESCE(real_takeoff_time, alterate_takeoff_time, scheme_takeoff_time) >= %s AND COALESCE(real_takeoff_time, alterate_takeoff_time, scheme_takeoff_time) < %s)
                                     OR (is_off_in = 'A' AND COALESCE(real_landin_time, alterate_landin_time, scheme_landin_time) >= %s AND COALESCE(real_landin_time, alterate_landin_time, scheme_landin_time) < %s))) AS late_00_05_window
            FROM public.mv_acdm_dyn_flight_detail
        """, (d0_00, d1_00, d0_00, d1_00, d0_00, d1_00, d0_00, d1_00, d0_00, d1_00, d1_00, d1_05, d1_00, d1_05))

        tomorrow_box = _q_one(cur, """
            SELECT
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND plan_time >= %s AND plan_time < %s) AS planned_tomorrow,
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND plan_time >= %s AND plan_time < %s AND is_off_in = 'D' AND ori_act = 'Y') AS early_origin_tomorrow,
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND plan_time >= %s AND plan_time < %s AND attribute IN ('2401', '2402')) AS intl_reg_tomorrow,
                COUNT(*) FILTER (WHERE COALESCE(flight_task_cd, '') <> 'A/N' AND plan_time >= %s AND plan_time < %s AND ori_act = 'Y' AND craft_site_is_remote = 'Y') AS origin_remote_tomorrow
            FROM public.mv_acdm_dyn_flight_detail
        """, (d1_00, d2_00, d1_06, d2_00, d1_00, d2_00, d1_06, d2_00))
        tomorrow_box['mom_vs_today_pct'] = (
            round(100.0 * (tomorrow_box['planned_tomorrow'] - today_box['planned_today']) / float(today_box['planned_today']), 2)
            if today_box.get('planned_today') else None
        )

        # Dual-caliber normality up to 22:00 (All Airport vs Ground Handling 210+2403)
        norm_22 = _q_one(cur, """
            SELECT
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_time >= %s AND real_takeoff_time < %s) AS all_dep_22,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_time >= %s AND real_takeoff_time < %s AND takeoff_on_time = 'Y') AS all_takeoff_norm_22,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_time >= %s AND real_takeoff_time < %s AND release_on_time = 'Y') AS all_release_norm_22,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_time >= %s AND real_takeoff_time < %s AND agency = '210' AND attribute = '2403') AS gh_dep_22,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_time >= %s AND real_takeoff_time < %s AND agency = '210' AND attribute = '2403' AND takeoff_on_time = 'Y') AS gh_takeoff_norm_22,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_time >= %s AND real_takeoff_time < %s AND agency = '210' AND attribute = '2403' AND release_on_time = 'Y') AS gh_release_norm_22,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D' AND real_takeoff_time >= %s AND real_takeoff_time < %s AND agency = '210' AND attribute = '2403' AND COALESCE(clearance_delay_time, 0) > 0) AS gh_release_delay_22
            FROM public.mv_acdm_dyn_flight_detail
        """, (d0_00, d0_22) * 7)
        norm_22['all_takeoff_rate_22'] = _pct(norm_22.get('all_takeoff_norm_22'), norm_22.get('all_dep_22'))
        norm_22['all_release_rate_22'] = _pct(norm_22.get('all_release_norm_22'), norm_22.get('all_dep_22'))
        norm_22['gh_takeoff_rate_22'] = _pct(norm_22.get('gh_takeoff_norm_22'), norm_22.get('gh_dep_22'))
        norm_22['gh_release_rate_22'] = _pct(norm_22.get('gh_release_norm_22'), norm_22.get('gh_dep_22'))

        early_origin_list = _q_dicts(cur, """
            SELECT flight_no, airline_two_char_cd, scheme_takeoff_time, craft_no, craft_site_cd,
                   COALESCE(craft_site_is_remote, '未知') AS is_remote_stand,
                   CASE WHEN craft_site_is_remote = 'Y' THEN '是（远机位外保障）' WHEN craft_site_is_remote = 'N' THEN '否（近机位）' ELSE '机位待定' END AS outer_support_flag,
                   terminal, gate_cd, agency
            FROM public.mv_acdm_dyn_flight_detail
            WHERE COALESCE(flight_task_cd, '') <> 'A/N'
              AND COALESCE(abnormal_flight_stat_cd, '') <> 'CAN'
              AND is_off_in = 'D' AND ori_act = 'Y'
              AND plan_time >= %s AND plan_time < %s
            ORDER BY scheme_takeoff_time ASC NULLS LAST, flight_no ASC
            LIMIT 40
        """, (d1_06, d2_00))

        canceled_list = _q_dicts(cur, """
            SELECT flight_no, direction, scheme_takeoff_time, scheme_landin_time,
                   COALESCE(s_cnl_stauts, '未填取消时段') AS s_cnl_stauts,
                   COALESCE(abnormal_reason_cd, '未填原因') AS abnormal_reason_cd,
                   agency, craft_type_icao_cd
            FROM public.mv_acdm_dyn_flight_detail
            WHERE COALESCE(flight_task_cd, '') <> 'A/N'
              AND abnormal_flight_stat_cd = 'CAN'
              AND plan_time >= %s AND plan_time < %s
            ORDER BY COALESCE(scheme_takeoff_time, scheme_landin_time) ASC NULLS LAST
            LIMIT 40
        """, (d0_00, d1_00))

        short_delay_dom = _q_dicts(cur, """
            SELECT flight_no, agency, craft_site_cd, terminal, clearance_delay_time, takeoff_delay_time,
                   COALESCE(abnormal_reason_cd, '未填原因') AS abnormal_reason_cd,
                   real_takeoff_time
            FROM public.mv_acdm_dyn_flight_detail
            WHERE is_operated_flight = 'Y' AND is_off_in = 'D'
              AND attribute = '2403'
              AND plan_time >= %s AND plan_time < %s
              AND clearance_delay_time BETWEEN 1 AND 5
            ORDER BY clearance_delay_time DESC, real_takeoff_time DESC
            LIMIT 40
        """, (d0_00, d1_00))
    finally:
        cur.close()

    d_str = target_date.isoformat()
    n_str = next_day.isoformat()
    cutoff_label = GROUND_BRIEFING_CUTOFF.strftime('%H:%M')

    def normality(scope: str, indicator: str) -> str:
        numerator = norm_22[f'{scope}_{indicator}_norm_22']
        denominator = norm_22[f'{scope}_dep_22']
        rate = norm_22[f'{scope}_{indicator}_rate_22']
        value = f'{rate:.2f}%' if rate is not None else '无有效分母'
        return f'{value}（分子 {numerator}，分母 {denominator}）'

    sections = [
        {
            'title': '1. 当日全天运行与次日凌晨收尾态势',
            'kind': 'facts',
            'claims': [
                {'text': f"{d_str} 计划航班 {today_box['planned_today']} 班，已执行 {today_box['operated_today']} 班，取消 {today_box['canceled_today']} 班，24:00前预计执行约 {today_box['est_before_24']} 班；延伸至次日（{n_str} 00:00–05:00）收尾航班 {today_box['late_00_05_window']} 班。"},
            ],
        },
        {
            'title': f'2. 截至 {cutoff_label} 的出港执行量与正常率',
            'kind': 'facts',
            'claims': [
                {'text': f"本场全量出港执行 {norm_22['all_dep_22']} 架次。放行正常率 {normality('all', 'release')}，起飞正常率 {normality('all', 'takeoff')}。"},
                {'text': f"地服口径出港执行 {norm_22['gh_dep_22']} 架次（AGENCY=210 且 ATTRIBUTE=2403）。放行正常率 {normality('gh', 'release')}，起飞正常率 {normality('gh', 'takeoff')}。地服放行延误 {norm_22['gh_release_delay_22']} 架次。"},
                {'text': f'按北京时间当日 00:00 起、{cutoff_label} 前实际起飞的出港航班统计。各项正常率以同口径的出港执行量为分母，以相应正常标志为 Y 的执行架次为分子。'},
            ],
        },
        {
            'title': '3. 次日开局准备（全天与 06:00–23:59 早始发）',
            'kind': 'facts',
            'claims': [
                {'text': f"次日（{n_str}）全天计划 {tomorrow_box['planned_tomorrow']} 班（较今日环比 {tomorrow_box['mom_vs_today_pct'] if tomorrow_box['mom_vs_today_pct'] is not None else '—'}%），其中早始发(06:00–23:59 且 ORI_ACT='Y') {tomorrow_box['early_origin_tomorrow']} 班（已识别远机位外场保障 {tomorrow_box['origin_remote_tomorrow']} 班），国际及地区航班 {tomorrow_box['intl_reg_tomorrow']} 班。"},
                {'text': '注：因源库预计靠桥时间(ALTERATE_BY_BRIDGE_TIME)全为空值，次日外场保障统一按关联机位 IS_REMOTE=\'Y\' 识别；取消时段取 S_CNL_STAUTS。'},
            ],
        },
        {
            'title': '4–6. 明细清单（次日早始发 / 当日取消 / 5分钟内国内放行延误）',
            'kind': 'facts',
            'claims': [
                {'text': f"已提取明细：次日早始发清单 {len(early_origin_list)} 条（详见关联表）、当日取消 {len(canceled_list)} 条、5分钟以内国内放行延误 {len(short_delay_dom)} 条。"},
            ],
        },
    ]
    summary_text = '\n\n'.join(f"【{s['title']}】\n" + '\n'.join(c['text'] for c in s['claims']) for s in sections)
    merged_summary = {**today_box, **tomorrow_box, **norm_22}
    return {
        'kind': 'ground_briefing',
        'title': f'《地服公司运行简报》· {d_str}',
        'coverage': cov,
        'target_date': d_str,
        'summary': merged_summary,
        'text': summary_text,
        'claims': [{'text': c['text']} for s in sections for c in s['claims']],
        'document': {'title': f'《地服公司运行简报》· {d_str}', 'sections': sections},
        'rows': [merged_summary],
        'total': int(today_box.get('planned_today') or len(early_origin_list) or 1),
        'related': [
            {'table': 'next_day_early_origin', 'name': f'次日({n_str})早始发明细与外场标识', 'note': 'ORI_ACT=Y & 06:00-23:59 & IS_REMOTE识别外场', 'type': 'report', 'level': 1, 'count': len(early_origin_list), 'rows': early_origin_list},
            {'table': 'today_canceled', 'name': f'当日({d_str})取消航班明细（含 S_CNL_STAUTS）', 'note': 'ABNORMAL_FLIGHT_STAT_CD=CAN 且非 A/N', 'type': 'report', 'level': 1, 'count': len(canceled_list), 'rows': canceled_list},
            {'table': 'short_delay_within_5m', 'name': '放行延误 5 分钟以内国内航班及原因', 'note': 'ATTRIBUTE=2403 & CLEARANCE_DELAY_TIME 1..5', 'type': 'report', 'level': 1, 'count': len(short_delay_dom), 'rows': short_delay_dom},
        ],
        'visual_meta': {
            'kind': 'stats',
            'title': f'{d_str} 地服简报核心看板',
            'stats': [
                {'label': '当日已执行/计划', 'value': f"{today_box['operated_today']}/{today_box['planned_today']}", 'unit': '班'},
                {'label': '22时全场起飞正常率', 'value': str(norm_22['all_takeoff_rate_22'] if norm_22['all_takeoff_rate_22'] is not None else '—'), 'unit': '%'},
                {'label': '22时地服起飞正常率', 'value': str(norm_22['gh_takeoff_rate_22'] if norm_22['gh_takeoff_rate_22'] is not None else '—'), 'unit': '%'},
                {'label': '次日早始发(含外场)', 'value': f"{tomorrow_box['early_origin_tomorrow']}({tomorrow_box['origin_remote_tomorrow']})", 'unit': '班'},
            ],
        },
        'sql_preview': 'SELECT ... FROM public.mv_acdm_dyn_flight_detail WHERE (22:00 / 00:00-05:00 / 06:00-23:59 windows)',
        'params': [d_str],
    }


def safeguard_node_counts_sql(safeguards, flights, definitions, where):
    return f"""
        WITH eligible AS (
            SELECT COUNT(DISTINCT f.pg_flight_id) AS all_cnt,
                   COUNT(DISTINCT f.pg_flight_id) FILTER (WHERE f.is_off_in='D') AS dep_cnt,
                   COUNT(DISTINCT f.pg_flight_id) FILTER (WHERE f.is_off_in='A') AS arr_cnt
            FROM {flights} f
            WHERE f.is_operated_flight='Y' AND f.real_exec_day >= %s AND f.real_exec_day < %s
        ), counts AS (
            SELECT s.safeguard_code,
                COALESCE(MAX(d.name), s.safeguard_code) AS node_name,
                COALESCE(MAX(d.is_off_in), '—') AS is_off_in,
                COUNT(*) AS total_records,
                COUNT(s.safeguard_real_time) AS real_recorded_cnt,
                COUNT(s.safeguard_alterate_time) AS alterate_cnt,
                SUM(COUNT(*)) OVER() AS all_recorded_total,
                COUNT(DISTINCT s.flight_id) AS distinct_flights,
                COUNT(DISTINCT s.flight_id) FILTER (WHERE c.is_operated_flight='Y'
                    AND c.real_exec_day >= %s AND c.real_exec_day < %s
                    AND c.is_off_in=CASE WHEN UPPER(d.is_off_in) IN ('D','DEP','1','O') THEN 'D'
                        WHEN UPPER(d.is_off_in) IN ('A','ARR','0','I') THEN 'A' ELSE c.is_off_in END) AS recorded_flights
            FROM {safeguards} s
            JOIN {flights} c ON c.pg_flight_id = s.flight_id
            LEFT JOIN {definitions} d ON d.code = s.safeguard_code AND d.deleted_at IS NULL
            {where}
            GROUP BY s.safeguard_code
        ), coverage AS (
            SELECT counts.*, CASE WHEN UPPER(is_off_in) IN ('D','DEP','1','O') THEN dep_cnt
                WHEN UPPER(is_off_in) IN ('A','ARR','0','I') THEN arr_cnt ELSE all_cnt END AS eligible_flights
            FROM counts CROSS JOIN eligible
        )
        SELECT coverage.*, ROUND(100.0 * recorded_flights / NULLIF(eligible_flights, 0), 2) AS entry_rate_pct
        FROM coverage
    """


def build_safeguard_and_resource_board(
    conn,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Generate Safeguard Node Entry Ranking & Air Bridge Turnover Board (Handbook Ch.7.3 & Ch.5.4)."""
    cur = conn.cursor()
    try:
        cov = _coverage_bounds(cur)
        operated = _q_one(cur, """
            SELECT COUNT(*) AS op_cnt
            FROM public.mv_acdm_dyn_flight_detail
            WHERE is_operated_flight = 'Y' AND real_exec_day BETWEEN %s AND %s
        """, (start_date, end_date))
        total_operated = operated.get('op_cnt') or 0
        period_start = datetime.combine(start_date, time.min, tzinfo=SHANGHAI_TZ)
        period_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=SHANGHAI_TZ)

        sg_sql = safeguard_node_counts_sql(
            'public.std_flight_safeguard_node', 'public.mv_acdm_dyn_flight_detail', 'public.dim_safeguard_node',
            'WHERE s.safeguard_real_time >= %s AND s.safeguard_real_time < %s',
        )
        days = (start_date, period_end.date())
        sg_rows = _q_dicts(cur, sg_sql + 'ORDER BY real_recorded_cnt DESC, safeguard_code LIMIT 25',
                          (*days, *days, period_start, period_end))

        bridge_rows = _q_dicts(cur, """
            SELECT
                COALESCE(b.air_bridge_cd, b.air_bridge_id, '未编码') AS bridge_code,
                COALESCE(MAX(db.name_zh), MAX(b.air_bridge_cd), MAX(b.air_bridge_id), '未编码') AS bridge_name,
                COUNT(*) AS use_count,
                ROUND(AVG(b.bridge_duration / 60.0) FILTER(WHERE b.bridge_duration>0)::numeric, 2) AS avg_bridge_duration_min,
                SUM(COUNT(*)) OVER() AS all_usage_count,
                SUM(SUM(CASE WHEN b.bridge_duration>0 THEN b.bridge_duration ELSE 0 END)) OVER() AS all_duration_seconds,
                SUM(COUNT(*) FILTER(WHERE b.bridge_duration>0)) OVER() AS all_duration_samples,
                COUNT(b.power_start_time) AS power_supply_cnt,
                COUNT(b.ac_start_time) AS ac_supply_cnt
            FROM public.std_flight_air_bridge b
            JOIN public.mv_acdm_dyn_flight_detail c ON c.pg_flight_id = b.flight_id
            LEFT JOIN public.dim_air_bridge db ON db.bridge_code = b.air_bridge_cd AND db.deleted_at IS NULL
            WHERE b.bridge_start_time>=%s AND b.bridge_start_time<%s
            GROUP BY COALESCE(b.air_bridge_cd, b.air_bridge_id, '未编码')
            ORDER BY use_count DESC
            LIMIT 20
        """, (period_start, period_end))

        terminal_rows = _q_dicts(cur, """
            SELECT
                COALESCE(NULLIF(TRIM(terminal), ''), '未标注航站楼') AS terminal,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y') AS operated_flights,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'D') AS dep_flights,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND is_off_in = 'A') AS arr_flights,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND attribute = '2403') AS domestic_flights,
                COUNT(*) FILTER (WHERE is_operated_flight = 'Y' AND attribute IN ('2401', '2402')) AS intl_reg_flights
            FROM public.mv_acdm_dyn_flight_detail
            WHERE real_exec_day BETWEEN %s AND %s
            GROUP BY 1
            ORDER BY operated_flights DESC
        """, (start_date, end_date))
    finally:
        cur.close()

    span_label = start_date.isoformat() if start_date == end_date else f'{start_date.isoformat()} ~ {end_date.isoformat()}'
    top_sg = '；'.join(f"{r['node_name']}: 实测 {r['real_recorded_cnt']} 条，覆盖适用航班 {r['recorded_flights']}/{r['eligible_flights']}（{r['entry_rate_pct'] if r['entry_rate_pct'] is not None else '—'}%）" for r in sg_rows[:6])
    top_br = '；'.join(f"{r['bridge_code']}: 使用 {r['use_count']} 次(均长 {r['avg_bridge_duration_min'] or '—'} 分)" for r in bridge_rows[:5])
    summary = {
        'operated_all': total_operated,
        'safeguard_node_recorded_count': sg_rows[0]['all_recorded_total'] if sg_rows else 0,
        'air_bridge_usage_count': bridge_rows[0]['all_usage_count'] if bridge_rows else 0,
        'avg_air_bridge_minutes': round(bridge_rows[0]['all_duration_seconds'] / 60.0 / bridge_rows[0]['all_duration_samples'], 2)
            if bridge_rows and bridge_rows[0]['all_duration_samples'] else None,
    }
    for row in sg_rows:
        row.pop('all_recorded_total', None)
        row.pop('distinct_flights', None)
    for row in bridge_rows:
        for key in ('all_usage_count', 'all_duration_seconds', 'all_duration_samples'):
            row.pop(key, None)
    sections = [
        {
            'title': '1. 保障节点录入质量统计',
            'kind': 'facts',
            'claims': [
                {'text': f"{span_label} 执飞航班共 {total_operated} 班；高频保障节点实测录入：{top_sg or '暂无记录'}。"},
            ],
        },
        {
            'title': '2. 登机桥周转与桥载使用',
            'kind': 'facts',
            'claims': [
                {'text': f"登机桥使用概况：{top_br or '当前时段无登机桥占用明细'}。"},
            ],
        },
        {
            'title': '3. T2 / T3 航站楼分流执行概况',
            'kind': 'facts',
            'claims': [
                {'text': '分航站楼执飞量：' + ('；'.join(f"{r['terminal']} 共 {r['operated_flights']} 班(出 {r['dep_flights']}/进 {r['arr_flights']})" for r in terminal_rows) or '暂无记录') + '。'},
            ],
        },
        {
            'title': '数据能力边界说明',
            'kind': 'limitations',
            'claims': [
                {'text': '按北京时间自然日统计作业实际记录；航班按实际起降日归属。节点覆盖率按适用进出港方向的当期执行航班去重计算，记录次数单列。缺少节点时限标准，暂不输出节点准点率或及时率。'},
            ],
        },
    ]
    summary_text = '\n\n'.join(f"【{s['title']}】\n" + '\n'.join(c['text'] for c in s['claims']) for s in sections)
    return {
        'kind': 'safeguard_board',
        'title': f'《保障节点质量与资源周转看板》· {span_label}',
        'coverage': cov,
        'summary': summary,
        'text': summary_text,
        'claims': [{'text': c['text']} for s in sections for c in s['claims']],
        'document': {'title': f'《保障节点质量与资源周转看板》· {span_label}', 'sections': sections},
        'rows': sg_rows[:12],
        'total': int(total_operated or len(sg_rows)),
        'related': [
            {'table': 'safeguard_ranking', 'name': '保障节点录入排行', 'note': '实测记录条数、适用航班去重覆盖率', 'type': 'report', 'level': 1, 'count': len(sg_rows), 'rows': sg_rows},
            {'table': 'air_bridge_turnover', 'name': '登机桥使用与桥载电/气统计', 'note': 'std_flight_air_bridge', 'type': 'report', 'level': 1, 'count': len(bridge_rows), 'rows': bridge_rows},
            {'table': 'terminal_split', 'name': '航站楼(T2/T3)执行分流', 'note': '含国内与国际/地区分项', 'type': 'report', 'level': 1, 'count': len(terminal_rows), 'rows': terminal_rows},
        ],
        'visual_meta': {
            'kind': 'bar' if len(sg_rows) > 1 else 'stats',
            'title': f'{span_label} 保障节点实测录入排行',
            'bars': [{'label': str(r['node_name']), 'value': float(r['real_recorded_cnt'] or 0), 'count': int(r['real_recorded_cnt'] or 0), 'percent': float(r['entry_rate_pct'] or 0)} for r in sg_rows[:10]],
            'unit': '条',
            'group_label': '保障节点',
        },
        'sql_preview': 'SELECT ... FROM public.std_flight_safeguard_node / public.std_flight_air_bridge / public.mv_acdm_dyn_flight_detail',
        'params': [start_date.isoformat(), end_date.isoformat()],
    }


def execute_acdm_report(conn, plan: dict[str, Any], catalog: Any = None) -> dict[str, Any]:
    """Dispatch an intent='acdm_report' plan into the corresponding report builder."""
    kind = plan.get('report_kind') or 'general'
    s_date = date.fromisoformat(plan['report_start']) if plan.get('report_start') else (date.today() - timedelta(days=1))
    e_date = date.fromisoformat(plan['report_end']) if plan.get('report_end') else s_date
    p_type = plan.get('report_period_type') or 'day'
    if kind == 'ground_briefing':
        return build_ground_briefing(conn, target_date=e_date)
    if kind == 'safeguard_board':
        return build_safeguard_and_resource_board(conn, start_date=s_date, end_date=e_date)
    return build_general_report(conn, start_date=s_date, end_date=e_date, period_type=p_type)
