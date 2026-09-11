import assert from 'node:assert/strict';
import test from 'node:test';
import { taskStatusLabel, isWaitingTask, controlTask, supportedTaskProgress, taskControlActions } from '../app/lib/task-progress.mjs';
import { progressCount, taskWaitingLabel } from '../app/lib/task-progress.mjs';
import { modelDiagnosticText } from '../app/lib/model-diagnostics.mjs';

test('model diagnostics preserve phase, elapsed and actual retries without raw text', () => {
  const text = modelDiagnosticText({ kind:'transient_network', attempts:2, diagnostics:{stage:'request_write',elapsed_ms:25000,body:'secret'} });
  assert.match(text, /上传请求.*25.0 秒.*重试 1 次/);
  assert.doesNotMatch(text, /鉴权|secret/);
  assert.equal(modelDiagnosticText({kind:'not-valid'}), '');
});

test('missing progress is not zero and interrupted executor is not an offline phone', () => {
  for (const value of [null, undefined, -1, NaN, '3']) assert.equal(progressCount(value), '尚无结果');
  assert.equal(progressCount(0), '0');
  assert.equal(progressCount(3), '3');
  assert.match(taskWaitingLabel('waiting_device', { waiting_reason: 'worker_interrupted' }), /执行者中断/);
  assert.doesNotMatch(taskWaitingLabel('waiting_device', { waiting_reason: 'worker_interrupted' }), /设备离线|手机离线/);
});

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
