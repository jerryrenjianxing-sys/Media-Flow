import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const server = await createServer({ configFile: false, cacheDir: 'node_modules/.vite-component-tests', plugins: [react()], server: { middlewareMode: true, hmr: false }, appType: 'custom' });
after(() => server.close());
const components = await server.ssrLoadModule('/app/components/task-groups.tsx');
const { default: RunPage } = await server.ssrLoadModule('/app/run/page.tsx');
const { TaskProgressPanel } = await server.ssrLoadModule('/app/components/task-progress.tsx');
const { default: RecordsPage } = await server.ssrLoadModule('/app/records/page.tsx');
const { default: InteractionsPage } = await server.ssrLoadModule('/app/interactions/page.tsx');

test('records and interaction pages do not assert empty results before their first read', () => {
  const records = renderToStaticMarkup(React.createElement(RecordsPage));
  assert.doesNotMatch(records, /暂无任务记录|暂无纠错记录/);
  const interactions = renderToStaticMarkup(React.createElement(InteractionsPage));
  assert.doesNotMatch(interactions, /尚无设备消息巡检回执|当前筛选没有巡检回执|尚无巡检回执/);
});

test('video progress explains suspended capabilities in Chinese with device batch scope', () => {
  const html = renderToStaticMarkup(React.createElement(TaskProgressPanel, { taskId: 'video', status: 'waiting_device', progress: { schema_supported: true, processed_slots: 3, successful_slots: 2, failed_slots: 1, unavailable_slots: 0, skipped_slots: 1, unknown_actions: 0, affected_capabilities: ['like', 'favorite'], waiting_reason: 'previous_device_task_failed' } }));
  assert.match(html, /本设备.*本批次.*点赞.*收藏/);
  assert.doesNotMatch(html, /previous_device_task_failed|暂停能力：like/);
});

test('initial run screen is unread, with disabled controls and no healthy empty assertion', () => {
  const html = renderToStaticMarkup(React.createElement(RunPage));
  assert.match(html, /未读取/);
  assert.doesNotMatch(html, /<b>正常<\/b>|当前没有活动任务|调度器处于待命状态/);
  assert.match(html, /disabled=""[^>]*>暂停领取新任务/);
});

test('inspection cards render pending, running, failed and completed without video or legacy panels', () => {
  assert.equal(typeof components.InspectionCard, 'function');
  for (const [status, result, text] of [
    ['pending', null, '尚未检查'],
    ['running', { navigation_message: '正在读取角标' }, '正在读取角标'],
    ['failed', null, '未取得检查结果'],
    ['completed', { workflow_version: 'home_badge', home_badge: { state: 'present', badge_text: '3' } }, '有消息，3条'],
    ['completed', { workflow_version: 'v3', sections: { private_messages: { status: 'available' } } }, '版本冲突'],
  ]) {
    const html = renderToStaticMarkup(React.createElement(components.InspectionCard, { inspection: { id: status, inspection_mode: 'home_badge', task_type: 'douyin_engagement_inspection', status, result, incidents: [], progress: { schema_supported: true, affected_capabilities: ['like', 'favorite'], processed_slots: 0 } } }));
    assert.ok(html.includes(text), html);
    assert.doesNotMatch(html, /私信列表|收到的赞|评论与弹幕|主页访客|已处理|暂停能力|下一名额/);
    assert.doesNotMatch(html, /开始只读自动复验|处理后继续复验/);
  }
});

test('historical legacy cards display only saved sections and no recovery mutation', () => {
  const html = renderToStaticMarkup(React.createElement(components.InspectionCard, { inspection: { id: 'old', status: 'failed', result: { workflow_version: 'v3', failure_class: 'recoverable_precondition', status: 'failed', sections: { received_likes: { status: 'available', count: 2 } } }, incidents: [] } }));
  assert.match(html, /历史只读/);
  assert.match(html, /点赞与收藏/);
  assert.doesNotMatch(html, /主页访客|开始只读自动复验|处理后继续复验/);
});

test('running cards do not claim not started and missing result retains the recorded task error', () => {
  const running = renderToStaticMarkup(React.createElement(components.InspectionCard, { inspection: { id: 'running', status: 'running', inspection_mode: 'home_badge', incidents: [], started_at: null } }));
  assert.doesNotMatch(running, /尚未开始/);
  const failed = renderToStaticMarkup(React.createElement(components.InspectionCard, { inspection: { id: 'failed', status: 'failed', inspection_mode: 'home_badge', incidents: [], result: null, error: '隔离设备截图读取失败' } }));
  assert.match(failed, /隔离设备截图读取失败/);
});

test('inspection-only groups ignore leftover video and model aggregate counters', () => {
  const html = renderToStaticMarkup(React.createElement(components.TaskGroupList, { groups: [{ id: 'inspection', device_name: '隔离设备', status: 'completed', rounds_total: 0, inspection_total: 1, created_at: '2026-09-09T09:00:00Z', videos_seen: 119, model_attempts: 30, model_valid_response_rate: .9, model_errors: 9, video_errors: 3 }], now: 0, onOpen() {} }));
  assert.doesNotMatch(html, /视频|模型有效|模型错误|页面异常/);
});
