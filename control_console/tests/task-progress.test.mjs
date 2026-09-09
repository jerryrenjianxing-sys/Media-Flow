import assert from 'node:assert/strict';
import test from 'node:test';
import { taskStatusLabel, isWaitingTask, controlTask } from '../app/lib/task-progress.mjs';

test('waiting tasks remain actionable and degraded differs from user stop', () => {
  for (const status of ['waiting_model', 'waiting_device', 'waiting_user']) assert.equal(isWaitingTask(status), true);
  assert.equal(taskStatusLabel('waiting_model'), '等待模型');
  assert.equal(taskStatusLabel('waiting_device'), '等待设备');
  assert.equal(taskStatusLabel('waiting_user'), '等待用户');
  assert.equal(taskStatusLabel('degraded'), '完成，有异常');
  assert.equal(taskStatusLabel('stopped'), '用户停止');
  assert.equal(isWaitingTask('stopped'), false);
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
