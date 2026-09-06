"use client";

import { useState } from "react";
import { agentRequest } from "../lib/agent-api";

export type AgentPlan = {
  plan_id: string; state: string; deadline: number; message: string;
  config: { device_ids: string[]; video_count: number; round_count: number; content_mode: string; search_query?: string; inspection_every_rounds: number; engagement_inspection_enabled: boolean };
  preview: { plan_hash: string; total_task_count: number; write_actions: string[]; requires_confirmation: boolean; warnings: string[]; blockers: string[]; probabilities: Record<string, number>; comment_mode: string };
  result?: { tasks: { id: string; status: string }[]; state: string };
  execution?: {message:string;reason_code:string;updated_at:number};
};

export default function AgentPlans({ plans, sessionId, onChanged, onNotice }: { plans: AgentPlan[]; sessionId: string; onChanged: () => Promise<void>; onNotice?:(value:string)=>void }) {
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [writeConfirm, setWriteConfirm] = useState<Record<string, boolean>>({});
  async function confirm(plan: AgentPlan) {
    setBusy(plan.plan_id); setNotice("");
    try {
      const result = await agentRequest<{ message: string }>(`sessions/${sessionId}/plans/${plan.plan_id}/confirm`, {
        plan_hash: plan.preview.plan_hash, confirmed: true, confirm_writes: writeConfirm[plan.plan_id] === true,
      });
      setNotice(result.message); onNotice?.(result.message); await onChanged();
    } catch (error) { const message=error instanceof Error ? error.message : "提交结果待确认，请刷新本计划；不会重复建任务";setNotice(message);onNotice?.(message); }
    finally { setBusy(""); }
  }
  async function repreview(plan:AgentPlan){
    setBusy(plan.plan_id);
    const key=`mediaflow-repreview:${plan.plan_id}`;
    try{const id=window.localStorage.getItem(key)||crypto.randomUUID();window.localStorage.setItem(key,id);await agentRequest(`sessions/${sessionId}/plans/${plan.plan_id}/repreview`,{request_id:id});await onChanged();onNotice?.("已重新检查，请核对新计划并确认；尚未执行。");}
    catch(error){onNotice?.(error instanceof Error?error.message:"重新检查失败");}finally{setBusy("");}
  }
  if (!plans?.length) return null;
  return <section className="agent-plans" aria-label="待确认计划与执行回执">
    {plans.map((plan) => <article key={plan.plan_id} className="agent-question">
      <h3>{plan.result ? "任务执行回执" : "请核对任务计划"}</h3>
      <p>{plan.config.device_ids.length}台设备 · 每轮{plan.config.video_count}条视频 · {plan.config.round_count}轮 · {plan.preview.total_task_count}个任务</p>
      <p>入口：{({ general: "首页", search: "搜索", mixed: "混合", hybrid: "搜索与首页交替" } as Record<string, string>)[plan.config.content_mode]}{plan.config.search_query && ` · 搜索“${plan.config.search_query}”`}</p>
      <p>互动巡检：{plan.config.engagement_inspection_enabled ? `每${plan.config.inspection_every_rounds}轮一次` : "关闭"} · 评论：{plan.preview.comment_mode}</p>
      <p>点赞{Math.round((plan.preview.probabilities.like || 0) * 100)}% · 收藏{Math.round((plan.preview.probabilities.favorite || 0) * 100)}% · 评论{Math.round((plan.preview.probabilities.comment || 0) * 100)}%</p>
      <details><summary>设备、提醒与参数</summary><p>{plan.config.device_ids.join("、")}</p>{plan.preview.warnings.map((x, i) => <p key={i}>{x}</p>)}</details>
      {plan.preview.blockers.map((x, i) => <p key={i} role="alert">{x}</p>)}
      {plan.execution && <p role="status">{plan.execution.message}</p>}
      {plan.result ? <div><p>本批次：{plan.result.state === "cancelled" ? "已停止" : "已提交，实际进度如下"}</p>
        {plan.result.tasks.map((task) => <p key={task.id}><a href="/results">{task.id.slice(0, 12)} · {({ pending: "排队", running: "执行中", completed: "完成", failed: "失败", degraded: "部分完成", cancelled: "已取消", stopped: "已停止" } as Record<string, string>)[task.status] || task.status}</a></p>)}
        {plan.result.state === "active" && plan.result.tasks.some((t) => t.status === "pending") && <button type="button" className="secondary" disabled={!!busy} onClick={() => void confirm(plan)}>检查并恢复本批执行者</button>}
      </div> : plan.state === "awaiting_confirmation" ? <>
        {plan.preview.requires_confirmation && <label><input type="checkbox" checked={writeConfirm[plan.plan_id] || false} onChange={(e) => setWriteConfirm((v) => ({ ...v, [plan.plan_id]: e.target.checked }))}/>我确认本计划允许{plan.preview.write_actions.join("、")}</label>}
        <button type="button" className="primary" disabled={!!busy || (plan.preview.requires_confirmation && !writeConfirm[plan.plan_id])} onClick={() => void confirm(plan)}>{busy === plan.plan_id ? "正在提交…" : "确认并执行这份计划"}</button>
      </> : <div><p>{plan.state === "expired" ? "计划已过期，需重新检查后确认" : plan.state === "cancelled" ? "计划已取消" : "当前条件不满足，请处理后重新检查"}</p>{plan.state !== "cancelled" && <button type="button" className="secondary" disabled={!!busy} onClick={()=>void repreview(plan)}>重新检查这份计划</button>}</div>}
    </article>)}
    {notice && <p role="status" className="agent-notice">{notice}</p>}
  </section>;
}
