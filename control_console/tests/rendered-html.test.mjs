import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(new URL(path, "http://localhost"), { headers: { accept: "text/html" } }),
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
  assert.match(html, /不限主题/);
  assert.match(html, /混合主题/);
  assert.match(html, /搜索主题/);
  assert.match(html, /匹配主题内容/);
  assert.match(html, /其他安全内容/);
  assert.match(html, /匹配必须带画面证据/);
  assert.match(html, /暂停领取新任务/);
  assert.match(html, /安全停止已选设备/);
  assert.match(html, /取消 0 个等待任务/);
  assert.match(html, /检索所有可用设备/);
  assert.match(html, /清空全部任务/);
  assert.match(html, /首页固定显示最近 5 条/);
  assert.match(html, /参数预设/);
  assert.match(html, /应用预设/);
  assert.match(html, /保存当前参数/);
  assert.match(html, /删除预设/);
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

test("links registered comment screenshots without exposing local paths", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /\/api\/comment-image\?task_id=/);
  assert.match(source, /评论截图 · 第/);
  assert.doesNotMatch(source, /href=\{[^\n]*screenshot_path/);
});

test("preset application preserves execution-specific fields", async () => {
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /\/api\/presets/);
  assert.match(source, /device_ids: current\.device_ids/);
  assert.match(source, /seed: current\.seed/);
  assert.match(source, /preview_only: current\.preview_only/);
});

test("homepage uses probability-only actions and links to complete records", async () => {
  const response = await render();
  const html = await response.text();
  assert.match(html, /主题点赞概率/);
  assert.match(html, /通用点赞概率/);
  assert.match(html, /连续异常停止阈值/);
  assert.match(html, /查看所有/);
  assert.doesNotMatch(html, /点赞上限|收藏上限|评论上限|每轮最多/);
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /slice\(0, 5\)/);
  assert.match(source, /\/records#tasks/);
  assert.match(source, /\/records#incidents/);
  assert.doesNotMatch(source, /主题判断阈值/);
  assert.match(source, /\/api\/tasks\/stop/);
  assert.match(source, /\/api\/tasks\/cancel-pending/);
  assert.match(source, /\/api\/workers\/restart/);
  assert.match(source, /已结束 · 安全停止/);
  assert.match(source, /已结束 · 已取消/);
});

test("server-renders the complete task and correction records page", async () => {
  const response = await render("/records");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /全部任务与纠错记录/);
  assert.match(html, /全部任务/);
  assert.match(html, /全部纠错记录/);
  assert.match(html, /展开全部/);
  const source = await readFile(new URL("../app/records/page.tsx", import.meta.url), "utf8");
  assert.match(source, /tasksExpanded/);
  assert.match(source, /incidentsExpanded/);
  assert.match(source, /SUMMARY_SIZE = 5/);
});
