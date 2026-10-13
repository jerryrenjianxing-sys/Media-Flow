'use strict';
const fs = require('node:fs');
const path = require('node:path');

function redact(value) {
  return String(value).replace(/(bearer\s+)[^\s"']+/gi, '$1[redacted]')
    .replace(/((?:api[_-]?key|token|secret|password|authorization)["']?\s*[:=]\s*["']?)[^\s,"'&]+/gi, '$1[redacted]')
    .replace(/(https?:\/\/)[^/\s:@]+:[^/\s@]+@/gi, '$1[redacted]@')
    .replace(/([?&](?:token|key|signature|credential|access_token)=)[^&\s]+/gi, '$1[redacted]');
}

function createDiagnostics(directory) {
  fs.mkdirSync(directory, { recursive: true });
  const file = path.join(directory, 'desktop-events.jsonl');
  function write(level, event, details = '') {
    try {
      if (fs.existsSync(file) && fs.statSync(file).size > 4 * 1024 * 1024) fs.renameSync(file, `${file}.previous`);
      fs.appendFileSync(file, `${JSON.stringify({ at: new Date().toISOString(), level, event: redact(event), details: redact(details instanceof Error ? details.stack || details.message : typeof details === 'string' ? details : JSON.stringify(details)) })}\n`, 'utf8');
    } catch { /* Disk errors must remain visible in the update state without crashing logging. */ }
  }
  return { directory, write, info: (...values) => write('info', 'updater', values.join(' ')), warn: (...values) => write('warn', 'updater', values.join(' ')), error: (...values) => write('error', 'updater', values.join(' ')), debug: (...values) => write('debug', 'updater', values.join(' ')) };
}

class Journal {
  constructor(file) { this.file = file; }
  read() { try { return JSON.parse(fs.readFileSync(this.file, 'utf8')); } catch { return null; } }
  write(value) {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const temporary = `${this.file}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(value, null, 2), 'utf8');
    fs.renameSync(temporary, this.file);
  }
  clear() { try { fs.unlinkSync(this.file); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
}
module.exports = { createDiagnostics, Journal, redact };
