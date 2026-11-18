"""Owner-scoped report jobs, deterministic templates, PDF and in-app subscriptions."""
from datetime import date, datetime, timedelta, timezone
from html import escape
import json
import logging
import re
import secrets
from time import time

from app.admin.store import now
from app.agent.analysis import shift_month
from app.config import settings
from app.query import json_safe

logger = logging.getLogger(__name__)
TZ = timezone(timedelta(hours=8))
KINDS = {'general': 'A-CDM通用报告', 'ground_briefing': '地服运行简报', 'safeguard_board': '保障质量与资源看板'}
PERIODS = {'day': '日报', 'week': '周报', 'month': '月报', 'quarter': '季报', 'year': '年报'}


def period_bounds(period, anchor, previous=False):
    if period not in PERIODS:
        raise ValueError('报告周期无效')
    if period == 'day':
        start = anchor - timedelta(days=1) if previous else anchor
        return start, start
    if period == 'week':
        start = anchor - timedelta(days=anchor.weekday())
        return (start - timedelta(days=7), start - timedelta(days=1)) if previous else (start, start + timedelta(days=6))
    months = {'month': 1, 'quarter': 3, 'year': 12}[period]
    start = anchor.replace(month=((anchor.month - 1) // months * months + 1), day=1)
    return (shift_month(start, -months), start - timedelta(days=1)) if previous else (start, shift_month(start, months) - timedelta(days=1))


def validate_request(data, business_user_id):
    kind, period = data.get('kind', 'general'), data.get('period', 'day')
    if not isinstance(kind, str) or not isinstance(period, str) or kind not in KINDS or period not in PERIODS:
        raise ValueError('报告类型或周期无效')
    if any(data.get(key) is not None and not isinstance(data[key], str) for key in ('target_date', 'start_date', 'end_date')):
        raise ValueError('报告日期必须为YYYY-MM-DD字符串')
    if kind == 'ground_briefing' and period != 'day':
        raise ValueError('地服简报仅支持日报')
    anchor = date.fromisoformat(data['target_date']) if data.get('target_date') else datetime.now(TZ).date()
    start, end = period_bounds(period, anchor, previous=not bool(data.get('target_date')))
    if data.get('start_date') or data.get('end_date'):
        if not data.get('start_date') or not data.get('end_date'):
            raise ValueError('请同时指定报告开始和结束日期')
        start, end = date.fromisoformat(data['start_date']), date.fromisoformat(data['end_date'])
    if start > end or (end - start).days > 366 or (kind == 'ground_briefing' and start != end):
        raise ValueError('报告期间无效，最长支持一个完整年度')
    if type(data.get('include_benchmarks', False)) is not bool:
        raise ValueError('对标选项必须是布尔值')
    return {'kind': kind, 'period': period, 'start_date': str(start), 'end_date': str(end),
            'include_benchmarks': data.get('include_benchmarks', False), 'business_user_id': str(business_user_id)}


def enqueue(store, owner, payload, run_key=None, subscription_id=None, db=None):
    if db is None:
        with store.db() as connection:
            return enqueue(store, owner, payload, run_key, subscription_id, connection)
    if run_key:
        existing = db.execute('SELECT id FROM reports WHERE run_key=?', (run_key,)).fetchone()
        if existing:
            return existing['id']
    rid = secrets.token_hex(16)
    title = payload.get('title') or f"{KINDS[payload['kind']]}·{payload['start_date']}至{payload['end_date']}"
    db.execute('INSERT INTO reports(id,owner,kind,title,status,payload,run_key,subscription_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
               (rid, owner, payload['kind'], title[:180], 'queued', store.vault.seal(json.dumps(payload, ensure_ascii=False)),
                run_key, subscription_id, now(), now()))
    store.log(db, owner, 'report.create', rid, payload['kind'])
    return rid


def enqueue_snapshot(store, owner, user_id, result, message_id):
    plan = result.get('plan') or {}
    disp = plan.get('disp')
    start_d = plan.get('report_start') or result.get('target_date') or ''
    kind = 'query'
    if plan.get('intent') == 'acdm_report' and plan.get('report_kind'):
        kind = plan['report_kind']
        if disp:
            title = f"湖北机场集团 · 武汉天河国际机场 {disp}" + (f"（{start_d}）" if start_d else "")
        else:
            title = f"武汉天河国际机场 · 地服运行简报" + (f"（{start_d}）" if start_d else "")
    elif result.get('question'):
        title = f"武汉天河国际机场 · {result['question'][:80]}"
    else:
        title = "武汉天河国际机场 · 运行分析简报"

    payload = {'kind': kind, 'title': title[:180],
               'business_user_id': str(user_id), 'snapshot': result}
    return enqueue(store, owner, payload, run_key='message:' + message_id)


def public_report(row):
    return {k: row[k] for k in ('id', 'kind', 'title', 'status', 'error', 'attempts', 'created_at', 'updated_at')}


def list_reports(store, owner, page=1):
    with store.db() as db:
        total = db.execute('SELECT COUNT(*) FROM reports WHERE owner=?', (owner,)).fetchone()[0]
        rows = db.execute('SELECT * FROM reports WHERE owner=? ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET ?', (owner, (page - 1) * 20)).fetchall()
        heartbeat = db.execute("SELECT value FROM preferences WHERE key='report_worker_heartbeat'").fetchone()
    active = bool(heartbeat and time() - json.loads(heartbeat['value']) < 90)
    return {'ok': True, 'items': [public_report(r) for r in rows], 'total': total, 'page': page, 'page_size': 20, 'worker_active': active}


def get_report(store, owner, report_id):
    with store.db() as db:
        row = db.execute('SELECT * FROM reports WHERE id=? AND owner=?', (report_id, owner)).fetchone()
    if not row:
        raise ValueError('报告不存在或无权访问')
    return dict(row)


def subscribe(store, owner, business_user_id, data, subscription_id=None):
    if subscription_id:
        with store.db() as db:
            old = db.execute('SELECT * FROM report_subscriptions WHERE id=? AND owner=?', (subscription_id, owner)).fetchone()
        if not old:
            raise ValueError('订阅不存在或无权访问')
        data = {**{k: old[k] for k in ('kind', 'period', 'send_time', 'status')},
                'include_benchmarks': bool(old['include_benchmarks']), **data}
    kind, period = data.get('kind', 'general'), data.get('period', 'day')
    if not isinstance(kind, str) or not isinstance(period, str) or kind not in KINDS or period not in PERIODS or (kind == 'ground_briefing' and period != 'day'):
        raise ValueError('订阅类型或周期无效')
    clock, status = data.get('send_time', '08:00'), data.get('status', 'paused')
    if not isinstance(clock, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock):
        raise ValueError('发送时间必须为北京时间HH:MM')
    if not isinstance(status, str) or status not in ('active', 'paused') or type(data.get('include_benchmarks', False)) is not bool:
        raise ValueError('订阅状态或对标选项无效')
    sid = subscription_id or secrets.token_hex(16)
    with store.db() as db:
        if subscription_id and not db.execute('SELECT 1 FROM report_subscriptions WHERE id=? AND owner=?', (sid, owner)).fetchone():
            raise ValueError('订阅不存在或无权访问')
        db.execute('''INSERT INTO report_subscriptions
            (id,owner,business_user_id,kind,period,send_time,status,include_benchmarks,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            kind=excluded.kind,period=excluded.period,send_time=excluded.send_time,status=excluded.status,
            include_benchmarks=excluded.include_benchmarks,updated_at=excluded.updated_at''',
            (sid, owner, str(business_user_id), kind, period, clock, status, int(data.get('include_benchmarks', False)), now(), now()))
        store.log(db, owner, 'report.subscribe', sid, status)
    return sid


def schedule_due(store, instant=None):
    local = instant.astimezone(TZ) if instant else datetime.now(TZ)
    with store.db() as db:
        db.execute("INSERT INTO preferences VALUES ('report_worker_heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(time()),))
        rows = db.execute("SELECT * FROM report_subscriptions WHERE status='active'").fetchall()
        count = 0
        for row in rows:
            period = row['period']
            due_day = period == 'day' or (period == 'week' and local.weekday() == 0) or (
                local.day == 1 and (period == 'month' or period == 'quarter' and local.month in (1, 4, 7, 10)
                                   or period == 'year' and local.month == 1))
            if not due_day or local.strftime('%H:%M') < row['send_time']:
                continue
            start, end = period_bounds(period, local.date(), previous=True)
            key = f'{start}:{end}'
            if row['last_period'] == key:
                continue
            payload = {'kind': row['kind'], 'period': period, 'start_date': str(start), 'end_date': str(end),
                       'business_user_id': row['business_user_id'], 'include_benchmarks': bool(row['include_benchmarks'])}
            enqueue(store, row['owner'], payload, f"subscription:{row['id']}:{key}", row['id'], db)
            db.execute('UPDATE report_subscriptions SET last_period=? WHERE id=?', (key, row['id']))
            count += 1
    return count


def claim(store):
    with store.db() as db:
        db.execute("UPDATE reports SET status='queued' WHERE status='running' AND lease_until<? AND attempts<3", (time(),))
        db.execute("UPDATE reports SET status='failed',error='任务租约过期且已达重试上限' WHERE status='running' AND lease_until<? AND attempts>=3", (time(),))
        row = db.execute("SELECT * FROM reports WHERE status='queued' AND next_attempt<=? ORDER BY created_at,id LIMIT 1", (time(),)).fetchone()
        if not row:
            return None
        lease = time() + 300
        db.execute("UPDATE reports SET status='running',attempts=attempts+1,lease_until=?,updated_at=? WHERE id=?", (lease, now(), row['id']))
        value = dict(row)
        value['attempts'] += 1
        value['lease_until'] = lease
        return value


def authorize(user_id, owner):
    from app import core, business_access
    from fastapi import HTTPException
    if settings.data_source != 'ops_ask':
        raise PermissionError('报告中心仅在ops_ask数据源开放')
    conn = core.connect_db(identity=True)
    try:
        try:
            access = business_access.load_access(conn, user_id)
        except HTTPException as exc:
            raise PermissionError('报告账号已停用或无权访问') from exc
        if 'admin' not in access.roles or access.owner != owner:
            raise PermissionError('报告账号权限已变化')
    finally:
        conn.close()


def build_report(payload):
    from app import core, acdm_reports
    if payload.get('snapshot') is not None:
        return payload['snapshot']
    if payload['kind'] == 'query':
        return payload['snapshot']
    start, end = date.fromisoformat(payload['start_date']), date.fromisoformat(payload['end_date'])
    conn = core.connect_db(repeatable_read=True)
    try:
        cur = conn.cursor()
        try:
            cur.execute('SELECT MIN(exec_date),MAX(exec_date) FROM public.mv_acdm_dyn_flight_detail')
            first, last = cur.fetchone()
        finally:
            cur.close()
        if first is None or end < first or start > last:
            raise ValueError('报告期间超出本地数据范围，不能将未同步数据解释为零')
        if payload['kind'] == 'general':
            report = acdm_reports.build_general_report(conn, start_date=start, end_date=end, period_type=payload['period'])
        elif payload['kind'] == 'ground_briefing':
            report = acdm_reports.build_ground_briefing(conn, target_date=end)
        else:
            report = acdm_reports.build_safeguard_and_resource_board(conn, start_date=start, end_date=end)
        report['ontology_version'] = core.current_catalog().version
        report['generated_at'] = now()
        cur = conn.cursor()
        try:
            cur.execute('SELECT job_name,watermark,last_success_at FROM public.sync_job_state ORDER BY job_name')
            report['sync_watermarks'] = json_safe([{'job': row[0], 'watermark': row[1], 'last_success_at': row[2]} for row in cur.fetchall()])
        finally:
            cur.close()
        report['boundary'] = '仅反映本地已同步记录，日期范围与连续性不证明源端同步完整。'
        if start < first or end > last:
            report['boundary'] += f'请求期间部分超出本地观测日期{first}至{last}，相关统计仅覆盖已同步部分。'
        if payload.get('include_benchmarks'):
            compare = core.do_ask(conn, f'{start}至{end} 武汉、长沙、南京、郑州航班量对比', use_model=False, use_cache=False, day_mode='CALENDAR')
            report['benchmark'] = compare
            report['is_simulated'] = compare.get('is_simulated', False)
        return json_safe(report)
    finally:
        conn.close()


# SVG Icons for PDF report
SVG_ICONS = {
    'brand': '<svg class="report-brand-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/></svg>',
    'plane': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/></svg>',
    'check': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
    'clock': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    'flag': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" x2="4" y1="22" y2="15"/></svg>',
    'layers': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="22 17 12 22 2 17"/><polyline points="22 12 12 17 2 12"/></svg>',
    'users': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    'chart': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" x2="18" y1="20" y2="10"/><line x1="12" x2="12" y1="20" y2="4"/><line x1="6" x2="6" y1="20" y2="14"/></svg>',
    'alert': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" x2="12" y1="9" y2="13"/><line x1="12" x2="12.01" y1="17" y2="17"/></svg>',
    'shield': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>',
    'info': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="16" y2="12"/><line x1="12" x2="12.01" y1="8" y2="8"/></svg>',
    'compare': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M11 18H8a2 2 0 0 1-2-2V9"/></svg>',
    'table': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/><path d="M9 3v18"/><path d="M15 3v18"/></svg>',
    'calendar': '<svg class="report-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="4" rx="2" ry="2"/><line x1="16" x2="16" y1="2" y2="6"/><line x1="8" x2="8" y1="2" y2="6"/><line x1="3" x2="21" y1="10" y2="10"/></svg>',
    'bullet': '<svg class="claim-bullet-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
}

COLUMN_LABELS = {
    'load_factor_pax': '客座率样本旅客量',
    'load_factor_sample_flights': '有效座位样本架次',
    'load_factor_excluded_pax': '未匹配正数座位的旅客量',
    'total_seats': '有效座位数',
    'n': '参与记录数',
    'airline_code': '航司代码',
    'airline_two_char_cd': '航司代码',
    'airline_name': '航司名称',
    'dep_flights': '出港执行量',
    'normal_dep': '正常出港',
    'takeoff_on_time_rate': '起飞正常率 (%)',
    'release_on_time_rate': '放行正常率 (%)',
    'reason_code': '原因代码',
    'reason_name': '延误/取消原因',
    'delay_flights': '延误架次',
    'cancel_flights': '取消架次',
    'flight_no': '航班号',
    'craft_type': '机型',
    'craft_type_icao_cd': '机型',
    'ac_type': '机型',
    'craft_no': '机号',
    'reg_no': '机号',
    'stand': '机位',
    'craft_site_cd': '机位',
    'stand_code': '机位',
    'remote_stand': '远机位',
    'is_remote_stand': '远机位',
    'is_remote': '远机位',
    'outer_support_flag': '外场保障标识',
    'terminal': '航站楼',
    'gate_cd': '登机口',
    'gate': '登机口',
    'agency': '地服代理',
    'direction': '进出港',
    'is_off_in': '进出港',
    'plan_turnaround_min': '计划过站 (分)',
    'sched_turnaround_minutes': '计划过站 (分)',
    'node_name': '保障节点',
    'safeguard_code': '节点代码',
    'record_cnt': '录入条数',
    'record_rate': '录入占比 (%)',
    'bridge_flights': '靠桥架次',
    'bridge_hours': '靠桥时长 (小时)',
    'power_hours': '桥载电时长 (小时)',
    'air_hours': '桥载气时长 (小时)',
    'power_rate': '桥载电使用率 (%)',
    'air_rate': '桥载气使用率 (%)',
    'flight_task_cd': '任务代码',
    'flight_task_name': '任务属性',
    'attribute': '属性代码',
    'exec_date': '执行日期',
    'plan_day': '计划日期',
    'flight_date': '航班日期',
    'real_exec_day': '实际执行日',
    'arr_flights': '进港航班量',
    'total_flights': '航班总量',
    'normal_rate': '正常率 (%)',
    'airport_code': '机场代码',
    'airport_name': '机场名称',
    'flight_count': '航班量',
    'ontime_rate': '正常率 (%)',
    'avg_delay': '平均延误 (分)',
    'plan_time': '计划时刻',
    'scheme_takeoff_time': '计划起飞',
    'scheme_landin_time': '计划降落',
    'real_takeoff_time': '实际起飞',
    'real_landin_time': '实际降落',
    'clearance_delay_time': '放行延误 (分)',
    'takeoff_delay_time': '起飞延误 (分)',
    'delay_time': '延误时长 (分)',
    's_cnl_stauts': '取消时段/状态',
    'abnormal_reason_cd': '异常原因代码',
    'abnormal_flight_stat_cd': '异常状态代码',
    'ori_act': '早始发',
    'is_operated_flight': '实际执飞',
    'seat_cnt': '座位数',
    'pax_cnt': '旅客人数',
    'total_pax': '总旅客数',
    'cargo_weight': '货邮重量 (kg)',
    'alterate_by_bridge_time': '预计靠桥时间',
    'arr_exec_date': '进港执行日',
    'arr_flight_no': '进港航班',
    'dep_flight_no': '出港航班',
    'flight_id': '航班ID',
    'flt_id': '航班ID',
    'carrier': '承运人',
    'bridge_code': '登机桥编码',
    'bridge_name': '登机桥名称',
    'use_count': '使用次数',
    'avg_bridge_duration_min': '平均使用时长 (分)',
    'power_supply_cnt': '桥载电使用次数',
    'ac_supply_cnt': '桥载气使用次数',
    'total_records': '总记录数',
    'real_recorded_cnt': '实测条数',
    'alterate_cnt': '预计/预警数',
    'entry_rate_pct': '适用航班覆盖率 (%)',
    'recorded_flights': '已录入适用航班数',
    'eligible_flights': '当期适用执行航班数',
    'item': '对标项目',
    'value': '数值',
    'unit': '单位',
    'status': '状态',
    'source': '数据来源',
    'planned_today': '当日计划航班',
    'canceled_today': '当日取消航班',
    'operated_today': '当日实际执行',
    'est_before_24': '24点前预计执行',
    'late_00_05_window': '次日收尾航班',
    'planned_tomorrow': '次日计划航班',
    'early_origin_tomorrow': '次日早始发航班',
    'intl_reg_tomorrow': '次日国际/地区航班',
    'origin_remote_tomorrow': '次日早始发远机位',
    'mom_vs_today_pct': '计划环比增减 (%)',
    'all_dep_22': '22时全场出港量',
    'all_takeoff_norm_22': '22时全场起飞正常数',
    'all_release_norm_22': '22时全场放行正常数',
    'gh_dep_22': '22时地服出港量',
    'gh_takeoff_norm_22': '22时地服起飞正常数',
    'gh_release_norm_22': '22时地服放行正常数',
    'gh_release_delay_22': '22时地服放行延误数',
    'all_takeoff_rate_22': '22时全场起飞正常率 (%)',
    'all_release_rate_22': '22时全场放行正常率 (%)',
    'gh_takeoff_rate_22': '22时地服起飞正常率 (%)',
    'gh_release_rate_22': '22时地服放行正常率 (%)',
}


def _build_kpi_grid(report):
    summary = report.get('summary') or {}
    if not summary and report.get('main_rows') and isinstance(report['main_rows'], list) and len(report['main_rows']) == 1 and isinstance(report['main_rows'][0], dict):
        summary = report['main_rows'][0]
    kind = report.get('kind', '')
    plan = report.get('plan') or {}
    if not kind or kind == 'query':
        kind = plan.get('report_kind') or ('ground_briefing' if '地服' in str(plan.get('disp')) else '')
    cards = []

    if kind == 'general' or ('operated_all' in summary and 'release_on_time_rate' in summary):
        if summary.get('operated_all') is not None:
            cards.append({
                'label': '实际执行航班',
                'val': f"{summary['operated_all']:,}" if isinstance(summary['operated_all'], (int, float)) else str(summary['operated_all']),
                'unit': '架次',
                'sub': f"计划 {summary.get('planned_all', '—')} 班 · 执行率 {summary.get('execution_rate', '—')}%",
                'icon': 'plane',
                'tone': 'blue',
            })
        if summary.get('release_on_time_rate') is not None:
            cards.append({
                'label': '放行正常率',
                'val': f"{summary['release_on_time_rate']}%",
                'unit': '',
                'sub': f"正常出港 {summary.get('release_normal_cnt', '—')} / {summary.get('dep_operated', '—')} 班",
                'icon': 'check',
                'tone': 'emerald',
            })
        if summary.get('takeoff_on_time_rate') is not None:
            cards.append({
                'label': '起飞正常率',
                'val': f"{summary['takeoff_on_time_rate']}%",
                'unit': '',
                'sub': f"正常起飞 {summary.get('takeoff_normal_cnt', '—')} 架次",
                'icon': 'clock',
                'tone': 'teal',
            })
        if summary.get('origin_dep_on_time_rate') is not None:
            cards.append({
                'label': '始发正常率',
                'val': f"{summary['origin_dep_on_time_rate']}%",
                'unit': '',
                'sub': f"始发出港 {summary.get('origin_normal_cnt', '—')} / {summary.get('origin_dep_cnt', '—')} 班",
                'icon': 'flag',
                'tone': 'indigo',
            })
        if summary.get('bridge_rate_all') is not None or summary.get('bridge_rate_arr') is not None:
            rate = summary.get('bridge_rate_all') if summary.get('bridge_rate_all') is not None else summary.get('bridge_rate_arr')
            cards.append({
                'label': '综合靠桥率',
                'val': f"{rate}%",
                'unit': '',
                'sub': f"进港靠桥率 {summary.get('bridge_rate_arr', '—')}%",
                'icon': 'layers',
                'tone': 'amber',
            })
        if summary.get('total_pax_sql') is not None:
            cards.append({
                'label': '旅客吞吐量',
                'val': f"{summary['total_pax_sql']:,}" if isinstance(summary['total_pax_sql'], (int, float)) else str(summary['total_pax_sql']),
                'unit': '人次',
                'sub': f"有效座位样本客座率 {summary.get('total_load_factor', '—')}%",
                'icon': 'users',
                'tone': 'purple',
            })
    elif kind == 'ground_briefing' or ('planned_today' in summary and 'operated_today' in summary):
        op = summary.get('operated_today') or summary.get('planned_today')
        cards.append({
            'label': '当日计划/执行',
            'val': f"{op:,}" if isinstance(op, (int, float)) else str(op),
            'unit': '架次',
            'sub': f"计划 {summary.get('planned_today', '—')} 班 · 取消 {summary.get('canceled_today', 0)} 班",
            'icon': 'plane',
            'tone': 'blue',
        })
        if summary.get('all_release_rate_22') is not None:
            cards.append({
                'label': '出港放行正常率',
                'val': f"{summary['all_release_rate_22']}%",
                'unit': '',
                'sub': f"正常出港 {summary.get('all_release_norm_22', '—')} / {summary.get('all_dep_22', '—')} 班",
                'icon': 'check',
                'tone': 'emerald',
            })
        if summary.get('all_takeoff_rate_22') is not None:
            cards.append({
                'label': '出港起飞正常率',
                'val': f"{summary['all_takeoff_rate_22']}%",
                'unit': '',
                'sub': f"正常起飞 {summary.get('all_takeoff_norm_22', '—')} / {summary.get('all_dep_22', '—')} 架次",
                'icon': 'clock',
                'tone': 'teal',
            })
        if summary.get('gh_release_rate_22') is not None:
            cards.append({
                'label': '地服代理放行率',
                'val': f"{summary['gh_release_rate_22']}%",
                'unit': '',
                'sub': f"放行正常 {summary.get('gh_release_norm_22', '—')} / {summary.get('gh_dep_22', '—')} 架次 · 延误 {summary.get('gh_release_delay_22', '—')} 班",
                'icon': 'shield',
                'tone': 'indigo',
            })
        if summary.get('gh_takeoff_rate_22') is not None:
            cards.append({
                'label': '地服代理起飞率',
                'val': f"{summary['gh_takeoff_rate_22']}%",
                'unit': '',
                'sub': f"起飞正常 {summary.get('gh_takeoff_norm_22', '—')} / 代理出港 {summary.get('gh_dep_22', '—')} 班",
                'icon': 'flag',
                'tone': 'purple',
            })
        if summary.get('early_origin_tomorrow') is not None:
            cards.append({
                'label': '次日早始发准备',
                'val': f"{summary['early_origin_tomorrow']} 班",
                'unit': '',
                'sub': f"远机位外场 {summary.get('origin_remote_tomorrow', '—')} 班 · 次日计划 {summary.get('planned_tomorrow', '—')} 班",
                'icon': 'calendar',
                'tone': 'amber',
            })
    elif kind == 'safeguard_board' or report.get('total'):
        tot = report.get('total')
        if tot:
            cards.append({
                'label': '执飞航班总量',
                'val': f"{tot:,}" if isinstance(tot, (int, float)) else str(tot),
                'unit': '架次',
                'sub': '本期执行航班覆盖总量',
                'icon': 'plane',
                'tone': 'blue',
            })
        rows = report.get('rows', [])
        if rows and isinstance(rows, list) and len(rows) > 0 and isinstance(rows[0], dict) and 'node_name' in rows[0]:
            total_records = sum(r.get('record_cnt', 0) for r in rows if isinstance(r.get('record_cnt'), (int, float)))
            if total_records:
                cards.append({
                    'label': '保障节点录入总数',
                    'val': f"{total_records:,}",
                    'unit': '条',
                    'sub': f"已覆盖 {len(rows)} 类核心保障节点",
                    'icon': 'shield',
                    'tone': 'teal',
                })

    # If cards empty, extract from visual_meta stats
    if not cards:
        vm = report.get('visual_meta') or {}
        vm_stats = vm.get('stats') or []
        if vm_stats:
            tones = ['blue', 'emerald', 'teal', 'indigo', 'amber', 'purple']
            icons = ['plane', 'check', 'clock', 'shield', 'calendar', 'layers']
            for i, s in enumerate(vm_stats):
                cards.append({
                    'label': s.get('label', ''),
                    'val': str(s.get('value', '—')),
                    'unit': s.get('unit', ''),
                    'sub': s.get('sub', '核心指标口径'),
                    'icon': icons[i % len(icons)],
                    'tone': tones[i % len(tones)],
                })

    if not cards:
        return ''

    html_cards = []
    for c in cards:
        ic = SVG_ICONS.get(c['icon'], SVG_ICONS['chart'])
        unit_html = f'<span class="kpi-unit">{escape(c["unit"])}</span>' if c['unit'] else ''
        html_cards.append(f'''
        <div class="kpi-card tone-{c['tone']}">
          <div class="kpi-icon-wrap">{ic}</div>
          <div class="kpi-body">
            <div class="kpi-label">{escape(c['label'])}</div>
            <div class="kpi-val-row">
              <span class="kpi-val">{escape(c['val'])}</span>
              {unit_html}
            </div>
            <div class="kpi-sub">{escape(c['sub'])}</div>
          </div>
        </div>
        ''')
    return f'<div class="kpi-grid">{"".join(html_cards)}</div>'


def _pick_section_icon(title):
    t = title or ''
    if any(k in t for k in ('1.', '总体', 'KPI', '指标')):
        return SVG_ICONS['chart']
    if any(k in t for k in ('2.', '结构', '概况', '航班')):
        return SVG_ICONS['plane']
    if any(k in t for k in ('3.', '过站', '效率', '正常率')):
        return SVG_ICONS['clock']
    if any(k in t for k in ('4.', '延误', '不正常', '取消', '原因')):
        return SVG_ICONS['alert']
    if any(k in t for k in ('5', '6', '靠桥', '高峰', '机位')):
        return SVG_ICONS['layers']
    if any(k in t for k in ('7', '8', '协同', '滑行', '保障', '节点')):
        return SVG_ICONS['shield']
    if any(k in t for k in ('边界', '标注', '缺口', '口径')):
        return SVG_ICONS['info']
    return SVG_ICONS['chart']


def html_report(report, title):
    """Calibrated civil aviation executive report markup; all data and database strings escaped."""
    esc = lambda value: escape('是' if value is True else '否' if value is False else '—' if value is None else str(value))

    plan = report.get('plan') or {}
    disp = plan.get('disp')
    start_d = plan.get('report_start') or report.get('target_date') or ''
    if disp:
        display_title = f"{disp}（{start_d}）" if start_d else disp
    else:
        doc_title = (report.get('document') or report.get('answer_document') or {}).get('title')
        if doc_title:
            display_title = doc_title.replace('《', '').replace('》', '').strip()
        elif title:
            display_title = re.sub(r'^(?:生成|请|导出|查询|出一份)\s*', '', title).strip()
        else:
            display_title = "运行分析简报"

    gen_raw = str(report.get('generated_at') or now())
    m_gen = re.match(r'^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})', gen_raw)
    gen_display = f"{m_gen.group(1)} {m_gen.group(2)}" if m_gen else gen_raw[:16]

    if not report.get('summary') and report.get('main_rows') and len(report['main_rows']) == 1 and isinstance(report['main_rows'][0], dict):
        report['summary'] = report['main_rows'][0]
    if not report.get('kind') or report.get('kind') == 'query':
        report['kind'] = (report.get('plan') or {}).get('report_kind') or report.get('kind')

    def format_cell_html(k, val):
        if val is None or val == '' or val == '—':
            return '<span class="cell-empty">—</span>'
        val_str = str(val).strip()
        k_lower = k.lower()
        m_time = re.match(r'^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2})?(?:\+\d{2}:\d{2})?$', val_str)
        if m_time:
            return f'<span class="cell-time">{m_time.group(1)}</span>'
        if k_lower in ('outer_support_flag', 'out_support_flag'):
            if '是' in val_str or '外' in val_str:
                return '<span class="badge-pill tone-amber">外场保障</span>'
            if '否' in val_str or '近' in val_str:
                return '<span class="badge-pill tone-green">近机位</span>'
        if k_lower in ('is_remote_stand', 'is_remote', 'remote_stand'):
            if val_str in ('Y', '是', '1', 'true', 'True'):
                return '<span class="badge-pill tone-amber">远机位</span>'
            if val_str in ('N', '否', '0', 'false', 'False'):
                return '<span class="badge-pill tone-green">近机位</span>'
        if k_lower in ('direction', 'is_off_in'):
            if val_str in ('ARR', '进港', 'A'):
                return '<span class="badge-pill tone-blue">进港</span>'
            if val_str in ('DEP', '出港', 'D'):
                return '<span class="badge-pill tone-teal">出港</span>'
        if k_lower == 'terminal' and val_str:
            return f'<span class="badge-pill tone-slate">{esc(val_str)}</span>'
        if k_lower == 'agency':
            if val_str == '210':
                return '<span class="badge-pill tone-blue">地服(210)</span>'
            return f'<span class="badge-pill tone-slate">{esc(val_str)}</span>'
        if k_lower == 's_cnl_stauts':
            if '未填' in val_str:
                return '<span class="badge-pill tone-slate">未填时段</span>'
            return f'<span class="badge-pill tone-red">{esc(val_str)} 取消</span>'
        if k_lower == 'abnormal_reason_cd':
            if val_str in ('未填原因', '—', 'None'):
                return '<span class="cell-empty">未填原因</span>'
            return f'<span class="badge-pill tone-purple">{esc(val_str)}</span>'
        if k_lower in ('clearance_delay_time', 'takeoff_delay_time', 'delay_time'):
            try:
                num = float(val_str)
                if num > 0:
                    return f'<span class="cell-delay">+{int(num) if num.is_integer() else num}m</span>'
                return '<span class="cell-zero">0m</span>'
            except Exception:
                pass
        if k_lower in ('flight_no', 'arr_flight_no', 'dep_flight_no'):
            return f'<span class="cell-flt">{esc(val_str)}</span>'
        if isinstance(val, float):
            return f"{round(val, 2):g}"
        if isinstance(val, (dict, list)):
            return esc(json.dumps(val, ensure_ascii=False))
        return esc(val_str)

    def table(rows, labels=None):
        if not rows:
            return '<p class="empty-hint">当前期间无匹配记录。</p>'
        columns = list(dict.fromkeys(k for row in rows for k in row))
        dict_labels = {**COLUMN_LABELS, 'n': '参与记录数', **(labels or {})}

        def get_col_label(col):
            if col in dict_labels:
                return dict_labels[col]
            if col.lower() in dict_labels:
                return dict_labels[col.lower()]
            if col in COLUMN_LABELS:
                return COLUMN_LABELS[col]
            if col.lower() in COLUMN_LABELS:
                return COLUMN_LABELS[col.lower()]
            return col

        chunks = [columns] if len(columns) <= 12 else [columns[i:i + 10] for i in range(0, len(columns), 10)]
        out = []
        for c_idx, chunk in enumerate(chunks):
            sub_caption = f'<div class="table-sub-caption">第 {c_idx + 1} 组字段（共 {len(columns)} 列）</div>' if len(chunks) > 1 else ''
            thead_th = ''.join('<th>' + esc(get_col_label(k)) + '</th>' for k in chunk)
            tbody_tr = []
            for r_idx, row in enumerate(rows):
                tds = []
                for k in chunk:
                    val = row.get(k)
                    is_num = isinstance(val, (int, float)) and not isinstance(val, bool) and 'time' not in k.lower()
                    cls = ' class="num-cell"' if is_num else ''
                    tds.append(f'<td{cls}>{format_cell_html(k, val)}</td>')
                tbody_tr.append(f'<tr><td class="idx-cell">{r_idx + 1}</td>' + ''.join(tds) + '</tr>')
            out.append(f'{sub_caption}<div class="table-wrap"><table class="data-table"><thead><tr><th class="idx-col">序号</th>{thead_th}</tr></thead><tbody>{"".join(tbody_tr)}</tbody></table></div>')
        return ''.join(out)

    warning = (
        '<div class="warning-banner">'
        f'<div class="warning-icon-box">{SVG_ICONS["alert"]}</div>'
        '<div class="warning-content"><strong>测试与模拟数据提示</strong><p>包含模拟测试数据，不代表长沙、南京、郑州机场实际运行，不可用于实际机场排名。</p></div>'
        '</div>'
    ) if report.get('is_simulated') else ''

    kpi_grid = _build_kpi_grid(report)

    doc = report.get('document') or report.get('answer_document') or {}
    sections = doc.get('sections', [])
    if not sections and report.get('answer'):
        raw_ans = report['answer']
        matches = list(re.finditer(r'【(\d+[.、\–\-][^】]+)】([\s\S]*?)(?=(?:【\d+[.、\–\-]|$))', raw_ans))
        if matches:
            sections = []
            for m in matches:
                sec_t = m.group(1).strip()
                sec_c = m.group(2).strip()
                claims = [{'text': line.strip()} for line in sec_c.split('\n') if line.strip()]
                sections.append({'title': sec_t, 'claims': claims})

    claims_map = {}
    for i, c in enumerate(report.get('claims', []), 1):
        cid = c.get('id') or f'C{i}'
        claims_map[cid] = c

    body = ''
    if sections:
        for s in sections:
            sec_icon = _pick_section_icon(s.get('title'))
            sec_claims = s.get('claims') or []
            if not sec_claims and s.get('claim_ids'):
                sec_claims = [claims_map[cid] for cid in s['claim_ids'] if cid in claims_map]
            claims_html = ''.join(
                f'<div class="claim-item"><div class="claim-bullet">{SVG_ICONS["bullet"]}</div><div class="claim-text">{esc(c.get("text", ""))}</div></div>'
                for c in sec_claims if c and c.get('text')
            )
            if claims_html:
                body += f'<section class="report-section"><h2 class="section-title"><span class="section-icon-box">{sec_icon}</span><span>{esc(s["title"])}</span></h2><div class="claim-list">{claims_html}</div></section>'

    if not body:
        body = '<div class="answer-card"><div class="answer-text">' + esc(report.get('answer') or report.get('text', '')) + '</div></div>'

    for item in report.get('sections', []):
        sub = item['result']
        labels = {k: v.get('label', k) for k, v in sub.get('field_meta', {}).get(sub['parse']['subject'], {}).items()}
        metric = sub.get('metric') or {}
        if metric.get('field'):
            labels[metric['field']] = metric.get('name') or metric['field']
        sim_badge = '<span class="sim-badge">模拟测试数据</span>' if item.get('is_simulated') else ''
        body += f'<section class="report-section table-section"><h2 class="section-title"><span class="section-icon-box">{SVG_ICONS["table"]}</span><span>{esc(item["label"])}</span>{sim_badge}</h2>' + table(sub.get('main_rows', []), labels) + '</section>'

    benchmark = report.get('benchmark')
    if benchmark:
        body += (
            f'<section class="report-section benchmark-section">'
            f'<h2 class="section-title"><span class="section-icon-box">{SVG_ICONS["compare"]}</span><span>机场对标分析</span><span class="sim-badge">包含模拟测试数据</span></h2>'
            f'<div class="benchmark-answer"><div class="benchmark-lead">{SVG_ICONS["info"]} 对标结论说明</div><div class="answer-text">{esc(benchmark.get("answer", ""))}</div></div>'
            + table(benchmark.get('main_rows', []))
            + '</section>'
        )

    for item in report.get('related', []):
        count_meta = f'<span class="table-meta-badge">共 {item["count"]} 条记录 · 展示前 {len(item.get("rows", []))} 条</span>' if item.get('count', 0) > len(item.get("rows", [])) else f'<span class="table-meta-badge">共 {len(item.get("rows", []))} 条记录</span>'
        note_text = f'<div class="table-note">{esc(item["note"])}</div>' if item.get('note') else ''
        body += f'<section class="report-section table-section"><div class="table-title-row"><h2 class="section-title"><span class="section-icon-box">{SVG_ICONS["table"]}</span><span>{esc(item.get("name") or item["table"])}</span></h2>{count_meta}</div>{note_text}' + table(item.get('rows', [])) + '</section>'

    rows_to_render = report.get('main_rows') or report.get('rows') or []
    if rows_to_render and (not report.get('sections') or report.get('intent') == 'detail'):
        subj = (report.get('parse') or {}).get('subject') or (report.get('plan') or {}).get('subject') or ''
        labels = {}
        if report.get('field_meta') and subj in report['field_meta']:
            labels = {k: v.get('label', k) for k, v in report['field_meta'][subj].items()}
        metric = report.get('metric') or {}
        if metric.get('field'):
            labels[metric['field']] = metric.get('name') or metric['field']
        table_title = (report.get('plan') or {}).get('disp') or ('查询明细记录' if report.get('intent') == 'detail' else '数据明细')
        body += f'<section class="report-section table-section"><h2 class="section-title"><span class="section-icon-box">{SVG_ICONS["table"]}</span><span>{esc(table_title)}</span></h2>' + table(rows_to_render, labels) + '</section>'
        if report.get('intent') == 'detail' and report.get('main_total', 0) > len(rows_to_render):
            body += f'<p class="meta" style="margin-top: 8px; color: #64748b; font-size: 10px;">文件包含当前结果快照提供的 {len(rows_to_render)} 条记录，全部 {report.get("main_total", 0)} 条明细请在系统工作台分页查看。</p>'

    boundary_notice = esc(report.get('boundary', '本报告只反映本地已同步记录，请结合来源口径核验。'))
    ontology_str = esc(report.get('ontology_version', ''))
    short_ont = ontology_str[:12] if len(ontology_str) > 12 else ontology_str

    watermarks = report.get('sync_watermarks', [])
    watermark_html = ''
    if watermarks and isinstance(watermarks, list):
        chips = []
        for w in watermarks[:4]:
            job_name = esc(w.get('job', ''))
            wm_val = esc(str(w.get('watermark') or ''))
            chips.append(f'<span class="wm-chip"><strong>{job_name}</strong> {wm_val}</span>')
        if chips:
            watermark_html = f'<div class="wm-strip"><span class="wm-title">同步水位：</span>{" ".join(chips)}</div>'

    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 14mm 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    font: 11px/1.65 "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", -apple-system, sans-serif;
    color: #1e293b;
    background: #ffffff;
    margin: 0;
    padding: 0;
  }}
  .report-header {{
    border-bottom: 2px solid #0f172a;
    padding-bottom: 12px;
    margin-bottom: 14px;
  }}
  .brand-bar {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }}
  .brand-badge {{
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: linear-gradient(135deg, #0f172a, #1e293b);
    display: flex;
    align-items: center;
    justify-content: center;
    color: #38bdf8;
    box-shadow: 0 2px 6px rgba(15, 23, 42, 0.18);
  }}
  .report-brand-icon {{
    width: 18px;
    height: 18px;
  }}
  .brand-text {{
    flex: 1;
  }}
  .brand-org {{
    display: block;
    font-size: 11.5px;
    font-weight: 700;
    color: #0f172a;
    letter-spacing: 0.3px;
  }}
  .brand-sub {{
    display: block;
    font-size: 9px;
    color: #64748b;
    margin-top: 1px;
  }}
  .report-tag {{
    font-size: 9px;
    font-weight: 600;
    color: #0369a1;
    background: #e0f2fe;
    border: 1px solid #bae6fd;
    padding: 2px 7px;
    border-radius: 4px;
    text-transform: uppercase;
  }}
  .report-title {{
    font-size: 20px;
    font-weight: 700;
    color: #0f172a;
    margin: 6px 0 8px;
    line-height: 1.35;
    letter-spacing: -0.3px;
  }}
  .meta-strip {{
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px 10px;
    font-size: 9px;
    color: #64748b;
  }}
  .meta-item {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }}
  .meta-divider {{
    color: #cbd5e1;
  }}
  .report-icon {{
    width: 13px;
    height: 13px;
    vertical-align: -2px;
  }}
  .warning-banner {{
    display: flex;
    align-items: flex-start;
    gap: 10px;
    border: 1px solid #fde68a;
    border-left: 4px solid #d97706;
    background: #fffbeb;
    border-radius: 6px;
    padding: 9px 12px;
    margin: 12px 0;
    color: #92400e;
    font-size: 10px;
    line-height: 1.55;
  }}
  .warning-icon-box {{
    color: #d97706;
    margin-top: 1px;
    flex-shrink: 0;
  }}
  .warning-icon-box .report-icon {{
    width: 16px;
    height: 16px;
  }}
  .warning-content strong {{
    display: block;
    font-size: 10.5px;
    font-weight: 700;
    margin-bottom: 2px;
  }}
  .warning-content p {{
    margin: 0;
  }}
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin: 14px 0 16px;
    break-inside: avoid;
  }}
  .kpi-card {{
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    background: #ffffff;
    padding: 9px 11px;
    display: flex;
    align-items: flex-start;
    gap: 9px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
    position: relative;
    overflow: hidden;
  }}
  .kpi-card::before {{
    content: "";
    position: absolute;
    top: 0;
    left: 0;
    width: 3px;
    height: 100%;
  }}
  .kpi-card.tone-blue::before {{ background: #0284c7; }}
  .kpi-card.tone-emerald::before {{ background: #059669; }}
  .kpi-card.tone-teal::before {{ background: #0d9488; }}
  .kpi-card.tone-indigo::before {{ background: #4f46e5; }}
  .kpi-card.tone-amber::before {{ background: #d97706; }}
  .kpi-card.tone-purple::before {{ background: #7c3aed; }}
  .kpi-icon-wrap {{
    width: 26px;
    height: 26px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }}
  .tone-blue .kpi-icon-wrap {{ background: #f0f9ff; color: #0284c7; }}
  .tone-emerald .kpi-icon-wrap {{ background: #ecfdf5; color: #059669; }}
  .tone-teal .kpi-icon-wrap {{ background: #f0fdfa; color: #0d9488; }}
  .tone-indigo .kpi-icon-wrap {{ background: #eef2ff; color: #4f46e5; }}
  .tone-amber .kpi-icon-wrap {{ background: #fffbeb; color: #d97706; }}
  .tone-purple .kpi-icon-wrap {{ background: #faf5ff; color: #7c3aed; }}
  .kpi-icon-wrap .report-icon {{ width: 14px; height: 14px; }}
  .kpi-body {{ flex: 1; min-width: 0; }}
  .kpi-label {{
    font-size: 9px;
    color: #64748b;
    font-weight: 500;
    margin-bottom: 2px;
  }}
  .kpi-val-row {{
    display: flex;
    align-items: baseline;
    gap: 4px;
  }}
  .kpi-val {{
    font-size: 16px;
    font-weight: 700;
    color: #0f172a;
    letter-spacing: -0.4px;
    font-variant-numeric: tabular-nums;
  }}
  .kpi-unit {{
    font-size: 9px;
    color: #64748b;
    font-weight: 500;
  }}
  .kpi-sub {{
    font-size: 8.5px;
    color: #94a3b8;
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .report-section {{
    margin-top: 14px;
    break-inside: avoid;
  }}
  .section-title {{
    font-size: 12px;
    font-weight: 700;
    color: #0f172a;
    display: flex;
    align-items: center;
    gap: 7px;
    border-bottom: 1.5px solid #e2e8f0;
    padding-bottom: 5px;
    margin: 0 0 8px;
    break-after: avoid;
  }}
  .section-icon-box {{
    width: 20px;
    height: 20px;
    border-radius: 4px;
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: #475569;
  }}
  .section-icon-box .report-icon {{
    width: 12px;
    height: 12px;
  }}
  .claim-list {{
    display: grid;
    gap: 5px;
    margin: 6px 0;
  }}
  .claim-item {{
    display: flex;
    align-items: flex-start;
    gap: 7px;
    font-size: 10px;
    line-height: 1.65;
    color: #334155;
    background: #f8fafc;
    border: 1px solid #f1f5f9;
    border-radius: 6px;
    padding: 6px 9px;
  }}
  .claim-bullet {{
    color: #059669;
    margin-top: 2px;
    flex-shrink: 0;
  }}
  .claim-bullet-icon {{
    width: 11px;
    height: 11px;
  }}
  .claim-text {{
    flex: 1;
    overflow-wrap: anywhere;
  }}
  .sim-badge {{
    font-size: 8.5px;
    font-weight: 600;
    color: #b45309;
    background: #fef3c7;
    border: 1px solid #fde68a;
    padding: 1px 5px;
    border-radius: 3px;
    margin-left: 6px;
  }}
  .table-title-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 4px;
    break-after: avoid;
  }}
  .table-meta-badge {{
    font-size: 8.5px;
    color: #64748b;
    background: #f1f5f9;
    padding: 2px 6px;
    border-radius: 4px;
  }}
  .table-note {{
    font-size: 9px;
    color: #64748b;
    margin-bottom: 6px;
  }}
  .table-sub-caption {{
    font-size: 8.5px;
    color: #64748b;
    margin: 5px 0 2px;
    font-weight: 500;
  }}
  .table-wrap {{
    margin: 6px 0 12px;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
    break-inside: avoid;
  }}
  .data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 8.5px;
    table-layout: auto;
    background: #ffffff;
  }}
  .data-table th {{
    background: #0f172a;
    color: #f8fafc;
    font-weight: 600;
    font-size: 8px;
    letter-spacing: 0.2px;
    text-align: left;
    border: 1px solid #1e293b;
    padding: 5px 6px;
    white-space: nowrap;
  }}
  .data-table th.idx-col {{
    width: 28px;
    text-align: center;
  }}
  .data-table td {{
    border: 1px solid #e2e8f0;
    padding: 4.5px 6px;
    vertical-align: middle;
    font-size: 8.5px;
    line-height: 1.4;
    overflow-wrap: break-word;
  }}
  .data-table tbody tr:nth-child(even) {{
    background: #f8fafc;
  }}
  .data-table tbody tr:hover {{
    background: #f1f5f9;
  }}
  .idx-col {{
    width: 28px;
    text-align: center !important;
  }}
  .idx-cell {{
    text-align: center;
    color: #94a3b8;
    font-weight: 500;
    font-size: 8px;
  }}
  .num-cell {{
    text-align: right;
    font-variant-numeric: tabular-nums;
  }}
  .cell-flt {{
    font-weight: 700;
    color: #0f172a;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 9px;
  }}
  .cell-time {{
    font-weight: 600;
    color: #0369a1;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 8.5px;
  }}
  .cell-delay {{
    font-weight: 700;
    color: #dc2626;
    font-variant-numeric: tabular-nums;
  }}
  .cell-zero {{
    color: #94a3b8;
    font-variant-numeric: tabular-nums;
  }}
  .cell-empty {{
    color: #cbd5e1;
  }}
  .badge-pill {{
    display: inline-block;
    padding: 1px 5px;
    border-radius: 9999px;
    font-size: 7.5px;
    font-weight: 600;
    line-height: 1.3;
    white-space: nowrap;
  }}
  .badge-pill.tone-green {{
    background: #ecfdf5;
    color: #059669;
    border: 1px solid #a7f3d0;
  }}
  .badge-pill.tone-amber {{
    background: #fffbeb;
    color: #d97706;
    border: 1px solid #fde68a;
  }}
  .badge-pill.tone-blue {{
    background: #f0f9ff;
    color: #0284c7;
    border: 1px solid #bae6fd;
  }}
  .badge-pill.tone-teal {{
    background: #f0fdfa;
    color: #0d9488;
    border: 1px solid #99f6e4;
  }}
  .badge-pill.tone-indigo {{
    background: #eef2ff;
    color: #4f46e5;
    border: 1px solid #c7d2fe;
  }}
  .badge-pill.tone-purple {{
    background: #faf5ff;
    color: #7c3aed;
    border: 1px solid #e9d5ff;
  }}
  .badge-pill.tone-red {{
    background: #fef2f2;
    color: #dc2626;
    border: 1px solid #fecaca;
  }}
  .badge-pill.tone-slate {{
    background: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
  }}
  .answer-card {{
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 10px 12px;
    margin: 8px 0;
  }}
  .answer-text {{
    font-size: 10px;
    line-height: 1.7;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    color: #1e293b;
  }}
  .benchmark-section {{
    background: #fcfcfd;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 10px 12px;
    margin-top: 14px;
  }}
  .benchmark-lead {{
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 9.5px;
    font-weight: 600;
    color: #0369a1;
    margin-bottom: 4px;
  }}
  .boundary-card {{
    margin-top: 18px;
    border-top: 1.5px solid #cbd5e1;
    padding-top: 10px;
    display: flex;
    align-items: flex-start;
    gap: 8px;
    color: #64748b;
    font-size: 9px;
    line-height: 1.6;
    break-inside: avoid;
  }}
  .boundary-icon-box {{
    color: #475569;
    margin-top: 1px;
    flex-shrink: 0;
  }}
  .boundary-icon-box .report-icon {{
    width: 14px;
    height: 14px;
  }}
  .boundary-text strong {{
    display: block;
    color: #334155;
    font-weight: 600;
    margin-bottom: 2px;
  }}
  .boundary-text p {{
    margin: 0;
  }}
  .wm-strip {{
    margin-top: 6px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 6px;
    font-size: 8.5px;
  }}
  .wm-title {{
    color: #94a3b8;
  }}
  .wm-chip {{
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
    padding: 1px 5px;
    border-radius: 3px;
    color: #475569;
  }}
  .wm-chip strong {{
    color: #1e293b;
    font-weight: 600;
  }}
  .empty-hint {{
    font-size: 9.5px;
    color: #94a3b8;
    padding: 6px 0;
    margin: 4px 0;
  }}
  .meta {{
    color: #64748b;
    font-size: 9px;
    margin-top: 6px;
  }}
</style>
</head><body>
<header class="report-header">
  <div class="brand-bar">
    <div class="brand-badge">{SVG_ICONS['brand']}</div>
    <div class="brand-text">
      <span class="brand-org">湖北机场集团 · 武汉天河国际机场 (WUH)</span>
      <span class="brand-sub">A-CDM 运行协同决策系统 · 智能运营分析简报</span>
    </div>
    <div class="report-tag">官方运营简报</div>
  </div>
  <h1 class="report-title">{esc(display_title)}</h1>
  <div class="meta-strip">
    <span class="meta-item">{SVG_ICONS['calendar']} 生成时间：{esc(gen_display)}</span>
    <span class="meta-divider">·</span>
    <span class="meta-item">{SVG_ICONS['shield']} 本体版本：{short_ont}</span>
    <span class="meta-divider">·</span>
    <span class="meta-item">{SVG_ICONS['info']} 观测范围：{esc(report.get('boundary', '已同步数据窗口')[:30])}</span>
  </div>
</header>
{warning}
{kpi_grid}
{body}
<div class="boundary-card">
  <div class="boundary-icon-box">{SVG_ICONS['shield']}</div>
  <div class="boundary-text">
    <strong>数据同步范围与合规声明</strong>
    <p>{boundary_notice}</p>
    {watermark_html}
  </div>
</div>
</body></html>'''


def render_pdf(html):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content(html, wait_until='load', timeout=30000)
            page.evaluate('document.fonts.ready')
            return page.pdf(format='A4', print_background=True, display_header_footer=True,
                            header_template='<span></span>', footer_template='<div style="width:100%;text-align:center;font-size:8px;color:#526874"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                            margin={'top': '18mm', 'bottom': '18mm', 'left': '14mm', 'right': '14mm'})
        finally:
            browser.close()


def process_one(store, authorize_fn=authorize, build_fn=build_report, render_fn=render_pdf):
    job = claim(store)
    if not job:
        return False
    if job.get('subscription_id'):
        with store.db() as db:
            active = db.execute("SELECT 1 FROM report_subscriptions WHERE id=? AND owner=? AND status='active'", (job['subscription_id'], job['owner'])).fetchone()
            if not active:
                db.execute("UPDATE reports SET status='cancelled',error='订阅已暂停或删除',lease_until=0,updated_at=? WHERE id=?", (now(), job['id']))
                return True
    path = None
    try:
        payload = json.loads(store.vault.open(job['payload']))
        authorize_fn(payload['business_user_id'], job['owner'])
        result = build_fn(payload)
        pdf = render_fn(html_report(result, job['title']))
        if not pdf.startswith(b'%PDF-'):
            raise ValueError('PDF生成结果无效')
        authorize_fn(payload['business_user_id'], job['owner'])
        directory = store.directory / 'reports'
        directory.mkdir(exist_ok=True)
        path = directory / (job['id'] + '.' + secrets.token_hex(4) + '.pdf.enc')
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(store.vault.cipher.encrypt(pdf))
        temporary.replace(path)
        with store.db() as db:
            changed = db.execute("UPDATE reports SET status='completed',result=?,pdf_path=?,error=NULL,lease_until=0,updated_at=? WHERE id=? AND status='running' AND attempts=? AND lease_until=?", (
                store.vault.seal(json.dumps(result, ensure_ascii=False, default=str)), path.name, now(), job['id'], job['attempts'], job['lease_until'])).rowcount
            if not changed:
                path.unlink(missing_ok=True)
                return True
            if job.get('subscription_id') and db.execute("SELECT 1 FROM report_subscriptions WHERE id=? AND owner=? AND status='active'", (job['subscription_id'], job['owner'])).fetchone():
                db.execute('INSERT OR IGNORE INTO notifications VALUES (?,?,?,?,?,?)',
                           (secrets.token_hex(16), job['owner'], job['id'], job['title'], 0, now()))
            store.log(db, job['owner'], 'report.complete', job['id'])
    except Exception as exc:
        if path:
            path.unlink(missing_ok=True)
        logger.exception('报告生成失败：%s', job['id'])
        forbidden = isinstance(exc, PermissionError)
        message = str(exc)[:200] if isinstance(exc, (ValueError, PermissionError)) else '报告生成失败，请检查浏览器安装、数据库连接及后台日志'
        retry = job['attempts'] < 3 and not forbidden and not isinstance(exc, ValueError)
        with store.db() as db:
            db.execute("UPDATE reports SET status=?,error=?,lease_until=0,next_attempt=?,updated_at=? WHERE id=? AND status='running' AND attempts=? AND lease_until=?",
                       ('queued' if retry else 'failed', message, time() + 30 * job['attempts'], now(), job['id'], job['attempts'], job['lease_until']))
            if forbidden and job.get('subscription_id'):
                db.execute("UPDATE report_subscriptions SET status='paused',updated_at=? WHERE id=?", (now(), job['subscription_id']))
    return True


def cleanup(store):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=store.preferences().get('retention_days', 90))).isoformat()
    with store.db() as db:
        rows = db.execute("SELECT id,pdf_path FROM reports WHERE updated_at<? AND status IN ('completed','failed','cancelled')", (cutoff,)).fetchall()
        for row in rows:
            if row['pdf_path']:
                path = store.directory / 'reports' / row['pdf_path']
                if path.parent.resolve() == (store.directory / 'reports').resolve():
                    path.unlink(missing_ok=True)
            db.execute('DELETE FROM reports WHERE id=?', (row['id'],))
    return len(rows)
