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

test("device preparation guide explains on-demand capabilities without legacy eligibility gates", async () => {
  const response = await render('/devices/guide');
  const html = await response.text();
  assert.equal(response.status, 200);
  assert.match(html, /真机按需准备/);
  assert.match(html, /未登录不阻断管理和参数准备/);
  assert.match(html, /消息巡检只看首页角标/);
  assert.match(html, /href="\/devices"/);
  assert.doesNotMatch(html, /抖音已安装并登录|运行3条零写入冒烟|完成后才把这台真机/);
});



test("interaction device summaries wrap instead of squeezing five columns on phones", async () => {
  const css=await source('../app/workspace-pages.css');
  assert.match(cssRule(css,'.interaction-device-overview > div'), /auto-fit/);
  assert.match(cssRule(css,'.interaction-device-overview > header'), /flex-direction:column/);
});

function cssRule(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
  assert.ok(match, `Missing CSS rule for ${selector}`);
  return match[1];
}

test("platform home offers Skill download and management without an embedded chat", async () => {
  const response = await render();
  const html = await response.text();
  assert.equal(response.status, 200);
  assert.doesNotMatch(html, /id="agent-input"|aria-label="会话历史"/);
  assert.doesNotMatch(html, /127\.0\.0\.1:3000|打开 Agent|返回 Agent/);
  assert.match(html, /下载 Skill/);
  assert.match(html, /复制 Skill/);
  assert.match(html, /href="\/manage"/);
  assert.doesNotMatch(html, /class="mf-management-nav"/);
  assert.doesNotMatch(html, /正在准备任务台/);
  assert.match(html, /任务台/);
  const settings=await render('/settings');
  assert.equal(settings.status,200);
  const settingsHtml=await settings.text();
  assert.match(settingsHtml,/设置与关于/);
  assert.doesNotMatch(settingsHtml,/id="agent-input"/);
  const legacy=await render('/?settings=models');
  assert.equal(legacy.status,307);
  assert.equal(legacy.headers.get('location'),'/settings');
});

test("Skill home keeps real local Agent marks and management links out of primary content", async () => {
  const html = await (await render()).text();
  assert.match(html,/src="\/agents\/codex.svg"/);
  assert.match(html,/暂停图标滚动/);
  assert.doesNotMatch(html,/href="\/(devices|workbench|governance|run)"/);
  const management=await (await render('/manage')).text();
  assert.match(management,/href="\/devices"/);
  assert.match(management,/class="mf-management-nav"/);
});

test("legacy session bookmarks stay on platform home and explain preserved data", async () => {
  const response = await render('/?session=ses_previous');
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('location'), null);
  const html = await response.text();
  assert.match(html, /旧会话数据仍保留/);
  assert.match(html, /下载 Skill/);
  assert.doesNotMatch(html, /127\.0\.0\.1:3000|id="agent-input"/);
});



test('legacy task links lead to workbench and emergency access remains visible', async () => {
  for (const path of ['devices', 'run']) {
    const page = await source(`../app/${path}/page.tsx`);
    assert.doesNotMatch(page, /href="\/"/);
    assert.match(page, /href="\/workbench"/);
  }
  const shell = await source('../app/components/console-shell.tsx');
  assert.match(shell, /className="mf-stop"\s+href="\/run"/);
  const studioCss = await source('../app/console-shell.css');
  assert.match(studioCss, /\.mf-status,\s*\.mf-issues,\s*\.mf-stop\s*\{[^}]*display:\s*inline-flex/);
  assert.doesNotMatch(studioCss, /[^{}]*\.mf-stop\b[^{}]*\{[^}]*display:\s*none/);
  assert.match(await source('../app/records/page.tsx'), /setSelectedGroup\(groups.items\[0\]\)/);
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

test("management retains one branded application header with persisted theme and recoverable status", async () => {
  const response = await render('/manage');
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /MediaFlow · 一站式媒体自动化Agent/);
  assert.equal([...html.matchAll(/<header\b[^>]*class="mf-topbar"/g)].length, 1);
  for (const label of ["管理中心", "停止入口", "设置"]) assert.ok(html.includes(label));
  assert.match(html, /aria-label="切换到(?:浅|深)色主题"/);
  assert.match(html, /href="#main-content"/);
  assert.match(html, /id="main-content"/);
  assert.match(html, /mediaflow-asset-recovery/);
  assert.match(html, /asset_reload/);
  assert.doesNotMatch(html, /正在准备任务台/);
  assert.doesNotMatch(html, /Building your site|Your site is taking shape/);

  const shell = await source("../app/components/console-shell.tsx");
  assert.match(shell, /mediaflow-theme/);
  assert.match(shell, /prefers-color-scheme: dark/);
  assert.match(shell, /mediaflow-theme-change/);
  assert.match(shell, /fetchLocalApi/);
  assert.match(shell, /\/api\/status/);
  assert.match(shell, /问题待处理/);
  assert.match(shell, /服务未连接 · 重试/);
  assert.match(shell, /\/devices#device-issues/);
  assert.match(shell, /className="mf-issues"[^>]*>[\s\S]*?<svg[^>]*aria-hidden="true"/);
  assert.match(shell, /<InteractionAlertBanner\s*\/>/);
});

test("management pages retain all business destinations and return to platform home", async () => {
  const response = await render('/manage');
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.equal([...html.matchAll(/<header\b[^>]*class="mf-topbar"/g)].length, 1);
  const navigation = html.match(/<nav class="mf-management-nav"[^>]*>[\s\S]*?<\/nav>/)?.[0];
  assert.ok(navigation, 'Management routes must expose shared navigation');
  for (const path of ['/', '/manage', '/devices', '/workbench', '/run', '/records', '/interactions', '/content', '/governance', '/settings']) {
    assert.ok(navigation.includes(`href="${path}"`), `Missing management destination: ${path}`);
  }
  assert.match(navigation, /href="\/manage"[^>]*aria-current="page"/);
  assert.doesNotMatch(html, /127\.0\.0\.1:3000|回到 Agent|返回 Agent/);
  const settingsHtml = await (await render('/settings')).text();
  assert.match(settingsHtml, /外部 Agent/);
  assert.match(settingsHtml, /平台首页/);
  assert.doesNotMatch(settingsHtml, /127\.0\.0\.1:3000|打开 Agent|试用对话引擎/);
});

test("settings group model access, operation guide, preferences, data and collapsed About details", async () => {
  const page = await source('../app/settings/page.tsx');
  for (const link of ['/content', '/devices/guide', '/content/guide', '/governance']) assert.ok(page.includes(link));
  assert.match(page, /<PreferenceSettings/);
  assert.match(page, /<AboutSettings/);
  const about = await source('../app/components/settings-about.tsx');
  for (const label of ['关于与开源许可', '使用说明与免责声明']) {
    assert.ok(about.includes(`<details><summary>${label}</summary>`));
  }
  assert.doesNotMatch(about, /<details\b[^>]*\bopen(?:\s|=|>)/);
  assert.match(about, /product_version\?\.display_version/);
  assert.match(about, /value="system"/);
  assert.match(about, /localStorage\.removeItem\("mediaflow-theme"\)/);
  assert.match(about, /mediaflow-theme-change/);
  const html = await (await render()).text();
  assert.doesNotMatch(html, /关于与开源许可|使用说明与免责声明|OpenCode（MIT）/);
});



test("task workbench follows the four decisions and server-owned planning contract", async () => {
  const page = await source("../app/workbench/page.tsx");
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
  const page = await source("../app/workbench/page.tsx");
  assert.match(page, /高级设置/);
  assert.match(page, /连续异常停止阈值/);
  assert.match(page, /轮次间隔/);
  assert.match(page, /消息巡检/);
  assert.match(page, /评论发送前约束/);
  assert.match(page, /search_trust_results/);
  assert.match(page, /matched_like_probability/);
  assert.match(page, /matched_favorite_probability/);
  assert.match(page, /matched_comment_probability/);
  assert.match(page, /device_ids: config\.device_ids/);
  assert.match(page, /preview_only: config\.preview_only/);
});

test("visual system uses real capsule switches, responsive layout and reduced motion", async () => {
  const css = await source("../app/globals.css");
  assert.match(css, /\.capsule-switch i \{/);
  assert.match(css, /border-radius: 999px/);
  assert.match(css, /\.capsule-switch input:checked \+ i::after/);
  assert.match(css, /prefers-reduced-motion: reduce/);
  assert.match(css, /@media \(max-width: 520px\)/);
  assert.match(css, /device-selection-count \{ white-space: nowrap/);
  assert.match(css, /\.inspection-mode-switch \{/);
  assert.match(css, /\.interaction-home-badge \{/);

  const tokens = await source("../app/design-tokens.css");
  const design = await source("../DESIGN.md");
  assert.match(css, /@import "\.\/design-tokens\.css"/);
  for (const token of ['--canvas', '--surface-1', '--hairline', '--ink', '--primary', '--danger']) {
    assert.ok(tokens.includes(`${token}:`), `Missing shared design token: ${token}`);
  }
  assert.match(tokens, /:root\[data-theme="light"\]/);
  assert.match(tokens, /color-scheme:\s*dark/);
  assert.match(tokens, /color-scheme:\s*light/);
  assert.match(design, /status: active/);
  assert.match(css, /container-name: workbench/);
  assert.match(css, /@container workbench \(max-width: 1100px\)/);
  assert.match(css, /container-name: segment-flow/);
  assert.match(css, /@container segment-flow \(max-width: 720px\)/);
  assert.match(css, /\.segment-config > div > span \{[^}]*white-space: nowrap/s);

  const studioCss = await source('../app/console-shell.css');
  assert.match(studioCss, /@media\s*\(max-width:\s*700px\)/);
  assert.match(studioCss, /prefers-reduced-motion:\s*reduce/);
  const issueIconCss = cssRule(studioCss, '.mf-issues svg');
  assert.match(issueIconCss, /width:\s*16px/);
  assert.match(issueIconCss, /height:\s*16px/);
  assert.match(issueIconCss, /flex:\s*none/);
  assert.doesNotMatch(css + studioCss, /(?:^|})\s*\.(?:selected|online|busy|ready)\s*\{/);
});

test("run workspace only monitors and safely controls existing work", async () => {
  const response = await render("/run");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /运行/);
  assert.match(html, /暂停领取新任务/);
  assert.match(html, /安全停止活动设备/);
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
});

test("task result and notification surfaces distinguish home messages from historical detailed inspection", async () => {
  const banner = await source("../app/components/interaction-alert-banner.tsx");
  const interactions = await source("../app/interactions/page.tsx");
  const groups = await source("../app/components/task-groups.tsx");
  const run = await source("../app/run/page.tsx");
  assert.match(banner, /home_badge/);
  assert.match(banner, /homeBadgeAlertPresentation/);
  assert.match(banner, /homeAlerts\.map/);
  assert.doesNotMatch(banner, /alerts\.find/);
  assert.match(banner, /平台确认不会清除抖音角标/);
  assert.match(interactions, /homeBadgePresentation/);
  assert.match(interactions, /summary\.home_badge/);
  assert.match(interactions, /未能确认/);
  assert.match(interactions, /本次没有可显示的截图证据/);
  assert.match(groups, /resolveInspection\(inspection\)/);
  assert.match(groups, /result\.home_badge/);
  assert.match(run, /resolveInspection\(task\)/);
});

test("task details retain verified action and recovery evidence", async () => {
  const groups = await source("../app/components/task-groups.tsx");
  assert.match(groups, /\/api\/task-image\?task_id=/);
  assert.match(groups, /evidence_groups/);
  assert.match(groups, /查看报错截图/);
  assert.match(groups, /action_routing/);
  assert.match(groups, /搜索来源可信/);
  assert.match(groups, /主题不符已拦截/);
  assert.match(groups, /消息巡检记录/);
  assert.match(groups, /刷视频记录/);
  assert.match(groups, /task-phase-grid/);
  assert.match(groups, /搜索视频流/);
  assert.match(groups, /主页视频流/);
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
  assert.match(page, /recheck_home_badge/);
  assert.match(page, /重新检查消息巡检/);
  assert.match(page, /inspection_recheck:\s*Boolean\(inspectionMode\)/);
  assert.match(page, /inspection_mode:\s*inspectionMode/);
  assert.match(page, /initializationRequest\(onlineDevice\.device_id, "start", "home_badge"\)/);
  assert.match(page, /安装完成，继续检查/);
  assert.match(page, /查看连接与能力诊断/);
  assert.doesNotMatch(page, /查看就绪检查（/);
  assert.match(page, /LocalTemplatePanel/);
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

  const taskPage = await source("../app/workbench/page.tsx");
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
  assert.match(governance, /重新读取/);
  assert.match(governance, /finally \{ setLoading\(false\); \}/);
  assert.doesNotMatch(governance, /本机控制服务未启动/);
  assert.match(governance, /\/api\/topic-reviews\/confirm/);
  assert.doesNotMatch(governance, /api\/evidence\/delete|api\/evidence\/cleanup/);
  const interactions = await source("../app/interactions/page.tsx");
  assert.match(interactions, /\/api\/interaction-inspections/);
  assert.match(interactions, /互动凭证/);
});
