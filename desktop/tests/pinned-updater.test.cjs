'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { NsisUpdater } = require('electron-updater');
const { ElectronHttpExecutor } = require('electron-updater/out/electronHttpExecutor');
const { createProfileUpdater } = require('../lib/profile-updater.cjs');

test('real pinned NsisUpdater binds Electron transport before creating GitHub provider with isolated cache adapter', async () => {
  assert.equal(require('electron-updater/package.json').version, '6.8.10');
  const adapter = { version: '0.4.1-dev.45', name: 'MediaFlow', isPackaged: true,
    userDataPath: 'C:\\Acceptance\\data\\desktop\\acceptance', baseCachePath: 'C:\\Acceptance\\data\\desktop\\acceptance\\update-cache',
    appUpdateConfigPath: 'C:\\Acceptance\\app\\resources\\app-update.yml', whenReady: async () => {}, quit() {}, relaunch() {}, onQuit() {} };
  const updater = createProfileUpdater(NsisUpdater, { profilePath: 'C:\\Acceptance\\profile.json', installDirectory: 'C:\\Acceptance\\app', adapter,
    publish: { provider: 'github', owner: 'jerryrenjianxing-sys', repo: 'Media-Flow', channel: 'dev', releaseType: 'prerelease' } });
  assert.ok(updater instanceof NsisUpdater);
  assert.ok(updater.httpExecutor instanceof ElectronHttpExecutor);
  assert.equal(updater.app, adapter);
  assert.equal(updater.app.baseCachePath, adapter.baseCachePath);
  const provider = await updater.clientPromise;
  assert.equal(provider.runtimeOptions.executor, updater.httpExecutor);
  assert.equal(provider.channel, 'dev');
  assert.equal(updater.allowDowngrade, false);
  assert.equal(updater.autoDownload, false);
  assert.equal(updater.autoInstallOnAppQuit, false);
  // Exercise the real provider-to-transport boundary without Electron net,
  // filesystem writes, live services or any GitHub request.
  let requested;
  updater.httpExecutor.request = async options => { requested = options; return 'offline transport receipt'; };
  const reply = await provider.httpRequest(new URL('https://github.com/jerryrenjianxing-sys/Media-Flow/releases.atom'));
  assert.equal(reply, 'offline transport receipt');
  assert.equal(requested.hostname, 'github.com');
  assert.equal(requested.path, '/jerryrenjianxing-sys/Media-Flow/releases.atom');
  let loginReceipt;
  updater.on('login', (authInfo, callback) => { loginReceipt = authInfo; callback('user', 'password'); });
  let credentials;
  updater.httpExecutor.proxyLoginCallback({ isProxy: true }, (...args) => { credentials = args; });
  assert.deepEqual(loginReceipt, { isProxy: true });
  assert.deepEqual(credentials, ['user', 'password']);
});
