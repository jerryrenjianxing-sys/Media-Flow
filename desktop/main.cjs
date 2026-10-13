'use strict';
const { app, BrowserWindow, ipcMain, Menu, Tray, nativeImage, shell, dialog, session, screen } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { resolveProfile, instanceUrls } = require('./lib/instance-profile.cjs');
const { DesktopState } = require('./lib/state.cjs');
const { createDiagnostics, Journal } = require('./lib/diagnostics.cjs');
const { RuntimeController } = require('./lib/runtime.cjs');
const { UpdateController } = require('./lib/update-controller.cjs');
const { createProfileUpdater } = require('./lib/profile-updater.cjs');
const { rewriteInstanceUrl, trustedSender } = require('./lib/session-policy.cjs');
const { clampBounds, externalBrowserUrl, canOpenInstanceBrowser } = require('./lib/window-state.cjs');

app.enableSandbox();
const packaged = app.isPackaged;
const sourceRoot = path.resolve(__dirname, '..');
const platformRoot = packaged ? path.join(process.resourcesPath, 'platform') : sourceRoot;
const splashUrl = pathToFileURL(path.join(__dirname, 'splash.html')).toString();
let profile;
let profileError;
try { profile = resolveProfile({ argv: process.argv, resourcesPath: process.resourcesPath, packaged, sourceRoot }); }
catch (error) { profileError = error; }
const userData = profile && !profile.development ? path.join(profile.data_root, 'desktop', profile.id) : path.join(app.getPath('appData'), 'MediaFlow', packaged ? 'invalid-instance' : 'development-desktop');
fs.mkdirSync(userData, { recursive: true });
app.setPath('userData', userData);
app.setPath('sessionData', path.join(userData, 'browser'));
const diagnostics = createDiagnostics(path.join(userData, 'logs'));
if (!app.requestSingleInstanceLock({ instanceId: profile?.id || 'invalid' })) { app.quit(); }
else {
  let window;
  let tray;
  let quitting = false;
  let startupPromise;
  const preferences = new Journal(path.join(userData, 'window-preferences.json'));
  const urls = profile ? instanceUrls(profile) : { apiUrl: '', uiUrl: '' };
  const packageInfo = require('./package.json');
  let buildInfo = {};
  try { buildInfo = JSON.parse(fs.readFileSync(path.join(__dirname, 'build-info.json'), 'utf8')); } catch { /* Source runs may not have a release stamp. */ }
  const browserAvailable = canOpenInstanceBrowser(profile);
  const state = new DesktopState({ desktopVersion: app.getVersion(), sourceRevision: buildInfo.sourceRevision || packageInfo.sourceRevision || 'development', instance: { ...urls, id: profile?.id || null, acceptance: Boolean(profile?.acceptance), browserAvailable }, packaged });
  const runtime = profile ? new RuntimeController({ profile, platformRoot, packaged, state, diagnostics }) : null;
  const cacheDirectory = path.join(userData, 'update-cache');
  let updater = null;
  if (packaged && profile) {
    const { NsisUpdater } = require('electron-updater');
    const adapter = { whenReady: () => app.whenReady(), get version() { return app.getVersion(); }, get name() { return app.getName(); }, isPackaged: true, userDataPath: userData,
      baseCachePath: cacheDirectory, appUpdateConfigPath: path.join(process.resourcesPath, 'app-update.yml'), quit: () => {
        void updater.waitForInstallerLaunch().then(() => { quitting = true; app.quit(); }).catch(async error => {
          quitting = false;
          updater.quitAndInstallCalled = false;
          diagnostics.write('error', 'installer-launch-failed', error);
          let message = `安装程序无法启动：${error.message}`;
          try { const identity = await runtime.startup(); await updates.recoverJournal(identity); }
          catch (recoveryError) { message += `；后台恢复需要重试：${recoveryError.message}`; }
          updates.patch({ phase: 'error', message, canCancel: false });
          showWindow();
        });
      }, relaunch: () => app.relaunch(), onQuit: handler => app.once('quit', (_event, code) => handler(code)) };
    updater = createProfileUpdater(NsisUpdater, { profilePath: profile.profilePath, installDirectory: path.dirname(process.execPath), adapter,
      publish: { provider: 'github', owner: 'jerryrenjianxing-sys', repo: 'Media-Flow', channel: 'dev', releaseType: 'prerelease' } });
  }
  const updates = new UpdateController({ updater, state, runtime, diagnostics, journal: new Journal(path.join(userData, 'update-operation.json')), profile: profile || { id: 'invalid' }, cacheDirectory,
    installDirectory: path.dirname(process.execPath), cancellationFactory: () => new (require('builder-util-runtime').CancellationToken)(), beforeInstall: showWindow });

  function showWindow() { if (!window) return; if (window.isMinimized()) window.restore(); window.show(); window.focus(); }
  function sendState(value) { if (window && !window.isDestroyed()) window.webContents.send('mediaflow:state', value); }
  state.on('change', sendState);
  app.on('second-instance', () => showWindow());
  app.on('activate', () => showWindow());
  app.on('window-all-closed', () => { /* Tray owns the desktop lifecycle; background is independent. */ });
  app.on('before-quit', () => { quitting = true; });

  async function startup() {
    if (startupPromise) return startupPromise;
    startupPromise = (async () => {
      if (profileError) { state.patch('startup', { phase: 'error', message: profileError.message, ready: false }); return state.get(); }
      try {
        const identity = await runtime.startup();
        await updates.recoverJournal(identity);
        await window.loadURL(urls.uiUrl);
        void updates.startupCheck();
      } catch (error) {
        state.patch('startup', { phase: 'error', message: error.message, ready: false });
        if (window.webContents.getURL() !== splashUrl) await window.loadURL(splashUrl);
        diagnostics.write('error', 'desktop-startup', error);
      }
      return state.get();
    })().finally(() => { startupPromise = null; });
    return startupPromise;
  }

  function registerBridge() {
    const actions = { getState: () => state.get(), checkForUpdates: () => updates.checkForUpdates(), startUpdate: () => updates.startUpdate(), deferUpdate: () => updates.deferUpdate(), cancelUpdate: () => updates.cancelUpdate(), retryStartup: startup,
      openLogs: async () => { const result = await shell.openPath(diagnostics.directory); if (result) throw new Error(result); return state.get(); } };
    for (const [name, action] of Object.entries(actions)) ipcMain.handle(`mediaflow:${name}`, (event, ...args) => {
      if (!trustedSender(event, window, urls.uiUrl, splashUrl) || args.length) throw new Error('Desktop request is not permitted.');
      return action();
    });
    ipcMain.on('mediaflow:getInstance', event => { event.returnValue = trustedSender(event, window, urls.uiUrl, splashUrl) ? { ...state.get().instance, streamUrl: profile ? `http://127.0.0.1:${profile.stream_port}` : '' } : null; });
  }

  async function exitDesktop() {
    if (updates.active && state.get().update.phase !== 'installing') {
      updates.active.cancelled = true;
      updates.active.token.cancel();
      await updates.runPromise;
      if (updates.journal.read()) { await dialog.showMessageBox(window, { type: 'error', message: '后台恢复尚未完成，请重试启动后再退出。' }); showWindow(); return; }
    }
    quitting = true;
    app.quit();
  }
  async function stopBackground() {
    if (updates.active) { showWindow(); return; }
    try {
      const result = await dialog.showMessageBox(window, { type: 'question', buttons: ['取消', '停止后台'], defaultId: 0, cancelId: 0, message: '停止该实例后台？', detail: '后台空闲时安全停止。关闭窗口或退出界面无需停止后台。' });
      if (result.response !== 1) return;
      await runtime.stop();
      await window.loadURL(splashUrl);
      showWindow();
    } catch (error) { diagnostics.write('error', 'tray-stop-failed', error); await dialog.showMessageBox(window, { type: 'error', message: error.message }); }
  }

  app.whenReady().then(async () => {
    const isolatedSession = session.fromPartition(`persist:mediaflow-${profile?.id || 'invalid'}`);
    isolatedSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
    isolatedSession.setPermissionCheckHandler(() => false);
    if (profile?.acceptance) isolatedSession.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*'] }, (details, callback) => {
      const redirectURL = rewriteInstanceUrl(details.url, profile);
      callback(redirectURL ? { redirectURL } : {});
    });
    const iconPath = path.join(platformRoot, 'assets', 'brand', 'mediaflow-tray.png');
    const saved = preferences.read();
    const validSavedBounds = saved?.bounds && ['x', 'y', 'width', 'height'].every(key => Number.isInteger(saved.bounds[key])) && saved.bounds.width > 0 && saved.bounds.height > 0;
    const area = validSavedBounds ? screen.getDisplayMatching(saved.bounds).workArea : screen.getPrimaryDisplay().workArea;
    const bounds = clampBounds(saved?.bounds, area);
    window = new BrowserWindow({ ...bounds, title: profile?.acceptance ? 'MediaFlow · 验收实例' : 'MediaFlow', backgroundColor: '#f8faf9', show: false, icon: iconPath,
      webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false, nodeIntegrationInWorker: false, webSecurity: true, session: isolatedSession } });
    window.removeMenu();
    if (saved?.maximized === true) window.maximize();
    let preferenceTimer;
    const savePreferences = () => { try { preferences.write({ schema: 1, bounds: window.getNormalBounds(), maximized: window.isMaximized() }); } catch (error) { diagnostics.write('warn', 'window-preference-save', error); } };
    const schedulePreferences = () => { clearTimeout(preferenceTimer); preferenceTimer = setTimeout(savePreferences, 250); };
    window.on('resize', schedulePreferences);
    window.on('move', schedulePreferences);
    window.on('close', event => { clearTimeout(preferenceTimer); savePreferences(); if (!quitting) { event.preventDefault(); window.hide(); } });
    window.webContents.setWindowOpenHandler(({ url }) => { const external = externalBrowserUrl(url, profile, rewriteInstanceUrl); if (external) void shell.openExternal(external); return { action: 'deny' }; });
    window.webContents.on('will-navigate', (event, url) => { if (url === splashUrl) return; try { if (new URL(url).origin === new URL(urls.uiUrl).origin) return; } catch { /* Deny unknown schemes. */ } event.preventDefault(); const external = externalBrowserUrl(url, profile, rewriteInstanceUrl); if (external) void shell.openExternal(external); });
    window.webContents.on('will-attach-webview', event => event.preventDefault());
    window.webContents.on('did-finish-load', () => sendState(state.get()));
    window.webContents.on('render-process-gone', (_event, details) => diagnostics.write('error', 'renderer-gone', details));
    registerBridge();
    tray = new Tray(nativeImage.createFromPath(iconPath));
    tray.setToolTip(profile?.acceptance ? 'MediaFlow · 验收实例' : 'MediaFlow');
    tray.on('double-click', showWindow);
    tray.setContextMenu(Menu.buildFromTemplate([{ label: '打开 MediaFlow', click: showWindow }, { label: '在浏览器中打开', visible: !profile?.acceptance, enabled: Boolean(urls.uiUrl) && browserAvailable, click: () => { if (browserAvailable) void shell.openExternal(urls.uiUrl); } },
      { label: '检查更新', enabled: packaged && Boolean(profile), click: () => { showWindow(); void updates.checkForUpdates(); } },
      { label: '打开日志', click: () => { void shell.openPath(diagnostics.directory); } }, { type: 'separator' }, { label: '停止后台', enabled: packaged && Boolean(profile), click: () => { void stopBackground(); } },
      { label: '退出界面（后台继续运行）', click: () => { void exitDesktop(); } }]));
    await window.loadURL(splashUrl);
    showWindow();
    diagnostics.write('info', 'desktop-started', { version: app.getVersion(), sourceRevision: state.get().sourceRevision, instance: profile?.id, packaged });
    void startup();
  }).catch(error => { diagnostics.write('error', 'desktop-fatal', error); dialog.showErrorBox('MediaFlow 启动失败', error.message); app.quit(); });
}
