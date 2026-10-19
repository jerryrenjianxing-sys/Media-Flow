'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

function mdFilename(value = 'MediaFlow连接说明.md') {
  if (typeof value !== 'string' || value.length > 100 || !/\.md$/i.test(value) ||
    /[<>:"/\\|?*]/u.test(value) || [...value].some(char => char.charCodeAt(0) < 32) || /[. ]$/.test(value) || value.trim() !== value ||
    !value.slice(0, -3).trim() || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(value)) {
    throw new Error('请输入合法的MD文件名（最多100个字符，后缀为.md）。');
  }
  return value;
}

function createConnectionFiles({ directory, loadConnection, writeFiles, revealFile }) {
  const owned = new Map();
  const pending = new Map();
  async function prepare(filename) {
    const document = await loadConnection();
    if (typeof document !== 'string' || !/^# [^\r\n]*MediaFlow/u.test(document) || Buffer.byteLength(document, 'utf8') > 4096) throw new Error('MD连接说明无效，请检查平台或重新打开软件。');
    const fileId = crypto.createHash('sha256').update(filename).update('\0').update(document).digest('hex');
    const folder = path.join(directory, fileId);
    const file = path.join(folder, filename);
    await fs.mkdir(folder, { recursive: true });
    try { await fs.writeFile(file, document, { encoding: 'utf8', flag: 'wx' }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
    if (await fs.readFile(file, 'utf8') !== document) throw new Error('缓存中的MD文件内容不一致，请查看日志。');
    owned.set(fileId, file);
    try {
      await writeFiles(file);
      return { copied: true, filename, fileId };
    } catch {
      return { copied: false, filename, fileId, message: '文件剪贴板暂不可用，可打开文件位置后拖入或选择此MD。' };
    }
  }
  return {
    copy(options = {}) {
      if (!options || Array.isArray(options) || Object.keys(options).some(key => key !== 'filename')) return Promise.reject(new Error('只允许指定MD文件名。'));
      let filename;
      try { filename = mdFilename(options.filename); } catch (error) { return Promise.reject(error); }
      if (!pending.has(filename)) pending.set(filename, prepare(filename).finally(() => pending.delete(filename)));
      return pending.get(filename);
    },
    async reveal(options) {
      if (!options || Object.keys(options).length !== 1 || typeof options.fileId !== 'string' || !owned.has(options.fileId)) throw new Error('请先生成本应用的MD连接文件。');
      const file = owned.get(options.fileId);
      await fs.access(file);
      await revealFile(file);
      return { revealed: true };
    }
  };
}
module.exports = { createConnectionFiles, mdFilename };
