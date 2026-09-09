export function inspectionModeFromPayload(payload) {
  return payload?.inspection_mode === "home_badge" || payload?.inspection_workflow_version === "home_badge"
    ? "home_badge"
    : "legacy";
}

export function inspectionTaskLabel(payload) {
  return inspectionModeFromPayload(payload) === "home_badge" ? "消息巡检" : "旧版详细巡检";
}

export function homeBadgePresentation(homeBadge) {
  const badgeText = String(homeBadge?.badge_text || "").trim();
  const quantityMessage = homeBadge?.state === "present" ? homeBadge?.quantity_status === "unreadable"
    ? "已发现角标，数字尚未识别" : homeBadge?.quantity_status === "conflict" ? "已发现角标，数量识别结果冲突" : badgeText ? `有消息，${badgeText}条` : "" : "";
  const message = quantityMessage || homeBadge?.message || (homeBadge?.state === "present"
    ? badgeText ? `有消息，${badgeText}条` : "有消息"
    : homeBadge?.state === "absent" ? "无消息" : "未能确认首页消息提醒状态");
  if (homeBadge?.state === "present") return { label: "有消息", kind: "alert", message };
  if (homeBadge?.state === "absent") return { label: "无消息", kind: "clear", message };
  return { label: "检查失败", kind: "incomplete", message };
}

export function homeBadgeQuantityNote(homeBadge) {
  if (homeBadge?.badge_text) return `记录到角标 ${homeBadge.badge_text}；没有进入消息核对。`;
  if (homeBadge?.quantity_status === "unreadable") return "可查看原图与角标裁剪核对；本次不推测数量。";
  if (homeBadge?.quantity_status === "dot") return "已发现消息红点；不推测数量。";
  if (homeBadge?.quantity_status === "none" && homeBadge?.state === "absent") return "本次检查未发现消息角标。";
  if (homeBadge?.quantity_status === "conflict") return "数量识别结果冲突；请查看原图与角标裁剪。";
  return "此回执未记录可靠数量；可查看原图与角标裁剪核对。";
}

export function homeBadgeSourceLabel(homeBadge) {
  const source = homeBadge?.quantity_source;
  if (source === "ui_tree") return "页面结构读取数字";
  if (source === "local_glyph") return "本地字形识别";
  if (source === "vision" || homeBadge?.source === "vision") return "视觉辅助判断";
  return homeBadge?.source === "local" ? "本地截图判断" : "回执未记录识别来源";
}

// Task-detail APIs flatten the frozen payload, while the live API nests it.
// Missing results never select historical detail panels.
export function resolveInspection(task = {}) {
  const payload = task.payload || task;
  const result = task.result || (task.summary ? { ...task, home_badge: task.summary.home_badge, sections: task.summary.sections } : null);
  const payloadVersion = payload.inspection_workflow_version;
  const payloadMode = payload.inspection_mode || (payloadVersion ? payloadVersion === "home_badge" ? "home_badge" : "legacy" : null);
  const resultVersion = result?.workflow_version;
  const resultMode = resultVersion ? resultVersion === "home_badge" ? "home_badge" : "legacy" : result?.home_badge ? "home_badge" : result?.sections ? "legacy" : null;
  const conflict = Boolean((payloadMode && resultMode && payloadMode !== resultMode)
    || (payloadVersion && resultVersion && payloadVersion !== resultVersion)
    || (result?.home_badge && resultMode === "legacy")
    || (payload.inspection_mode && payloadVersion && (payload.inspection_mode === "home_badge") !== (payloadVersion === "home_badge")));
  const mode = conflict ? "conflict" : payloadMode || resultMode || "unknown";
  const status = task.status || result?.status;
  const phase = status === "pending" ? "pending" : ["running", "waiting_model", "waiting_device", "waiting_user"].includes(status) ? "running" : "final";
  let display;
  if (conflict) display = { label: "版本冲突", kind: "incomplete", message: "冻结任务参数与实际结果版本冲突；请核对原始回执与证据。" };
  else if (phase === "pending") display = { label: "尚未检查", kind: "incomplete", message: "尚未检查" };
  else if (phase === "running") display = { label: "检查进行中", kind: "incomplete", message: result?.navigation_message || ({ waiting_model: "等待模型恢复", waiting_device: "等待设备恢复", waiting_user: "等待用户处理" }[status]) || "正在检查，等待阶段回执" };
  else if (!result || !Object.keys(result).length) display = { label: status === "stopped" || status === "cancelled" ? "已停止" : "未取得检查结果", kind: "incomplete", message: "未取得检查结果；请查看任务状态与已保存证据。" };
  else if (mode === "home_badge") display = receiptOutcome({ workflow_version: "home_badge", status, result_kind: task.result_kind || (result.status === "failed" || result.status === "degraded" ? "incomplete" : undefined), summary: { home_badge: result.home_badge } });
  else if (mode === "legacy") display = { label: "旧版详细巡检 · 历史只读", kind: "incomplete", message: "以下仅展示原始历史结果，不提供旧版执行入口。" };
  else display = { label: status === "failed" ? "检查失败" : "巡检模式未记录", kind: "incomplete", message: "巡检模式未记录；请核对任务状态、原始回执与已保存证据。" };
  return { ...display, mode, phase, showLegacySections: mode === "legacy" && phase === "final" && Boolean(result?.sections && Object.keys(result.sections).length) };
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
  const resolved = resolveInspection(inspection || {});
  if (resolved.mode === "conflict" || resolved.mode === "unknown" || resolved.phase !== "final") {
    return { label: resolved.label, kind: resolved.kind, message: resolved.message };
  }
  return receiptOutcome(inspection);
}

function receiptOutcome(inspection) {
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
