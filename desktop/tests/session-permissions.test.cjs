'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { clipboardWriteAllowed } = require('../lib/session-policy.cjs');
const uiUrl = 'http://127.0.0.1:31301/';
function fixture() {
  const webContents = { mainFrame: { url: uiUrl }, isDestroyed: () => false };
  const window = { webContents, isDestroyed: () => false };
  return { webContents, window, details: { isMainFrame: true, requestingUrl: uiUrl } };
}

test('owned current main page may write sanitized clipboard through both permission handlers', () => {
  const f = fixture();
  assert.equal(clipboardWriteAllowed(f.webContents, 'clipboard-sanitized-write', f.details, f.window, uiUrl), true);
  assert.equal(clipboardWriteAllowed(f.webContents, 'clipboard-sanitized-write', f.details, f.window, uiUrl, 'http://127.0.0.1:31301'), true);
});

test('clipboard reads, unsanitized write aliases and every other capability remain denied', () => {
  const f = fixture();
  for (const permission of ['clipboard-read', 'deprecated-sync-clipboard-read', 'clipboard-write', 'media', 'notifications', 'openExternal', 'geolocation', 'display-capture', 'usb', 'hid', 'serial', 'unknown']) {
    assert.equal(clipboardWriteAllowed(f.webContents, permission, f.details, f.window, uiUrl), false, permission);
  }
});

test('foreign web contents, null contexts and child frames cannot write even at the UI origin', () => {
  const f = fixture();
  assert.equal(clipboardWriteAllowed({ ...f.webContents }, 'clipboard-sanitized-write', f.details, f.window, uiUrl), false);
  assert.equal(clipboardWriteAllowed(null, 'clipboard-sanitized-write', f.details, f.window, uiUrl), false);
  assert.equal(clipboardWriteAllowed(f.webContents, 'clipboard-sanitized-write', f.details, null, uiUrl), false);
  assert.equal(clipboardWriteAllowed(f.webContents, 'clipboard-sanitized-write', { ...f.details, isMainFrame: false }, f.window, uiUrl), false);
  assert.equal(clipboardWriteAllowed(f.webContents, 'clipboard-sanitized-write', { requestingUrl: uiUrl }, f.window, uiUrl), false);
});

test('foreign or stale requesting documents, origins and destroyed windows are denied', () => {
  const f = fixture();
  for (const requestingUrl of ['http://127.0.0.1:3001/', 'http://localhost:31301/', 'https://example.org/', 'file:///splash.html', `${uiUrl}settings`]) {
    assert.equal(clipboardWriteAllowed(f.webContents, 'clipboard-sanitized-write', { ...f.details, requestingUrl }, f.window, uiUrl), false);
  }
  for (const origin of ['http://127.0.0.1:3001', 'null', 'https://example.org']) {
    assert.equal(clipboardWriteAllowed(f.webContents, 'clipboard-sanitized-write', f.details, f.window, uiUrl, origin), false);
  }
  f.webContents.mainFrame.url = 'https://example.org/';
  assert.equal(clipboardWriteAllowed(f.webContents, 'clipboard-sanitized-write', f.details, f.window, uiUrl), false);
  f.webContents.mainFrame.url = uiUrl;
  f.window.isDestroyed = () => true;
  assert.equal(clipboardWriteAllowed(f.webContents, 'clipboard-sanitized-write', f.details, f.window, uiUrl), false);
});
