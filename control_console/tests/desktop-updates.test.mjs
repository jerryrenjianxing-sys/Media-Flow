import assert from 'node:assert/strict';
import {after, test} from 'node:test';
import {createServer} from 'vite';
import react from '@vitejs/plugin-react';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

// Supply desktop event snapshots while rendering the real production components.
const server = await createServer({configFile:false, cacheDir:'node_modules/.vite-desktop-tests',
  plugins:[{name:'desktop-event-fixture',enforce:'pre',transform(code,id){
    if(id.endsWith('/app/lib/desktop-bridge.ts')) return 'export function useDesktopState(){return globalThis.__desktopFixture}';
  }},react()],server:{middlewareMode:true,hmr:false},appType:'custom'});
after(()=>{delete globalThis.__desktopFixture;return server.close();});
const {DesktopUpdateBanner,DesktopUpdateSettings,UpdateStatus} = await server.ssrLoadModule('/app/components/desktop-updates.tsx');
function snapshot(update,packaged=true){
  globalThis.__desktopFixture={state:update?{desktopVersion:'0.4.1-dev.45',sourceRevision:'abcdef',packaged,update}:null,error:null,invoke(){}};
}
const render = component => renderToStaticMarkup(React.createElement(component));

test('browser with no desktop bridge renders no update controls',()=>{
  snapshot(null);assert.equal(render(DesktopUpdateBanner),'');assert.equal(render(DesktopUpdateSettings),'');
});
test('available release permits download and defer and escapes release notes',()=>{
  snapshot({phase:'available',version:'0.4.1-dev.46',releaseNotes:'<script>untrusted</script>'});
  const html=render(DesktopUpdateBanner);
  assert.match(html,/>更新<\/button>/);assert.match(html,/>稍后<\/button>/);
  assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
});
test('busy update displays authoritative blocker and cancellation, not another download',()=>{
  snapshot({phase:'waiting_idle',version:'0.4.1-dev.46',canCancel:true,blockers:[{kind:'manual_control',message:'设备正在人工控制'}]});
  const html=render(DesktopUpdateBanner);
  assert.match(html,/设备正在人工控制/);assert.match(html,/>取消更新<\/button>/);
  assert.doesNotMatch(html,/>更新<\/button>/);
});
test('download progress is bounded and displays transfer size and speed',()=>{
  const html=renderToStaticMarkup(React.createElement(UpdateStatus,{update:{phase:'downloading',percent:150,transferred:1048576,total:2097152,bytesPerSecond:524288}}));
  assert.match(html,/<progress[^>]*value="100"/);assert.match(html,/1\.0 MB \/ 2\.0 MB/);assert.match(html,/0\.5 MB\/s/);
});
test('countdown and retry preserve a visible recovery action',()=>{
  snapshot({phase:'countdown',countdownSeconds:5,canCancel:true});assert.match(render(DesktopUpdateBanner),/5 秒后重启安装/);
  snapshot({phase:'error',version:'0.4.1-dev.46',message:'下载校验失败'});
  assert.match(render(DesktopUpdateBanner),/重试更新/);assert.match(render(DesktopUpdateBanner),/下载校验失败/);
});
test('update banner rejects raw bridge errors rather than merely wrapping their stack',()=>{
  const raw = `HttpError: 404 Not Found\nHeaders: authorization Bearer private\n at C:\\Private User\\app.asar\\main.cjs ${'upstream body '.repeat(2000)}`;
  for(const message of [raw,'更新失败：C:\\Private User\\app.asar','安装程序无法启动：spawn \\\\private-server\\Users\\Jerry\\setup.exe EACCES','更新失败：authorization Bearer private','错误正文'.repeat(100)]){
    snapshot({phase:'error',message});
    globalThis.__desktopFixture.error=message;
    const html=render(DesktopUpdateBanner);
    assert.match(html,/更新未完成/);
    assert.match(html,/查看日志/);
    assert.doesNotMatch(html,/HttpError|Headers|authorization|Bearer|Private User|private-server|Jerry|app\.asar|upstream body|错误正文/i);
    assert.ok(html.length < 2000);
  }
});
test('source environment cannot initiate installed-release checks',()=>{
  snapshot({phase:'idle'},false);assert.match(render(DesktopUpdateSettings),/<button[^>]*disabled=""[^>]*>检查更新/);
});
