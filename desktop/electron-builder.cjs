'use strict';
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const canonical = JSON.parse(fs.readFileSync(path.join(root, 'packaging/version.json'), 'utf8').replace(/^\uFEFF/, ''));
const version = process.env.MEDIAFLOW_RELEASE_VERSION || `${canonical.version}-dev.${canonical.development_iteration}`;
const stage = process.env.MEDIAFLOW_PLATFORM_STAGE || path.join(root, 'packaging/out/electron-platform');
module.exports = {
  appId: 'com.mediaflow.desktop',
  productName: 'MediaFlow',
  executableName: 'MediaFlow',
  copyright: 'Copyright © 2026 jerryrenjianxing-sys',
  asar: true,
  extraMetadata: { version, sourceRevision: process.env.MEDIAFLOW_SOURCE_COMMIT || 'development' },
  directories: { buildResources: '../installer/electron', output: process.env.MEDIAFLOW_ELECTRON_OUTPUT || '../packaging/out/electron-build' },
  files: ['main.cjs', 'preload.cjs', 'lib/**/*.cjs', 'splash.html', 'splash.cjs', 'package.json'],
  extraResources: [{ from: stage, to: 'platform', filter: ['**/*'] }, { from: '../LICENSE', to: 'LICENSE' },
    ...(process.env.MEDIAFLOW_LOCAL_ONLY_MANIFEST ? [{ from: process.env.MEDIAFLOW_LOCAL_ONLY_MANIFEST, to: 'local-only-build.json' }] : [])],
  win: { target: [{ target: 'nsis', arch: ['x64'] }], icon: '../assets/brand/mediaflow.ico', signAndEditExecutable: true },
  artifactName: 'MediaFlow-${version}-win-x64-Setup.${ext}',
  nsis: {
    oneClick: false, perMachine: false, allowElevation: false,
    allowToChangeInstallationDirectory: true, runAfterFinish: true,
    deleteAppDataOnUninstall: false, differentialPackage: true,
    createDesktopShortcut: false, createStartMenuShortcut: false,
    script: '../installer/electron/installer.nsi',
    installerLanguages: ['zh_CN', 'en_US'],
    installerIcon: '../assets/brand/mediaflow.ico', uninstallerIcon: '../assets/brand/mediaflow.ico',
  },
  publish: [{ provider: 'github', owner: 'jerryrenjianxing-sys', repo: 'Media-Flow', releaseType: 'prerelease', channel: 'dev', publishAutoUpdate: true }],
};
