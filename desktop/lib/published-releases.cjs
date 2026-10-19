'use strict';
const semver = require('semver');
const { CancellationToken } = require('builder-util-runtime');
const { GitHubProvider } = require('electron-updater/out/providers/GitHubProvider');
const { parseUpdateInfo } = require('electron-updater/out/providers/Provider');

function unavailable(message = '公开发行资料不完整，请稍后重新检查。') {
  return Object.assign(new Error(message), { code: 'ERR_MEDIAFLOW_RELEASE_DISCOVERY' });
}
function devVersion(release) {
  if (release?.draft !== false || !Number.isFinite(Date.parse(release.published_at)) || !/^v\d+\.\d+\.\d+-dev\.\d+$/.test(release.tag_name || '')) return null;
  return semver.valid(release.tag_name);
}

// Only discovery differs from the pinned provider. File/blockmap resolution,
// transport, differential download, signature checks and NSIS remain upstream.
class PublishedGitHubProvider extends GitHubProvider {
  async getLatestVersion() {
    const token = new CancellationToken();
    let timer;
    const deadline = new Promise((_resolve, reject) => {
      timer = setTimeout(() => { reject(unavailable('检查公开发行超过10秒，请稍后重试。')); token.cancel(); }, 10000);
    });
    try { return await Promise.race([this.discover(token), deadline]); }
    finally { clearTimeout(timer); token.cancel(); }
  }
  async discover(token) {
    let selected = null;
    let complete = false;
    for (let page = 1; page <= 3; page += 1) {
      const url = new URL(`/repos/${this.options.owner}/${this.options.repo}/releases?per_page=100&page=${page}`, this.baseApiUrl);
      const raw = await this.httpRequest(url, { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' }, token);
      if (typeof raw !== 'string' || raw.length > 5 * 1024 * 1024) throw unavailable();
      let releases;
      try { releases = JSON.parse(raw); } catch { throw unavailable(); }
      if (!Array.isArray(releases) || releases.length > 100) throw unavailable();
      for (const release of releases) {
        const version = devVersion(release);
        if (version && (!selected || semver.gt(version, devVersion(selected)))) selected = release;
      }
      if (releases.length < 100) { complete = true; break; }
    }
    if (!complete) throw unavailable('公开发行列表超出检查范围，暂时无法确认最新版。');
    if (!selected) throw unavailable('尚无可核实的公开开发版本。');
    const assets = Array.isArray(selected.assets) ? selected.assets : [];
    const uploaded = name => assets.find(asset => asset.name === name && asset.state === 'uploaded' && Number.isSafeInteger(asset.size) && asset.size > 0);
    if (!uploaded('dev.yml')) throw unavailable();
    const url = new URL(this.getBaseDownloadPath(selected.tag_name, 'dev.yml'), this.baseUrl);
    const raw = await this.httpRequest(url, { Accept: 'text/yaml' }, token);
    if (typeof raw !== 'string' || raw.length > 65536) throw unavailable();
    const info = parseUpdateInfo(raw, 'dev.yml', url.href);
    if (info?.version !== devVersion(selected) || !Array.isArray(info.files) || info.files.length !== 1 || info.packages) throw unavailable();
    const file = info.files[0];
    const name = file?.url;
    if (typeof name !== 'string' || !/^[A-Za-z0-9_.-]+\.exe$/.test(name) || !uploaded(name) || file.size !== uploaded(name).size ||
      typeof file.sha512 !== 'string' || Buffer.from(file.sha512, 'base64').length !== 64) throw unavailable();
    return { ...info, tag: selected.tag_name, releaseName: selected.name || selected.tag_name, releaseNotes: selected.body || '' };
  }
}
module.exports = { PublishedGitHubProvider };
