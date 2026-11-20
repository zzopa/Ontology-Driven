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
        report['start_date'], report['end_date'] = start.isoformat(), end.isoformat()
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


def _report_metrics(report):
    summary = report.get('summary') or {}
    if not summary and report.get('main_rows') and isinstance(report['main_rows'], list) and len(report['main_rows']) == 1 and isinstance(report['main_rows'][0], dict):
        summary = report['main_rows'][0]
    kind = report.get('kind', '')
    plan = report.get('plan') or {}
    if not kind or kind == 'query':
        kind = plan.get('report_kind') or ('ground_briefing' if '地服' in str(plan.get('disp')) else '')
    metrics = []

    if kind == 'general' or ('operated_all' in summary and 'release_on_time_rate' in summary):
        if summary.get('operated_all') is not None:
            metrics.append({
                'label': '实际执行航班',
                'val': f"{summary['operated_all']:,}" if isinstance(summary['operated_all'], (int, float)) else str(summary['operated_all']),
                'unit': '架次',
                'sub': f"计划 {summary.get('planned_all', '—')} 班 · 执行率 {summary.get('execution_rate', '—')}%",
            })
        if summary.get('release_on_time_rate') is not None:
            metrics.append({
                'label': '放行正常率',
                'val': f"{summary['release_on_time_rate']}%",
                'unit': '',
                'sub': f"正常出港 {summary.get('release_normal_cnt', '—')} / {summary.get('dep_operated', '—')} 班",
            })
        if summary.get('takeoff_on_time_rate') is not None:
            metrics.append({
                'label': '起飞正常率',
                'val': f"{summary['takeoff_on_time_rate']}%",
                'unit': '',
                'sub': f"正常起飞 {summary.get('takeoff_normal_cnt', '—')} 架次",
            })
        if summary.get('origin_dep_on_time_rate') is not None:
            metrics.append({
                'label': '始发正常率',
                'val': f"{summary['origin_dep_on_time_rate']}%",
                'unit': '',
                'sub': f"始发出港 {summary.get('origin_normal_cnt', '—')} / {summary.get('origin_dep_cnt', '—')} 班",
            })
        if summary.get('bridge_rate_all') is not None or summary.get('bridge_rate_arr') is not None:
            rate = summary.get('bridge_rate_all') if summary.get('bridge_rate_all') is not None else summary.get('bridge_rate_arr')
            metrics.append({
                'label': '综合靠桥率',
                'val': f"{rate}%",
                'unit': '',
                'sub': f"进港靠桥率 {summary.get('bridge_rate_arr', '—')}%",
            })
        if summary.get('total_pax_sql') is not None:
            metrics.append({
                'label': '旅客吞吐量',
                'val': f"{summary['total_pax_sql']:,}" if isinstance(summary['total_pax_sql'], (int, float)) else str(summary['total_pax_sql']),
                'unit': '人次',
                'sub': f"有效座位样本客座率 {summary.get('total_load_factor', '—')}%",
            })
    elif kind == 'ground_briefing' or ('planned_today' in summary and 'operated_today' in summary):
        op = summary.get('operated_today') or summary.get('planned_today')
        metrics.append({
            'label': '当日计划/执行',
            'val': f"{op:,}" if isinstance(op, (int, float)) else str(op),
            'unit': '架次',
            'sub': f"计划 {summary.get('planned_today', '—')} 班 · 取消 {summary.get('canceled_today', 0)} 班",
        })
        if summary.get('all_release_rate_22') is not None:
            metrics.append({
                'label': '出港放行正常率',
                'val': f"{summary['all_release_rate_22']}%",
                'unit': '',
                'sub': f"正常出港 {summary.get('all_release_norm_22', '—')} / {summary.get('all_dep_22', '—')} 班",
            })
        if summary.get('all_takeoff_rate_22') is not None:
            metrics.append({
                'label': '出港起飞正常率',
                'val': f"{summary['all_takeoff_rate_22']}%",
                'unit': '',
                'sub': f"正常起飞 {summary.get('all_takeoff_norm_22', '—')} / {summary.get('all_dep_22', '—')} 架次",
            })
        if summary.get('gh_release_rate_22') is not None:
            metrics.append({
                'label': '地服代理放行率',
                'val': f"{summary['gh_release_rate_22']}%",
                'unit': '',
                'sub': f"放行正常 {summary.get('gh_release_norm_22', '—')} / {summary.get('gh_dep_22', '—')} 架次 · 延误 {summary.get('gh_release_delay_22', '—')} 班",
            })
        if summary.get('gh_takeoff_rate_22') is not None:
            metrics.append({
                'label': '地服代理起飞率',
                'val': f"{summary['gh_takeoff_rate_22']}%",
                'unit': '',
                'sub': f"起飞正常 {summary.get('gh_takeoff_norm_22', '—')} / 代理出港 {summary.get('gh_dep_22', '—')} 班",
            })
        if summary.get('early_origin_tomorrow') is not None:
            metrics.append({
                'label': '次日早始发准备',
                'val': f"{summary['early_origin_tomorrow']} 班",
                'unit': '',
                'sub': f"远机位外场 {summary.get('origin_remote_tomorrow', '—')} 班 · 次日计划 {summary.get('planned_tomorrow', '—')} 班",
            })
    elif kind == 'safeguard_board' or report.get('total'):
        tot = report.get('total')
        if tot:
            metrics.append({
                'label': '执飞航班总量',
                'val': f"{tot:,}" if isinstance(tot, (int, float)) else str(tot),
                'unit': '架次',
                'sub': '本期执行航班覆盖总量',
            })
        rows = report.get('rows', [])
        if rows and isinstance(rows, list) and len(rows) > 0 and isinstance(rows[0], dict) and 'node_name' in rows[0]:
            total_records = sum(r.get('record_cnt', 0) for r in rows if isinstance(r.get('record_cnt'), (int, float)))
            if total_records:
                metrics.append({
                    'label': '保障节点录入总数',
                    'val': f"{total_records:,}",
                    'unit': '条',
                    'sub': f"已覆盖 {len(rows)} 类核心保障节点",
                })

    # If metrics empty, extract from visual_meta stats
    if not metrics:
        vm = report.get('visual_meta') or {}
        vm_stats = vm.get('stats') or []
        if vm_stats:
            for s in vm_stats:
                metrics.append({
                    'label': s.get('label', ''),
                    'val': str(s.get('value', '—')),
                    'unit': s.get('unit', ''),
                    'sub': s.get('sub') or '',
                })

    if not metrics:
        return ''

    rows = []
    for item in metrics:
        value = ' '.join(filter(None, (item['val'], item['unit'])))
        rows.append(f'<tr><th scope="row">{escape(item["label"])}</th>'
                    f'<td class="metric-value">{escape(value)}</td><td>{escape(item["sub"])}</td></tr>')
    return (
        '<section><h2>主要指标</h2><table class="metric-table">'
        '<thead><tr><th>指标</th><th>数值</th><th>说明</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></section>'
    )


def html_report(report, title):
    """Render the report as an A4 document using escaped report data."""
    esc = lambda value: escape('是' if value is True else '否' if value is False else '—' if value is None else str(value))

    plan = report.get('plan') or {}
    doc = report.get('document') or report.get('answer_document') or {}
    disp = plan.get('disp')
    start_d = report.get('start_date') or plan.get('report_start') or report.get('target_date')
    end_d = report.get('end_date') or plan.get('report_end') or start_d
    span = str(start_d) if start_d == end_d else f'{start_d} 至 {end_d}'
    if disp:
        display_title = disp
    elif doc.get('title'):
        display_title = doc['title'].replace('《', '').replace('》', '').strip()
        if start_d:
            title_span = str(start_d) if start_d == end_d else f'{start_d} ~ {end_d}'
            display_title = display_title.removesuffix(f'· {title_span}').rstrip()
    elif title:
        title = title.removeprefix('武汉天河国际机场 · ')
        display_title = re.sub(r'^(?:生成|请|导出|查询|出一份)\s*', '', title).strip()
    else:
        display_title = '运行分析简报'

    gen_raw = str(report.get('generated_at') or now())
    try:
        generated = datetime.fromisoformat(gen_raw)
        if generated.tzinfo is not None:
            generated = generated.astimezone(TZ)
        gen_display = generated.strftime('%Y-%m-%d %H:%M')
    except ValueError:
        gen_display = gen_raw[:16]

    if not report.get('summary') and report.get('main_rows') and len(report['main_rows']) == 1 and isinstance(report['main_rows'][0], dict):
        report['summary'] = report['main_rows'][0]
    if not report.get('kind') or report.get('kind') == 'query':
        report['kind'] = plan.get('report_kind') or report.get('kind')

    def prose(value):
        return re.sub(r'\*\*([^*\n]+)\*\*|`([^`\n]+)`',
                      lambda match: f'<strong>{match[1]}</strong>' if match[1] else match[2], esc(value))

    def format_cell_html(k, val):
        if val is None or val == '' or val == '—':
            return '<span class="cell-empty">—</span>'
        val_str = str(val).strip()
        k_lower = k.lower()
        m_time = re.match(r'^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2})?(?:\+\d{2}:\d{2})?$', val_str)
        if m_time:
            return m_time.group(1)
        if k_lower in ('outer_support_flag', 'out_support_flag'):
            if '是' in val_str or '外' in val_str:
                return '外场保障'
            if '否' in val_str or '近' in val_str:
                return '近机位'
        if k_lower in ('is_remote_stand', 'is_remote', 'remote_stand'):
            if val_str in ('Y', '是', '1', 'true', 'True'):
                return '远机位'
            if val_str in ('N', '否', '0', 'false', 'False'):
                return '近机位'
        if k_lower in ('direction', 'is_off_in'):
            if val_str in ('ARR', '进港', 'A'):
                return '进港'
            if val_str in ('DEP', '出港', 'D'):
                return '出港'
        if k_lower == 'agency' and val_str == '210':
            return '地服（210）'
        if k_lower == 's_cnl_stauts':
            if '未填' in val_str:
                return '未填时段'
            return f'{esc(val_str)} 取消'
        if k_lower == 'abnormal_reason_cd' and val_str in ('未填原因', 'None'):
            return '<span class="cell-empty">未填原因</span>'
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
            thead_th = ''.join('<th scope="col">' + esc(get_col_label(k)) + '</th>' for k in chunk)
            tbody_tr = []
            for r_idx, row in enumerate(rows):
                tds = []
                for k in chunk:
                    val = row.get(k)
                    is_num = isinstance(val, (int, float)) and not isinstance(val, bool)
                    cls = ' class="num-cell"' if is_num else ''
                    tds.append(f'<td{cls}>{format_cell_html(k, val)}</td>')
                tbody_tr.append(f'<tr><td class="idx-cell">{r_idx + 1}</td>' + ''.join(tds) + '</tr>')
            out.append(f'{sub_caption}<table class="data-table"><thead><tr><th scope="col" class="idx-col">序号</th>{thead_th}</tr></thead><tbody>{"".join(tbody_tr)}</tbody></table>')
        return ''.join(out)

    warning = (
        '<p class="simulation-note"><strong>模拟测试数据</strong>　报告含模拟记录，仅供测试，不可用于实际运行分析或机场排名。</p>'
    ) if report.get('is_simulated') else ''

    metric_table = _report_metrics(report)

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
            sec_claims = s.get('claims') or []
            if not sec_claims and s.get('claim_ids'):
                sec_claims = [claims_map[cid] for cid in s['claim_ids'] if cid in claims_map]
            claims_html = ''.join(
                f'<p class="claim-text">{prose(c["text"])}</p>'
                for c in sec_claims if c and c.get('text')
            )
            if claims_html:
                body += f'<section><h2>{esc(s["title"])}</h2>{claims_html}</section>'

    if not body:
        body = '<p class="claim-text">' + prose(report.get('answer') or report.get('text', '')) + '</p>'

    for item in report.get('sections', []):
        sub = item['result']
        labels = {k: v.get('label', k) for k, v in sub.get('field_meta', {}).get(sub['parse']['subject'], {}).items()}
        metric = sub.get('metric') or {}
        if metric.get('field'):
            labels[metric['field']] = metric.get('name') or metric['field']
        sim_label = '（模拟测试数据）' if item.get('is_simulated') else ''
        body += f'<section><h2>{esc(item["label"])}{sim_label}</h2>' + table(sub.get('main_rows', []), labels) + '</section>'

    benchmark = report.get('benchmark')
    if benchmark:
        body += (
            '<section><h2>机场对标分析（包含模拟测试数据）</h2>'
            f'<p class="claim-text">{prose(benchmark.get("answer", ""))}</p>'
            + table(benchmark.get('main_rows', []))
            + '</section>'
        )

    for item in report.get('related', []):
        rows = item.get('rows', [])
        count = len(rows)
        count_meta = f'共 {esc(item["count"])} 条记录，本表列出前 {count} 条' if item.get('count', 0) > count else f'共 {count} 条记录'
        if item.get('note'):
            count_meta += f'　{esc(item["note"])}'
        body += f'<section><h2>{esc(item.get("name") or item["table"])}</h2><p class="table-note">{count_meta}</p>' + table(rows) + '</section>'

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
        body += f'<section><h2>{esc(table_title)}</h2>' + table(rows_to_render, labels) + '</section>'
        if report.get('intent') == 'detail' and report.get('main_total', 0) > len(rows_to_render):
            body += f'<p class="footnote">本报告包含当前查询快照中的 {len(rows_to_render)} 条记录，全部 {report["main_total"]} 条明细可在工作台分页查看。</p>'

    boundary_notice = esc(report.get('boundary', '本报告只反映本地已同步记录，请结合来源口径核验。'))
    version = report.get('ontology_version')
    version_html = f'<p>统计规则版本　{esc(version)}</p>' if version else ''

    watermarks = report.get('sync_watermarks', [])
    watermark_html = ''
    if watermarks and isinstance(watermarks, list):
        records = '；'.join(f'{esc(w.get("job", ""))} {esc(w.get("watermark") or "")}' for w in watermarks[:4])
        watermark_html = f'<p>同步记录　{records}</p>'

    period_html = f'<div><dt>统计期间</dt><dd>{esc(span)}</dd></div>' if start_d else ''

    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{esc(display_title)}</title>
<style>
  @page {{ size: A4; margin: 17mm 16mm 18mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font: 10.5pt/1.65 "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    color: #20262e;
    font-variant-numeric: tabular-nums;
  }}
  .report-header {{ border-top: 2pt solid #263d56; padding-top: 10pt; margin-bottom: 15pt; }}
  .organization {{ margin: 0; font-size: 10pt; color: #44515f; }}
  h1 {{ font-size: 21pt; line-height: 1.32; margin: 6pt 0 8pt; overflow-wrap: anywhere; }}
  .report-meta {{
    display: flex; flex-wrap: wrap; gap: 2pt 20pt; margin: 0; padding-bottom: 8pt;
    border-bottom: .5pt solid #a6afba; color: #44515f; font-size: 9pt;
  }}
  .report-meta > div {{ display: flex; gap: 6pt; }}
  .report-meta dt {{ font-weight: 600; }}
  .report-meta dd {{ margin: 0; }}
  section {{ margin: 0 0 12pt; }}
  h2 {{ margin: 13pt 0 5pt; font-size: 12pt; line-height: 1.45; color: #263d56; break-after: avoid; }}
  p {{ margin: 0 0 5pt; overflow-wrap: anywhere; orphans: 2; widows: 2; }}
  .claim-text {{ white-space: pre-line; }}
  .simulation-note {{
    border-left: 2pt solid #78501d; color: #5d411d; padding: 1pt 0 1pt 8pt;
    margin: 0 0 12pt; font-size: 9.5pt; break-inside: avoid;
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 9pt; line-height: 1.45; }}
  .data-table {{ table-layout: fixed; }}
  thead {{ display: table-header-group; }}
  tr {{ break-inside: avoid; }}
  th, td {{ padding: 4pt 5pt; vertical-align: top; overflow-wrap: anywhere; }}
  thead th {{
    color: #263d56; background: #eef1f4; border-top: .75pt solid #7e8b9a;
    border-bottom: .5pt solid #adb6c0; font-weight: 600; text-align: left;
  }}
  tbody td, tbody th {{ border-bottom: .5pt solid #d4dae0; }}
  .metric-table th[scope="row"] {{ width: 28%; font-weight: 500; text-align: left; }}
  .metric-value {{ width: 20%; font-weight: 600; }}
  .idx-col {{ width: 29pt; text-align: center; }}
  .idx-cell {{ text-align: center; color: #535f6d; }}
  .num-cell {{ text-align: right; }}
  .cell-flt {{ font-weight: 600; }}
  .cell-empty {{ color: #5d6875; }}
  .table-note, .table-sub-caption, .empty-hint, .footnote {{ font-size: 9pt; color: #4a5765; }}
  .table-note, .table-sub-caption {{ break-after: avoid; }}
  .table-sub-caption {{ margin: 7pt 0 3pt; }}
  .footnote {{ margin-top: -7pt; }}
  .source-note {{
    margin-top: 9pt; border-top: .5pt solid #a6afba; color: #44515f; font-size: 9pt;
    break-before: avoid; break-inside: avoid;
  }}
  .source-note h2 {{ margin-top: 6pt; font-size: 10pt; }}
  .source-note p {{ margin-bottom: 3pt; }}
  .source-note p:last-child {{ margin-bottom: 0; }}
</style>
</head><body>
<header class="report-header">
  <p class="organization">湖北机场集团 · 武汉天河国际机场（WUH）</p>
  <h1>{esc(display_title)}</h1>
  <dl class="report-meta">
    {period_html}
    <div><dt>生成时间</dt><dd>{esc(gen_display)}</dd></div>
  </dl>
</header>
{warning}
{metric_table}
{body}
<section class="source-note">
  <h2>数据范围与说明</h2>
  <p>{boundary_notice}</p>
  {version_html}
  {watermark_html}
</section>
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
            return page.pdf(format='A4', prefer_css_page_size=True, print_background=True, display_header_footer=True,
                            header_template='<span></span>',
                            footer_template='<div style="width:100%;text-align:center;font:9pt Microsoft YaHei,sans-serif;color:#44515f">第 <span class="pageNumber"></span> 页 / 共 <span class="totalPages"></span> 页</div>',
                            margin={'top': '17mm', 'bottom': '18mm', 'left': '16mm', 'right': '16mm'})
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
