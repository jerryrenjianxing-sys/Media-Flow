'use strict';

function rewriteInstanceUrl(value, profile) {
  if (!profile?.acceptance) return null;
  let url;
  try { url = new URL(value); } catch { return null; }
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || !['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol)) return null;
  const ports = { 3001: profile.ui_port, 48138: profile.api_port, 48139: profile.stream_port };
  const port = ports[url.port];
  if (!port) return null;
  url.hostname = '127.0.0.1';
  url.port = String(port);
  return url.toString();
}

function trustedSender(event, window, uiUrl, splashUrl) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) return false;
  if (event.senderFrame.url === splashUrl) return true;
  try { return new URL(event.senderFrame.url).origin === new URL(uiUrl).origin; } catch { return false; }
}

function clipboardWriteAllowed(webContents, permission, details, window, uiUrl, requestingOrigin) {
  if (permission !== 'clipboard-sanitized-write' || !window || window.isDestroyed?.() || !webContents || webContents !== window.webContents ||
    webContents.isDestroyed?.() || details?.isMainFrame !== true || typeof details.requestingUrl !== 'string') return false;
  try {
    const expected = new URL(uiUrl);
    const current = new URL(webContents.mainFrame.url);
    const requesting = new URL(details.requestingUrl);
    if (!['http:', 'https:'].includes(expected.protocol) || current.origin !== expected.origin || requesting.origin !== expected.origin || requesting.href !== current.href) return false;
    return requestingOrigin === undefined || new URL(requestingOrigin).origin === expected.origin;
  } catch { return false; }
}

module.exports = { rewriteInstanceUrl, trustedSender, clipboardWriteAllowed };
