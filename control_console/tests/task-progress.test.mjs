import assert from 'node:assert/strict';
import test from 'node:test';
import { taskStatusLabel, isWaitingTask, controlTask, supportedTaskProgress, taskControlActions } from '../app/lib/task-progress.mjs';

test('waiting tasks remain actionable and degraded differs from user stop', () => {
  for (const status of ['waiting_model', 'waiting_device', 'waiting_user']) assert.equal(isWaitingTask(status), true);
  assert.equal(taskStatusLabel('waiting_model'), '等待模型');
  assert.equal(taskStatusLabel('waiting_device'), '等待设备');
  assert.equal(taskStatusLabel('waiting_user'), '等待用户');
  assert.equal(taskStatusLabel('degraded'), '完成，有异常');
  assert.equal(taskStatusLabel('stopped'), '已停止');
  assert.equal(taskStatusLabel('stopped', { error: 'stopped_by_user' }), '用户停止');
  assert.equal(taskStatusLabel('stopped', { result: { stopped_by_user: true } }), '用户停止');
  assert.equal(taskStatusLabel('stopped', { error: 'model_circuit_open' }), '已停止');
  assert.equal(isWaitingTask('stopped'), false);
});

test('legacy slots are unavailable rather than zero completed work', () => {
  assert.equal(supportedTaskProgress({ schema_supported: false, processed_slots: 0 }), undefined);
  assert.equal(supportedTaskProgress({ processed_slots: 0 }), undefined);
  const current = { schema_supported: true, processed_slots: 20 };
  assert.equal(supportedTaskProgress(current), current);
});

test('a resumed task still offers stop while queued', () => {
  assert.deepEqual(taskControlActions('pending', { available_actions: [] }), ['stop_task']);
  assert.deepEqual(taskControlActions('waiting_device', { available_actions: ['resume_task', 'stop_task'] }), ['resume_task', 'stop_task']);
  assert.deepEqual(taskControlActions('stopped', { available_actions: [] }), []);
});

test('task control sends stable receipt ID and preserves a not-ready response', async () => {
  const requests = [];
  const transport = async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    return { ok: false, json: async () => ({ ok: false, status: 'waiting_model', user_message: '等待后端复查' }) };
  };
  const result = await controlTask(transport, 'http://localhost:1', 'original', 'resume_task', 'stable');
  assert.deepEqual(requests, [{ url: 'http://localhost:1/api/tasks/original/resume', body: { request_id: 'stable' } }]);
  assert.equal(result.status, 'waiting_model');
  assert.equal(result.ok, false);
});
