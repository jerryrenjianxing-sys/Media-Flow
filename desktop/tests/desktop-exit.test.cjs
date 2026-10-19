'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createDesktopExit } = require('../lib/desktop-exit.cjs');

test('complete exit waits for startup and update recovery before confirmed shutdown and merges repeated requests', async () => {
  const receipt = [];
  let ready;
  const startup = new Promise(resolve => { ready = resolve; });
  const exit = createDesktopExit({ waitForStartup: () => startup,
    updates: { prepareExit: async () => { receipt.push('update-recovered'); } },
    runtime: { stop: async () => { receipt.push('stopped-confirmed'); } }, quit: () => receipt.push('desktop-exited') });
  const first = exit.request(true);
  assert.equal(first, exit.request(true));
  assert.equal(exit.pending, true);
  assert.deepEqual(receipt, []);
  ready();
  await first;
  assert.deepEqual(receipt, ['update-recovered', 'stopped-confirmed', 'desktop-exited']);
  assert.equal(exit.pending, false);
});

test('busy or failed shutdown keeps the desktop open and permits a later interface-only exit', async () => {
  let exited = false;
  const exit = createDesktopExit({ waitForStartup: async () => {}, updates: { prepareExit: async () => {} },
    runtime: { stop: async () => { throw Object.assign(new Error('业务占用'), { code: 'stop_refused' }); } }, quit: () => { exited = true; } });
  await assert.rejects(exit.request(true), error => error.code === 'stop_refused');
  assert.equal(exited, false);
  await exit.request(false);
  assert.equal(exited, true);
});

test('installation handoff or incomplete recovery cannot be bypassed by either exit mode', async () => {
  const exit = createDesktopExit({ waitForStartup: async () => {}, updates: { prepareExit: async () => { throw new Error('安装正在交接'); } },
    runtime: { stop: async () => assert.fail('must not stop') }, quit: () => assert.fail('must not quit') });
  await assert.rejects(exit.request(true), /交接/);
  await assert.rejects(exit.request(false), /交接/);
});
