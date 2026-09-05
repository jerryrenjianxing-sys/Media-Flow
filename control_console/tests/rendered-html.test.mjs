import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${Math.random()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request(new URL(path, "http://localhost"), { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

async function source(path) {
  return readFile(new URL(path, import.meta.url), "utf8");
}

test("homepage adds Agent without replacing the existing workbench or claiming full control", async () => {
  const response = await render();
  const html = await response.text();
  assert.equal(response.status, 200);
  assert.match(html, /告诉助手你要做什么/);
  assert.match(html, /正在准备任务台/);
  assert.match(html, /不会假装执行成功/);
  const page = await source("../app/components/agent-workbench.tsx");
  assert.match(page, /request_id: id/);
  assert.match(page, /failures.current >= 3/);
  const api = await source('../app/lib/agent-api.ts');
  assert.match(api, /当前后台尚未提供助手接口/);
  assert.match(api, /无法连接本机对话服务/);
  assert.match(page, /尚未完成的接入/);
});

test('Agent settings keep native authentication and expose recoverable OAuth progress', async () => {
  const page = await source('../app/components/agent-provider-settings.tsx');
  for (const text of ['auth_methods', '保存 Key', '开始网页登录', '已完成登录，继续', '取消登录', '刷新登录状态', 'autoComplete="new-password"']) assert.ok(page.includes(text));
  assert.ok(page.includes('failures >= 3'));
  assert.ok(page.includes('使用现有 MediaFlow 千问配置'));
  assert.ok(!page.includes('localStorage'));
});

test("model settings expose isolated experimental Token Plan save test and enable", async () => {
  const response = await render("/content");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /千问AI平台/);
  assert.match(html, /启用为全平台模型/);
  const page = await source("../app/content/page.tsx");
  for (const value of ["qwen3.8-flash", "qwen_token_plan", "upload_consent", "requests_remaining", "config_version", "can_enable"]) assert.match(page, new RegExp(value));
  assert.match(page, /个人套餐官方FAQ限制后台自动化/);
  assert.match(page, /重启不重置/);
  assert.match(page, /Credits以千问工作台为准/);
  assert.match(page, /setProvider\(selected \|\| value\.active_provider/);
  assert.match(page, /finally \{ setModelAction\(""\); setBusy\(false\); \}/);
});

test("renders the five-workspace shell and persisted presentation controls", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /MediaFlow 媒体自动化平台/);
  for (const label of ["任务台", "运行", "结果", "设备", "资产与设置"]) assert.match(html, new RegExp(label));
  assert.match(html, /workspace-sidebar-toggle/);
  assert.match(html, /workspace-sidebar-scrim/);
  assert.match(html, /workspace-sidebar-head/);
  assert.match(html, /workspace-menu-icon/);
  assert.match(html, /workspace-nav-copy/);
  assert.match(html, /切换到浅色主题/);
  assert.match(html, /mediaflow-asset-recovery/);
  assert.match(html, /asset_reload/);
  assert.match(html, /正在准备任务台/);
  assert.doesNotMatch(html, /Building your site|Your site is taking shape/);

  const shell = await source("../app/components/console-shell.tsx");
  assert.match(shell, /mediaflow-sidebar-collapsed/);
  assert.match(shell, /gsap\.fromTo/);
  assert.doesNotMatch(shell, /gsap\/Flip|Flip\.getState|Flip\.from/);
  assert.match(shell, /sidebarFromWidthRef/);
  assert.doesNotMatch(shell, /scale: 0\.9/);
  assert.match(shell, /stagger: 0\.015/);
  assert.match(shell, /max-width: 1180px/);
  assert.match(shell, /power3\.inOut/);
  assert.match(shell, /clipPath/);
  assert.match(shell, /function collapseSidebarFromWorkspace/);
  assert.match(shell, /document\.addEventListener\("click", collapseSidebarFromWorkspace\)/);
  assert.match(shell, /sidebarRef\.current\?\.contains\(target\)/);
  assert.match(shell, /sidebarScrimRef\.current\?\.contains\(target\)/);
  assert.match(shell, /pathname === "\/interactions"/);
  assert.match(shell, /pathname === "\/governance"/);
  assert.match(shell, /问题待处理/);
  assert.match(shell, /本机服务未连接 · 点击重试/);
  assert.match(shell, /\/devices#device-issues/);
});

test("task workbench follows the four decisions and server-owned planning contract", async () => {
  const page = await source("../app/page.tsx");
  for (const text of ["从哪里开始", "关注什么", "如何运行", "在哪些虚拟机运行"]) assert.match(page, new RegExp(text));
  for (const mode of ["搜索＋主页交替", "主页不限主题", "主页主题筛选", "搜索主题视频"]) assert.match(page, new RegExp(mode));
  for (const field of ["search_segment_min", "search_segment_max", "home_segment_min", "home_segment_max"]) assert.match(page, new RegExp(field));
  assert.match(page, /推荐主模式/);
  assert.match(page, /备用模式/);
  assert.match(page, /\/api\/workbench\/draft/);
  assert.match(page, /method: "PUT"/);
  assert.match(page, /\/api\/workbench\/preview/);
  assert.match(page, /\/api\/workbench\/submit/);
  assert.match(page, /plan_hash/);
  assert.match(page, /requires_confirmation \? setConfirmOpen/);
  assert.match(page, /评论仅生成预览/);
  assert.match(page, /允许真实发送评论/);
  assert.match(page, /所有修改已自动保存/);
  assert.match(page, /提交后冻结参数与内容版本/);
  assert.doesNotMatch(page, /\/api\/run/);
});

test("task workbench preserves precise controls behind progressive disclosure", async () => {
  const page = await source("../app/page.tsx");
  assert.match(page, /高级设置/);
  assert.match(page, /连续异常停止阈值/);
  assert.match(page, /轮次间隔/);
  assert.match(page, /互动巡检/);
  assert.match(page, /评论发送前约束/);
  assert.match(page, /search_trust_results/);
  assert.match(page, /matched_like_probability/);
  assert.match(page, /matched_favorite_probability/);
  assert.match(page, /matched_comment_probability/);
  assert.match(page, /device_ids: config\.device_ids/);
  assert.match(page, /preview_only: config\.preview_only/);
  assert.match(page, /互动巡检 v3/);
  assert.match(page, /只检查互动消息聚合页，不进入普通私信/);
  assert.match(page, /请先在抖音隐私设置中打开访客记录/);
  assert.match(page, /\/api\/engagement-preflight/);
  assert.match(page, /visitor-acknowledgement/);
});

test("visual system uses real capsule switches, responsive layout and reduced motion", async () => {
  const css = await source("../app/globals.css");
  assert.match(css, /\.capsule-switch i \{/);
  assert.match(css, /border-radius: 999px/);
  assert.match(css, /\.capsule-switch input:checked \+ i::after/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.match(css, /grid-template-columns: var\(--sidebar-rail\) minmax\(0, 1fr\)/);
  assert.doesNotMatch(css, /--sidebar-width/);
  assert.match(css, /\.workspace-sidebar-scrim/);
  assert.match(css, /position: fixed/);
  assert.match(css, /--sidebar-expanded: 280px/);
  assert.match(css, /--sidebar-inset: 16px/);
  assert.match(css, /--sidebar-rail: 64px;[^}]*--sidebar-inset: 12px/);
  assert.match(css, /padding: 12px var\(--sidebar-inset\) 14px/);
  assert.match(css, /\.sidebar-collapsed \.workspace-sidebar-status \{ width: 40px/);
  assert.match(css, /grid-template-columns: 40px minmax\(0, 1fr\)/);
  assert.match(css, /\.sidebar-collapsed \.workspace-nav a \{ grid-template-columns: 40px 0/);
  assert.match(css, /\.sidebar-collapsed \.workspace-nav a\.active \{ background: transparent/);
  assert.match(css, /\.sidebar-collapsed \.workspace-nav a\.active > \.workspace-nav-mark \{ background:/);
  assert.doesNotMatch(css, /\.sidebar-collapsed \.workspace-sidebar-head \{[^}]*justify-content: center/s);
  assert.doesNotMatch(css, /\.sidebar-collapsed \.workspace-sidebar \{[^}]*padding-(?:right|left): 10px/s);
  assert.doesNotMatch(css, /\.workspace-sidebar-scrim \{[^}]*backdrop-filter/s);
  assert.doesNotMatch(css, /\.workspace-shell \{[^}]*transition: grid-template-columns/s);
  assert.match(css, /@media \(max-width: 520px\)/);
  assert.match(css, /device-selection-count \{ white-space: nowrap/);

  const tokens = await source("../app/design-tokens.css");
  const design = await source("../DESIGN.md");
  assert.match(css, /@import "\.\/design-tokens\.css"/);
  assert.match(tokens, /--primary: #5e6ad2/);
  assert.match(design, /status: active/);
  assert.match(design, /GSAP/);
  assert.match(design, /主工作区宽度不变/);
  assert.match(css, /\.workspace-nav h2 \{ height: 16px;[^}]*white-space: nowrap/);
  assert.doesNotMatch(css, /\.sidebar-collapsed \.workspace-nav \{[^}]*gap:/s);
  assert.match(design, /并在展开前、动画中和展开后保持完全一致/);
  assert.match(css, /container-name: workbench/);
  assert.match(css, /@container workbench \(max-width: 1100px\)/);
  assert.match(css, /container-name: segment-flow/);
  assert.match(css, /@container segment-flow \(max-width: 720px\)/);
  assert.match(css, /\.segment-config > div > span \{[^}]*white-space: nowrap/s);

  const sidebar = await source("../app/components/workspace-sidebar.tsx");
  assert.match(sidebar, /workspaceGroups/);
  assert.match(sidebar, /WorkspaceIcon/);
  assert.match(sidebar, /viewBox="0 0 24 24"/);
  assert.doesNotMatch(sidebar, /mark: "(?:录|机|文)"/);
});

test("run workspace only monitors and safely controls existing work", async () => {
  const response = await render("/run");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /运行/);
  assert.match(html, /暂停领取新任务/);
  assert.match(html, /安全停止运行中设备/);
  assert.match(html, /当前设备进度/);
  const page = await source("../app/run/page.tsx");
  assert.match(page, /\/api\/tasks\/stop/);
  assert.match(page, /\/api\/tasks\/cancel-pending/);
  assert.match(page, /active_tasks/);
  assert.match(page, /status\?\.active_tasks \|\| status\?\.tasks/);
  assert.doesNotMatch(page, /\/api\/run/);
  assert.doesNotMatch(page, /\/api\/model-key/);
});

test("results workspace joins tasks, incidents and interaction evidence", async () => {
  const response = await render("/records");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /结果/);
  assert.match(html, /任务/);
  assert.match(html, /纠错记录/);
  assert.match(html, /互动凭证/);
  const page = await source("../app/records/page.tsx");
  assert.match(page, /SUMMARY_SIZE = 5/);
  assert.match(page, /\/api\/records\/task-groups/);
  assert.match(page, /TaskGroupDetail/);
  assert.match(page, /已恢复并继续/);
  assert.match(page, /已跳过当前视频/);
});

test("interaction alert acknowledgement fails closed and v3 omits private-message scanning", async () => {
  const banner = await source("../app/components/interaction-alert-banner.tsx");
  const interactions = await source("../app/interactions/page.tsx");
  const groups = await source("../app/components/task-groups.tsx");
  assert.match(banner, /确认失败，提醒仍保留在这里/);
  assert.match(banner, /if \(!response\.ok\) throw/);
  assert.match(interactions, /unified_activity/);
  assert.match(interactions, /互动消息聚合页/);
  assert.match(groups, /workflow_version === "v3"/);
  assert.match(groups, /不会进入普通私信/);
});

test("task details retain verified action and recovery evidence", async () => {
  const groups = await source("../app/components/task-groups.tsx");
  assert.match(groups, /\/api\/task-image\?task_id=/);
  assert.match(groups, /evidence_groups/);
  assert.match(groups, /查看报错截图/);
  assert.match(groups, /action_routing/);
  assert.match(groups, /搜索来源可信/);
  assert.match(groups, /主题不符已拦截/);
  assert.match(groups, /互动消息记录/);
  assert.match(groups, /刷视频记录/);
  assert.match(groups, /task-phase-grid/);
  assert.match(groups, /搜索视频流/);
  assert.match(groups, /主页视频流/);
  assert.match(groups, /开始只读自动复验/);
  assert.match(groups, /处理后继续复验/);
  assert.match(groups, /const continuation = .*"\/continue"/);
  assert.match(groups, /\/api\/tasks\/\$\{encodeURIComponent\(inspection\.id\)\}\/recover\$\{continuation\}/);
  assert.doesNotMatch(groups, /href=\{[^\n]*screenshot_path/);
});

test("device workspace defaults to the standard virtual pool and gates physical devices", async () => {
  const response = await render("/devices");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /MuMu虚拟机管理/);
  assert.match(html, /添加虚拟机/);
  assert.match(html, /刷新虚拟机/);
  assert.match(html, /一键配置标准虚拟机池/);
  assert.match(html, /返回任务台/);
  const page = await source("../app/devices/page.tsx");
  assert.match(page, /\/api\/device-image\?device_id=/);
  assert.match(page, /开始初始化/);
  assert.match(page, /继续初始化/);
  assert.match(page, /DeviceOnboardingDialog/);
  assert.match(page, /\/api\/virtual-devices\/batch/);
  assert.match(page, /\/api\/virtual-device-backups/);
  assert.match(page, /恢复为新虚拟机/);
  assert.match(page, /批量启动/);
  assert.match(page, /批量停止/);
  assert.match(page, /问题与待办/);
  assert.match(page, /virtualDevice\.user_message/);
  assert.match(page, /virtualDevice\.suggested_action/);
  assert.match(page, /安装完成，继续检查/);
  assert.match(page, /查看连接与能力诊断/);
  assert.doesNotMatch(page, /查看就绪检查（/);
  assert.match(page, /LocalTemplatePanel/);
  assert.match(page, /重新检查并恢复巡检/);
  assert.match(page, /诊断编号/);
  assert.match(page, /启用真机支持/);
  assert.match(page, /physical_devices_enabled/);
  assert.match(page, /900×1600/);
  assert.match(page, /删除全部并重建/);
  assert.doesNotMatch(page, /\/api\/run/);
});

test("shared add-device dialog gates physical guidance and exposes MuMu handoff", async () => {
  const dialog = await source("../app/components/device-onboarding-dialog.tsx");
  for (const label of ["连接真机", "或者", "添加虚拟机", "前往MuMu官方下载", "复制Agent初始化文档"]) {
    assert.match(dialog, new RegExp(label));
  }
  assert.match(dialog, /\/api\/device-onboarding\/scan/);
  assert.match(dialog, /\/api\/virtual-devices/);
  assert.match(dialog, /不会自动开始正式任务/);
  assert.match(dialog, /physicalEnabled &&/);
  assert.match(dialog, /添加标准虚拟机/);
  assert.match(dialog, /waitForVirtualOperation\(API, result\.operation/);
  assert.match(dialog, /waitForInitialization\(API, deviceId/);
});

test("live-view fallback closes the remote session and releases control", async () => {
  const liveView = await source("../app/components/device-live-view.tsx");
  assert.match(liveView, /const enterFallback = \(detail: string\)/);
  assert.match(liveView, /closeSession\(\);\s*updateState\("fallback", detail\)/);
  assert.doesNotMatch(liveView, /\.catch\(\(\) => updateState\("fallback"/);
  assert.match(liveView, /if \(kind !== 0 && !firstFrameReceived\)/);
  assert.match(liveView, /视频通道已连接，正在等待首帧/);
});

test("assets and settings workspace includes content, presets, model and governance", async () => {
  const response = await render("/content");
  assert.equal(response.status, 200);
  const html = await response.text();
  for (const label of ["资产与设置", "内容计划", "参数预设", "模型连接", "评测与证据"]) assert.match(html, new RegExp(label));
  const page = await source("../app/content/page.tsx");
  assert.match(page, /\/api\/content-plans/);
  assert.match(page, /\/api\/presets/);
  assert.match(page, /\/api\/workbench\/draft/);
  assert.match(page, /\/api\/model-key/);
  assert.match(page, /fetchLocalApi/);
  assert.match(page, /modelAction/);
  assert.doesNotMatch(page, /auth_status: "saving"/);
  assert.match(page, /归档计划/);

  const localApi = await source("../app/lib/local-api.ts");
  assert.match(localApi, /AbortController/);
  assert.match(localApi, /响应超时/);
  const virtualOperations = await source("../app/lib/virtual-device-operations.ts");
  assert.match(virtualOperations, /fetchLocalApi/);
});

test("prompt guide documents every user-authored content field and links from forms", async () => {
  const response = await render("/content/guide");
  assert.equal(response.status, 200);
  const html = await response.text();
  for (const label of ["内容与提示词填写规范", "目标主题与判定标准", "搜索词", "全局评论写作模板", "主题模板补充要求", "评论词池", "评论发送前约束", "固定生效顺序"]) {
    assert.match(html, new RegExp(label));
  }

  const taskPage = await source("../app/page.tsx");
  const assetPage = await source("../app/content/page.tsx");
  for (const section of ["topic", "search", "comment-policy"]) assert.match(taskPage, new RegExp(`PromptGuideLink section="${section}"`));
  for (const section of ["topic", "search", "comment-template", "theme-template", "comment-pool"]) assert.match(assetPage, new RegExp(`PromptGuideLink section="${section}"`));
  assert.match(assetPage, /href="\/content\/guide"/);

  const guide = await source("../../docs/content-prompt-guide.md");
  for (const heading of ["目标主题与主题判定标准", "搜索词", "全局评论写作模板", "主题模板补充要求", "评论词池", "评论发送前约束"]) assert.match(guide, new RegExp(heading));
  assert.match(guide, /固定安全规则/);
});

test("governance and legacy evidence routes remain available", async () => {
  for (const path of ["/governance", "/interactions"]) {
    const response = await render(path);
    assert.equal(response.status, 200);
  }
  const governance = await source("../app/governance/page.tsx");
  assert.match(governance, /创建数据库备份/);
  assert.match(governance, /\/api\/topic-reviews\/confirm/);
  assert.doesNotMatch(governance, /api\/evidence\/delete|api\/evidence\/cleanup/);
  const interactions = await source("../app/interactions/page.tsx");
  assert.match(interactions, /\/api\/interaction-inspections/);
  assert.match(interactions, /互动凭证/);
});
