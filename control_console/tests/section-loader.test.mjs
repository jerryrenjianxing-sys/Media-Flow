import test from 'node:test';
import assert from 'node:assert/strict';
import { loadIndependentSections } from '../app/lib/section-loader.mjs';

test('one slow or failed section does not hide a successful section', async () => {
  let rejectSlow;
  const seen = [];
  const slow = new Promise((_, reject) => { rejectSlow = reject; });
  const pending = loadIndependentSections([
    { label: '评测', run: async () => { seen.push(await Promise.resolve(12)); } },
    { label: '库存', run: async () => { await slow; assert.fail('failed data was applied'); } },
  ]);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(seen, [12]);
  rejectSlow(new Error('读取超时'));
  assert.deepEqual(await pending, ['库存：读取超时']);
  assert.deepEqual(seen, [12]);
});

test('network failure is readable and successful retry updates the same section', async () => {
  let value = 'previous';
  assert.deepEqual(await loadIndependentSections([
    { label: '库存', run: async () => { throw new TypeError('Failed to fetch'); } },
  ]), ['库存：连接暂不可用，请检查连接后重新读取']);
  assert.equal(value, 'previous');
  assert.deepEqual(await loadIndependentSections([{ label: '库存', run: async () => { value = 'new'; } }]), []);
  assert.equal(value, 'new');
});
