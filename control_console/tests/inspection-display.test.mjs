import assert from "node:assert/strict";
import test from "node:test";

async function displayModule() {
  try {
    return await import("../app/lib/inspection-display.mjs");
  } catch (error) {
    if (error?.code === "ERR_MODULE_NOT_FOUND") return {};
    throw error;
  }
}

test("task labels require an explicit home-badge payload and keep historical tasks legacy", async () => {
  const display = await displayModule();
  const label = (payload) => display.inspectionTaskLabel?.(payload) ?? "missing display helper";

  assert.equal(label({ inspection_mode: "home_badge" }), "消息巡检");
  assert.equal(label({ inspection_workflow_version: "home_badge" }), "消息巡检");
  assert.equal(label({ inspection_workflow_version: "v3" }), "旧版详细巡检");
  assert.equal(label({}), "旧版详细巡检");
});

test("frozen mode and observed result resolve without inventing legacy sections", async () => {
  const { resolveInspection } = await displayModule();
  assert.equal(typeof resolveInspection, "function");
  for (const payload of [{ inspection_mode: "home_badge" }, {}]) {
    const pending = resolveInspection({ payload, status: "pending", result: null });
    assert.equal(pending.phase, "pending");
    assert.equal(pending.message, "尚未检查");
    assert.equal(pending.showLegacySections, false);
  }
  const running = resolveInspection({ payload: { inspection_mode: "home_badge" }, status: "running", result: { navigation_message: "正在读取首页角标" } });
  assert.equal(running.message, "正在读取首页角标");
  assert.equal(running.showLegacySections, false);
  const conflict = resolveInspection({ payload: { inspection_mode: "home_badge" }, status: "completed", result: { workflow_version: "v3", sections: { received_likes: { status: "available" } } } });
  assert.equal(conflict.mode, "conflict");
  assert.equal(conflict.showLegacySections, false);
  assert.match(conflict.message, /版本冲突/);
  assert.equal(resolveInspection({ status: "failed", result: null }).showLegacySections, false);
  assert.equal(resolveInspection({ status: "completed", result: { workflow_version: "v3", sections: { received_likes: {} } } }).showLegacySections, true);
});

test("quantity status distinguishes unreadable digits and absent historic metadata", async () => {
  const display = await displayModule();
  assert.equal(display.homeBadgePresentation({ state: "present", quantity_status: "unreadable", message: "有消息" }).message, "已发现角标，数字尚未识别");
  assert.equal(display.homeBadgePresentation({ state: "present", badge_text: "3", message: "首页显示红点" }).message, "有消息，3条");
  assert.equal(display.homeBadgeQuantityNote?.({ state: "present" }), "此回执未记录可靠数量；可查看原图与角标裁剪核对。");
});

test('failure-only receipts without frozen mode remain unknown diagnostics', async () => {
  const { resolveInspection, inspectionReceiptPresentation } = await displayModule();
  const task = { status: 'failed', payload: {}, result: { status: 'failed', failure_reason: 'screenshot_failed' } };
  const display = resolveInspection(task);
  assert.equal(display.mode, 'unknown');
  assert.equal(display.label, '检查失败');
  assert.match(display.message, /模式未记录/);
  assert.equal(display.showLegacySections, false);
  assert.equal(inspectionReceiptPresentation(task).label, '检查失败');
});

test("receipt summaries expose conflicting versions as diagnostics in overview and details", async () => {
  const display = await displayModule();
  const receipt = display.inspectionReceiptPresentation({ workflow_version: "v3", status: "completed", result_kind: "alert", summary: { home_badge: { state: "present", badge_text: "3" } } });
  assert.equal(receipt.label, "版本冲突");
  assert.equal(receipt.kind, "incomplete");
});

test("home-badge presentation has two normal outcomes and treats unknown as a failed check", async () => {
  const display = await displayModule();
  const present = display.homeBadgePresentation?.({ state: "present", message: "首页消息按钮显示红点" });
  const absent = display.homeBadgePresentation?.({ state: "absent", message: "首页未见消息提醒" });
  const unknown = display.homeBadgePresentation?.({ state: "unknown", message: "首页被弹窗遮挡" });

  assert.deepEqual(present, { label: "有消息", kind: "alert", message: "首页消息按钮显示红点" });
  assert.deepEqual(absent, { label: "无消息", kind: "clear", message: "首页未见消息提醒" });
  assert.deepEqual(unknown, { label: "检查失败", kind: "incomplete", message: "首页被弹窗遮挡" });
});

test("home-badge presentation repeats readable badge text without inventing an exact count", async () => {
  const display = await displayModule();

  assert.deepEqual(display.homeBadgePresentation?.({ state: "present", badge_text: "5", message_count: 5, count_is_lower_bound: false }), { label: "有消息", kind: "alert", message: "有消息，5条" });
  assert.deepEqual(display.homeBadgePresentation?.({ state: "present", badge_text: "99+", message_count: null, count_is_lower_bound: true }), { label: "有消息", kind: "alert", message: "有消息，99+条" });
  assert.deepEqual(display.homeBadgePresentation?.({ state: "present", badge_text: null, message_count: null, count_is_lower_bound: false }), { label: "有消息", kind: "alert", message: "有消息" });
});

test("receipt presentation retains detailed historical meanings", async () => {
  const display = await displayModule();
  const receipt = (item) => display.inspectionReceiptPresentation?.(item) ?? { label: "missing", kind: "incomplete" };

  assert.deepEqual(receipt({ workflow_version: "home_badge", summary: { home_badge: { state: "present", message: "有红点" } } }), { label: "有消息", kind: "alert", message: "有红点" });
  assert.deepEqual(receipt({ workflow_version: "v3", result_kind: "alert", summary: {} }), { label: "发现新互动", kind: "alert" });
  assert.deepEqual(receipt({ workflow_version: "v3", result_kind: "clear", summary: {} }), { label: "无新互动", kind: "clear" });
  assert.deepEqual(receipt({ workflow_version: "v3", result_kind: "incomplete", summary: {} }), { label: "检查未完成", kind: "incomplete" });
});

test("incomplete home-badge receipts never present a reliable observation as an overall success", async () => {
  const display = await displayModule();
  const receipt = display.inspectionReceiptPresentation?.({
    workflow_version: "home_badge",
    status: "degraded",
    result_kind: "incomplete",
    summary: { home_badge: { state: "present", badge_text: "99+", count_is_lower_bound: true } },
  });

  assert.deepEqual(receipt, {
    label: "检查失败",
    kind: "incomplete",
    message: "未能完成检查；画面显示有消息，99+条",
  });
});

test("an unconfirmed alert separates the current failed check from the last reliable badge", async () => {
  const display = await displayModule();
  const alert = display.homeBadgeAlertPresentation?.({
    confirmed: false,
    last_checked_at: "2026-09-08T04:10:00Z",
    last_check_message: "本次检查失败，之前的提醒状态尚未确认",
    home_badge: { state: "present", badge_text: "99+", count_is_lower_bound: true },
  });

  assert.deepEqual(alert, {
    label: "本次未确认",
    kind: "incomplete",
    message: "本次未确认：本次检查失败，之前的提醒状态尚未确认｜上次有消息，99+条｜检查时间 2026-09-08T04:10:00Z",
  });
});
