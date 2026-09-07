// Exercises the built frontend, not a reconstructed router. No paid/device writes.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {chromium}=require(process.env.MEDIAFLOW_PLAYWRIGHT_MODULE||'@playwright/test');
const origin=process.env.MEDIAFLOW_TEST_URL;
if(!origin||!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin))throw Error('Explicit loopback test URL required');

test('Manage opens the separate console without replacing chat; native settings still opens a dialog',async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const context=await browser.newContext({locale:'zh-CN',viewport:{width:1366,height:768}});
 const page=await context.newPage(); const errors=[],writes=[];
 page.on('pageerror',error=>errors.push(String(error)));
 await context.route('**/*',route=>{
  const request=route.request();
  if(!['GET','HEAD','OPTIONS'].includes(request.method())){
   writes.push(request.method()+' '+new URL(request.url()).pathname);
   return route.abort();
  }
  return route.continue();
 });
 try{
  await page.goto(origin);
  const settings=page.getByRole('button',{name:'设置',exact:true});
  await settings.waitFor({state:'visible'});
  const chatUrl=page.url();
  await settings.click();
  await page.getByRole('dialog').waitFor({state:'visible'});
  assert.equal(page.url(),chatUrl,'Settings must not navigate to a draft');
  await page.keyboard.press('Escape');
  const popupPromise=context.waitForEvent('page',{timeout:5000});
  await page.getByRole('link',{name:'管理中心',exact:true}).first().click();
  const manage=await popupPromise;
  await manage.waitForURL(url=>url.origin!==origin&&url.pathname==='/manage');
  await manage.getByRole('heading',{name:'管理中心',exact:true}).waitFor();
  assert.equal(page.url(),chatUrl,'Manage must preserve the current chat');
  const devices=manage.getByRole('link',{name:/设备管理/});
  await devices.first().click();
  await manage.waitForURL('**/devices');
  assert.equal(page.url(),chatUrl);
  await manage.getByRole('navigation',{name:'管理工作区'}).getByRole('link',{name:'设置',exact:true}).click();
  await manage.waitForURL('**/settings',{timeout:5000});
  await manage.getByRole('heading',{name:'设置与关于',exact:true}).waitFor();
  if(process.env.MEDIAFLOW_TEST_OUTPUT){
   await fs.mkdir(process.env.MEDIAFLOW_TEST_OUTPUT,{recursive:true});
   await manage.screenshot({path:process.env.MEDIAFLOW_TEST_OUTPUT+'/management-settings.png'});
  }
  await manage.getByRole('link',{name:'← 返回 Agent',exact:true}).click();
  await manage.waitForURL('http://127.0.0.1:3000/**',{timeout:5000});
  assert.equal(page.url(),chatUrl);
  await settings.click();
  await page.getByRole('dialog').waitFor({state:'visible'});
  assert.deepEqual(errors,[]);
  assert.deepEqual(writes,[],'Navigation must not create sessions or device operations');
  if(process.env.MEDIAFLOW_TEST_OUTPUT){
   await fs.mkdir(process.env.MEDIAFLOW_TEST_OUTPUT,{recursive:true});
   await page.screenshot({path:process.env.MEDIAFLOW_TEST_OUTPUT+'/native-settings.png'});
  }
 } finally {await browser.close();}
});

test('opening management preserves a native draft and does not submit its text',async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const context=await browser.newContext({locale:'zh-CN',viewport:{width:1920,height:1080}});
 const page=await context.newPage(); const writes=[];
 await context.route('**/*',route=>{
  const request=route.request();
  if(!['GET','HEAD','OPTIONS'].includes(request.method())){
   writes.push(request.method()+' '+new URL(request.url()).pathname);
   return route.abort();
  }
  return route.continue();
 });
 try{
  await page.goto(origin);
  await page.getByRole('button',{name:'新建会话',exact:true}).click();
  // A fresh browser has no selected project. Use the native chooser, not a
  // fabricated session route or a copied production browser profile.
  await page.getByRole('dialog').getByText('workspace',{exact:true}).click({timeout:5000});
  const input=page.locator('[contenteditable="true"]').first();
  await input.fill('未发送的导航验收草稿，不启动任务');
  const draftUrl=page.url();
  const pending=context.waitForEvent('page',{timeout:5000});
  await page.getByRole('link',{name:'管理中心',exact:true}).first().click();
  const manage=await pending;
  await manage.getByRole('heading',{name:'管理中心',exact:true}).waitFor();
  assert.equal(page.url(),draftUrl);
  assert.equal(await input.innerText(),'未发送的导航验收草稿，不启动任务');
  await manage.close();
  assert.equal(await input.innerText(),'未发送的导航验收草稿，不启动任务');
  assert.deepEqual(writes,[]);
  if(process.env.MEDIAFLOW_TEST_OUTPUT)await page.screenshot({path:process.env.MEDIAFLOW_TEST_OUTPUT+'/draft-preserved.png'});
 } finally {await browser.close();}
});

test('management browser can read real status from the platform API',async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const context=await browser.newContext({viewport:{width:1366,height:768}});
 const page=await context.newPage();
 await context.route('**/*',route=>['GET','HEAD','OPTIONS'].includes(route.request().method())?route.continue():route.abort());
 try{
  await page.goto('http://127.0.0.1:3001/manage');
  const status=await page.evaluate(async()=>{
   const response=await fetch('http://127.0.0.1:48138/api/status');
   const data=await response.json();
   return {ok:response.ok,hasTasks:!!data.task_summary,hasDevices:Array.isArray(data.devices)};
  });
  assert.deepEqual(status,{ok:true,hasTasks:true,hasDevices:true});
  await page.locator('.mf-status').filter({hasText:'台在线'}).waitFor();
 } finally{await browser.close();}
});
