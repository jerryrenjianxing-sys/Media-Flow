"use client";

import { useRef, useState } from "react";
import { fetchLocalApi } from "../lib/local-api";
import { navigationReason } from "../lib/navigation-feedback";
import { controlTask, isWaitingTask, supportedTaskProgress, taskControlActions, taskStatusLabel, type TaskProgress } from "../lib/task-progress.mjs";

const capabilityLabels: Record<string, string> = { like: "点赞", favorite: "收藏", like_favorite: "点赞与收藏", comment: "评论", comment_send: "评论发送", comment_preview: "评论预览", browse_home: "首页浏览", search_input: "搜索输入", topic_analysis: "主题识别" };
const waitingLabels: Record<string, string> = { previous_device_task_failed: "本设备前一任务失败，请核对证据后继续", model_circuit_open: "模型连续异常，等待连接恢复", model_unavailable: "模型暂不可用", device_offline: "设备离线", unknown_action_result: "动作结果未确认，请先核对证据", stopped_by_user: "用户已请求停止" };
function waitingReason(reason?: string | null) { return navigationReason(reason) || waitingLabels[reason || ""] || (reason && /[\u4e00-\u9fff]/.test(reason) ? reason : "等待条件尚未满足，请查看任务证据与连接状态"); }

export function TaskProgressPanel({ taskId, status, progress, onChanged }: { taskId: string; status: string; progress?: TaskProgress; onChanged?: () => void }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [updated, setUpdated] = useState<{ base: TaskProgress | undefined; task: { status: string; progress: TaskProgress } } | null>(null);
  const request = useRef<{ action: string; id: string } | null>(null);
  const current = updated && updated.base === progress ? updated.task : { status, progress };
  const p = supportedTaskProgress(current.progress);
  if (!p) return null;
  const act = async (action: string) => {
    if (!request.current || request.current.action !== action) request.current = { action, id: crypto.randomUUID() };
    setBusy(true);
    try {
      const reply = await controlTask(fetchLocalApi, "http://127.0.0.1:48138", taskId, action, request.current.id);
      setMessage(reply.user_message || taskStatusLabel(reply.status));
      if (reply.result?.task) setUpdated({ base: progress, task: reply.result.task });
      if (reply.status !== "unknown") request.current = null;
      onChanged?.();
    } catch { setMessage("请求结果未确认；再次点击将查询同一请求回执，不会重复提交"); }
    finally { setBusy(false); }
  };
  return <section className="task-progress-feedback" aria-label="本轮持久进度">
    <p>已处理 {p.processed_slots} · 成功 {p.successful_slots} · 失败 {p.failed_slots} · 不可用 {p.unavailable_slots}</p>
    <p>安全跳过 {p.skipped_slots}（不占名额） · 未确认动作 {p.unknown_actions}{p.next_slot != null ? ` · 下一名额 ${p.next_slot}` : ""}</p>
    {p.last_progress_at != null && <p>最近进展：{new Date(p.last_progress_at * 1000).toLocaleString()}</p>}
    {!!p.affected_capabilities?.length && <p>本设备 · 本批次暂停能力：{p.affected_capabilities.map((name) => capabilityLabels[name] || "其他任务能力").join("、")}</p>}
    {!!p.evidence_dirs?.length && <details><summary>已保存证据：{p.evidence_dirs.length} 处</summary>{p.evidence_dirs.map((path) => <p key={path}>{path}</p>)}</details>}
    {isWaitingTask(current.status) && <><p>{taskStatusLabel(current.status)}：{waitingReason(p.waiting_reason)}</p><p>{p.next_check_at ? `下一次自动检查：${new Date(p.next_check_at * 1000).toLocaleString()}` : "等待条件修复后恢复原任务"}</p></>}
    {taskControlActions(current.status, p).map((action) => <button key={action} type="button" className="secondary" disabled={busy} onClick={() => void act(action)}>{action === "resume_task" ? "恢复此任务" : "停止此任务"}</button>)}
    {message && <p role="status">{message}</p>}
  </section>;
}
