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

  assert.equal(label({ inspection_mode: "home_badge" }), "消息提醒检查");
  assert.equal(label({ inspection_workflow_version: "home_badge" }), "消息提醒检查");
  assert.equal(label({ inspection_workflow_version: "v3" }), "旧版详细巡检");
  assert.equal(label({}), "旧版详细巡检");
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
