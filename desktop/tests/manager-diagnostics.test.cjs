'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { runManager, managementError } = require('../lib/runtime.cjs');
const profile = { profilePath: 'C:\\Data\\profile.json' };
const invoke = (error, stdout, stderr) => runManager({ platformRoot: 'C:\\App', profile, action: 'Start', execFileImpl: (_program, _args, options, callback) => {
  assert.equal(options.encoding, 'utf8'); assert.equal(options.windowsHide, true);
  callback(error, stdout, stderr);
} });

test('nonzero management result preserves structured Chinese reason instead of PowerShell stack', async () => {
  const result = { ok: false, code: 'task_disabled', phase: 'registration', message: 'MediaFlow 后台计划任务已被禁用，请修复后台注册后重试。', diagnostic: { task_name: 'MediaFlow Background' } };
  await assert.rejects(invoke(new Error('exited 1'), '\uFEFF' + JSON.stringify(result), 'At C:\\secret.ps1:123\nraw stack'), error => {
    assert.equal(error.code, 'task_disabled'); assert.equal(error.phase, 'registration'); assert.equal(error.message, result.message);
    assert.equal(error.diagnostic.task_name, 'MediaFlow Background'); assert.equal(error.message.includes('secret'), false); return true;
  });
});

test('structured refusal is honored even when process exits zero', async () => {
  await assert.rejects(invoke(null, JSON.stringify({ ok: false, code: 'service_early_exit', phase: 'imports' }), ''), error => error.code === 'service_early_exit' && error.phase === 'imports');
});

test('unstable and unreadable physical storage paths retain structured recovery diagnostics', async () => {
  for (const code of ['storage_path_unstable', 'storage_path_unverifiable']) {
    const result = { ok: false, code, phase: 'configuration', diagnostic: { path_role: 'instance profile', recovery: 'choose_stable_path' } };
    await assert.rejects(invoke(new Error('exit 1'), JSON.stringify(result), 'raw native stack'), error => {
      assert.equal(error.code, code); assert.equal(error.phase, 'configuration');
      assert.equal(error.diagnostic.recovery, 'choose_stable_path');
      assert.match(error.message, /稳定/u); assert.equal(error.message.includes('stack'), false); return true;
    });
  }
});

test('legacy, malformed, empty and mixed output do not leak raw stderr or resolve as success', async () => {
  for (const output of ['', 'not JSON', 'debug\n{"ok":true}']) {
    await assert.rejects(invoke(new Error('raw traceback and credential'), output, 'Unicode乱码 stack'), error => !/traceback|credential|乱码|stack/u.test(error.message) && error.code === 'management_invalid_response');
    await assert.rejects(invoke(null, output, ''), /未返回可验证/);
  }
  await assert.rejects(invoke(null, '{}', ''), /未返回可验证/);
});

test('unknown error codes, multiline errors and encoding replacement characters use safe local messages', () => {
  for (const message of ['At line 1\nstack', '\uFFFD\uFFFD corrupted', 'x'.repeat(241)]) {
    assert.equal(managementError({ code: 'task_missing', message }, null, 'Start').message, 'MediaFlow 后台尚未登记，请修复后台注册后重试。');
  }
  assert.equal(managementError({ code: 'new_upstream_error', message: 'raw' }, null, 'Start').message.includes('raw'), false);
});

test('management timeout has an explicit safe reason and verified success is returned', async () => {
  const killed = Object.assign(new Error('timeout secret'), { killed: true });
  await assert.rejects(invoke(killed, '', ''), error => error.code === 'manager_timeout' && /超时/u.test(error.message));
  assert.deepEqual(await invoke(null, '{"ok":true,"instance_id":"production"}', ''), { ok: true, instance_id: 'production' });
});
