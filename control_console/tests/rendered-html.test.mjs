import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the RiskFlow control console", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>RiskFlow 社媒风控实验台<\/title>/);
  assert.match(html, /策略控制台/);
  assert.match(html, /点赞与收藏/);
  assert.match(html, /评论主题范围/);
  assert.match(html, /全部安全内容/);
  assert.match(html, /仅匹配主题/);
  assert.match(html, /暂停所有任务/);
  assert.match(html, /清空全部任务/);
  assert.match(html, /切换到浅色主题/);
  assert.match(html, /ADB 在线/);
  assert.doesNotMatch(html, /Building your site|Your site is taking shape/);
});

test("renders the lightweight correction monitor in the main console", async () => {
  const response = await render();
  const html = await response.text();

  assert.match(html, /纠错监控/);
  assert.match(html, /异常自动留档/);
  assert.match(html, /已恢复/);
  assert.match(html, /已跳过/);
  assert.match(html, /需处理/);
  assert.match(html, /暂无异常记录，固定程序运行正常/);
});
