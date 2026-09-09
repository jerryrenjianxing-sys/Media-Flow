"use client";

import { useRef, useState } from "react";
import { fetchLocalApi } from "../lib/local-api";
import { controlTask, isWaitingTask, taskStatusLabel, type TaskProgress } from "../lib/task-progress.mjs";

export function TaskProgressPanel({ taskId, status, progress, onChanged }: { taskId: string; status: string; progress?: TaskProgress; onChanged?: () => void }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [updated, setUpdated] = useState<{ base: TaskProgress | undefined; task: { status: string; progress: TaskProgress } } | null>(null);
  const request = useRef<{ action: string; id: string } | null>(null);
  const current = updated && updated.base === progress ? updated.task : { status, progress };
  const p = current.progress;
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
    {!!p.affected_capabilities?.length && <p>暂停能力：{p.affected_capabilities.join("、")}</p>}
    {!!p.evidence_dirs?.length && <details><summary>已保存证据：{p.evidence_dirs.length} 处</summary>{p.evidence_dirs.map((path) => <p key={path}>{path}</p>)}</details>}
    {isWaitingTask(current.status) && <><p>{taskStatusLabel(current.status)}：{p.waiting_reason || "请查看任务证据"}</p><p>{p.next_check_at ? `下一次后端检查：${new Date(p.next_check_at * 1000).toLocaleString()}` : "等待条件修复后恢复原任务"}</p>{p.available_actions?.map((action) => <button key={action} type="button" className="secondary" disabled={busy} onClick={() => void act(action)}>{action === "resume_task" ? "恢复此任务" : "停止此任务"}</button>)}</>}
    {message && <p role="status">{message}</p>}
  </section>;
}
