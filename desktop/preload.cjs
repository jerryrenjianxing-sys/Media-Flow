'use strict';
const { contextBridge, ipcRenderer } = require('electron');
const invoke = name => () => ipcRenderer.invoke(`mediaflow:${name}`);
contextBridge.exposeInMainWorld('mediaflowDesktop', Object.freeze({
  getState: invoke('getState'),
  getInstance: () => ipcRenderer.sendSync('mediaflow:getInstance'),
  subscribe(callback) {
    if (typeof callback !== 'function') throw new TypeError('subscribe needs a function');
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('mediaflow:state', listener);
    let subscribed = true;
    return () => { if (subscribed) ipcRenderer.removeListener('mediaflow:state', listener); subscribed = false; };
  },
  checkForUpdates: invoke('checkForUpdates'), startUpdate: invoke('startUpdate'),
  deferUpdate: invoke('deferUpdate'), cancelUpdate: invoke('cancelUpdate'),
  retryStartup: invoke('retryStartup'), openLogs: invoke('openLogs')
}));
