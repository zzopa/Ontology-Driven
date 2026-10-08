"""Public execution milestones, never model-internal reasoning or raw prompts."""
from copy import deepcopy
from time import perf_counter


class ExecutionTrace:
    def __init__(self, on_event=None):
        self.on_event = on_event
        self.started = perf_counter()
        self.steps = {}

    def start(self, stage, label, message, details=()):
        self.steps[stage] = {'id': stage, 'label': label, 'state': 'running',
                             'message': message, 'details': list(details),
                             'started_at': round(perf_counter() - self.started, 3), 'elapsed': 0}
        self._emit(stage)

    def finish(self, stage, message, details=(), state='completed'):
        step = self.steps[stage]
        step.update(state=state, message=message, details=list(details),
                    elapsed=round(perf_counter() - self.started - step['started_at'], 3))
        self._emit(stage)

    def update(self, stage, message):
        """A real in-flight heartbeat; preserve the original start time/state."""
        step = self.steps[stage]
        step.update(message=message,
                    elapsed=round(perf_counter() - self.started - step['started_at'], 3))
        self._emit(stage)

    def fail_active(self, message):
        active = False
        for stage, step in self.steps.items():
            if step['state'] == 'running':
                active = True
                self.finish(stage, message, step['details'], state='failed')
        if not active and not any(step['state'] == 'failed' for step in self.steps.values()):
            self.start('query_error', '查询未完成', '检查查询条件与执行结果')
            self.finish('query_error', message, state='failed')

    def _emit(self, stage):
        if self.on_event:
            self.on_event({'type': 'step', 'step': deepcopy(self.steps[stage])})

    def snapshot(self):
        return deepcopy(list(self.steps.values()))
