'use strict';

function profileInstallerArguments(command, args, profilePath) {
  if (!args.includes('--updated')) return args;
  const scoped = args.filter(arg => !arg.startsWith('/INSTANCEPROFILE='));
  const directoryIndex = scoped.findIndex(arg => arg.startsWith('/D='));
  scoped.splice(directoryIndex < 0 ? scoped.length : directoryIndex, 0, `/INSTANCEPROFILE=${profilePath}`);
  return scoped;
}

function createProfileUpdater(NsisUpdater, { profilePath, installDirectory, publish, adapter }) {
  // Pinned electron-updater 6.8.10 has no public installerArgs option. Its spawn
  // boundary is overridden narrowly, preserving signature checks and install flow.
  class ProfileNsisUpdater extends NsisUpdater {
    spawnLog(command, args = [], env, stdio) {
      const result = super.spawnLog(command, profileInstallerArguments(command, args, profilePath), env, stdio);
      const launch = args.includes('--updated') ? Promise.resolve(result).catch(error => {
        // Prevent the pinned updater's ENOENT shell.openPath fallback: it drops
        // INSTANCEPROFILE and would launch the wrong instance's installer.
        if (error.code === 'ENOENT') error.code = 'ERR_MEDIAFLOW_INSTALLER_LAUNCH';
        throw error;
      }) : result;
      if (args.includes('--updated')) this.installerLaunch = Promise.resolve(launch);
      return launch;
    }
    async waitForInstallerLaunch() {
      if (!this.installerLaunch) throw new Error('安装程序未产生启动回执。');
      for (let attempt = 0; attempt < 3; attempt += 1) {
        const launch = this.installerLaunch;
        try { await launch; return; }
        catch (error) {
          // The pinned updater may retry a failed direct launch through elevate.exe.
          await Promise.resolve();
          if (this.installerLaunch === launch) throw error;
        }
      }
      throw new Error('安装程序无法启动。');
    }
  }
  // Pinned AppUpdater deliberately leaves httpExecutor null when an AppAdapter
  // is supplied. Bind Electron's real transport before constructing a provider.
  const updater = new ProfileNsisUpdater(null, adapter);
  if (updater.httpExecutor == null) {
    const { ElectronHttpExecutor } = require('electron-updater/out/electronHttpExecutor');
    updater.httpExecutor = new ElectronHttpExecutor((authInfo, callback) => updater.emit('login', authInfo, callback));
  }
  updater.installDirectory = installDirectory;
  updater.autoDownload = false;
  updater.autoInstallOnAppQuit = false;
  updater.autoRunAppAfterInstall = true;
  updater.disableWebInstaller = true;
  updater.disableDifferentialDownload = false;
  updater.allowPrerelease = true;
  updater.channel = 'dev';
  updater.allowDowngrade = false;
  if (publish) updater.setFeedURL(publish);
  return updater;
}
module.exports = { createProfileUpdater, profileInstallerArguments };
