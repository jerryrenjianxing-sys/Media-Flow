"""One execution's durable progress; the owning TaskStore holds the lease."""
from task_resilience import VERSION
from task_resilience import TaskWaiting


def tuple_tree(value):
    return tuple(tuple_tree(item) for item in value) if isinstance(value, list) else value


def verify_task_identity(store, task, device):
    """Read Android identity only after owning the task's action lease."""
    try:
        serial_result = device.shell('getprop ro.serialno', timeout=5)
        android_result = device.shell('settings get secure android_id', timeout=5)
        serial = str(getattr(serial_result, 'output', serial_result)).strip()
        android = str(getattr(android_result, 'output', android_result)).strip()
        if not serial or not android or android == 'null':
            raise ValueError('empty identity')
    except Exception:
        raise TaskWaiting('waiting_device', 'device_identity_unavailable') from None
    current = {'adb_target': task.device_id, 'android_serial': serial, 'android_id': android}
    state = store.get_task_checkpoint(task.id)
    if state.get('device_identity') and state['device_identity'] != current:
        raise TaskWaiting('waiting_device', 'device_identity_conflict')
    store.save_task_checkpoint(task.id, {**state, 'device_identity': current})


class SessionCheckpoint:
    def __init__(self, store=None, task_id=None, evidence_dir=None):
        self.store, self.task_id = store, task_id
        self.state = store.get_task_checkpoint(task_id) if store and task_id else {}
        if evidence_dir:
            dirs = list(self.state.get('evidence_dirs', []))
            if str(evidence_dir) not in dirs:
                dirs.append(str(evidence_dir))
            self.state['evidence_dirs'] = dirs
            if store:
                store.save_task_checkpoint(task_id, self.state)

    def save(self, summary, rng, planner, decisions, *, streak=0, used=(), in_progress=False):
        # Action receipts live in the same checkpoint. Never overwrite a receipt
        # just written by the action boundary with an older in-memory copy.
        current = self.store.get_task_checkpoint(self.task_id) if self.store else self.state
        self.state = {**current, 'version': VERSION, 'next_slot': int(summary['processed_slots']) + 1,
                      'summary': dict(summary), 'rng': rng.getstate(), 'planner': planner.checkpoint() if planner else None,
                      'decisions': list(decisions), 'model_failure_streak': streak,
                      'used_comment_candidates': sorted(used), 'slot_in_progress': in_progress}
        if self.store:
            self.store.save_task_checkpoint(self.task_id, self.state)

    def before_action(self, slot, action):
        if self.store:
            self.store.begin_task_action(self.task_id, slot=slot, action=action)
        else:
            self.state['pending_action'] = {'slot': slot, 'action': action}

    def action_pending(self):
        state = self.store.get_task_checkpoint(self.task_id) if self.store else self.state
        return bool(state.get('pending_action'))

    def after_action(self, confirmed, outcome=None):
        if self.store:
            self.store.finish_task_action(self.task_id, confirmed=confirmed, outcome=outcome)
        else:
            self.state.pop('pending_action', None)

    def disable(self, action, reason='action_result_unknown'):
        if self.store:
            self.store.disable_task_action(self.task_id, action, reason)

    def disabled(self):
        return self.store.disabled_task_actions(self.task_id) if self.store else []
