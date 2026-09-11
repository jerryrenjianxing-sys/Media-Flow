/* Desktop-only production UI check. Supply isolated API and output directory. */
/* eslint-disable @typescript-eslint/no-require-imports -- Optional Node QA tooling is resolved through NODE_PATH, not bundled in the product. */
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '..');
  const output = path.resolve(process.argv[2]);
  const api = process.argv[3];
  assert.match(api, /^http:\/\/127\.0\.0\.1:\d+$/);
  assert.notEqual(api, 'http://127.0.0.1:48138');
  await fs.mkdir(output, { recursive: true });
  const { default: worker } = await import(pathToFileURL(path.join(root, 'dist/server/index.js')));
  const assets = async (req) => {
    const name = path.resolve(root, 'dist/client', '.' + decodeURIComponent(new URL(req.url).pathname));
    if (!name.startsWith(path.join(root, 'dist/client') + path.sep)) return new Response('', { status:404 });
    try {
      const mime = { '.js':'text/javascript', '.css':'text/css', '.svg':'image/svg+xml', '.png':'image/png', '.woff2':'font/woff2' }[path.extname(name)] || 'application/octet-stream';
      return new Response(await fs.readFile(name), { headers:{'content-type':mime} });
    } catch { return new Response('', {status:404}); }
  };
  const server = http.createServer(async (req, res) => {
    try {
      const request = new Request(`http://127.0.0.1:${server.address().port}${req.url}`, { headers:req.headers });
      const asset = await assets(request);
      const response = asset.status === 200 ? asset : await worker.fetch(request, { ASSETS:{fetch:assets} }, {waitUntil(){},passThroughOnException(){}});
      res.writeHead(response.status, Object.fromEntries(response.headers));
      res.end(Buffer.from(await response.arrayBuffer()));
    } catch (error) { res.writeHead(500); res.end(String(error)); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({channel:'chrome', headless:true});
  const report = {browser:'Chrome', plugin:'Browser plugin not available; regular Playwright', checks:[], errors:[]};
  try {
    const context = await browser.newContext({permissions:['clipboard-read','clipboard-write']});
    const page = await context.newPage();
    page.on('pageerror', e => report.errors.push(e.message));
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.port === '48138') {
        const headers = {...route.request().headers()}; delete headers.origin; delete headers.host;
        const response = await route.fetch({url:api+url.pathname+url.search,headers});
        await route.fulfill({response});
      } else await route.continue();
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    for (const theme of ['light','dark']) for (const width of [1366,1920]) for (const zoom of [1.25,1.5]) {
      await page.setViewportSize({width:Math.floor(width/zoom),height:Math.floor((width===1366?768:1080)/zoom)});
      await page.emulateMedia({colorScheme:theme,reducedMotion:'reduce'});
      await page.addInitScript(t => localStorage.setItem('mediaflow-theme',t), theme);
      for (const route of ['/','/workbench','/run','/records','/settings']) {
        await page.goto(base+route,{waitUntil:'networkidle'});
        await page.waitForTimeout(300);
        const check = await page.evaluate(() => ({text:document.body.innerText.length,
          overflow:document.documentElement.scrollWidth>innerWidth+2,
          title:document.title,headings:[...document.querySelectorAll('h1,h2')].map(x=>x.textContent)}));
        const name = `${theme}-${width}-${zoom}-${route.slice(1)||'home'}.png`;
        await page.screenshot({path:path.join(output,name),fullPage:true});
        report.checks.push({route,width,zoom,theme,...check,screenshot:name});
        assert.ok(check.text>80, 'blank '+route);
        assert.equal(check.overflow,false,'horizontal overflow '+name);
      }
    }
    await page.goto(base+'/',{waitUntil:'networkidle'});
    await page.getByRole('button',{name:'复制 Skill',exact:true}).click();
    await page.getByRole('button',{name:'已复制',exact:true}).waitFor();
    const markdown = await page.evaluate(()=>navigator.clipboard.readText());
    assert.match(markdown, /worker_interrupted/);
    assert.match(markdown, /42-external-skill-diagnostics/);
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button',{name:'下载 Skill',exact:true}).click();
    const download = await downloadPromise;
    await download.saveAs(path.join(output,'MediaFlow-Skill.zip'));
    report.skillCopyAndDownload = 'passed';
    await page.goto(base+'/records',{waitUntil:'networkidle'});
    await page.getByRole('button',{name:'查看详情',exact:true}).first().click();
    await page.getByRole('button',{name:'关闭任务详情',exact:true}).waitFor();
    await page.getByText('执行者中断，进度已保留；设备连接尚未核实',{exact:true}).waitFor();
    assert.equal(await page.getByText('worker_interrupted',{exact:true}).count(), 0);
    await page.screenshot({path:path.join(output,'task-detail.png'),fullPage:true});
    await page.getByRole('button',{name:'关闭任务详情',exact:true}).click();
    report.taskDetail = 'passed';
    await page.goto(base+'/',{waitUntil:'networkidle'});
    await page.getByRole('link',{name:'任务台',exact:true}).click();
    await page.waitForURL('**/manage');
    report.managementLink = 'passed';
    assert.deepEqual(report.errors, []);
    await page.unrouteAll({behavior:'wait'});
  } finally {
    await fs.writeFile(path.join(output,'report.json'), JSON.stringify(report,null,2));
    await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
  console.log(JSON.stringify({checks:report.checks.length,errors:report.errors}));
})().catch(e=>{console.error(e);process.exitCode=1;});
