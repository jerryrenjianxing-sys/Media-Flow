'use strict';
const fs = require('node:fs');
const crypto = require('node:crypto');
const semver = require('semver');
const { isWithin } = require('./instance-profile.cjs');
const { delay, releaseRevisionMatches } = require('./runtime.cjs');

class UpdateCancelled extends Error { constructor() { super('已取消更新。'); this.name = 'UpdateCancelled'; } }
function versionAllowed(version, current, channel = 'dev') {
  if (!semver.valid(version) || !semver.valid(current) || !semver.gt(version, current)) return false;
  const prerelease = semver.prerelease(version);
  return channel === 'stable' ? prerelease === null : prerelease?.[0] === channel;
}
function installerInfo(info) {
  const file = info?.files?.find(item => /\.exe(?:\?|$)/i.test(item.url || ''));
  return file || { url: info?.path, sha512: info?.sha512, size: info?.size };
}
function notesText(notes) { return typeof notes === 'string' ? notes : Array.isArray(notes) ? notes.map(item => `${item.version || ''}\n${item.note || ''}`).join('\n\n') : ''; }
function confirmedTerminal(response, operationId, terminal) {
  const operation = response.operation || response;
  if (operation.operation_id !== operationId || operation.status !== terminal) throw new Error('后台尚未确认该更新操作已结束，恢复记录将保留。');
}
function expectedOperation(response, expected) {
  const operation = response.operation || response;
  if (!operation.operation_id || (expected.operationId && operation.operation_id !== expected.operationId) || operation.request_id !== expected.requestId ||
    operation.target_version !== expected.targetVersion || operation.sha512 !== expected.sha512) throw new Error('后台更新回执与原请求、版本或校验值不一致。');
  return operation;
}

async function verifySha512(file, expected, cacheDirectory, assertActive) {
  if (!expected || Buffer.from(expected, 'base64').length !== 64) throw new Error('更新清单缺少有效 SHA512。');
  const realFile = await fs.promises.realpath(file);
  if (!isWithin(cacheDirectory, realFile)) throw new Error('下载文件不在该实例更新缓存中。');
  const metadata = await fs.promises.stat(realFile);
  if (!metadata.isFile()) throw new Error('更新缓存不是可安装文件。');
  const hash = crypto.createHash('sha512');
  for await (const chunk of fs.createReadStream(realFile)) { assertActive(); hash.update(chunk); }
  assertActive();
  const actual = hash.digest();
  if (!crypto.timingSafeEqual(actual, Buffer.from(expected, 'base64'))) throw new Error('SHA512 校验失败。未停止后台或安装。');
  return actual.toString('base64');
}

async function ensureDiskSpace({ bytes, cacheDirectory, installDirectory }) {
  if (!Number.isSafeInteger(bytes) || bytes <= 0) throw new Error('更新清单缺少有效文件大小，无法检查可用空间。');
  await fs.promises.mkdir(cacheDirectory, { recursive: true });
  const required = bytes * 3 + 100 * 1024 * 1024;
  for (const directory of new Set([cacheDirectory, installDirectory])) {
    const disk = await fs.promises.statfs(directory);
    if (Number(disk.bavail) * Number(disk.bsize) < required) throw new Error('磁盘空间不足。请释放空间后重试更新。');
  }
}

class UpdateController {
  constructor({ updater, state, runtime, diagnostics, journal, profile, cacheDirectory, installDirectory, cancellationFactory,
    diskCheck = ensureDiskSpace, verify = verifySha512, sleep = delay, beforeInstall = () => {}, countdownSeconds = 10 }) {
    Object.assign(this, { updater, state, runtime, diagnostics, journal, profile, cacheDirectory, installDirectory, cancellationFactory, diskCheck, verify, sleep, beforeInstall, countdownSeconds });
    this.info = null;
    this.checkPromise = null;
    this.runPromise = null;
    this.active = null;
    this.startupChecked = false;
    if (updater) this.bindUpdater();
  }
  patch(changes) { return this.state.patch('update', changes); }
  bindUpdater() {
    this.updater.on('checking-for-update', () => this.patch({ phase: 'checking', message: '正在检查新版…', canCancel: false }));
    this.updater.on('update-available', info => {
      if (!versionAllowed(info.version, this.state.get().desktopVersion)) {
        this.info = null;
        this.patch({ phase: 'error', message: '更新清单不属于较新的开发版本。', canCancel: false });
        return;
      }
      this.info = info;
      this.patch({ phase: 'available', version: info.version, publishedVersion: info.version, releaseNotes: notesText(info.releaseNotes), message: `发现新版 ${info.version}。点击更新后开始下载。`, canCancel: false });
    });
    this.updater.on('update-not-available', info => {
      this.info = null;
      const publishedVersion = semver.valid(info?.version);
      const ahead = publishedVersion && semver.gt(this.state.get().desktopVersion, publishedVersion);
      this.patch({ phase: 'current', version: null, publishedVersion, message: ahead ? '当前安装版本高于最新公开版本，暂无可更新版本。' : '当前已是最新公开开发版本。', canCancel: false });
    });
    this.updater.on('download-progress', progress => {
      if (!this.active || this.active.cancelled) return;
      this.patch({ phase: 'downloading', percent: progress.percent, bytesPerSecond: progress.bytesPerSecond, transferred: progress.transferred, total: progress.total, message: this.state.get().update.differential === 'full' ? '差分不可用，正在下载完整安装包…' : '正在下载更新…', canCancel: true });
    });
    this.updater.on('error', error => {
      this.diagnostics.write('error', 'updater-error', error);
      if (!this.active && error.name !== 'CancellationError') { this.info = null; this.patch({ phase: 'check_unavailable', version: null, publishedVersion: null, message: this.errorMessage(error, 'checking'), canCancel: false }); }
    });
    const diagnostics = this.diagnostics;
    this.updater.logger = {
      info: (...args) => { diagnostics.info(...args); if (/Update has already been downloaded/i.test(args.join(' '))) this.patch({ differential: 'cached', message: '正在复用已下载的安装包，并重新校验。' }); },
      debug: (...args) => diagnostics.debug(...args), warn: (...args) => diagnostics.warn(...args),
      error: (...args) => {
        const text = args.join(' ');
        diagnostics.error(...args);
        if (/fallback to full download|Cannot download differentially/i.test(text)) this.patch({ differential: 'full', message: '差分不可用，正在改用完整安装包下载。' });
      }
    };
  }
  startupCheck() { if (this.startupChecked) return Promise.resolve(this.state.get()); this.startupChecked = true; return this.checkForUpdates({ automatic: true }); }
  checkForUpdates({ automatic = false } = {}) {
    if (this.runPromise) return Promise.resolve(this.state.get());
    if (!this.updater) { this.patch({ phase: 'current', message: '开发模式连接现有后台；安装版支持在线更新。' }); return Promise.resolve(this.state.get()); }
    if (this.checkPromise) { if (!automatic) this.patch({ checkAutomatic: false }); return this.checkPromise; }
    this.patch({ checkAutomatic: automatic });
    this.checkPromise = this.updater.checkForUpdates().then(() => this.state.get()).catch(error => {
      this.diagnostics.write('error', 'update-check-failed', error);
      this.info = null;
      this.patch({ phase: 'check_unavailable', version: null, publishedVersion: null, message: this.errorMessage(error, 'checking'), canCancel: false });
      return this.state.get();
    }).finally(() => { this.checkPromise = null; });
    return this.checkPromise;
  }
  startUpdate() {
    if (this.runPromise) return this.runPromise;
    if (!this.updater || !this.info) return Promise.resolve(this.patch({ phase: 'error', message: '请先检查并选择可用更新。', canCancel: false }));
    const active = { cancelled: false, token: this.cancellationFactory(), operationId: null, request: null, backgroundStopped: false, stopping: false };
    this.active = active;
    this.runPromise = this.performUpdate(active).finally(() => { this.active = null; this.runPromise = null; });
    return this.runPromise;
  }
  assertActive(active) { if (active.cancelled || this.active !== active) throw new UpdateCancelled(); }
  async performUpdate(active) {
    try {
      if (this.journal.read()) await this.recoverJournal(await this.runtime.probe());
      const selected = this.info;
      const file = installerInfo(selected);
      this.patch({ phase: 'downloading', version: selected.version, percent: 0, transferred: 0, total: file.size || 0, bytesPerSecond: 0, message: '正在检查空间并准备下载…', canCancel: true, differential: 'preferred' });
      await this.diskCheck({ bytes: file.size, cacheDirectory: this.cacheDirectory, installDirectory: this.installDirectory });
      this.assertActive(active);
      const files = await this.updater.downloadUpdate(active.token);
      this.assertActive(active);
      const downloaded = this.state.get().update;
      this.diagnostics.write('info', 'download-finished', { version: selected.version, transferred: downloaded.transferred, total: downloaded.total, differential: downloaded.differential });
      this.patch({ phase: 'verifying', message: '正在校验安装包 SHA512…', percent: 100, bytesPerSecond: 0, canCancel: true });
      if (!files?.[0]) throw new Error('下载未返回安装文件。');
      const sha512 = await this.verify(files[0], file.sha512 || selected.sha512, this.cacheDirectory, () => this.assertActive(active));
      this.assertActive(active);
      this.diagnostics.write('info', 'download-sha512-verified', { version: selected.version, sha512 });
      const requestId = crypto.randomUUID();
      active.request = { request_id: requestId, target_version: selected.version, sha512 };
      // Persist before POST: a lost response must be retried with the same ID.
      this.journal.write({ schema: 1, instanceId: this.profile.id, operationId: null, requestId, targetVersion: selected.version, sha512, phase: 'preparing' });
      this.patch({ phase: 'waiting_idle', message: '正在检查后台是否空闲…', canCancel: true });
      let prepared = await this.apiWithReceipt('/api/runtime/update/prepare', { method: 'POST', body: active.request });
      let operation = expectedOperation(prepared, { requestId, targetVersion: selected.version, sha512 });
      active.operationId = operation.operation_id || prepared.operation_id;
      if (!active.operationId || !/^[a-zA-Z0-9_-]{1,128}$/.test(active.operationId)) throw new Error('后台未返回有效更新操作编号。');
      this.journal.write({ schema: 1, instanceId: this.profile.id, operationId: active.operationId, requestId, targetVersion: selected.version, sha512, phase: 'prepared' });
      this.diagnostics.write('info', 'update-prepared', { operationId: active.operationId, requestId, version: selected.version });
      this.assertActive(active);
      while (operation.status !== 'ready') {
        if (operation.status !== 'waiting_idle') throw new Error('后台更新操作状态无效。');
        this.patch({ phase: 'waiting_idle', message: operation.message || '任务或设备维护仍占用后台。空闲后继续。', blockers: operation.blockers || [], operationId: active.operationId, canCancel: true });
        await this.sleep(1500);
        this.assertActive(active);
        prepared = await this.apiWithReceipt(`/api/runtime/update/${encodeURIComponent(active.operationId)}`);
        operation = expectedOperation(prepared, { operationId: active.operationId, requestId, targetVersion: selected.version, sha512 });
      }
      this.assertActive(active);
      active.stopping = true;
      this.patch({ phase: 'waiting_idle', message: '正在安全停止该实例后台并核验进程退出…', blockers: [], canCancel: false });
      try { await this.runtime.stop(active.operationId); }
      catch (error) { this.diagnostics.write('error', 'update-background-stop-failed', error); throw Object.assign(new Error('后台未能确认已停止。'), { code: 'ERR_MEDIAFLOW_UPDATE_STOP' }); }
      active.backgroundStopped = true;
      active.stopping = false;
      this.journal.write({ ...this.journal.read(), phase: 'stopped' });
      this.assertActive(active);
      for (let seconds = this.countdownSeconds; seconds > 0; seconds -= 1) {
        this.patch({ phase: 'countdown', countdownSeconds: seconds, message: `${seconds} 秒后打开更新安装程序…`, canCancel: true });
        await this.sleep(1000);
        this.assertActive(active);
      }
      this.patch({ phase: 'installing', countdownSeconds: 0, message: '正在打开安装程序，完成后将重新启动。', canCancel: false });
      this.journal.write({ ...this.journal.read(), phase: 'installing' });
      this.beforeInstall();
      this.updater.quitAndInstall(false, true);
      if (this.updater.quitAndInstallCalled === false) throw new Error('安装程序未能启动，请重试。');
      return this.state.get();
    } catch (error) {
      active.stopping = false;
      const cancelled = active.cancelled || error instanceof UpdateCancelled || error.name === 'CancellationError';
      let recoveryError = null;
      try { await this.releaseOperation(active); } catch (releaseError) { recoveryError = releaseError; }
      this.diagnostics.write(cancelled ? 'info' : 'error', cancelled ? 'update-cancelled' : 'update-failed', error);
      if (recoveryError) this.diagnostics.write('error', 'update-recovery-failed', recoveryError);
      this.patch({ phase: recoveryError ? 'error' : cancelled ? 'available' : 'error', countdownSeconds: null, blockers: [], canCancel: false,
        message: recoveryError ? `${cancelled ? '更新已取消。' : this.errorMessage(error)}后台恢复尚未确认，请查看日志后重试；不会继续安装。` : cancelled ? '更新已取消，可稍后重新开始。' : this.errorMessage(error) });
      return this.state.get();
    }
  }
  async releaseOperation(active) {
    if (!active.operationId && active.request) {
      const receipt = await this.apiWithReceipt('/api/runtime/update/prepare', { method: 'POST', body: active.request });
      active.operationId = expectedOperation(receipt, { requestId: active.request.request_id, targetVersion: active.request.target_version, sha512: active.request.sha512 }).operation_id;
      if (!active.operationId) throw new Error('更新准备回执尚未确认，保留原请求供恢复。');
    }
    if (!active.operationId) return;
    if (active.backgroundStopped) await this.runtime.startup();
    const response = await this.apiWithReceipt(`/api/runtime/update/${encodeURIComponent(active.operationId)}/cancel`, { method: 'POST', body: {} });
    confirmedTerminal(response, active.operationId, 'cancelled');
    this.journal.clear();
  }
  async apiWithReceipt(endpoint, options) {
    let lastError;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try { return await this.runtime.api(endpoint, options); }
      catch (error) {
        lastError = error;
        if (error.status && error.status < 500) throw error;
        if (attempt < 2) await this.sleep(250);
      }
    }
    throw lastError;
  }
  cancelUpdate() {
    if (!this.active || !this.state.get().update.canCancel || this.active.stopping) return Promise.resolve(this.state.get());
    this.active.cancelled = true;
    this.active.token.cancel();
    return this.runPromise || Promise.resolve(this.state.get());
  }
  async prepareExit({ keepBackground = false } = {}) {
    if (this.state.get().update.phase === 'installing' || this.journal.read()?.phase === 'installing') throw Object.assign(new Error('安装程序正在交接，请等待安装结果，暂不能另行退出。'), { code: 'ERR_MEDIAFLOW_INSTALLER_HANDOFF' });
    if (this.active) {
      this.active.cancelled = true;
      this.active.token.cancel();
      await this.runPromise;
    }
    if (this.journal.read() && !keepBackground) throw new Error('更新恢复尚未确认，请查看日志或重试启动后再完全退出。');
  }
  deferUpdate() {
    if (this.active) return this.cancelUpdate();
    return Promise.resolve(this.patch({ phase: 'idle', message: '稍后更新。', canCancel: false }));
  }
  async recoverJournal(identity) {
    let pending = this.journal.read();
    if (!pending) return;
    if (pending.instanceId !== this.profile.id || !semver.valid(pending.targetVersion) || !pending.requestId || Buffer.from(pending.sha512 || '', 'base64').length !== 64) throw new Error('更新恢复记录与该实例不一致。');
    if (!pending.operationId) {
      const receipt = await this.apiWithReceipt('/api/runtime/update/prepare', { method: 'POST', body: { request_id: pending.requestId, target_version: pending.targetVersion, sha512: pending.sha512 } });
      const confirmed = expectedOperation(receipt, pending);
      pending = { ...pending, operationId: confirmed.operation_id, phase: 'prepared' };
      this.journal.write(pending);
    }
    if (!/^[a-zA-Z0-9_-]{1,128}$/.test(pending.operationId || '')) throw new Error('更新恢复操作编号无效。');
    const backendVersion = typeof identity.product_version === 'string' ? identity.product_version : identity.product_version?.version;
    const desktopRevision = this.state.get().sourceRevision;
    const backendRevision = identity.product_version?.source_revision;
    const revisionMatches = releaseRevisionMatches(desktopRevision, backendRevision);
    const installedTarget = pending.targetVersion === this.state.get().desktopVersion && backendVersion === pending.targetVersion && revisionMatches;
    if (pending.phase === 'installing' && pending.targetVersion === this.state.get().desktopVersion && !installedTarget) throw new Error('桌面和后台版本不一致，更新门禁保持关闭。请修复安装后重试。');
    const response = await this.apiWithReceipt(`/api/runtime/update/${encodeURIComponent(pending.operationId)}/${installedTarget ? 'complete' : 'cancel'}`, { method: 'POST', body: {} });
    confirmedTerminal(response, pending.operationId, installedTarget ? 'completed' : 'cancelled');
    this.journal.clear();
    this.diagnostics.write('info', installedTarget ? 'update-completed-verified' : 'update-interrupted-recovered', { version: pending.targetVersion, instance: this.profile.id });
  }
  async installerLaunchFailed(error) {
    if (this.updater) this.updater.quitAndInstallCalled = false;
    this.diagnostics.write('error', 'installer-launch-failed', error);
    let recovered = false;
    try {
      const identity = await this.runtime.startup();
      await this.recoverJournal(identity);
      recovered = true;
    } catch (recoveryError) {
      this.diagnostics.write('error', 'installer-recovery-failed', recoveryError);
    }
    this.patch({ phase: 'error', canCancel: false, message: recovered ? '安装程序未能启动。后台已恢复，请查看日志后重试更新。' : '安装程序未能启动。后台恢复尚未确认，请查看日志后重试；不会继续安装。' });
    return this.state.get();
  }
  errorMessage(error, stage = 'update') {
    // Classify upstream detail; never send its body, headers, paths or stack to the page.
    const message = String(error?.message || '');
    const code = String(error?.code || '');
    const status = Number(error?.statusCode || error?.status);
    if (code === 'ENOSPC' || /磁盘空间不足/.test(message)) return '磁盘空间不足，请释放空间后重试。';
    if (code === 'ERR_MEDIAFLOW_UPDATE_STOP') return '后台未能确认已停止，未安装。请检查后台状态或查看日志后重试。';
    if (/sha512|checksum|hash mismatch/i.test(message)) return '安装包校验失败，未安装。请重新下载。';
    if (stage === 'checking' && (status === 404 || /(?:HttpError|HTTP|status(?: code)?)\D{0,10}404\b/i.test(message) || /cannot find.*(?:dev|latest)\.yml/i.test(message))) return '更新资料暂不可用，暂时无法确认新版。仍可继续使用平台，稍后重新检查或查看日志。';
    if (/E(?:CONN|NET|HOST|TIMEDOUT)|ERR_(?:INTERNET|NETWORK|CONNECTION|NAME_NOT_RESOLVED)|ETIMEDOUT/i.test(code) || /network|timed?\s*out|ERR_(?:INTERNET|NETWORK|CONNECTION|NAME_NOT_RESOLVED)/i.test(message)) return '更新网络连接失败。平台仍可使用，请检查网络后重试或查看日志。';
    if (/更新清单缺少有效文件大小/.test(message)) return '更新资料缺少文件大小，无法检查空间。请稍后重新检查。';
    if (/下载文件不在|更新缓存不是|下载未返回/.test(message)) return '更新安装包不可用，未安装。请重新下载或查看日志。';
    if (/版本不一致|实例不一致|回执.*不一致|编号无效|未返回有效/.test(message)) return '更新身份或回执核对失败，未继续安装。请查看日志并修复安装后重试。';
    if (/安装程序未能启动|ERR_MEDIAFLOW_INSTALLER_LAUNCH/.test(`${code} ${message}`)) return '安装程序未能启动。请查看日志后重试。';
    return stage === 'checking' ? '暂时无法检查更新。平台仍可使用，请稍后重试或查看日志。' : '更新未完成，未继续安装。请查看日志后重试。';
  }
}
module.exports = { UpdateController, verifySha512, ensureDiskSpace, versionAllowed, installerInfo, UpdateCancelled };
