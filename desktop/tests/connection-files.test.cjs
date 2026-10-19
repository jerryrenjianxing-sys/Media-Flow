'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createConnectionFiles } = require('../lib/connection-files.cjs');

test('copy delivers a persistent named MD file with exactly the connection document', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mediaflow-md-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const document = '# MediaFlow连接说明\n\n读取本机现成 Skill。\n';
  let copied;
  let revealed;
  const files = createConnectionFiles({ directory, loadConnection: async () => document,
    writeFiles: async file => { copied = file; }, revealFile: file => { revealed = file; } });
  const result = await files.copy({ filename: '坚果 接入.md' });
  assert.equal(result.copied, true);
  assert.equal(path.basename(copied), '坚果 接入.md');
  assert.equal(fs.readFileSync(copied, 'utf8'), document);
  await files.reveal({ fileId: result.fileId });
  assert.equal(revealed, copied);
  assert.deepEqual(await files.copy({ filename: '坚果 接入.md' }), result);
  assert.equal(fs.readFileSync(copied, 'utf8'), document);
});

test('only MD names and application-created file identifiers can cross the interface', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mediaflow-names-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const files = createConnectionFiles({ directory, loadConnection: async () => '# MediaFlow\n', writeFiles: async () => {}, revealFile: () => assert.fail('unowned reveal') });
  for (const filename of ['a.txt', '../a.md', 'C:\\a.md', 'CON.md', 'aux.more.md', '.md', ' a.md', 'a.md ', 'x'.repeat(100) + '.md']) {
    await assert.rejects(files.copy({ filename }));
  }
  await assert.rejects(files.copy({ filename: 'a.md', content: 'injected' }));
  await assert.rejects(files.reveal({ fileId: '../a.md' }));
  assert.equal(fs.readdirSync(directory).length, 0);
});

test('clipboard failure retains a real MD for file selection and changed content does not overwrite a prior copy', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mediaflow-snapshot-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  let document = '# MediaFlow\n原版本\n';
  const delivered = [];
  let revealed;
  const files = createConnectionFiles({ directory, loadConnection: async () => document,
    writeFiles: async file => { delivered.push(file); throw new Error('OS clipboard busy'); }, revealFile: file => { revealed = file; } });
  const first = await files.copy({ filename: '连接.md' });
  assert.equal(first.copied, false);
  await files.reveal({ fileId: first.fileId });
  assert.equal(fs.readFileSync(revealed, 'utf8'), '# MediaFlow\n原版本\n');
  document = '# MediaFlow\n新版本\n';
  const second = await files.copy({ filename: '连接.md' });
  assert.notEqual(first.fileId, second.fileId);
  assert.equal(fs.readFileSync(delivered[0], 'utf8'), '# MediaFlow\n原版本\n');
  assert.equal(fs.readFileSync(delivered[1], 'utf8'), document);
});

test('oversize or non-connection documents never reach the OS clipboard', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mediaflow-bound-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  for (const document of ['<html>error</html>', '# MediaFlow\n' + '中'.repeat(1400)]) {
    const files = createConnectionFiles({ directory, loadConnection: async () => document, writeFiles: async () => assert.fail('unsafe document'), revealFile() {} });
    await assert.rejects(files.copy());
  }
});
