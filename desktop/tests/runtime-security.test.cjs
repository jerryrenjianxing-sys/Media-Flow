'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { validateProfile, resolveProfile, sameInstance } = require('../lib/instance-profile.cjs');
const { RuntimeController, manageArguments, releaseRevisionMatches } = require('../lib/runtime.cjs');
const { DesktopState } = require('../lib/state.cjs');
const { rewriteInstanceUrl, trustedSender } = require('../lib/session-policy.cjs');
const { redact } = require('../lib/diagnostics.cjs');
const profile = { schema: 1, id: 'acceptance', acceptance: true, data_root: 'C:\\Isolated Data', ui_port: 31301, api_port: 49338, stream_port: 49339, task_name: 'MediaFlow Background Acceptance', profilePath: 'C:\\Isolated Data\\profile.json' };
const identity = { ok: true, ...profile, app_root: 'C:\\App\\resources\\platform', product_version: { version: '0.4.1-dev.45', source_revision: '123456789abc' } };
const fullRevision = '123456789abc' + '0'.repeat(28);
function fixture({ fetchImpl, packaged = true, now, sleep } = {}) {
  const calls = [];
  const state = new DesktopState({ desktopVersion: '0.4.1-dev.45', sourceRevision: fullRevision, instance: {}, packaged });
  const runtime = new RuntimeController({ profile, platformRoot: identity.app_root, packaged, state, diagnostics: { write() {} },
    fetchImpl: fetchImpl || (async url => url.endsWith('/api/runtime/instance') ? { ok: true, status: 200, text: async () => JSON.stringify(identity) } : { ok: true, body: { cancel: async () => {} } }),
    manager: async options => { calls.push(options); return options.action === 'Status' ? { instance_id: profile.id, stopped_confirmed: true, background: { state: 'stopped' }, processes: [] } : {}; }, now, sleep });
  return { runtime, state, calls };
}

test('profile rejects production collisions and paths inside install root', () => {
  const parsed = validateProfile(profile, profile.profilePath, 'C:\\App');
  assert.equal(parsed.id, 'acceptance');
  assert.throws(() => validateProfile({ ...profile, api_port: 48138 }, profile.profilePath, 'C:\\App'), /生产端口/);
  assert.throws(() => validateProfile({ ...profile, data_root: 'C:\\App\\data' }, profile.profilePath, 'C:\\App'), /程序目录外/);
  assert.throws(() => validateProfile({ ...profile, acceptance: false }, profile.profilePath), /身份不一致/);
  assert.throws(() => validateProfile({ ...profile, stream_port: profile.api_port }, profile.profilePath), /互不相同/);
});

test('packaged profile uses installer marker and never silently falls back to production', () => {
  const files = new Map([['C:\\App\\resources\\instance-profile.path', profile.profilePath], [profile.profilePath, JSON.stringify(profile)]]);
  const resolved = resolveProfile({ argv: [], resourcesPath: 'C:\\App\\resources', packaged: true, readFile: file => { if (!files.has(file)) throw new Error('missing'); return files.get(file); } });
  assert.equal(resolved.id, 'acceptance');
  assert.throws(() => resolveProfile({ argv: [], resourcesPath: 'C:\\Missing\\resources', packaged: true, readFile: () => { throw new Error('missing'); } }), /未找到安装实例登记/);
  assert.equal(sameInstance(identity, profile), true);
  assert.equal(sameInstance({ ...identity, id: 'production' }, profile), false);
});

test('existing healthy background is reused without Start or Restart', async () => {
  const f = fixture(); await Promise.all([f.runtime.startup(), f.runtime.startup()]);
  assert.deepEqual(f.calls, []); assert.equal(f.state.get().startup.ready, true);
});

test('wrong instance occupying port fails startup without starting or stopping it', async () => {
  const f = fixture({ fetchImpl: async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ ...identity, id: 'production' }) }) });
  await assert.rejects(f.runtime.startup(), /另一实例/); assert.deepEqual(f.calls, []);
});

test('packaged startup rejects wrong program root or version without starting or stopping any process', async () => {
  for (const changed of [{ app_root: 'C:\\AnotherApp\\resources\\platform' }, { product_version: { ...identity.product_version, version: '0.4.1-dev.44' } }]) {
    const f = fixture({ fetchImpl: async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ ...identity, ...changed }) }) });
    await assert.rejects(f.runtime.startup(), /另一程序目录|版本不一致/);
    assert.deepEqual(f.calls, []);
    assert.equal(f.state.get().startup.ready, false);
  }
});

test('packaged startup accepts matching full/short release revision and refuses another revision', async () => {
  assert.equal(releaseRevisionMatches(fullRevision, '123456789abc'), true);
  assert.equal(releaseRevisionMatches('123456789abc', fullRevision), true);
  assert.equal(releaseRevisionMatches('123456789abc', '123456789abd'), false);
  assert.equal(releaseRevisionMatches('123456789abc', 'unknown'), false);
  const f = fixture({ fetchImpl: async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ ...identity, product_version: { ...identity.product_version, source_revision: 'abcdefabcdef' } }) }) });
  await assert.rejects(f.runtime.startup(), /提交不一致/);
  assert.deepEqual(f.calls, []);
});

test('development mode never launches missing background', async () => {
  const f = fixture({ packaged: false, fetchImpl: async () => { throw new Error('offline'); } });
  await assert.rejects(f.runtime.startup(), /开发后台尚未就绪/); assert.deepEqual(f.calls, []);
});

test('bounded startup surfaces timeout and safe stop requires actual process confirmation', async () => {
  let clock = 0;
  const f = fixture({ now: () => clock, sleep: async milliseconds => { clock += milliseconds; }, fetchImpl: async () => { throw new Error('offline'); } });
  await assert.rejects(f.runtime.startup(), /60 秒/);
  assert.equal(f.calls.filter(value => value.action === 'Start').length, 1);
  assert.ok(clock <= 60000);
  const healthy = fixture(); healthy.runtime.manager = async () => ({ stopped_confirmed: false });
  await assert.rejects(healthy.runtime.stop('op1'), /进程存活/);
  assert.equal(healthy.state.get().startup.phase, 'checking');
});

test('management uses explicit profile/update operation and cannot start developer fallback', () => {
  const args = manageArguments(identity.app_root, profile, 'Stop', 'op1');
  assert.equal(args[args.indexOf('-InstanceProfilePath') + 1], profile.profilePath);
  assert.equal(args[args.indexOf('-UpdateOperationId') + 1], 'op1');
  assert.equal(args.includes('-NoBrowser'), true);
  assert.throws(() => manageArguments('C:\\source', { profilePath: null }, 'Start'), /开发模式/);
});

test('acceptance request routing rewrites exact production origins before network access', () => {
  assert.equal(rewriteInstanceUrl('http://127.0.0.1:48138/api/config?a=1', profile), 'http://127.0.0.1:49338/api/config?a=1');
  assert.equal(rewriteInstanceUrl('ws://localhost:48139/device/d1', profile), 'ws://127.0.0.1:49339/device/d1');
  assert.equal(rewriteInstanceUrl('http://127.0.0.1:3001/path', profile), 'http://127.0.0.1:31301/path');
  assert.equal(rewriteInstanceUrl('https://example.org:48138/api', profile), null);
  assert.equal(rewriteInstanceUrl('http://127.0.0.1:49338/api', profile), null);
  assert.equal(rewriteInstanceUrl('http://127.0.0.1:48138/api', { ...profile, acceptance: false }), null);
});

test('bridge rejects foreign origins and subframes even in same window', () => {
  const frame = { url: 'http://127.0.0.1:31301/settings' }; const webContents = { mainFrame: frame }; const window = { webContents };
  const event = { sender: webContents, senderFrame: frame };
  assert.equal(trustedSender(event, window, 'http://127.0.0.1:31301/', 'file:///splash.html'), true);
  assert.equal(trustedSender({ ...event, senderFrame: { ...frame } }, window, 'http://127.0.0.1:31301/', 'file:///splash.html'), false);
  frame.url = 'http://127.0.0.1:3001/'; assert.equal(trustedSender(event, window, 'http://127.0.0.1:31301/', 'file:///splash.html'), false);
});

test('diagnostic logs redact credentials and authenticated URLs', () => {
  const output = redact('Authorization: Bearer abc123 api_key=secret https://user:pass@example.org/file?token=abc&x=1');
  assert.equal(output.includes('abc123'), false); assert.equal(output.includes('api_key=secret'), false);
  assert.equal(output.includes('user:pass'), false); assert.equal(output.includes('token=abc'), false);
});
