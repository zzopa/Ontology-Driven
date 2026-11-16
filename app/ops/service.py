"""Scope-limited snapshots, local dispatch simulation and atomic work orders."""
from datetime import timedelta
import json

from .domain import (Conflict, KINDS, check_candidate, dt, evaluate_alerts, fingerprint, interval,
                     iso, make_alert, now, overlaps, transfer_minutes)
from .store import dispatch_version, encode, event, items, record, uid

TERMINAL_STATES = {'complete', 'cancelled'}


class OpsService:
    def __init__(self, store, source, mode='live', clock=None):
        self.store, self.source = store, source
        self.mode = mode
        self.clock = clock or (lambda: now())

    def prepare_replay(self, terminal, actor):
        """Explicit demo assumptions in the isolated replay ledger, never real dispatch."""
        if self.mode != 'replay' or not self.store.simulation:
            raise ValueError('模拟场景只能写入独立历史预演台账')
        observed = self.clock()
        rows = self.source.read(terminal, observed, 180, replay=True)['flights']
        flights, used_stands = [], set()
        for f in sorted(rows, key=lambda f: f.get('target_at') or ''):
            op = next(o for o in f['operations'] if o['kind'] == 'cleaning')
            if f['cancelled'] or f['direction'] != 'DEP' or not dt(f.get('target_at')) or dt(f['target_at']) < observed + timedelta(minutes=60) or op.get('actual_start') or op.get('actual_end') or op.get('invalid_time') or op.get('invalid_extension'):
                continue
            stand = f['stand'] or f'模拟机位{len(flights) + 1}'
            if stand in used_stands:
                continue
            used_stands.add(stand)
            flights.append({**f, 'demo_stand': stand})
            if len(flights) == 3:
                break
        if len(flights) < 3:
            raise ValueError('所选预演时段缺少三班符合条件的出港航班；可切换时间或在班组管理手动登记模拟任务')
        target, occupied, following = flights
        a, b, target_id = uid(), uid(), uid()
        with self.store.db() as db:
            view = self.store.view(terminal, db)
            if view['resources'] or view['tasks'] or view['transfers'] or view['rosters'] or any(p['confirmed'] or p['duration_minutes'] for p in view['policies'].values()):
                raise Conflict('模拟台账已有内容，未覆盖任何资料；请使用现有任务预演')
            for ident, name, position in ((a, '模拟保洁A', occupied['demo_stand']), (b, '模拟保洁B', '模拟待命点')):
                payload = {'name': name, 'position': position, 'availability': 'idle', 'enabled': True,
                           'qualified_until': iso(observed + timedelta(days=7)), 'qualifications_confirmed': True, 'equipment_confirmed': True,
                           'confirmed_at': iso(observed), 'assumption': '模拟资格、设备与状态假设，非历史真实班组'}
                db.execute('INSERT INTO resources VALUES (?,?,?,?,1)', (ident, terminal, 'cleaning', encode(payload)))
                db.execute('INSERT INTO rosters VALUES (?,?,?,?,?)', (uid(), terminal, ident, iso(observed - timedelta(hours=1)), iso(observed + timedelta(hours=8))))
            for ident, f, resource, start, end, deadline in ((target_id, target, a, 3, 23, 30), (uid(), occupied, a, 3, 23, 30), (uid(), following, b, 31, 51, 60)):
                payload = {'kind': 'cleaning', 'flight_no': f['flight_no'], 'stand': f['demo_stand'], 'stand_source': '源系统' if f['stand'] else '模拟设定',
                           'latest_end_at': iso(observed + timedelta(minutes=deadline)), 'source': '模拟场景假设', 'actual_start': None, 'actual_end': None}
                db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,1)', (ident, terminal, resource, f['id'], iso(observed + timedelta(minutes=start)), iso(observed + timedelta(minutes=end)), 'planned', encode(payload)))
            db.execute('INSERT INTO transfers VALUES (?,?,?,?)', (terminal, '模拟待命点', target['demo_stand'], 2))
            db.execute('INSERT INTO transfers VALUES (?,?,?,?)', (terminal, target['demo_stand'], following['demo_stand'], 4))
            db.execute('INSERT INTO policies VALUES (?,?,?,?,?) ON CONFLICT(terminal,kind) DO UPDATE SET duration_minutes=excluded.duration_minutes,grace_minutes=excluded.grace_minutes,confirmed=excluded.confirmed', (terminal, 'cleaning', 20, 5, 1))
            event(db, actor, 'simulation.prepare', terminal, {'flight_ids': [f['id'] for f in flights], 'assumptions': '两个模拟班组、模拟排班、20分钟作业与2/4分钟转场；不下发现场'})
        return self.preview({'task_id': target_id, 'start_at': iso(observed + timedelta(minutes=5))}, terminal, actor)

    def snapshot(self, terminal='T2', minutes=90, observed=None, persist=False):
        observed = observed or self.clock()
        replay = self.mode == 'replay'
        raw = self.source.read(terminal, observed, minutes, replay=replay)
        worker = self.store.state('worker:' + terminal)
        if not replay and worker and worker.get('error') and dt(worker.get('heartbeat')) and now() - dt(worker['heartbeat']) < timedelta(seconds=120):
            raw['source'].update(fresh=False, message=worker['error'] + '；暂停自动推荐')
        view = self.store.view(terminal)
        flights = raw['flights']
        by_id = {f['id']: f for f in flights}
        tasks = view['tasks']
        # Assigned work can outlive the window or come from a different business day.
        extra_ids = list({t['flight_id'] for t in tasks if t['status'] not in TERMINAL_STATES and dt(t['start_at']) <= observed + timedelta(minutes=minutes) and dt(t['end_at']) >= observed - timedelta(minutes=30)} - set(by_id))
        previous_alerts = self.store.alerts(terminal)
        if persist:
            extra_ids = sorted(set(extra_ids) | {fid for a in previous_alerts if a['status'] != 'resolved' for fid in a['flight_ids'] if fid not in by_id})
        for offset in range(0, len(extra_ids), 100):
            for flight in self.source.read(terminal, observed, minutes, ids=extra_ids[offset:offset + 100], replay=replay)['flights']:
                if persist or not flight['cancelled']:
                    flights.append(flight)
                    by_id[flight['id']] = flight
        for task in tasks:
            flight = by_id.get(task['flight_id'])
            if not flight or task['status'] == 'cancelled':
                continue
            if not flight['stand'] and (task.get('stand_source') == '人工确认' or replay and task.get('stand_source') == '模拟设定'):
                flight.update(stand=task['stand'], stand_source='模拟设定' if task.get('stand_source') == '模拟设定' else '人工台账确认')
            op = next(o for o in flight['operations'] if o['kind'] == task['kind'])
            op.update(plan_start=task['start_at'], plan_end=task['end_at'], task_id=task['id'], resource_id=task['resource_id'], plan_source='人工台账')
            if task.get('actual_start') or task.get('actual_end'):
                op['manual_start'], op['manual_end'] = task.get('actual_start'), task.get('actual_end')
                op['manual_state'] = task['status']
                op['source_note'] = '人工报工与源系统实绩分别保留'
                if op.get('actual_end') and task.get('actual_end') and abs((dt(op['actual_end']) - dt(task['actual_end'])).total_seconds()) > 60:
                    op['source_note'] = '人工结束与源实绩相差超过1分钟，请核验并保留两类来源'
            if op['status'] == 'missing':
                op.update(status='planned', status_label='待开始')
        alerts = evaluate_alerts(flights, tasks, view['policies'], terminal, observed)
        if not replay and not raw['source']['fresh']:
            alerts.insert(0, make_alert(['source', terminal], '数据更新待核查', terminal, [], severity='high', detail=raw['source']['message']))
        # A stale feed cannot prove an old source-data incident has recovered.
        if persist:
            active = {a['id'] for a in alerts}
            alerts += [a for a in previous_alerts if a['status'] != 'resolved' and a['id'] not in active and
                       (not replay and not raw['source']['fresh'] or a['flight_ids'] and any(fid not in by_id for fid in a['flight_ids']))]
            self.store.persist_alerts(terminal, alerts, observed)
        saved = {a['id']: a for a in self.store.alerts(terminal)}
        for alert in alerts:
            if saved.get(alert['id'], {}).get('status') == 'acknowledged':
                alert['status'] = 'acknowledged'
        risk_ids = {ident for a in alerts for ident in a['flight_ids']}
        for flight in flights:
            flight['risk'] = flight['id'] in risk_ids
        flights.sort(key=lambda f: (not f['risk'], f.get('target_at') or '', f['id']))
        load = self.workload(view, observed, minutes)
        gaps_by_slot = {x['start_at']: sum(y['gap'] or 0 for y in load if y['start_at'] == x['start_at']) for x in load}
        metrics = {'flights': len(flights), 'in_progress': sum(any(o['status'] == 'in_progress' or o.get('manual_state') == 'active' for o in f['operations']) for f in flights),
                   'attention': len(risk_ids & set(by_id)), 'resource_gaps': max(gaps_by_slot.values(), default=0),
                   'pending': sum(p['state'] == 'pending' and dt(p['expires_at']) > observed for p in view['proposals'])}
        if any(x['available'] is None for x in load):
            metrics['resource_gaps'] = None
        active = bool(worker and dt(worker.get('heartbeat')) and timedelta(0) <= now() - dt(worker['heartbeat']) < timedelta(seconds=120))
        value = {'terminal': terminal, 'mode': self.mode, 'real_time': iso(now()), 'clock_offset_days': (observed.date() - now().date()).days if replay else 0,
                 'captured_at': iso(observed), 'window_end': iso(observed + timedelta(minutes=minutes)), 'minutes': minutes,
                 'flights': flights, 'stands': raw['stands'], 'source': raw['source'], 'alerts': alerts, 'metrics': metrics,
                 'resources': view['resources'], 'tasks': tasks, 'rosters': view['rosters'], 'transfers': view['transfers'],
                 'policies': view['policies'], 'settings': view['settings'], 'workload': load,
                 'worker': {'active': active, 'heartbeat': worker.get('heartbeat') if worker else None,
                            'message': ('历史预演扫描运行中' if active else '历史预演 · 按需快照') if replay else '影子值守运行中' if active and view['settings']['shadow_mode'] else '值守运行中' if active else '值守进程未启动；当前为按需快照'}}
        value['snapshot_id'] = fingerprint({'flights': flights, 'ledger': dispatch_version(view), 'source': raw['source']['jobs']})
        return value

    @staticmethod
    def workload(view, observed, minutes):
        result = []
        for kind, name in KINDS.items():
            resources = [r for r in view['resources'] if r['kind'] == kind and r['enabled']]
            for offset in range(0, minutes, 30):
                start, end = observed + timedelta(minutes=offset), observed + timedelta(minutes=min(offset + 30, minutes))
                booked = [t for t in view['tasks'] if t['kind'] == kind and t['status'] not in ('cancelled', 'complete', 'reported_complete') and overlaps(t['start_at'], t['end_at'], start, end)]
                available = [r for r in resources if r.get('availability') in ('idle', 'busy') and r['qualifications_confirmed'] and r['equipment_confirmed'] and dt(r.get('qualified_until')) and dt(r['qualified_until']) >= end and dt(r.get('confirmed_at')) and observed - timedelta(minutes=10) <= dt(r['confirmed_at']) <= observed + timedelta(minutes=1) and any(x['resource_id'] == r['id'] and dt(x['start_at']) <= start and dt(x['end_at']) >= end for x in view['rosters'])]
                points = [start] + [dt(t['start_at']) for t in booked if start < dt(t['start_at']) < end]
                demand = max((sum(dt(t['start_at']) <= p < dt(t['end_at']) for t in booked) for p in points), default=0)
                configured = bool(resources and any(x['resource_id'] in {r['id'] for r in resources} for x in view['rosters']))
                result.append({'kind': kind, 'name': name, 'start_at': iso(start), 'end_at': iso(end), 'demand': demand,
                               'available': len(available) if configured else None, 'gap': max(0, demand - len(available)) if configured else None,
                               'basis': '仅计算已登记任务与已确认班组；未登记需求不计入'})
        return result

    def preview(self, data, terminal, actor, save=True):
        observed = self.clock()
        with self.store.db(False) as db:
            view = self.store.view(terminal, db)
        target = next((t for t in view['tasks'] if t['id'] == data.get('task_id')), None)
        if not target or target['status'] != 'planned' or target.get('work_order_id'):
            raise ValueError('请选择尚未开始的已登记保障任务')
        flight, source = self.source.flight(terminal, target['flight_id'], observed, replay=self.mode == 'replay')
        if flight['cancelled']:
            raise Conflict('航班已取消，不能生成调配方案')
        if flight['stand'] and flight['stand'] != target['stand']:
            raise Conflict('航班机位已变化，请先更新任务台账')
        op = next(o for o in flight['operations'] if o['kind'] == target['kind'])
        if op.get('actual_start') or op.get('actual_end') or op.get('invalid_time') or op.get('invalid_extension'):
            raise Conflict('源记录显示作业已开始、已结束或时刻异常，请先核实')
        if self.mode == 'live' and not source['fresh']:
            raise Conflict(source['message'])
        worker = self.store.state('worker:' + terminal)
        if self.mode == 'live' and worker and worker.get('error') and dt(worker.get('heartbeat')) and now() - dt(worker['heartbeat']) < timedelta(seconds=120):
            raise Conflict('最近一轮值守采集失败，请先恢复数据采集')
        policy = view['policies'][target['kind']]
        if not policy['confirmed'] or not policy['duration_minutes']:
            raise ValueError('此类作业的标准耗时与规则尚未业务确认')
        start = dt(data.get('start_at'))
        if not start or start < observed or start > observed + timedelta(hours=24):
            raise ValueError('方案开始时间必须在当前时刻至24小时内')
        end = start + timedelta(minutes=policy['duration_minutes'])
        candidates = []
        for resource in view['resources']:
            if resource['kind'] != target['kind'] or resource['id'] == target['resource_id']:
                continue
            check = check_candidate(resource, target, view['tasks'], view['rosters'], view['transfers'], start, end, observed)
            candidates.append({'resource': resource, **check})
        candidates.sort(key=lambda c: (not c['eligible'], c['transfer_before'] if c['transfer_before'] is not None else 999, c['resource']['id']))
        chosen = next((c for c in candidates if c['resource']['id'] == data.get('resource_id')), None) if data.get('resource_id') else next((c for c in candidates if c['eligible']), None)
        if not chosen or not chosen['eligible']:
            return {'ok': True, 'eligible': False, 'candidates': candidates, 'message': '没有通过约束检查的班组，请补齐依据或人工协调'}
        current_related = [{'flight_id': t['flight_id'], 'flight_no': t['flight_no'], 'relation': '原班组当前任务', 'detail': '保持原安排'}
                           for t in view['tasks'] if t['id'] != target['id'] and t['resource_id'] == target['resource_id'] and t['status'] != 'cancelled' and overlaps(t['start_at'], t['end_at'], target['start_at'], target['end_at'])]
        impacted_ids = sorted({target['flight_id'], *(i['flight_id'] for i in current_related + chosen['impacts'])})
        affected = self.source.read(terminal, observed, ids=impacted_ids, replay=self.mode == 'replay')['flights']
        source_versions = {f['id']: fingerprint(f) for f in affected}
        if set(source_versions) != set(impacted_ids) or any(f['cancelled'] or f['stand'] and f['stand'] != next(t['stand'] for t in view['tasks'] if t['flight_id'] == f['id']) for f in affected):
            raise Conflict('关联航班或机位已变化，请核实全部受影响任务')
        payload = {'target': target, 'resource': chosen['resource'], 'mode': self.mode, 'start_at': iso(start), 'end_at': iso(end),
                   'check': {k: v for k, v in chosen.items() if k != 'resource'}, 'impacts': current_related + chosen['impacts'],
                   'tobt': flight.get('tobt'), 'flight_version': fingerprint(flight), 'source_versions': source_versions, 'ledger_version': dispatch_version(view), 'created_by': str(actor)}
        proposal = {**payload, 'id': uid(), 'terminal': terminal, 'state': 'pending', 'version': fingerprint(payload),
                    'created_at': iso(observed), 'expires_at': iso(observed + timedelta(minutes=5))}
        if save:
            with self.store.db() as db:
                if dispatch_version(self.store.view(terminal, db)) != payload['ledger_version']:
                    raise Conflict('台账在预演期间已变化，请重新计算')
                db.execute('INSERT INTO proposals VALUES (?,?,?,?,?,?,?)', (proposal['id'], terminal, encode(payload), 'pending', proposal['version'], proposal['created_at'], proposal['expires_at']))
                event(db, actor, 'proposal.create', proposal['id'])
        return {'ok': True, 'eligible': True, 'proposal': proposal, 'candidates': candidates}

    def approve(self, ident, version, key, terminal, actor):
        if not isinstance(key, str) or not 8 <= len(key) <= 100:
            raise ValueError('审批缺少有效的防重复标识')
        with self.store.db(False) as db:
            proposal = record(db.execute('SELECT * FROM proposals WHERE id=? AND terminal=?', (ident, terminal)).fetchone())
        if not proposal:
            raise ValueError('方案不存在')
        with self.store.db(False) as db:
            existing = record(db.execute('SELECT * FROM work_orders WHERE proposal_id=?', (ident,)).fetchone())
            if existing and existing.get('approval_key') == key and proposal['version'] == version:
                return existing
        observed_source = self.source.read(terminal, observed=self.clock(), ids=list(proposal['source_versions']), replay=self.mode == 'replay')
        source = observed_source['source']
        versions = {f['id']: fingerprint(f) for f in observed_source['flights']}
        with self.store.db() as db:
            proposal = record(db.execute('SELECT * FROM proposals WHERE id=? AND terminal=?', (ident, terminal)).fetchone())
            existing = record(db.execute('SELECT * FROM work_orders WHERE proposal_id=?', (ident,)).fetchone())
            if existing:
                if existing.get('approval_key') == key and proposal['version'] == version:
                    return existing
                raise Conflict('方案已生成工单，请刷新查看')
            observed = self.clock()
            view = self.store.view(terminal, db)
            if not view['settings'].get('dispatch_enabled'):
                raise Conflict('当前仅开放预演；业务验收后由管理员开放人工审批')
            if proposal['state'] != 'pending' or proposal['version'] != version or not dt(proposal['created_at']) <= observed < dt(proposal['expires_at']):
                raise Conflict('方案状态已变化或已过期，请重新计算')
            worker = self.store.state('worker:' + terminal, db=db)
            worker_error = worker and worker.get('error') and dt(worker.get('heartbeat')) and now() - dt(worker['heartbeat']) < timedelta(seconds=120)
            if (self.mode == 'live' and (not source['fresh'] or worker_error)) or versions != proposal['source_versions'] or dispatch_version(view) != proposal['ledger_version']:
                raise Conflict('航班、采集状态或班组台账已变化，请重新计算')
            target = next(t for t in view['tasks'] if t['id'] == proposal['target']['id'])
            resource = next(r for r in view['resources'] if r['id'] == proposal['resource']['id'])
            if target['status'] != 'planned' or dt(proposal['start_at']) < observed:
                raise Conflict('任务已开始或拟开始时间已过，请重新计算')
            check = check_candidate(resource, target, view['tasks'], view['rosters'], view['transfers'], dt(proposal['start_at']), dt(proposal['end_at']), observed)
            if not check['eligible']:
                raise Conflict('；'.join(check['errors']))
            order_id = uid()
            payload = {'flight_no': target['flight_no'], 'stand': target['stand'], 'kind': target['kind'], 'task_id': target['id'],
                       'start_at': proposal['start_at'], 'end_at': proposal['end_at'], 'resource_name': resource['name'], 'approval_key': key,
                       'approved_by': str(actor), 'approved_at': iso(observed), 'actual_start': None, 'actual_end': None,
                       'mode': self.mode, 'source': '模拟工单·不下发现场' if self.mode == 'replay' else '本系统工单·人工执行', 'proposal_version': version}
            db.execute('INSERT INTO work_orders VALUES (?,?,?,?,?,?,?,1)', (order_id, terminal, resource['id'], target['flight_id'], ident, 'pending', encode(payload)))
            task_payload = {k: target.get(k) for k in ('kind', 'flight_no', 'stand', 'stand_source', 'latest_end_at', 'actual_start', 'actual_end')}
            task_payload.update(source='本系统工单', work_order_id=order_id)
            db.execute('UPDATE tasks SET resource_id=?,start_at=?,end_at=?,payload=?,revision=revision+1 WHERE id=?',
                       (resource['id'], proposal['start_at'], proposal['end_at'], encode(task_payload), target['id']))
            db.execute("UPDATE proposals SET state='approved' WHERE id=?", (ident,))
            event(db, actor, 'proposal.approve', ident, {'work_order_id': order_id, 'version': version})
            event(db, actor, 'work_order.create', order_id)
            return {**payload, 'id': order_id, 'terminal': terminal, 'resource_id': resource['id'], 'flight_id': target['flight_id'], 'proposal_id': ident, 'state': 'pending', 'revision': 1}

    def reject(self, ident, version, terminal, actor):
        with self.store.db() as db:
            changed = db.execute("UPDATE proposals SET state='rejected' WHERE id=? AND terminal=? AND state='pending' AND version=?", (ident, terminal, version)).rowcount
            if not changed:
                raise Conflict('方案已被处理，请刷新')
            event(db, actor, 'proposal.reject', ident)

    def transition(self, ident, action, revision, terminal, actor, resource_ids=None, note='', actual_at=None):
        rules = {'accept': ({'pending'}, 'accepted'), 'start': ({'accepted'}, 'active'), 'finish': ({'active'}, 'review'),
                 'verify': ({'review'}, 'complete'), 'decline': ({'pending', 'accepted'}, 'exception'),
                 'exception': ({'accepted', 'active', 'review'}, 'exception'), 'cancel': ({'pending', 'accepted', 'exception'}, 'cancelled')}
        if action not in rules:
            raise ValueError('工单操作无效')
        if action in ('verify', 'decline', 'exception', 'cancel') and not str(note).strip():
            raise ValueError('请填写处理说明或核验依据')
        actual = dt(actual_at) if actual_at else self.clock()
        if not actual or actual > self.clock() + timedelta(minutes=1):
            raise ValueError('报工实绩时间无效，不能晚于当前时刻')
        with self.store.db() as db:
            order = record(db.execute('SELECT * FROM work_orders WHERE id=? AND terminal=?', (ident, terminal)).fetchone())
            if not order or resource_ids is not None and order['resource_id'] not in resource_ids:
                raise PermissionError('无权操作该班组工单')
            if order['revision'] != revision or order['state'] not in rules[action][0]:
                raise Conflict('工单状态已变化，请刷新')
            if self.mode == 'replay' and any(dt(order.get(k)) and dt(order[k]) > self.clock() for k in ('approved_at', 'actual_start', 'actual_end')):
                raise Conflict('所选预演时刻早于此工单的处理记录，请调整时间后继续')
            task = record(db.execute('SELECT * FROM tasks WHERE id=?', (order['task_id'],)).fetchone())
            observed = iso(self.clock())
            payload = {k: v for k, v in order.items() if k not in ('id', 'terminal', 'resource_id', 'flight_id', 'proposal_id', 'state', 'revision')}
            if action == 'start':
                payload['actual_start'] = iso(actual)
            if action in ('accept', 'start') and dt(order['end_at']) <= self.clock():
                raise Conflict('计划作业窗口已过，请由调度取消并重新协调')
            if action == 'finish':
                if not dt(payload.get('actual_start')) or actual < dt(payload['actual_start']):
                    raise ValueError('结束时间不能早于实际开始时间')
                payload['actual_end'] = iso(actual)
            if action == 'verify':
                payload.update(verified_by=str(actor), verified_at=observed, verification_source='模拟核验' if self.mode == 'replay' else '人工核验')
            state = rules[action][1]
            payload['last_note'] = str(note)[:1000]
            db.execute('UPDATE work_orders SET state=?,payload=?,revision=revision+1 WHERE id=?', (state, encode(payload), ident))
            task_payload = {k: v for k, v in task.items() if k not in ('id', 'terminal', 'resource_id', 'flight_id', 'start_at', 'end_at', 'status', 'revision')}
            task_payload.update(actual_start=payload.get('actual_start'), actual_end=payload.get('actual_end'))
            task_state = {'active': 'active', 'review': 'reported_complete', 'complete': 'complete', 'cancelled': 'cancelled'}.get(state, task['status'])
            db.execute('UPDATE tasks SET status=?,payload=?,revision=revision+1 WHERE id=?', (task_state, encode(task_payload), task['id']))
            event(db, actor, 'work_order.' + action, ident, {'note': str(note)[:1000], 'state': state})
            return {**order, **payload, 'state': state, 'revision': revision + 1}

    def report_task(self, ident, action, revision, terminal, actor, resource_ids=None, note='', actual_at=None):
        states = {'start': ('planned', 'active'), 'finish': ('active', 'reported_complete'), 'verify': ('reported_complete', 'complete')}
        if action not in states or action == 'verify' and not str(note).strip():
            raise ValueError('请填写有效操作及核验依据')
        actual = dt(actual_at) if actual_at else self.clock()
        if not actual or actual > self.clock() + timedelta(minutes=1):
            raise ValueError('实绩时间无效，不能晚于当前时刻')
        with self.store.db() as db:
            task = record(db.execute('SELECT * FROM tasks WHERE id=? AND terminal=?', (ident, terminal)).fetchone())
            if not task or resource_ids is not None and task['resource_id'] not in resource_ids:
                raise PermissionError('只能报工所属班组任务')
            if task.get('work_order_id') or task['revision'] != revision or task['status'] != states[action][0]:
                raise Conflict('任务状态已变化或已有工单，请刷新并从工单中心处理')
            payload = {k: v for k, v in task.items() if k not in ('id', 'terminal', 'resource_id', 'flight_id', 'start_at', 'end_at', 'status', 'revision')}
            if action == 'start':
                payload['actual_start'] = iso(actual)
            if action == 'finish':
                if not dt(payload.get('actual_start')) or actual < dt(payload['actual_start']):
                    raise ValueError('结束时间不能早于实际开始时间')
                payload['actual_end'] = iso(actual)
            if action == 'verify':
                payload.update(verified_by=str(actor), verified_at=iso(self.clock()), verification_source='模拟核验' if self.mode == 'replay' else '人工核验')
            db.execute('UPDATE tasks SET status=?,payload=?,revision=revision+1 WHERE id=?', (states[action][1], encode(payload), ident))
            event(db, actor, 'task.' + action, ident, {'note': str(note)[:1000], 'actual_at': iso(actual)})

    @staticmethod
    def assist(snapshot, question):
        """Only describe authorized, computed facts; no free-form operational claims."""
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
            raise ValueError('请输入1000字以内的问题')
        refs = []
        selected = next((f for f in snapshot['flights'] if f['flight_no'].upper() in question.upper()), None)
        if selected:
            answer = f"{selected['flight_no']} · {selected['stand'] or '机位未确认'} · 业务日{selected['flight_date']}\n"
            answer += '\n'.join(f"{o['name']}：{o['status_label']}；源开始 {o.get('actual_start') or '未记录'}；源结束 {o.get('actual_end') or '未记录'}" + (f"；人工报工状态 {o['manual_state']}" if o.get('manual_state') else '') for o in selected['operations'])
            answer += f"\n发布TOBT：{selected.get('tobt') or '未记录'}，此查询不修改发布时隙。"
            refs = [selected['id']]
        elif any(word in question for word in ('资源', '缺口', '班组', '负荷')):
            if snapshot['metrics']['resource_gaps'] is None:
                answer = '部分班组或排班尚未配置，无法给出完整的资源缺口。请在班组管理补齐现有任务、排班和转场依据。'
            else:
                rows = [x for x in snapshot['workload'] if x['gap']]
                answer = '；'.join(f"{x['name']} {dt(x['start_at']):%H:%M}—{dt(x['end_at']):%H:%M}：已登记需求{x['demand']}、确认能力{x['available']}" for x in rows) or '已登记任务中未发现资源缺口；未登记的外部任务不在计算范围。'
            refs = ['workload']
        elif any(word in question for word in ('多少', '几班', '架次', '数量', '统计')):
            metrics = snapshot['metrics']
            answer = f"{snapshot['terminal']} 当前未来{snapshot['minutes']}分钟窗口共{metrics['flights']}班，保障进行中{metrics['in_progress']}班，关注航班{metrics['attention']}班，待审批方案{metrics['pending']}项。\n数据依据：{snapshot['source']['message']}。统计覆盖完整筛选范围，不是当前屏幕行数。"
            refs = ['metrics']
        else:
            alerts = snapshot['alerts']
            answer = '\n'.join(f"{i + 1}. {a['title']}：{a['detail']}" for i, a in enumerate(alerts[:8])) or '当前授权快照未触发已启用规则。未记录不等于未发生；请同时核对数据新鲜度。'
            refs = [a['id'] for a in alerts[:8]]
            if len(alerts) > 8:
                answer += f"\n当前共{len(alerts)}项，此处列出前8项；完整列表见统筹处置队列。"
        return {'ok': True, 'mode': 'deterministic', 'answer': answer, 'fact_refs': refs, 'snapshot_id': snapshot['snapshot_id'],
                'boundary': '仅解释当前运行快照、告警和已登记资源；通用问数请打开原智能问询。'}
