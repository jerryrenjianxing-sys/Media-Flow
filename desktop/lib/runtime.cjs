'use strict';
const path = require('node:path');
const { execFile } = require('node:child_process');
const { instanceUrls, sameInstance } = require('./instance-profile.cjs');

const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
function identityMismatch(message) {
  const error = new Error(message);
  error.code = 'ERR_MEDIAFLOW_INSTANCE_MISMATCH';
  return error;
}

function releaseRevisionMatches(expected, actual) {
  if (['development', 'unknown', ''].includes(expected || '')) return true;
  return typeof expected === 'string' && typeof actual === 'string' && /^[a-f0-9]{12,40}$/i.test(expected) && /^[a-f0-9]{12,40}$/i.test(actual) &&
    (expected.toLowerCase().startsWith(actual.toLowerCase()) || actual.toLowerCase().startsWith(expected.toLowerCase()));
}

async function requestJson(url, { fetchImpl = globalThis.fetch, timeout = 4000, method = 'GET', body } = {}) {
  const response = await fetchImpl(url, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(timeout) });
  const text = await response.text();
  if (text.length > 1024 * 1024) throw new Error('后台响应过大。');
  let result;
  try { result = JSON.parse(text); } catch { throw new Error(`后台返回无效响应（${response.status}）。`); }
  if (!response.ok || result.ok === false) {
    const error = new Error(result.message || result.error || `后台请求失败（${response.status}）。`);
    error.status = response.status;
    error.response = result;
    throw error;
  }
  return result;
}

function manageArguments(platformRoot, profile, action, operationId) {
  if (!profile.profilePath) throw new Error('开发模式只连接现有后台。');
  const args = ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', path.join(platformRoot, 'manage-mediaflow.ps1'), '-Action', action, '-NoBrowser', '-InstanceProfilePath', profile.profilePath];
  if (action === 'Stop') args.push('-TimeoutSeconds', '30');
  if (operationId) args.push('-UpdateOperationId', operationId);
  return args;
}

function runManager({ platformRoot, profile, action, operationId, timeout = 55000, execFileImpl = execFile }) {
  const powershell = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  return new Promise((resolve, reject) => {
    execFileImpl(powershell, manageArguments(platformRoot, profile, action, operationId), { cwd: platformRoot, windowsHide: true, timeout, maxBuffer: 1024 * 1024, encoding: 'utf8' }, (error, stdout, stderr) => {
      if (error) { reject(new Error(`${action === 'Stop' ? '后台未能安全停止' : '后台管理失败'}：${stderr.trim() || error.message}`)); return; }
      const output = stdout.replace(/^\uFEFF/, '').trim();
      if (!output) { resolve({}); return; }
      try { resolve(JSON.parse(output)); } catch { reject(new Error('后台管理未返回可验证的状态。请打开日志检查。')); }
    });
  });
}

class RuntimeController {
  constructor({ profile, platformRoot, packaged, state, diagnostics, fetchImpl = globalThis.fetch, manager = runManager, now = Date.now, sleep = delay }) {
    Object.assign(this, { profile, platformRoot, packaged, state, diagnostics, fetchImpl, manager, now, sleep });
    this.urls = instanceUrls(profile);
    this.startPromise = null;
  }
  api(endpoint, options = {}) { return requestJson(`${this.urls.apiUrl}${endpoint}`, { fetchImpl: this.fetchImpl, ...options }); }
  async probe(timeout = 2000, deadline = this.now() + timeout * 2) {
    const identity = await this.api('/api/runtime/instance', { timeout });
    const sameDevelopment = this.profile.development && (identity.id || identity.instance_id) === 'production' && identity.acceptance === false &&
      path.resolve(identity.app_root || '').toLowerCase() === path.resolve(this.platformRoot).toLowerCase() &&
      ['api_port', 'ui_port', 'stream_port'].every(key => identity[key] === this.profile[key]);
    if (!(this.profile.development ? sameDevelopment : sameInstance(identity, this.profile))) throw identityMismatch('该端口运行的是另一实例。不会操作它的后台。');
    if (this.packaged) {
      if (typeof identity.app_root !== 'string' || !path.isAbsolute(identity.app_root) || path.resolve(identity.app_root).toLowerCase() !== path.resolve(this.platformRoot).toLowerCase()) throw identityMismatch('本地后台属于另一程序目录。请关闭旧版或修复该实例安装后重试。');
      const metadata = identity.product_version;
      const version = typeof metadata === 'string' ? metadata : metadata?.version;
      const expected = this.state.get();
      if (version !== expected.desktopVersion) throw identityMismatch('本地后台版本与桌面版本不一致。请关闭旧版或修复该实例安装后重试。');
      if (!releaseRevisionMatches(expected.sourceRevision, metadata?.source_revision)) throw identityMismatch('本地后台提交与桌面提交不一致。请修复该实例安装后重试。');
    }
    const remaining = deadline - this.now();
    if (remaining <= 0) throw new Error('本地页面就绪检查超时。');
    const response = await this.fetchImpl(this.urls.uiUrl, { signal: AbortSignal.timeout(Math.max(1, Math.min(timeout, remaining))) });
    await response.body?.cancel();
    if (!response.ok) throw new Error(`本地页面尚未就绪（${response.status}）。`);
    return identity;
  }
  startup() {
    if (this.startPromise) return this.startPromise;
    this.startPromise = this.performStartup().finally(() => { this.startPromise = null; });
    return this.startPromise;
  }
  async performStartup() {
    const deadline = this.now() + 60000;
    this.state.patch('startup', { phase: 'checking', message: '正在检查本地后台…', ready: false });
    try {
      try {
        const identity = await this.probe();
        this.state.patch('startup', { phase: 'ready', message: '已连接本地后台。', ready: true });
        return identity;
      } catch (error) {
        if (error.code === 'ERR_MEDIAFLOW_INSTANCE_MISMATCH') throw error;
        if (!this.packaged) throw new Error('开发后台尚未就绪。请通过现有后台管理入口启动后重试。');
      }
      this.state.patch('startup', { phase: 'starting', message: '正在启动独立后台…', ready: false });
      await this.manager({ platformRoot: this.platformRoot, profile: this.profile, action: 'Start', timeout: Math.max(1000, deadline - this.now() - 2000) });
      this.state.patch('startup', { phase: 'waiting', message: '正在等待页面和后台就绪…', ready: false });
      while (this.now() < deadline) {
        try {
          const identity = await this.probe(Math.min(2000, Math.max(1, deadline - this.now())), deadline);
          this.state.patch('startup', { phase: 'ready', message: '本地后台已就绪。', ready: true });
          return identity;
        } catch (error) {
          if (error.code === 'ERR_MEDIAFLOW_INSTANCE_MISMATCH') throw error;
          await this.sleep(Math.min(500, Math.max(0, deadline - this.now())));
        }
      }
      throw new Error('启动超过 60 秒。请打开日志检查后重试。');
    } catch (error) {
      this.diagnostics.write('error', 'startup-failed', error);
      this.state.patch('startup', { phase: 'error', message: error.message, ready: false });
      throw error;
    }
  }
  async stop(operationId) {
    if (!this.packaged) throw new Error('开发模式不能停止现有后台。');
    await this.manager({ platformRoot: this.platformRoot, profile: this.profile, action: 'Stop', operationId, timeout: 45000 });
    const status = await this.manager({ platformRoot: this.platformRoot, profile: this.profile, action: 'Status', timeout: 10000 });
    if (status.instance_id !== this.profile.id || status.stopped_confirmed !== true || status.background?.state !== 'stopped' || !Array.isArray(status.processes) ||
      status.processes.some(process => process.running !== false || ['unknown', 'mismatch'].includes(process.identity))) throw new Error('仍有该实例的后台进程存活或身份未核实。安装已取消。');
    this.diagnostics.write('info', 'background-stopped-confirmed', { instance: this.profile.id });
    this.state.patch('startup', { phase: 'stopped', message: '该实例后台已安全停止。', ready: false });
  }
}
module.exports = { RuntimeController, requestJson, runManager, manageArguments, delay, releaseRevisionMatches };
