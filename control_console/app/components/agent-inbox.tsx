"use client";
import { useState } from "react";
import { agentRequest } from "../lib/agent-api";

type Handoff = { id: string; source: string; prompt: string; state: string; session_id?: string };
export default function AgentInbox({ sessionId, onChanged }: { sessionId: string; onChanged: () => Promise<void> }) {
  const [items, setItems] = useState<Handoff[]>([]);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  async function refresh() {
    setBusy(true);
    try { setItems((await agentRequest<{ handoffs: Handoff[] }>("handoffs")).handoffs); }
    catch (error) { setNotice(error instanceof Error ? error.message : "需求读取失败"); }
    finally { setBusy(false); }
  }
  async function accept(id: string) {
    setBusy(true); setNotice("");
    try {
      const result = await agentRequest<Handoff>(`handoffs/${id}/accept`, { session_id: sessionId });
      setItems((old) => old.map((item) => item.id === id ? result : item));
      setNotice(result.state === "accepted" ? "需求已交给当前助手；设备操作仍通过计划卡片确认。" : "投递结果待确认，请查看当前会话，不会重复投递。");
      await onChanged();
    } catch (error) { setNotice(error instanceof Error ? error.message : "投递失败，请刷新状态"); }
    finally { setBusy(false); }
  }
  return <details><summary>交给助手的外部需求</summary>
    <p>这里接收明确投递的需求，不会自动读取其他聊天。选择对话后，可把需求交给当前助手继续处理。</p>
    <button type="button" className="secondary" disabled={busy} onClick={() => void refresh()}>读取待办需求</button>
    {items.map((item) => <article className="agent-question" key={item.id}><strong>{item.source}</strong><p>{item.prompt}</p>
      <p>{({ waiting_user: "等待交给助手", accepted: "已投递", dispatching: "投递结果待确认", unknown: "结果待确认，请先查看原会话" } as Record<string, string>)[item.state] || item.state}</p>
      {item.state === "waiting_user" && <button className="secondary" type="button" disabled={busy || !sessionId} onClick={() => void accept(item.id)}>交给当前对话</button>}
    </article>)}
    {notice && <p role="status">{notice}</p>}
  </details>;
}
