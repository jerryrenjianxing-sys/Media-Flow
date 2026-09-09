"use client";
/* eslint-disable @next/next/no-img-element -- local runtime evidence is loaded on demand */

import { useEffect, useState } from "react";
import { fetchLocalApi } from "../lib/local-api";
import { navigationReason } from "../lib/navigation-feedback";
import { homeBadgeQuantityNote, homeBadgeSourceLabel, resolveInspection, type HomeBadge } from "../lib/inspection-display.mjs";
import { isWaitingTask, taskStatusLabel, type TaskProgress } from "../lib/task-progress.mjs";
import { TaskProgressPanel } from "./task-progress";

const API = "http://127.0.0.1:48138";

export type TaskStatus = "pending" | "running" | "waiting_model" | "waiting_device" | "waiting_user" | "completed" | "degraded" | "failed" | "stopped" | "cancelled";
export type TaskRound = {
  progress?: TaskProgress;
  id: string;
  device_id: string;
  task_type: string;
  status: TaskStatus;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  round_index: number | null;
  round_count: number | null;
  inspection_index?: number | null;
  after_round_index?: number | null;
  inspection_every_rounds?: number | null;
  inspection_mode?: "home_badge" | "legacy";
  inspection_workflow_version?: "home_badge" | "v1" | "v2" | "v3";
  content_plan?: { revision_number?: number; plan_name?: string; theme_name?: string; theme_queue_index?: number; theme_queue_size?: number; search_query?: string } | null;
  parent_task_id?: string | null;
  recovery?: { status?: "queued" | "running" | "ready" | "waiting_user" | "failed"; progress_current?: number; progress_total?: number; message?: string; replacement_task_id?: string | null; expected?: { app_version?: string; display_signature?: string }; actual?: { app_version?: string; display_signature?: string }; error?: string | null } | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
};
export type TaskGroup = {
  progress?: Pick<TaskProgress, "schema_supported" | "processed_slots" | "successful_slots" | "failed_slots" | "unavailable_slots" | "unknown_actions" | "skipped_slots">;
  id: string;
  device_id: string;
  device_name: string;
  task_type: string;
  status: TaskStatus | "partial_failed" | "partial_degraded";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  rounds_total: number;
  completed_rounds: number;
  degraded_rounds: number;
  failed_rounds: number;
  running_rounds: number;
  pending_rounds: number;
  stopped_rounds: number;
  cancelled_rounds: number;
  inspection_status?: TaskStatus | "partial_failed" | "partial_degraded" | null;
  inspection_total: number;
  completed_inspections: number;
  degraded_inspections: number;
  failed_inspections: number;
  pending_inspections: number;
  recovered_preconditions?: number;
  control_flow_success?: number;
  control_flow_total?: number;
  control_flow_success_rate?: number;
  data_sections_available?: number;
  data_sections_total?: number;
  data_completeness_rate?: number;
  videos_seen: number;
  non_video_feed_items: number;
  feed_phase_reentries: number;
  likes: number;
  favorites: number;
  comments_sent: number;
  video_errors: number;
  model_attempts: number;
  model_valid_decisions: number;
  model_errors: number;
  model_valid_response_rate: number;
  tasks: TaskRound[];
  inspections: TaskRound[];
};

type EvidenceImage = { name: string; label: string; video_index?: number; feed_phase?: "search" | "home" | null };
type EvidenceKind = "video" | "like" | "favorite" | "comment" | "correction";
type EvidenceGroups = Record<EvidenceKind, EvidenceImage[]>;
type ActionRoute = { route: string; feed_phase?: "search" | "home" | null; count: number };
type ActionRouting = Record<"like" | "favorite" | "comment", ActionRoute[]>;
type TaskIncident = {
  id: string;
  video_index: number | null;
  stage: string;
  error_type: string;
  error_message: string;
  outcome: "recovered" | "skipped" | "device_fatal" | "model_failed" | "model_circuit_open";
  context?: { model_error?: { kind?: string; retryable?: boolean; status_code?: number | null } };
  has_screenshot: boolean;
  has_ui_tree?: boolean;
  analysis_status?: string;
  analysis?: { summary?: string; suggested_rule?: string; auto_applicable?: false } | null;
};
type TaskDetailRound = TaskRound & { images: EvidenceImage[]; evidence_groups: EvidenceGroups; action_routing: ActionRouting; incidents: TaskIncident[]; incident_evidence_status?: "available" | "not_captured_historical" | "not_required" };
type InspectionEntry = { display_name?: string; time?: string; preview?: string; summary?: string; unread_count?: number | null };
type InspectionSection = { status: "available" | "unavailable" | "failed"; count?: number | null; unread_count?: number | null; entries?: InspectionEntry[]; truncated?: boolean; reason?: string | null; scroll_count?: number; complete?: boolean; baseline_status?: string; entry_badge?: { has_unread?: boolean; unread_count?: number | null; indicator?: "number" | "dot" | "none" } };
type InspectionResult = { status?: "completed" | "degraded" | "failed"; workflow_version?: "home_badge" | "v1" | "v2" | "v3"; restored?: boolean; failure_reason?: string | null; failure_class?: "recoverable_precondition"; expected_app_version?: string; actual_app_version?: string; expected_display_signature?: string; actual_display_signature?: string; side_effect_notice?: string; home_badge?: HomeBadge; unified_activity?: { complete?: boolean; read_boundary?: string | null; scroll_count?: number; unread_item_count?: number | null; reason_code?: string | null }; sections?: Record<string, InspectionSection> };
type PhaseSummary = { label?: string; videos?: number; model_attempts?: number; model_valid_decisions?: number; topic_exact?: number; likes?: number; favorites?: number; comments_sent?: number; known_safe_skips?: number; unknown_blocked_pages?: number; incidents?: number; recoveries?: number };

const evidenceKinds: { kind: EvidenceKind; label: string; resultKey: string }[] = [
  { kind: "video", label: "视频", resultKey: "videos_seen" },
  { kind: "like", label: "点赞", resultKey: "likes" },
  { kind: "favorite", label: "收藏", resultKey: "favorites" },
  { kind: "comment", label: "评论", resultKey: "comments_sent" },
  { kind: "correction", label: "纠错", resultKey: "video_errors" },
];

const formatDuration = (start?: string | null, end?: string | null, now = Date.now()) => {
  if (!start) return "尚未开始";
  const total = Math.max(0, Math.round(((end ? new Date(end).getTime() : now) - new Date(start).getTime()) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours ? `${hours}时${minutes}分` : minutes ? `${minutes}分${seconds}秒` : `${seconds}秒`;
};

export const groupStatusText = (group: TaskGroup) => {
  if (!group.rounds_total && group.inspection_total) {
    return `消息巡检 · ${inspectionGroupText(group)}`;
  }
  if (isWaitingTask(group.status)) return taskStatusLabel(group.status);
  if (group.status === "running") return `${group.running_rounds} 轮执行中`;
  if (group.status === "pending") return `${group.pending_rounds} 轮等待`;
  if (group.status === "partial_failed") return `${group.completed_rounds} 成功 · ${group.failed_rounds} 失败`;
  if (group.status === "partial_degraded") return `${group.completed_rounds} 完整 · ${group.degraded_rounds} 降级`;
  if (group.status === "failed") return `${group.failed_rounds}/${group.rounds_total} 轮失败`;
  if (group.status === "degraded") return `${group.degraded_rounds}/${group.rounds_total} 轮完成，有异常`;
  if (group.status === "completed") return `${group.completed_rounds}/${group.rounds_total} 轮成功`;
  if (group.status === "stopped") return "已停止";
  return "已取消";
};

const inspectionTaskStatusText = (status: TaskStatus) => taskStatusLabel(status);
const inspectionGroupText = (group: TaskGroup) => {
  if (!group.inspection_total) return "";
  // The API aggregates inspection tasks independently of video rounds, with
  // active states before historical failures. Keep that ordering in both views.
  const status = group.inspection_status ?? (!group.rounds_total ? group.status : null);
  if (status === "running") return "正在检查";
  if (status && isWaitingTask(status)) return taskStatusLabel(status);
  if (status === "pending") return "等待检查";
  if (status === "cancelled") return "已取消";
  if (status === "stopped") return "已停止";
  const completed = group.completed_inspections ?? (status === "completed" ? group.inspection_total : 0);
  if (status === "completed") return `${completed}/${group.inspection_total} 次完成`;
  if (status === "partial_failed" || status === "partial_degraded") {
    return [`${completed}/${group.inspection_total} 次完成`,
      group.failed_inspections ? `${group.failed_inspections} 次失败` : "",
      group.degraded_inspections ? `${group.degraded_inspections} 次部分可用` : "",
    ].filter(Boolean).join(" · ");
  }
  if (status === "failed") return `${group.failed_inspections ?? group.inspection_total} 次失败`;
  if (status === "degraded") return `${group.degraded_inspections ?? group.inspection_total} 次部分可用`;
  return "巡检状态未记录";
};
const inspectionSectionStatusText = (section?: InspectionSection) => section?.status === "available" ? "已读取" : section?.status === "unavailable" ? "当前不可用" : "检查失败";
const inspectionReasonText = (reason?: string | null) => navigationReason(reason) ?? (reason === "visitor_history_disabled" || reason === "visitor_entry_not_available" || reason === "visitor_entry_not_found" ? "账号未开启访客记录，访客类互动可能不完整" : reason === "list_boundary_not_confirmed" ? "未找到已读边界，也无法确认列表到底；本次不会误报无新互动" : reason === "interaction_entry_not_found" ? "消息页中没有找到可确认的互动消息入口" : reason === "interaction_entry_ambiguous" ? "出现多个互动消息候选，已停止以避免点错" : reason === "unified_activity_page_not_recognized" || reason === "unified_activity_page_changed" ? "互动消息聚合页结构无法安全确认" : reason === "v3_calibration_missing" || reason === "v3_calibration_unstable" ? "这台标准虚拟机尚未完成互动巡检v3三次复验" : reason === "v3_standard_display_required" ? "设备不符合900×1600、320 DPI标准" : reason === "private_message_rows_ambiguous" ? "当前消息列表无法可靠区分私信和推荐卡片" : reason === "like_rows_ambiguous" ? "收到的赞页面暂时无法可靠识别" : reason === "home_restore_failed" ? "检查后未能确认返回首页" : reason === "v2_app_version_changed" || reason === "v3_app_version_changed" ? "抖音版本已变化，任务在点击任何巡检入口前安全停止" : reason === "v2_display_signature_changed" || reason === "v3_display_signature_changed" ? "设备显示环境已变化，任务在点击任何巡检入口前安全停止" : "当前页面未能安全识别");

const roundStatusText = (round: TaskRound) => taskStatusLabel(round.status, round);
const incidentStatusText = (incident: TaskIncident) => incident.outcome === "recovered" ? "页面已恢复" : incident.outcome === "device_fatal" ? "页面需处理" : incident.outcome === "model_circuit_open" ? "模型通道已熔断" : incident.outcome === "model_failed" ? "模型调用失败" : "已安全跳过";
const actionRouteText = (route: string) => route === "search_source_trusted" ? "搜索来源可信" : route === "topic_matched" ? "主题匹配" : route === "topic_mismatch_blocked" ? "主题不符已拦截" : route === "safety_blocked" ? "安全检查已拦截" : route === "other_safe_content" ? "其他安全内容" : route;

export function TaskGroupList({ groups, now, onOpen }: { groups: TaskGroup[]; now: number; onOpen: (group: TaskGroup) => void }) {
  if (!groups.length) return <p className="empty">暂无任务记录。配置策略后提交第一轮测试。</p>;
  return <>{groups.map((group) => <article key={group.id} className="task-group-row">
    <span className={`task-dot ${group.status}`}/>
    <div className="task-group-main">
      <div className="task-group-title"><strong>{group.device_name}</strong><span>{group.rounds_total} 轮{group.inspection_total ? ` · ${group.inspection_total} 次巡检` : ""}</span></div>
      <small>{group.rounds_total ? "刷视频" : "消息巡检"} · {new Date(group.created_at).toLocaleString("zh-CN", { hour12: false })} · {group.started_at ? `总耗时 ${formatDuration(group.started_at, group.finished_at, now)}` : group.status === "pending" ? "尚未开始" : "开始时间未记录"}</small>
      <div className="task-group-metrics">{group.rounds_total > 0 && <><span>视频 <b>{group.videos_seen}</b></span>{group.non_video_feed_items > 0 && <span>图文跳过 <b>{group.non_video_feed_items}</b></span>}{group.feed_phase_reentries > 0 && <span>阶段重入 <b>{group.feed_phase_reentries}</b></span>}<span>点赞 <b>{group.likes}</b></span><span>收藏 <b>{group.favorites}</b></span><span>评论 <b>{group.comments_sent}</b></span></>}{group.inspection_total > 0 && <><span className={group.failed_inspections ? "danger" : group.degraded_inspections ? "warning" : ""}>巡检 <b>{inspectionGroupText(group)}</b></span></>}{group.recovered_preconditions ? <span className="warning">自动复验 <b>{group.recovered_preconditions}</b></span> : null}{group.rounds_total > 0 && group.model_attempts > 0 && <span>模型有效 <b>{Math.round(group.model_valid_response_rate * 100)}%</b></span>}{group.rounds_total > 0 && group.model_errors > 0 && <span className="warning">模型错误 <b>{group.model_errors}</b></span>}{group.rounds_total > 0 && group.video_errors > 0 && <span className="danger">页面异常 <b>{group.video_errors}</b></span>}</div>
      {group.rounds_total > 0 && group.progress?.schema_supported === true && <p>已处理 {group.progress.processed_slots} · 成功 {group.progress.successful_slots} · 失败 {group.progress.failed_slots} · 不可用 {group.progress.unavailable_slots}</p>}
    </div>
    <div className="task-group-actions"><b className={`task-status ${group.status}`}>{groupStatusText(group)}</b><button type="button" className="task-detail-button" onClick={() => onOpen(group)}>查看详情</button></div>
  </article>)}</>;
}

export function InspectionCard({ inspection }: { inspection: TaskDetailRound }) {
  const result = (inspection.result || {}) as InspectionResult;
  const homeBadge = result.home_badge;
  const display = resolveInspection(inspection);
  const isHomeBadge = display.mode === "home_badge";
  const sections = result.sections || {};
  const definitions = result.workflow_version === "v3" ? [
    { key: "received_likes", label: "点赞与收藏" },
    { key: "comment_danmaku", label: "评论、回复与弹幕" },
    { key: "profile_visitors", label: "主页访客" },
  ] : [
    { key: "private_messages", label: "私信列表" },
    { key: "received_likes", label: "收到的赞" },
    { key: "comment_danmaku", label: "评论与弹幕" },
    { key: "profile_visitors", label: "主页访客" },
  ];
  return <article className={`inspection-card ${inspection.status}`}>

    <div className="inspection-card-heading"><div><span>第 {inspection.inspection_index || "-"} 次{display.mode === "legacy" ? "旧版详细巡检 · 历史只读" : "消息巡检"}</span><small>{inspection.after_round_index ? `计划在第 ${inspection.after_round_index} 轮后检查 · ` : ""}{inspection.started_at ? formatDuration(inspection.started_at, inspection.finished_at) : display.phase === "pending" ? "等待排期" : "开始时间未记录"}</small></div><b className={`task-status ${inspection.status}`}>{inspectionTaskStatusText(inspection.status)}</b></div>
    <p className="inspection-card-notice">{isHomeBadge ? "只检查抖音首页的消息提醒，不进入消息，也不把平台确认当成抖音已读。" : display.mode === "legacy" ? "历史只读：保留原始详细巡检结果。" : "依据冻结任务参数和实际回执显示状态。"}</p>
    <div className={`inspection-recovery ${display.kind === "clear" ? "ready" : display.kind === "incomplete" ? "" : "alert"}`}><strong>{display.label}</strong>{display.message !== display.label && <span>{display.message}</span>}</div>
    {isHomeBadge && display.phase === "final" && homeBadge && <div className="inspection-recovery"><small>{homeBadgeQuantityNote(homeBadge)}</small><small>{homeBadgeSourceLabel(homeBadge)}</small>{homeBadge.evidence_missing?.length ? <small>部分截图证据未取得，请查看已保存的证据。</small> : null}</div>}
    {display.showLegacySections && result.workflow_version === "v3" && result.unified_activity && <div className={`inspection-recovery ${result.unified_activity.complete ? "ready" : "failed"}`}><strong>{result.unified_activity.complete ? "统一互动列表已完成" : "统一互动列表未完整完成"}</strong><span>{result.unified_activity.complete ? `${result.unified_activity.read_boundary === "first_screen" ? "首屏发现已读" : result.unified_activity.read_boundary === "after_scroll" ? "滑动后发现已读" : "列表结束"} · 滑动 ${result.unified_activity.scroll_count || 0} 次${typeof result.unified_activity.unread_item_count === "number" ? ` · 边界上方 ${result.unified_activity.unread_item_count} 条` : ""}` : inspectionReasonText(result.unified_activity.reason_code)}</span></div>}
    {inspection.parent_task_id && <div className="inspection-recovery ready"><strong>这是自动复验后的关联补跑</strong><span>原任务 {inspection.parent_task_id.slice(0, 8)} 保留原失败记录，本任务使用新档案执行。</span></div>}
    {inspection.recovery && <div className={`inspection-recovery ${inspection.recovery.status || "queued"}`}><strong>{inspection.recovery.status === "ready" ? "前置条件已自动复验" : inspection.recovery.status === "waiting_user" ? "等待你处理" : inspection.recovery.status === "failed" ? "自动复验失败" : "正在自动复验"}</strong><span>{inspection.recovery.message || `复验进度 ${inspection.recovery.progress_current || 0}/${inspection.recovery.progress_total || 3}`}</span>{inspection.recovery.expected?.app_version && <small>抖音 {inspection.recovery.expected.app_version} → {inspection.recovery.actual?.app_version || "未知"}{inspection.recovery.replacement_task_id ? ` · 替代任务 ${inspection.recovery.replacement_task_id.slice(0, 8)}` : ""}</small>}</div>}
    {display.showLegacySections && <div className="inspection-section-grid">{definitions.filter(({ key }) => sections[key]).map(({ key, label }) => {
      const section = sections[key];
      const badge = section?.entry_badge;
      const countText = section?.status === "available" ? typeof section.count === "number" ? `${section.count} 条可见记录` : section.complete === false ? "列表未完整清查" : "列表检查完成" : inspectionReasonText(section?.reason);
      const unreadText = badge?.indicator === "dot" ? "进入前有未读提示，页面未显示具体数量" : badge?.indicator === "number" ? `进入前显示 ${badge.unread_count || 0} 条未读提示` : "";
      return <section key={key} className={`inspection-section ${section?.status || "failed"}`}><div><strong>{label}</strong><b>{inspectionSectionStatusText(section)}</b></div><p>{countText}</p>{unreadText && <small>{unreadText}</small>}{Array.isArray(section?.entries) && section.entries.length > 0 && <ul>{section.entries.map((entry, index) => <li key={`${entry.display_name || "entry"}-${index}`}><b>{entry.display_name || "未显示名称"}</b><span>{entry.time || entry.summary || entry.preview || "当前列表可见"}</span></li>)}</ul>}{section?.truncated && <small>只显示本地有限摘要，其余已截断。</small>}</section>;
    })}</div>}
    {display.mode !== "conflict" && result.status === "failed" && <div className="task-round-error"><strong>{result.failure_class === "recoverable_precondition" ? "可恢复前置条件" : "未完成原因"}</strong><p>{inspectionReasonText(result.failure_reason)}</p>{result.expected_app_version && <small>抖音 {result.expected_app_version} → {result.actual_app_version || "未知"}</small>}</div>}
    {inspection.error && <div className="task-round-error"><strong>任务记录的异常</strong><p>{navigationReason(inspection.error) || inspection.error}</p></div>}
    {inspection.incidents.length > 0 && <div className="task-round-incidents"><strong>纠错记录与异常现场</strong>{inspection.incidents.map((incident) => <div key={incident.id}><span>{incident.stage} · {incidentStatusText(incident)}</span><p>{incident.error_type}: {incident.error_message}</p>{incident.analysis?.summary && <small>只读建议：{incident.analysis.summary}</small>}{incident.analysis?.suggested_rule && <small>候选规则：{incident.analysis.suggested_rule}</small>}{incident.has_ui_tree && <small>UI结构已配对保存</small>}{incident.has_screenshot && <a href={`${API}/api/incident-image?id=${encodeURIComponent(incident.id)}`} target="_blank" rel="noreferrer">查看异常现场</a>}</div>)}</div>}
    {inspection.incident_evidence_status === "not_captured_historical" && <div className="task-round-error"><strong>历史证据说明</strong><p>该历史运行未保存异常现场；系统不会补造截图或UI结构。</p></div>}
  </article>;
}

export function TaskGroupDetail({ group, onClose }: { group: TaskGroup | null; onClose: () => void }) {
  if (!group) return null;
  return <TaskGroupDetailDialog key={group.id} group={group} onClose={onClose}/>;
}

function TaskGroupDetailDialog({ group, onClose }: { group: TaskGroup; onClose: () => void }) {
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [rounds, setRounds] = useState<TaskDetailRound[]>([]);
  const [inspections, setInspections] = useState<TaskDetailRound[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeEvidence, setActiveEvidence] = useState<{ taskId: string; kind: EvidenceKind } | null>(null);
  useEffect(() => {
    if (![...group.tasks, ...(group.inspections || [])].some((task) => isWaitingTask(task.status) || task.status === "running")) return;
    const timer = window.setInterval(() => setRefreshVersion((value) => value + 1), 5000);
    return () => window.clearInterval(timer);
  }, [group]);

  useEffect(() => {
    const controller = new AbortController();
    const ids = [...group.tasks, ...(group.inspections || [])].map((task) => task.id);
    const chunks = Array.from({ length: Math.ceil(ids.length / 20) }, (_, index) => ids.slice(index * 20, index * 20 + 20));
    Promise.all(chunks.map(async (chunk) => {
      const response = await fetchLocalApi(`${API}/api/records/task-detail?ids=${chunk.map((id) => encodeURIComponent(id)).join(",")}`, { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error("详情读取失败");
      return (await response.json() as { tasks: TaskDetailRound[] }).tasks;
    }))
      .then((values) => {
        const items = values.flat();
        setRounds(items.filter((task) => task.task_type === "douyin_topic_session"));
        setInspections(items.filter((task) => task.task_type === "douyin_engagement_inspection"));
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "详情读取失败");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => { controller.abort(); window.removeEventListener("keydown", closeOnEscape); };
  }, [group, onClose, refreshVersion]);

  return <div className="task-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="task-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="task-detail-title">
      <header className="task-detail-header"><div><p className="section-index">DEVICE RUN DETAIL</p><h2 id="task-detail-title">{group.device_name}</h2><p>{groupStatusText(group)}{group.rounds_total > 0 ? ` · 共 ${group.rounds_total} 轮 · ${group.videos_seen} 条视频` : ""} · {group.inspection_total} 次消息巡检</p></div><button type="button" className="task-detail-close" aria-label="关闭任务详情" onClick={onClose}>×</button></header>
      {group.rounds_total > 0 && <div className="task-detail-summary"><div><span>完整成功</span><b>{group.completed_rounds}</b></div><div><span>降级轮次</span><b className={group.degraded_rounds ? "warning" : ""}>{group.degraded_rounds}</b></div><div><span>失败轮次</span><b className={group.failed_rounds ? "danger" : ""}>{group.failed_rounds}</b></div><div><span>互动消息</span><b className={group.failed_inspections ? "danger" : group.degraded_inspections ? "warning" : ""}>{group.inspection_total}</b></div><div><span>模型有效率</span><b>{Math.round((group.model_valid_response_rate ?? 1) * 100)}%</b></div><div><span>有效视频</span><b>{group.videos_seen}</b></div></div>}
      {loading && <p className="task-detail-loading">正在读取各轮结果与截图…</p>}
      {error && <p className="task-detail-error">{error}</p>}
      {inspections.length > 0 && <section className="inspection-detail-block"><div className="inspection-detail-title"><strong>消息巡检记录</strong><span>{inspectionGroupText(group)}</span></div><div className="inspection-list">{inspections.map((inspection) => <InspectionCard key={inspection.id} inspection={inspection}/>)}</div></section>}
      {rounds.length > 0 && <div className="inspection-detail-title"><strong>刷视频记录</strong><span>{rounds.length} 轮</span></div>}
      <div className="task-round-list">{rounds.map((round) => {
        const selectedKind = activeEvidence?.taskId === round.id ? activeEvidence.kind : null;
        const selectedImages = selectedKind ? (round.evidence_groups?.[selectedKind] || []) : [];
        const selectedLabel = evidenceKinds.find((item) => item.kind === selectedKind)?.label;
        const phaseSummaries = (round.result?.phase_summaries || {}) as Record<string, PhaseSummary>;
        const visiblePhases = (["search", "home"] as const).filter((phase) => phaseSummaries[phase]);
        return <article key={round.id} className={`task-round-card ${round.status}`}>
          <div className="task-round-heading"><div><span>第 {round.round_index || "-"} 轮</span><small>{round.id.slice(0, 8)} · {formatDuration(round.started_at, round.finished_at)}</small></div><b className={`task-status ${round.status}`}>{roundStatusText(round)}</b></div>
          <TaskProgressPanel taskId={round.id} status={round.status} progress={round.progress} onChanged={() => setRefreshVersion((value) => value + 1)}/>
          {round.content_plan && <div className="task-content-plan"><strong>{round.content_plan.theme_name}</strong><span>{round.content_plan.plan_name} · v{round.content_plan.revision_number} · 主题 {round.content_plan.theme_queue_index}/{round.content_plan.theme_queue_size}</span>{round.content_plan.search_query && <small>搜索词：{round.content_plan.search_query}</small>}</div>}
          {Array.isArray(round.result?.comment_asset_reviews) && round.result.comment_asset_reviews.length > 0 && <div className="task-comment-assets"><strong>评论资产</strong>{(round.result.comment_asset_reviews as Array<Record<string, unknown>>).map((item, index) => <span key={`${String(item.video_index)}-${index}`}>第 {String(item.video_index)} 条 · {item.source_type === "theme_pool" ? "主题词池" : item.source_type === "common_pool" ? "通用词池" : "自由生成"} · {String(item.final_comment || "已跳过")}</span>)}</div>}
          {visiblePhases.length > 0 && <div className="task-phase-grid">{visiblePhases.map((phase) => { const item = phaseSummaries[phase]; return <section key={phase} className={`task-phase-card ${phase}`}><div><strong>{item.label || (phase === "search" ? "搜索视频流" : "主页视频流")}</strong><span>有效视频 {Number(item.videos || 0)}</span></div><p>主题精确 {Number(item.topic_exact || 0)} · 点赞 {Number(item.likes || 0)} · 收藏 {Number(item.favorites || 0)} · 评论 {Number(item.comments_sent || 0)}</p><small>模型 {Number(item.model_valid_decisions || 0)}/{Number(item.model_attempts || 0)} · 已知跳过 {Number(item.known_safe_skips || 0)} · 漂移 {Number(item.unknown_blocked_pages || 0)} · 恢复 {Number(item.recoveries || 0)}</small></section>; })}</div>}
          <div className="task-round-metrics">{evidenceKinds.map(({ kind, label, resultKey }) => {
            const images = round.evidence_groups?.[kind] || [];
            const metric = <>{label} {Number(round.result?.[resultKey] || 0)}{images.length > 0 && <small>截图 {images.length}</small>}</>;
            if (!images.length) return <span key={kind}>{metric}</span>;
            const active = selectedKind === kind;
            return <button key={kind} type="button" className={`has-evidence${active ? " active" : ""}`} aria-expanded={active} onClick={() => setActiveEvidence(active ? null : { taskId: round.id, kind })}>{metric}</button>;
          })}</div>
          {(["like", "favorite", "comment"] as const).some((action) => (round.action_routing?.[action] || []).length > 0) && <div className="task-action-routing"><strong>动作路由</strong>{(["like", "favorite", "comment"] as const).map((action) => {
            const routes = round.action_routing?.[action] || [];
            if (!routes.length) return null;
            const label = action === "like" ? "点赞" : action === "favorite" ? "收藏" : "评论";
            return <span key={action}><b>{label}</b>{routes.map((item) => `${item.feed_phase === "search" ? "搜索流 · " : item.feed_phase === "home" ? "主页流 · " : ""}${actionRouteText(item.route)} ×${item.count}`).join(" · ")}</span>;
          })}</div>}
          {(Number(round.result?.model_attempts || 0) > 0 || Number(round.result?.model_errors || 0) > 0) && <div className="task-model-metrics"><span>模型尝试 <b>{Number(round.result?.model_attempts || 0)}</b></span><span>有效判断 <b>{Number(round.result?.model_valid_decisions || 0)}</b></span><span>有效率 <b>{Math.round(Number(round.result?.model_valid_response_rate || 0) * 100)}%</b></span><span>模型错误 <b>{Number(round.result?.model_errors || 0)}</b></span></div>}
          <p className="task-evidence-hint">有截图留档的项目可点击查看，数量为本轮现有证据数。</p>
          {selectedKind && selectedImages.length > 0 && <div className="task-action-evidence">
            <div className="task-action-evidence-header"><strong>{selectedLabel}截图 · {selectedImages.length} 张</strong><button type="button" onClick={() => setActiveEvidence(null)}>收起</button></div>
            <div className="task-image-grid">{selectedImages.map((image) => <a key={image.name} href={`${API}/api/task-image?task_id=${encodeURIComponent(round.id)}&name=${encodeURIComponent(image.name)}`} target="_blank" rel="noreferrer"><img loading="lazy" src={`${API}/api/task-image?task_id=${encodeURIComponent(round.id)}&name=${encodeURIComponent(image.name)}`} alt={image.label}/><span>{image.feed_phase === "search" ? "搜索流 · " : image.feed_phase === "home" ? "主页流 · " : ""}{image.label}</span></a>)}</div>
          </div>}
          {round.error && <div className="task-round-error"><strong>失败原因</strong><p>{round.error}</p></div>}
          {round.incidents.length > 0 && <div className="task-round-incidents"><strong>异常与恢复</strong>{round.incidents.map((incident) => <div key={incident.id}><span>第 {incident.video_index ?? "-"} 条 · {incident.stage} · {incidentStatusText(incident)}</span><p>{incident.error_type}: {incident.error_message}</p>{incident.has_screenshot && <a href={`${API}/api/incident-image?id=${encodeURIComponent(incident.id)}`} target="_blank" rel="noreferrer">查看报错截图</a>}</div>)}</div>}
        </article>;
      })}</div>
    </section>
  </div>;
}
