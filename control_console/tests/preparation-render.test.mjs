import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

// Seed only initial read state; render the production components and conditions.
const server = await createServer({ configFile: false, cacheDir: 'node_modules/.vite-preparation-tests',
  plugins: [{ name: 'offline-preparation-state', enforce: 'pre', transform(code, id) {
    if (id.endsWith('/app/devices/page.tsx')) return code
      .replace('useState<Status | null>(null)', 'useState<Status | null>(globalThis.__preparation.status)')
      .replace('useState<Config | null>(null)', 'useState<Config | null>({device_id:"vm",device_ids:["vm"]})');
    if (id.endsWith('/app/components/device-onboarding-dialog.tsx')) return code
      .replace('useState<Snapshot | null>(null)', 'useState<Snapshot | null>(globalThis.__preparation.snapshot)');
  } }, react()], server: { middlewareMode: true, hmr: false }, appType: 'custom' });
after(() => { delete globalThis.__preparation; return server.close(); });
const { default: DevicesPage } = await server.ssrLoadModule('/app/devices/page.tsx');
const { DeviceOnboardingDialog } = await server.ssrLoadModule('/app/components/device-onboarding-dialog.tsx');

function render(status, legacy, appInstall = false) {
  const initialization = { id:'init', status, legacy_inspection_recheck:legacy, message:'已有准备记录',
    progress_current:1, progress_total:3, report_path:'saved-report' };
  const device = {device_id:'vm',device_type:'virtual',state:'device',initialization};
  globalThis.__preparation = {
    status:{devices:[device],paused:true,stop_requested_device_ids:[],virtualization:{devices:[{
      virtual_device_id:'vm',adb_endpoint:'vm',name:'离线样本',state:'running',presence_status:'present',
      connected_device:device,available_actions:appInstall ? ['continue_onboarding'] : ['continue_initialization','cancel_initialization'],initialization,
      active_operation:appInstall ? {id:'new-install',status:'waiting_user',stage:'waiting_app_install'} : null,
    }]}},
    snapshot:{physical_devices:[{...device,device_type:'physical'}],virtual_devices:[],
      device_preferences:{physical_devices_enabled:true},mumu:{provider:{status:'ready',compatible:true},instances:[]}},
  };
  return [renderToStaticMarkup(React.createElement(DevicesPage)),
    renderToStaticMarkup(React.createElement(DeviceOnboardingDialog,{open:true,source:'devices',onClose(){}}))];
}

test('active legacy preparation hides continuation while retaining cancel and reports', () => {
  for (const status of ['waiting_user','queued','running']) {
    const [devices,onboarding] = render(status,true);
    assert.doesNotMatch(devices, />继续复验<|>继续初始化<|>继续人工步骤</);
    assert.doesNotMatch(onboarding, /<button[^>]*>继续<\/button>/);
    assert.match(devices, /查看报告/);
    assert.match(devices, /历史只读/);
    if (['waiting_user','queued','running'].includes(status)) assert.match(devices,/安全取消/);
  }
});

test('terminal legacy history allows new preparation and current app installation', () => {
  for (const status of ['failed','cancelled','ready','stale']) {
    const [devices] = render(status,true);
    assert.match(devices, />继续复验</);
    assert.match(devices, />重新校准<|>开始初始化<|>按指南开始初始化</);
    assert.match(devices, /查看报告/);
  }
  for (const status of ['waiting_user','failed','cancelled','ready','stale']) {
    const [devices] = render(status,true,true);
    assert.match(devices, />安装完成，继续检查</);
    assert.doesNotMatch(devices, />继续复验<|>继续初始化<|>继续人工步骤</);
  }
});

test('ordinary and home badge preparation keep generic continuation', () => {
  // Both supported modes have a false marker; older service responses may omit it.
  for (const legacy of [false,undefined]) {
    const [devices,onboarding] = render('waiting_user',legacy);
    assert.match(devices, />继续复验</);
    assert.match(devices, />继续初始化<|>继续人工步骤</);
    assert.match(onboarding, /<button[^>]*>继续<\/button>/);
  }
});
