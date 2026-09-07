import test from 'node:test';
import assert from 'node:assert/strict';
import { platformSummary, loadSkillArchive } from '../app/lib/platform-home-state.mjs';

test('loading and failure never claim stale devices or tasks are current', () => {
  assert.equal(platformSummary(null, false).state, 'loading');
  const result = platformSummary({ devices: [{state:'device'}], task_summary:{running:2} }, true);
  assert.equal(result.state, 'error');
  assert.equal(result.metrics, null);
  assert.match(result.message, /未确认/);
});

test('connected snapshot reports real counts and separates queue pause from device availability', () => {
  const result = platformSummary({paused:true, devices:[{state:'device'},{state:'offline'}], task_summary:{running:1,pending:4}}, false);
  assert.equal(result.state, 'ready');
  assert.deepEqual(result.metrics, {online:1,running:1,pending:4,queue:'已暂停'});
  assert.equal(platformSummary({}, false).metrics.online, null);
  assert.equal(platformSummary({}, false).metrics.queue, '未读取');
});

test('Skill download accepts a ZIP receipt and rejects failures without retry', async () => {
  const zip = await loadSkillArchive(async () => new Response('PK\u0003\u0004fixture', {headers:{'content-type':'application/zip'}}));
  assert.equal(zip.type, 'application/zip');
  assert.equal(zip.size, 11);
  for (const response of [new Response('failed',{status:503}), new Response('<html>error</html>',{headers:{'content-type':'text/html'}})]) {
    let calls=0;
    await assert.rejects(loadSkillArchive(async () => {calls++;return response;}));
    assert.equal(calls,1);
  }
});
