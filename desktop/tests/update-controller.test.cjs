'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { DesktopState } = require('../lib/state.cjs');
const { UpdateController, versionAllowed, verifySha512, ensureDiskSpace } = require('../lib/update-controller.cjs');
const { createProfileUpdater, profileInstallerArguments } = require('../lib/profile-updater.cjs');

const sha512 = crypto.createHash('sha512').update('installer').digest('base64');
const info = { version: '0.4.1-dev.46', releaseNotes: '修复与改进', files: [{ url: 'MediaFlow-Setup.exe', sha512, size: 1024 }] };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function fixture(overrides = {}) {
  const state = new DesktopState({ desktopVersion: '0.4.1-dev.45', sourceRevision: 'development', instance: { apiUrl: '', uiUrl: '' }, packaged: true });
  const calls = [];
  let stored = null;
  const journal = { read: () => stored && structuredClone(stored), write: value => { stored = structuredClone(value); }, clear: () => { stored = null; } };
  const updater = new EventEmitter();
  updater.checkForUpdates = async () => { calls.push('check'); updater.emit('update-available', info); };
  updater.downloadUpdate = async token => { calls.push('download'); assert.equal(token.cancelled, false); return ['installer.exe']; };
  updater.quitAndInstall = (...args) => calls.push(['install', ...args]);
  const runtime = {
    api: async (endpoint, options) => {
      calls.push({ endpoint, body: options?.body });
      const status = endpoint.endsWith('/cancel') ? 'cancelled' : endpoint.endsWith('/complete') ? 'completed' : 'ready';
      const request = options?.body?.request_id ? options.body : calls.findLast(value => value?.body?.request_id)?.body;
      return { ok: true, operation: { operation_id: 'operation-1', status, blockers: [], request_id: request?.request_id || 'request-1', target_version: request?.target_version || info.version, sha512: request?.sha512 || sha512 } };
    },
    stop: async id => calls.push(['stop', id]), startup: async () => { calls.push('restart'); return { product_version: '0.4.1-dev.45' }; },
    probe: async () => ({ product_version: '0.4.1-dev.45' })
  };
  const diagnostics = { write() {}, info() {}, warn() {}, error() {}, debug() {} };
  const controller = new UpdateController({ updater, state, runtime, diagnostics, journal, profile: { id: 'acceptance' }, cacheDirectory: 'cache', installDirectory: 'install',
    cancellationFactory: () => ({ cancelled: false, cancel() { this.cancelled = true; } }), diskCheck: async () => calls.push('space'), verify: async (_file, hash) => { calls.push('verify'); return hash; },
    sleep: async () => {}, countdownSeconds: 1, beforeInstall: () => calls.push('before-install'), ...overrides });
  return { controller, updater, state, runtime, calls, journal };
}

test('dev channel admits higher future versions and rejects downgrade, same, malformed and another channel', () => {
  assert.equal(versionAllowed('0.4.1-dev.46', '0.4.1-dev.45'), true);
  assert.equal(versionAllowed('0.4.2-dev.1', '0.4.1-dev.45'), true);
  for (const candidate of ['0.4.1-dev.45', '0.4.1-dev.44', 'garbage', '0.4.2-beta.1', '0.4.2']) assert.equal(versionAllowed(candidate, '0.4.1-dev.45'), false);
  assert.equal(versionAllowed('0.4.2', '0.4.1', 'stable'), true);
});

test('updater configuration prevents automatic download, quit install, downgrade and web installer', () => {
  class FakeUpdater { constructor(publish, adapter) { this.publish = publish; this.adapter = adapter; } setFeedURL(publish) { this.publish = publish; } spawnLog(command, args) { return { command, args }; } }
  const updater = createProfileUpdater(FakeUpdater, { profilePath: 'C:\\Data Space\\instance.json', installDirectory: 'C:\\App', publish: {}, adapter: {} });
  assert.equal(updater.autoDownload, false);
  assert.equal(updater.autoInstallOnAppQuit, false);
  assert.equal(updater.disableWebInstaller, true);
  assert.equal(updater.disableDifferentialDownload, false);
  assert.equal(updater.allowPrerelease, true);
  assert.equal(updater.allowDowngrade, false);
  assert.equal(updater.channel, 'dev');
  const launched = updater.spawnLog('elevate.exe', ['setup.exe', '--updated', '--force-run', '/D=C:\\App']);
  assert.ok(launched instanceof Promise);
  assert.deepEqual(profileInstallerArguments('other.exe', ['--help'], 'C:\\p.json'), ['--help']);
  assert.deepEqual(profileInstallerArguments('elevate.exe', ['setup.exe', '--updated', '--force-run', '/D=C:\\App'], 'C:\\Data Space\\instance.json').slice(-2), ['/INSTANCEPROFILE=C:\\Data Space\\instance.json', '/D=C:\\App']);
});

test('missing installer cannot trigger unscoped shell fallback', async () => {
  class FakeUpdater { constructor() {} setFeedURL() {} spawnLog() { return Promise.reject(Object.assign(new Error('missing installer'), { code: 'ENOENT' })); } }
  const updater = createProfileUpdater(FakeUpdater, { profilePath: 'C:\\p.json', installDirectory: 'C:\\App', publish: {}, adapter: {} });
  await assert.rejects(updater.spawnLog('setup.exe', ['--updated']), error => error.code === 'ERR_MEDIAFLOW_INSTALLER_LAUNCH');
});

test('installer launch failure does not authorize quitting and successful elevation fallback does', async () => {
  class FakeUpdater { constructor() {} setFeedURL() {} spawnLog(_command, args) { return args[0] === 'fail.exe' ? Promise.reject(new Error('cannot execute installer')) : Promise.resolve(true); } }
  const updater = createProfileUpdater(FakeUpdater, { profilePath: 'C:\\p.json', installDirectory: 'C:\\App', publish: {}, adapter: {} });
  await assert.rejects(updater.waitForInstallerLaunch(), /启动回执/);
  await updater.spawnLog('setup.exe', ['fail.exe', '--updated']).catch(() => {});
  await assert.rejects(updater.waitForInstallerLaunch(), /cannot execute/);
  const first = updater.spawnLog('setup.exe', ['fail.exe', '--updated']);
  first.catch(() => { void updater.spawnLog('elevate.exe', ['success.exe', '--updated']); });
  await updater.waitForInstallerLaunch();
});

test('asynchronous installer launch failure recovers original background and reports only safe state', async () => {
  const logged = [];
  const f = fixture({ diagnostics: { write: (...event) => logged.push(event), info() {}, warn() {}, error() {}, debug() {} } });
  const launchError = new Error('spawn \\\\private-server\\Users\\Jerry\\setup.exe EACCES');
  const recoveryError = new Error('HttpError: recovery response Headers: authorization Bearer private');
  f.updater.quitAndInstallCalled = true;
  f.runtime.startup = async () => { throw recoveryError; };
  f.journal.write({ schema: 1, instanceId: 'acceptance', operationId: 'operation-1', requestId: 'request-1', targetVersion: info.version, sha512, phase: 'installing' });
  await f.controller.installerLaunchFailed(launchError);
  const {message, phase} = f.state.get().update;
  assert.equal(phase, 'error');
  assert.match(message, /安装程序未能启动/);
  assert.match(message, /后台恢复尚未确认/);
  assert.ok(message.length <= 160);
  assert.doesNotMatch(message, /private-server|Users|Jerry|HttpError|Headers|authorization|Bearer/i);
  assert.equal(f.updater.quitAndInstallCalled, false);
  assert.ok(logged.some(event => event[1] === 'installer-launch-failed' && event[2] === launchError));
  assert.ok(logged.some(event => event[1] === 'installer-recovery-failed' && event[2] === recoveryError));
  assert.equal(f.journal.read().operationId, 'operation-1');
  assert.equal(f.calls.some(value => value === 'download' || Array.isArray(value) && value[0] === 'install'), false);
});

test('installer launch failure confirms original journal cancellation before saying background restored', async () => {
  const f = fixture();
  f.journal.write({ schema: 1, instanceId: 'acceptance', operationId: 'operation-1', requestId: 'request-1', targetVersion: info.version, sha512, phase: 'installing' });
  await f.controller.installerLaunchFailed(new Error('spawn \\\\private-server\\setup.exe EACCES'));
  assert.equal(f.state.get().update.phase, 'error');
  assert.match(f.state.get().update.message, /后台已恢复/);
  assert.doesNotMatch(f.state.get().update.message, /private-server|EACCES/i);
  assert.equal(f.calls[0], 'restart');
  assert.match(f.calls[1].endpoint, /operation-1\/cancel$/);
  assert.equal(f.journal.read(), null);
  assert.equal(f.calls.some(value => value === 'download' || Array.isArray(value) && value[0] === 'install'), false);
});

test('startup checks once and page refresh/concurrent checks neither download nor install', async () => {
  const f = fixture();
  await Promise.all([f.controller.startupCheck(), f.controller.startupCheck()]);
  assert.deepEqual(f.calls, ['check']);
  assert.equal(f.state.get().update.phase, 'available');
  const snapshot = f.state.get(); snapshot.update.phase = 'installing';
  assert.equal(f.state.get().update.phase, 'available');
  await f.controller.deferUpdate();
  assert.equal(f.state.get().update.phase, 'idle');
  assert.deepEqual(f.calls, ['check']);
});

test('installed candidate and published version remain separate and automatic check failure is nonblocking', async () => {
  const f = fixture();
  f.updater.checkForUpdates = async () => { f.updater.emit('update-not-available', { version: '0.4.1-dev.44' }); };
  await f.controller.startupCheck();
  assert.equal(f.state.get().desktopVersion, '0.4.1-dev.45');
  assert.equal(f.state.get().update.publishedVersion, '0.4.1-dev.44');
  assert.match(f.state.get().update.message, /高于最新公开版本/);
  f.updater.checkForUpdates = async () => { throw new Error('offline'); };
  await f.controller.checkForUpdates({ automatic: true });
  assert.equal(f.state.get().update.phase, 'check_unavailable');
  assert.equal(f.state.get().update.checkAutomatic, true);
});

test('an inactive recovery journal permits interface-only exit without clearing the gate, but blocks complete exit', async () => {
  const f = fixture();
  f.journal.write({ phase: 'stopped', operationId: 'operation-1' });
  await assert.rejects(f.controller.prepareExit(), /恢复尚未确认/);
  await f.controller.prepareExit({ keepBackground: true });
  assert.equal(f.journal.read().operationId, 'operation-1');
  assert.deepEqual(f.calls, []);
  f.journal.write({ phase: 'installing', operationId: 'operation-1' });
  await assert.rejects(f.controller.prepareExit({ keepBackground: true }), error => error.code === 'ERR_MEDIAFLOW_INSTALLER_HANDOFF');
});

test('unavailable update metadata reports a short safe reason while platform remains usable', async () => {
  const logged = [];
  const f = fixture({ diagnostics: { write: (...event) => logged.push(event), info() {}, warn() {}, error() {}, debug() {} } });
  const failure = Object.assign(new Error(`HttpError: 404 Not Found\nHeaders: { authorization: Bearer private }\n at C:\\Private User\\MediaFlow\\resources\\app.asar\\main.cjs\n${'upstream body '.repeat(2000)}`), { statusCode: 404 });
  f.updater.checkForUpdates = async () => { throw failure; };
  await f.controller.checkForUpdates();
  const {message, phase} = f.state.get().update;
  assert.equal(phase, 'check_unavailable');
  assert.ok(message.length <= 160);
  assert.match(message, /更新资料暂不可用/);
  assert.match(message, /仍可继续使用平台/);
  assert.doesNotMatch(message, /HttpError|Headers|authorization|Bearer|Private User|app\.asar|upstream body/i);
  assert.equal(f.calls.some(value => value === 'download' || Array.isArray(value) && ['stop', 'install'].includes(value[0])), false);
  assert.ok(logged.some(event => event[1] === 'update-check-failed' && event[2] === failure));
});

test('network and unknown updater errors have concise Chinese recovery text without raw details', async () => {
  for (const failure of [Object.assign(new Error('connect failed at C:\\Private\\cache'), { code: 'ETIMEDOUT' }), new Error('upstream secret body'), Object.assign(new Error('HttpError: 404\nHeaders: private'), { statusCode: 404 })]) {
    const f = fixture();
    f.updater.emit('error', failure);
    const {message, phase} = f.state.get().update;
    assert.equal(phase, 'check_unavailable');
    assert.ok(message.length <= 160 && /[\u3400-\u9fff]/.test(message));
    assert.doesNotMatch(message, /HttpError|Headers|private|cache|upstream|secret/i);
    assert.match(message, /平台.*可.*使用|可继续使用平台/);
    if (failure.code === 'ETIMEDOUT') assert.match(message, /网络/);
    assert.deepEqual(f.calls, []);
  }
});

test('concurrent update clicks perform one download and verify before preparing/stopping/visible install', async () => {
  const wait = deferred();
  const f = fixture();
  f.updater.downloadUpdate = async () => { f.calls.push('download'); return wait.promise; };
  await f.controller.checkForUpdates();
  const first = f.controller.startUpdate();
  const second = f.controller.startUpdate();
  assert.equal(first, second);
  await Promise.resolve();
  assert.equal(f.calls.filter(value => value === 'download').length, 1);
  f.updater.emit('download-progress', { percent: 25, bytesPerSecond: 120, transferred: 256, total: 1024 });
  assert.equal(f.state.get().update.percent, 25);
  wait.resolve(['installer.exe']);
  await first;
  const verifyIndex = f.calls.indexOf('verify');
  const prepareIndex = f.calls.findIndex(value => value?.endpoint?.endsWith('/prepare'));
  const stopIndex = f.calls.findIndex(value => Array.isArray(value) && value[0] === 'stop');
  assert.ok(verifyIndex < prepareIndex && prepareIndex < stopIndex);
  assert.deepEqual(f.calls.at(-1), ['install', false, true]);
  assert.equal(f.journal.read().phase, 'installing');
});

test('hash and disk failure leave background untouched and never install', async () => {
  for (const stage of ['diskCheck', 'verify']) {
    const f = fixture({ [stage]: async () => { throw new Error(stage === 'verify' ? 'SHA512 mismatch' : '磁盘空间不足'); } });
    await f.controller.checkForUpdates(); await f.controller.startUpdate();
    assert.equal(f.state.get().update.phase, 'error');
    assert.equal(f.calls.some(value => value?.endpoint), false);
    assert.equal(f.calls.some(value => Array.isArray(value) && ['stop', 'install'].includes(value[0])), false);
    assert.match(f.state.get().update.message, stage === 'verify' ? /校验失败/ : /磁盘空间不足/);
  }
});

test('differential fallback is reported while download progress stays available', async () => {
  const wait = deferred(); const f = fixture();
  f.updater.downloadUpdate = () => wait.promise;
  await f.controller.checkForUpdates(); const run = f.controller.startUpdate(); await Promise.resolve();
  f.updater.logger.error('Cannot download differentially, fallback to full download: missing blockmap');
  f.updater.emit('download-progress', { percent: 50, transferred: 512, total: 1024, bytesPerSecond: 100 });
  assert.equal(f.state.get().update.differential, 'full');
  assert.match(f.state.get().update.message, /完整安装包/);
  wait.resolve(['installer.exe']); await run;
});

test('download cancellation stops token and never prepares backend', async () => {
  const wait = deferred(); const f = fixture();
  f.updater.downloadUpdate = token => { token.cancel = () => { token.cancelled = true; wait.reject(Object.assign(new Error('cancelled'), { name: 'CancellationError' })); }; return wait.promise; };
  await f.controller.checkForUpdates(); const run = f.controller.startUpdate(); await Promise.resolve();
  await f.controller.cancelUpdate(); await run;
  assert.equal(f.state.get().update.phase, 'available');
  assert.equal(f.calls.some(value => value?.endpoint), false);
});

test('waiting busy permits cancel, releases original gate, and never stops host', async () => {
  const f = fixture();
  f.runtime.api = async (endpoint, options) => { f.calls.push({ endpoint, body: options?.body }); const request = options?.body?.request_id ? options.body : f.calls.findLast(value => value?.body?.request_id)?.body; return { ok: true, operation: { operation_id: 'operation-1', request_id: request.request_id, target_version: request.target_version, sha512: request.sha512, status: endpoint.endsWith('/cancel') ? 'cancelled' : 'waiting_idle', blockers: [{ kind: 'initialization', id: 'job1' }] } }; };
  f.state.on('change', value => { if (value.update.blockers.length) void f.controller.cancelUpdate(); });
  await f.controller.checkForUpdates(); await f.controller.startUpdate();
  assert.ok(f.calls.some(value => value?.endpoint?.endsWith('/operation-1/cancel')));
  assert.equal(f.calls.some(value => Array.isArray(value) && value[0] === 'stop'), false);
  assert.equal(f.journal.read(), null);
});

test('countdown cancel restarts original instance then releases gate without installer', async () => {
  const f = fixture();
  f.state.on('change', value => { if (value.update.phase === 'countdown') void f.controller.cancelUpdate(); });
  await f.controller.checkForUpdates(); await f.controller.startUpdate();
  const restart = f.calls.indexOf('restart');
  const cancel = f.calls.findIndex(value => value?.endpoint?.endsWith('/cancel'));
  assert.ok(restart >= 0 && restart < cancel);
  assert.equal(f.calls.some(value => Array.isArray(value) && value[0] === 'install'), false);
  assert.equal(f.journal.read(), null);
});

test('actual stop failure prevents install and records error', async () => {
  const f = fixture(); f.runtime.stop = async () => { throw new Error('own process still running'); };
  await f.controller.checkForUpdates(); await f.controller.startUpdate();
  assert.equal(f.state.get().update.phase, 'error');
  assert.match(f.state.get().update.message, /后台未能确认已停止，未安装/);
  assert.doesNotMatch(f.state.get().update.message, /still running/);
  assert.equal(f.calls.some(value => Array.isArray(value) && value[0] === 'install'), false);
});

test('prepare response lost reuses persisted request id instead of orphaning gate', async () => {
  const f = fixture(); const requests = []; const original = f.runtime.api;
  f.runtime.api = async (endpoint, options) => {
    if (endpoint.endsWith('/prepare')) {
      assert.equal(f.journal.read().phase, 'preparing');
      assert.equal(f.journal.read().requestId, options.body.request_id);
      requests.push(structuredClone(options.body));
      if (requests.length === 1) throw new Error('response lost after accepted');
    }
    return original(endpoint, options);
  };
  await f.controller.checkForUpdates(); await f.controller.startUpdate();
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0], requests[1]);
  assert.deepEqual(f.calls.at(-1), ['install', false, true]);
});

test('preparing journal survives restart and reconciles same request then cancellation', async () => {
  const f = fixture();
  f.journal.write({ schema: 1, instanceId: 'acceptance', operationId: null, requestId: 'request-old', targetVersion: info.version, sha512, phase: 'preparing' });
  await f.controller.recoverJournal({ product_version: '0.4.1-dev.45' });
  assert.equal(f.calls[0].body.request_id, 'request-old');
  assert.match(f.calls[1].endpoint, /operation-1\/cancel$/);
  assert.equal(f.journal.read(), null);
});

test('unconfirmed cancel retains journal for subsequent startup recovery', async () => {
  const f = fixture(); const original = f.runtime.api;
  f.runtime.api = async (endpoint, options) => { if (endpoint.endsWith('/cancel')) throw new Error('HttpError: private recovery body at C:\\Private\\app.asar'); return original(endpoint, options); };
  f.state.on('change', value => { if (value.update.phase === 'countdown') void f.controller.cancelUpdate(); });
  await f.controller.checkForUpdates(); await f.controller.startUpdate();
  assert.equal(f.state.get().update.phase, 'error'); assert.equal(f.journal.read().operationId, 'operation-1');
  assert.match(f.state.get().update.message, /后台恢复尚未确认/);
  assert.ok(f.state.get().update.message.length <= 160);
  assert.doesNotMatch(f.state.get().update.message, /HttpError|private|app\.asar/i);
  f.runtime.api = original;
  await f.controller.recoverJournal({ product_version: '0.4.1-dev.45' });
  assert.equal(f.journal.read(), null);
});

test('completed update requires exact new desktop and backend versions before release', async () => {
  const f = fixture();
  f.journal.write({ schema: 1, instanceId: 'acceptance', operationId: 'operation-1', requestId: 'request-1', targetVersion: '0.4.1-dev.45', sha512, phase: 'installing' });
  await assert.rejects(f.controller.recoverJournal({ product_version: '0.4.1-dev.44' }), /版本不一致/);
  assert.ok(f.journal.read()); assert.equal(f.calls.length, 0);
  await f.controller.recoverJournal({ product_version: '0.4.1-dev.45' });
  assert.match(f.calls[0].endpoint, /complete$/); assert.equal(f.journal.read(), null);
});

test('real API version metadata is read before completing an installed update', async () => {
  const f = fixture();
  f.journal.write({ schema: 1, instanceId: 'acceptance', operationId: 'operation-1', requestId: 'request-1', targetVersion: '0.4.1-dev.45', sha512, phase: 'installing' });
  await f.controller.recoverJournal({ product_version: { version: '0.4.1-dev.45', source_revision: 'test' } });
  assert.match(f.calls[0].endpoint, /complete$/);
  assert.equal(f.journal.read(), null);
});

test('unclear terminal receipt does not clear durable recovery journal', async () => {
  const f = fixture();
  f.journal.write({ schema: 1, instanceId: 'acceptance', operationId: 'operation-1', requestId: 'request-1', targetVersion: info.version, sha512, phase: 'prepared' });
  f.runtime.api = async () => ({ ok: true, operation: { operation_id: 'operation-other', status: 'cancelled' } });
  await assert.rejects(f.controller.recoverJournal({ product_version: '0.4.1-dev.45' }), /尚未确认/);
  assert.equal(f.journal.read().operationId, 'operation-1');
});

test('real SHA512 verification accepts original installer and rejects modified bytes', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mediaflow-desktop-hash-'));
  try {
    const file = path.join(directory, 'setup.exe'); fs.writeFileSync(file, 'installer');
    assert.equal(await verifySha512(file, sha512, directory, () => {}), sha512);
    fs.writeFileSync(file, 'corrupt');
    await assert.rejects(verifySha512(file, sha512, directory, () => {}), /SHA512/);
    await assert.rejects(verifySha512(file, sha512, path.join(directory, 'other'), () => {}), /缓存/);
    await assert.rejects(ensureDiskSpace({ bytes: 0, cacheDirectory: directory, installDirectory: directory }), /文件大小/);
  } finally { fs.rmSync(directory, { recursive: true }); }
});
