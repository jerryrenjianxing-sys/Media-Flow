'use strict';
const { EventEmitter } = require('node:events');

class DesktopState extends EventEmitter {
  constructor({ desktopVersion, sourceRevision, instance, packaged }) {
    super();
    this.value = { desktopVersion, sourceRevision, instance, packaged,
      startup: { phase: 'checking', message: '正在检查本地后台…', ready: false },
      update: { phase: 'idle', version: null, publishedVersion: null, checkAutomatic: true, releaseNotes: '', percent: 0, bytesPerSecond: 0, transferred: 0, total: 0, message: '', canCancel: false, differential: 'pending', countdownSeconds: null, blockers: [] } };
  }
  get() { return structuredClone(this.value); }
  patch(section, changes) {
    this.value[section] = { ...this.value[section], ...changes };
    this.emit('change', this.get());
    return this.get();
  }
}
module.exports = { DesktopState };
