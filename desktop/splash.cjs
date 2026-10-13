'use strict';
const bridge = window.mediaflowDesktop;
function render(state) {
  document.getElementById('message').textContent = state.startup.message;
  document.getElementById('retry').disabled = ['checking', 'starting', 'waiting'].includes(state.startup.phase);
  const browser = document.getElementById('browser');
  const browserAvailable = Boolean(state.instance.uiUrl && state.instance.browserAvailable === true);
  if (browserAvailable) browser.href = state.instance.uiUrl;
  else browser.removeAttribute('href');
  browser.hidden = !browserAvailable;
  document.getElementById('version').textContent = `${state.desktopVersion} · ${state.instance.acceptance ? '隔离验收实例' : '本地实例'}`;
}
bridge.subscribe(render);
bridge.getState().then(render);
document.getElementById('retry').addEventListener('click', () => { void bridge.retryStartup().then(render); });
document.getElementById('logs').addEventListener('click', () => { void bridge.openLogs(); });
