'use strict';
const fs = require('node:fs');
const path = require('node:path');

function isWithin(parent, child) {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}

function validateProfile(value, profilePath, programRoot) {
  if (!value || value.schema !== 1) throw new Error('实例配置格式无效（需要 schema 1）。');
  if (typeof value.id !== 'string' || !/^(production|acceptance(?:-[a-z0-9-]+)?)$/.test(value.id)) throw new Error('实例身份无效。');
  if (value.acceptance !== (value.id !== 'production')) throw new Error('验收实例标记与身份不一致。');
  if (typeof value.data_root !== 'string' || !path.isAbsolute(value.data_root)) throw new Error('数据目录必须是绝对路径。');
  if (typeof value.task_name !== 'string' || !value.task_name.trim() || /[\r\n\0]/.test(value.task_name)) throw new Error('后台登记名称无效。');
  const ports = ['ui_port', 'api_port', 'stream_port'].map(key => value[key]);
  if (ports.some(port => !Number.isInteger(port) || port < 1024 || port > 65535) || new Set(ports).size !== ports.length) throw new Error('实例端口必须有效且互不相同。');
  if (value.acceptance && (ports.some(port => [3001, 48138, 48139].includes(port)) || value.task_name === 'MediaFlow Background')) throw new Error('验收实例不得使用生产端口或后台登记。');
  if (programRoot && (isWithin(programRoot, value.data_root) || isWithin(programRoot, profilePath))) throw new Error('实例配置和数据必须保存在程序目录外。');
  return Object.freeze({ ...value, data_root: path.resolve(value.data_root), profilePath: path.resolve(profilePath) });
}

function argumentProfile(argv) {
  const args = argv.filter(arg => typeof arg === 'string');
  const explicit = args.find(arg => arg.startsWith('--instance-profile='));
  if (explicit) return explicit.slice('--instance-profile='.length);
  const index = args.indexOf('--instance-profile');
  return index < 0 ? null : args[index + 1];
}

function resolveProfile({ argv, resourcesPath, packaged, sourceRoot, readFile = fs.readFileSync }) {
  let profilePath = argumentProfile(argv);
  if (!profilePath && packaged) {
    const marker = path.join(resourcesPath, 'instance-profile.path');
    try { profilePath = readFile(marker, 'utf8').replace(/^\uFEFF/, '').trim(); }
    catch { throw new Error('未找到安装实例登记。请使用安装程序修复，或显式指定实例配置。'); }
  }
  if (!profilePath && !packaged) {
    // Developer mode only attaches to the existing source service; it never starts it.
    return Object.freeze({ schema: 1, id: 'production', acceptance: false, data_root: sourceRoot, ui_port: 3001, api_port: 48138, stream_port: 48139, task_name: 'MediaFlow Background', profilePath: null, app_root: sourceRoot, development: true });
  }
  if (!profilePath || !path.isAbsolute(profilePath)) throw new Error('实例配置路径必须是绝对路径。');
  const programRoot = packaged ? path.dirname(resourcesPath) : null;
  const value = JSON.parse(readFile(profilePath, 'utf8').replace(/^\uFEFF/, ''));
  return validateProfile(value, profilePath, programRoot);
}

function instanceUrls(profile) {
  return { apiUrl: `http://127.0.0.1:${profile.api_port}`, uiUrl: `http://127.0.0.1:${profile.ui_port}/` };
}

function sameInstance(identity, profile) {
  return Boolean(identity && identity.ok !== false && (identity.id || identity.instance_id) === profile.id &&
    typeof identity.data_root === 'string' && path.resolve(identity.data_root).toLowerCase() === path.resolve(profile.data_root).toLowerCase() &&
    ['api_port', 'ui_port', 'stream_port'].every(key => identity[key] === profile[key]) && identity.acceptance === profile.acceptance);
}

module.exports = { validateProfile, resolveProfile, argumentProfile, instanceUrls, sameInstance, isWithin };
