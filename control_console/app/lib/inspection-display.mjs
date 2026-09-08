export function inspectionModeFromPayload(payload) {
  return payload?.inspection_mode === "home_badge" || payload?.inspection_workflow_version === "home_badge"
    ? "home_badge"
    : "legacy";
}

export function inspectionTaskLabel(payload) {
  return inspectionModeFromPayload(payload) === "home_badge" ? "消息提醒检查" : "旧版详细巡检";
}

export function homeBadgePresentation(homeBadge) {
  const badgeText = String(homeBadge?.badge_text || "").trim();
  const message = homeBadge?.message || (homeBadge?.state === "present"
    ? badgeText ? `有消息，${badgeText}条` : "有消息"
    : homeBadge?.state === "absent" ? "无消息" : "未能确认首页消息提醒状态");
  if (homeBadge?.state === "present") return { label: "有消息", kind: "alert", message };
  if (homeBadge?.state === "absent") return { label: "无消息", kind: "clear", message };
  return { label: "检查失败", kind: "incomplete", message };
}

export function homeBadgeAlertPresentation(summary, checkedAt = summary?.last_checked_at) {
  const previous = homeBadgePresentation(summary?.home_badge);
  if (summary?.confirmed !== false) return previous;
  const reason = String(summary?.last_check_message || "本次检查失败，之前的提醒状态尚未确认").trim();
  return {
    label: "本次未确认",
    kind: "incomplete",
    message: `本次未确认：${reason}｜上次${previous.message}${checkedAt ? `｜检查时间 ${checkedAt}` : ""}`,
  };
}

export function inspectionReceiptPresentation(inspection) {
  if (inspection?.workflow_version === "home_badge" || inspection?.summary?.home_badge) {
    const homeBadge = inspection?.summary?.home_badge;
    const display = homeBadgePresentation(homeBadge);
    const incomplete = inspection?.result_kind === "incomplete" || (inspection?.status && inspection.status !== "completed");
    if (incomplete) {
      const observation = homeBadge?.state === "present" || homeBadge?.state === "absent"
        ? `；画面显示${display.message}`
        : display.message ? `；${display.message}` : "";
      return { label: "检查失败", kind: "incomplete", message: `未能完成检查${observation}` };
    }
    return display;
  }
  if (inspection?.result_kind === "alert") return { label: "发现新互动", kind: "alert" };
  if (inspection?.result_kind === "clear" || inspection?.result_kind === "no_new_activity") return { label: "无新互动", kind: "clear" };
  return { label: "检查未完成", kind: "incomplete" };
}
