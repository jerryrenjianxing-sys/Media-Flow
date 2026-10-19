'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { clampBounds, externalBrowserUrl, canOpenInstanceBrowser } = require('../lib/window-state.cjs');
const { rewriteInstanceUrl } = require('../lib/session-policy.cjs');

test('window fits small and scaled work areas and restores within visible monitor', () => {
  const area = { x: 0, y: 0, width: 1366, height: 728 };
  const initial = clampBounds(null, area);
  assert.ok(initial.height <= area.height); assert.ok(initial.y >= 0);
  const scaled = { x: 0, y: 0, width: 911, height: 486 };
  const restored = clampBounds({ x: 2000, y: -500, width: 1360, height: 900 }, scaled);
  assert.deepEqual(restored, { x: 0, y: 0, width: 911, height: 486, minWidth: 900, minHeight: 486 });
  const secondMonitor = clampBounds({ x: -9000, y: 0, width: 1000, height: 700 }, { x: -1920, y: 0, width: 1920, height: 1040 });
  assert.equal(secondMonitor.x, -1920);
});

test('external links allow browser protocols, deny privileged schemes and remain instance scoped', () => {
  assert.equal(externalBrowserUrl('https://github.com/jerryrenjianxing-sys/Media-Flow', null, rewriteInstanceUrl), 'https://github.com/jerryrenjianxing-sys/Media-Flow');
  for (const url of ['file:///C:/secret.txt', 'powershell:run', 'javascript:alert(1)', 'https://user:secret@example.org/']) assert.equal(externalBrowserUrl(url), null);
  assert.equal(externalBrowserUrl('http://127.0.0.1:3001/settings', { acceptance: true, ui_port: 31301 }, rewriteInstanceUrl), null);
});

test('acceptance UI cannot escape session routing through browser links, production and documentation remain usable', () => {
  const acceptance = { acceptance: true, ui_port: 31301 };
  assert.equal(canOpenInstanceBrowser(acceptance), false);
  assert.equal(canOpenInstanceBrowser({ acceptance: false }), true);
  assert.equal(canOpenInstanceBrowser(null), false);
  for (const url of ['http://127.0.0.1:31301/', 'http://localhost:31301/settings', 'http://127.0.0.1:3001/']) assert.equal(externalBrowserUrl(url, acceptance, rewriteInstanceUrl), null);
  assert.equal(externalBrowserUrl('https://www.mumuplayer.com/', acceptance, rewriteInstanceUrl), 'https://www.mumuplayer.com/');
  assert.equal(externalBrowserUrl('http://127.0.0.1:3001/', { acceptance: false, ui_port: 3001 }, rewriteInstanceUrl), 'http://127.0.0.1:3001/');
});

test('splash removes acceptance browser href and preserves production browser action', async () => {
  const elements = Object.fromEntries(['message', 'retry', 'browser', 'version', 'logs'].map(id => [id, { addEventListener() {}, removeAttribute(name) { delete this[name]; } }]));
  let render;
  const state = { startup: { phase: 'error', message: 'startup error' }, desktopVersion: 'test', instance: { acceptance: true, browserAvailable: false, uiUrl: 'http://127.0.0.1:31301/' } };
  const context = { document: { getElementById: id => elements[id] }, window: { mediaflowDesktop: { subscribe: callback => { render = callback; }, getState: async () => state } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../splash.cjs'), 'utf8'), context);
  await Promise.resolve();
  assert.equal(elements.browser.hidden, true); assert.equal(elements.browser.href, undefined);
  render({ ...state, instance: { acceptance: false, browserAvailable: true, uiUrl: 'http://127.0.0.1:3001/' } });
  assert.equal(elements.browser.hidden, false); assert.equal(elements.browser.href, 'http://127.0.0.1:3001/');
});

test('sandbox preload exposes only scoped methods and subscription hides IPC event with idempotent unsubscribe', async () => {
  const ipc = new EventEmitter(); const calls = [];
  ipc.invoke = async channel => { calls.push(channel); return { value: 1 }; };
  ipc.sendSync = channel => { calls.push(channel); return { apiUrl: 'http://127.0.0.1:49338' }; };
  let exposed;
  const context = { require: module => { assert.equal(module, 'electron'); return { ipcRenderer: ipc, contextBridge: { exposeInMainWorld: (name, bridge) => { assert.equal(name, 'mediaflowDesktop'); exposed = bridge; } } }; } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../preload.cjs'), 'utf8'), context);
  assert.deepEqual(Object.keys(exposed).sort(), ['cancelUpdate', 'checkForUpdates', 'copyConnectionFile', 'revealConnectionFile', 'deferUpdate', 'getInstance', 'getState', 'openLogs', 'retryStartup', 'startUpdate', 'subscribe'].sort());
  const received = [];
  const unsubscribe = exposed.subscribe((...args) => received.push(args));
  ipc.emit('mediaflow:state', { sender: 'privileged' }, { phase: 'available' });
  assert.equal(received[0].length, 1); assert.equal(received[0][0].phase, 'available');
  unsubscribe(); unsubscribe(); ipc.emit('mediaflow:state', {}, { phase: 'changed' });
  assert.equal(received.length, 1);
  await exposed.startUpdate(); assert.deepEqual(calls, ['mediaflow:startUpdate']);
});
