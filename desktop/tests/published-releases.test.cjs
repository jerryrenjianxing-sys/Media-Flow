'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { PublishedGitHubProvider } = require('../lib/published-releases.cjs');
const sha512 = Buffer.alloc(64, 1).toString('base64');
function release(number, draft = false) {
  return { tag_name: `v0.4.1-dev.${number}`, draft, published_at: draft ? null : '2026-10-15T00:00:00Z', name: `dev.${number}`, body: '发行说明',
    assets: [{ name: 'dev.yml', state: 'uploaded', size: 200 }, { name: `MediaFlow-${number}.exe`, state: 'uploaded', size: 1000 }] };
}
test('draft tags cannot hide the highest published dev release or change installer resolution', async () => {
  const requested = [];
  const provider = new PublishedGitHubProvider({ owner: 'owner', repo: 'repo' }, {}, { platform: 'win32', executor: {
    async request(options) {
      requested.push(options.path);
      if (options.hostname === 'api.github.com') return JSON.stringify([release(53, true), release(50)]);
      return `version: 0.4.1-dev.50\nfiles:\n  - url: MediaFlow-50.exe\n    sha512: ${sha512}\n    size: 1000\n`;
    }
  } });
  const info = await provider.getLatestVersion();
  assert.equal(info.version, '0.4.1-dev.50');
  assert.equal(provider.resolveFiles(info)[0].url.href, 'https://github.com/owner/repo/releases/download/v0.4.1-dev.50/MediaFlow-50.exe');
  assert.deepEqual(requested, ['/repos/owner/repo/releases?per_page=100&page=1', '/owner/repo/releases/download/v0.4.1-dev.50/dev.yml']);
});

function providerFor(releases, manifest) {
  return new PublishedGitHubProvider({ owner: 'owner', repo: 'repo' }, {}, { platform: 'win32', executor: { request: async options => {
    if (options.hostname === 'api.github.com') return JSON.stringify(releases);
    return manifest;
  } } });
}
test('highest semantic published dev wins regardless of listing order; other channels and unpublished releases are ignored', async () => {
  const candidate = release(54);
  const releases = [release(50), release(53, true), { ...release(60), published_at: null }, { ...release(70), tag_name: 'v0.4.1-beta.70' }, candidate, release(51)];
  const info = await providerFor(releases, `version: 0.4.1-dev.54\nfiles:\n  - url: MediaFlow-54.exe\n    sha512: ${sha512}\n    size: 1000\n`).getLatestVersion();
  assert.equal(info.version, '0.4.1-dev.54');
});
test('missing resources and mismatched or unsafe metadata never authorize an installer', async () => {
  for (const manifest of [
    `version: 0.4.1-dev.49\nfiles: []`,
    `version: 0.4.1-dev.50\nfiles:\n  - url: https://other.test/setup.exe\n    sha512: ${sha512}\n    size: 1000\n`,
    `version: 0.4.1-dev.50\nfiles:\n  - url: MediaFlow-50.exe\n    sha512: bad\n    size: 1000\n`,
    `version: 0.4.1-dev.50\nfiles:\n  - url: MediaFlow-50.exe\n    sha512: ${sha512}\n    size: 2000\n`
  ]) await assert.rejects(providerFor([release(50)], manifest).getLatestVersion());
  await assert.rejects(providerFor([{ ...release(50), assets: [] }], '').getLatestVersion());
  await assert.rejects(providerFor([release(50, true)], '').getLatestVersion());
});
test('discovery has a shared ten-second deadline even when the transport stalls', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const provider = new PublishedGitHubProvider({ owner: 'owner', repo: 'repo' }, {}, { platform: 'win32', executor: { request: async () => new Promise(() => {}) } });
  const failed = assert.rejects(provider.getLatestVersion(), /10秒/);
  t.mock.timers.tick(10000);
  await failed;
});
test('pagination never treats a truncated release history as a known latest version', async () => {
  const allDrafts = Array.from({ length: 100 }, () => release(53, true));
  await assert.rejects(providerFor(allDrafts, '').getLatestVersion(), /范围/);
});
test('network, rate-limit and malformed list errors are not converted into a current-version result', async () => {
  for (const statusCode of [404, 429, 500]) {
    const provider = new PublishedGitHubProvider({ owner: 'owner', repo: 'repo' }, {}, { platform: 'win32', executor: { request: async () => { throw Object.assign(new Error('transport failure'), { statusCode }); } } });
    await assert.rejects(provider.getLatestVersion(), error => error.statusCode === statusCode);
  }
  await assert.rejects(providerFor({}, '').getLatestVersion());
});
