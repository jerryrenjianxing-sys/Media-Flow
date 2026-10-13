'use strict';

function clampBounds(saved, workArea) {
  const minWidth = Math.min(900, workArea.width);
  const minHeight = Math.min(640, workArea.height);
  const finite = value => Number.isFinite(value) && Number.isInteger(value);
  const width = Math.min(workArea.width, Math.max(minWidth, finite(saved?.width) ? saved.width : 1360));
  const height = Math.min(workArea.height, Math.max(minHeight, finite(saved?.height) ? saved.height : 900));
  const x = Math.min(workArea.x + workArea.width - width, Math.max(workArea.x, finite(saved?.x) ? saved.x : workArea.x + Math.round((workArea.width - width) / 2)));
  const y = Math.min(workArea.y + workArea.height - height, Math.max(workArea.y, finite(saved?.y) ? saved.y : workArea.y + Math.round((workArea.height - height) / 2)));
  return { x, y, width, height, minWidth, minHeight };
}

function canOpenInstanceBrowser(profile, runtimeConfigured = false) {
  return Boolean(profile && (!profile.acceptance || runtimeConfigured === true));
}

function externalBrowserUrl(value, profile, rewrite, runtimeConfigured = false) {
  if (typeof value !== 'string' || value.length > 8192) return null;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
    const result = rewrite?.(url.toString(), profile) || url.toString();
    const destination = new URL(result);
    if (profile?.acceptance && !canOpenInstanceBrowser(profile, runtimeConfigured) && ['127.0.0.1', 'localhost', '[::1]'].includes(destination.hostname) && Number(destination.port) === profile.ui_port) return null;
    return result;
  } catch { return null; }
}

module.exports = { clampBounds, externalBrowserUrl, canOpenInstanceBrowser };
