"""Read-only, batched standard-flight adapter for the operations UI."""
from datetime import timedelta
import json

from app.config import settings
from app.sync.transforms import map_status
from .domain import KINDS, dt, iso, now, operation


class OpsSource:
    def read(self, terminal, observed=None, minutes=90, ids=None, all_day=False, details=False, replay=False):
        from app.core import connect_db
        if settings.data_source != 'ops_ask':
            raise ValueError('运行工作台需要启用 ops_ask 数据源')
        observed = observed or now()
        conn = connect_db(repeatable_read=True)
        try:
            cur = conn.cursor()
            day = observed.replace(hour=0, minute=0, second=0, microsecond=0)
            selector = 'c.id::text IN (' + ','.join('%s' for _ in ids) + ')' if ids else 'c.flight_date >= %s AND c.flight_date < %s'
            params = list(ids) if ids else [day - timedelta(days=1), day + timedelta(days=2)]
            cur.execute('''
                SELECT c.id::text,c.flight_no,c.flight_date,c.direction,c.aircraft_reg,c.stand_code,c.gate_code,
                  c.scheduled_departure,c.estimated_departure,c.actual_departure,c.scheduled_arrival,c.estimated_arrival,c.actual_arrival,
                  c.flight_status,c.operation_status,c.airport_code,c.updated_at,COALESCE(to_jsonb(p),'{}'::jsonb),
                  COALESCE(NULLIF(c.terminal,''),NULLIF(c.aircraft_terminal,''),s.terminal_code,g.terminal_code,'')
                FROM std_flight_core c LEFT JOIN std_flight_process p ON p.flight_id=c.id
                LEFT JOIN dim_stand s ON s.airport_code=c.airport_code AND s.stand_code=c.stand_code AND s.deleted_at IS NULL
                LEFT JOIN dim_gate g ON g.airport_code=c.airport_code AND g.gate_code=c.gate_code AND g.deleted_at IS NULL
                WHERE c.deleted_at IS NULL AND COALESCE(c.is_virtual,FALSE)=FALSE AND (''' + selector + ''')
                  AND COALESCE(NULLIF(c.terminal,''),NULLIF(c.aircraft_terminal,''),s.terminal_code,g.terminal_code,'')=%s
                ORDER BY c.id
            ''', (*params, terminal))
            flights = []
            for row in cur.fetchall():
                (ident, number, date, direction, aircraft, stand, gate, sched_dep, est_dep, actual_dep,
                 sched_arr, est_arr, actual_arr, status, operation_status, airport, updated, process, term) = row
                if isinstance(process, str):
                    process = json.loads(process)
                process = process or {}
                cancelled = map_status(status, None) == '取消' or map_status(operation_status, None) == '取消'
                target = dt(process.get('tobt') or process.get('scheduled_off_block') or est_dep or sched_dep) if direction == 'DEP' else dt(est_arr or sched_arr)
                actual = dt(actual_dep if direction == 'DEP' else actual_arr)
                if not ids and not all_day:
                    if cancelled or not target:
                        continue
                    if direction == 'DEP' and actual and actual <= observed:
                        continue
                    active_arrival = direction == 'ARR' and actual and observed - timedelta(hours=2) <= actual <= observed
                    if not active_arrival and not observed - timedelta(minutes=30) <= target <= observed + timedelta(minutes=minutes):
                        continue
                flights.append({'id': ident, 'flight_no': number, 'flight_date': str(date)[:10], 'direction': direction,
                                'aircraft_reg': aircraft, 'stand': stand or '', 'gate': gate or '', 'terminal': term, 'airport': airport,
                                'cancelled': cancelled, 'updated_at': iso(updated), 'target_at': iso(target),
                                'aibt': iso(process.get('actual_on_block')), 'sobt': iso(process.get('scheduled_off_block')),
                                'tobt': iso(process.get('tobt')), 'tsat': iso(process.get('tsat')),
                                'operations': [operation(kind, process, observed, replay=replay) for kind in KINDS]})
                if replay:
                    for key in ('aibt',):
                        if dt(flights[-1][key]) and dt(flights[-1][key]) > observed:
                            flights[-1][key] = None
                if details:
                    flights[-1]['_process'] = process
                    flights[-1]['_flight'] = {'id': ident, 'flight_no': number, 'flight_date': str(date)[:10], 'direction': direction,
                                              'aircraft_reg': aircraft, 'stand_code': stand, 'gate_code': gate,
                                              'scheduled_departure': sched_dep, 'estimated_departure': est_dep, 'actual_departure': actual_dep,
                                              'scheduled_arrival': sched_arr, 'estimated_arrival': est_arr, 'actual_arrival': actual_arr}
            cur.execute('SELECT airport_code,stand_code,name_zh FROM dim_stand WHERE deleted_at IS NULL AND terminal_code=%s ORDER BY stand_code', (terminal,))
            stands = [{'airport': r[0], 'code': r[1], 'name': r[2]} for r in cur.fetchall()]
            cur.execute("SELECT job_name,last_success_at,last_result_json FROM sync_job_state WHERE job_name IN ('flight_dyn_sync_fims','flight_dyn_sync_acdm','flight_safeguard_sync')")
            rows = cur.fetchall()
            jobs = {r[0]: iso(r[1]) for r in rows}
            failed = []
            for job, _, payload in rows:
                try:
                    result = json.loads(payload) if isinstance(payload, str) else payload or {}
                    if result.get('failed'):
                        failed.append(job)
                except (ValueError, TypeError, AttributeError):
                    failed.append(job)
            required = ('flight_dyn_sync_fims', 'flight_dyn_sync_acdm', 'flight_safeguard_sync')
            fresh = not failed and all(dt(jobs.get(k)) and observed - timedelta(seconds=120) <= dt(jobs[k]) <= observed + timedelta(minutes=1) for k in required)
            return {'flights': flights, 'stands': stands, 'source': {'fresh': fresh and not replay, 'jobs': jobs, 'mode': 'replay' if replay else 'live',
                    'message': '历史快照按所选时刻预演；非事件级回放，后续修订前的计划与TOBT无法还原' if replay else '源端增量采集在有效期内' if fresh else '源端采集已延迟或缺少成功记录；暂停自动推荐', 'checked_at': iso(observed)}}
        finally:
            conn.close()

    def flight(self, terminal, ident, observed=None, replay=False):
        result = self.read(terminal, observed, ids=[str(ident)], replay=replay)
        if not result['flights']:
            raise ValueError('当前授权航站楼没有该航班')
        return result['flights'][0], result['source']

    def linked(self, terminal, ident, observed=None, replay=False):
        from app.core import connect_db
        conn = connect_db()
        try:
            cur = conn.cursor()
            cur.execute('''SELECT DISTINCT p.id::text FROM std_flight_link l
                JOIN std_flight_core c ON c.id::text=%s
                JOIN std_flight_core p ON p.id=CASE WHEN l.flight_id=c.id THEN l.linked_flight_id ELSE l.flight_id END
                WHERE (l.flight_id=c.id OR l.linked_flight_id=c.id) AND l.link_type='TURNAROUND'
                  AND p.deleted_at IS NULL AND c.airport_code=p.airport_code
                  AND NULLIF(replace(upper(c.aircraft_reg),'-',''),'')=replace(upper(p.aircraft_reg),'-','')''', (str(ident),))
            ids = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
        return self.read(terminal, observed=observed, ids=ids, replay=replay)['flights'] if ids else []
