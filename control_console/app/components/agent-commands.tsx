"use client";
import { useState } from "react";
import { agentRequest } from "../lib/agent-api";

export type AgentCommand = { command_id: string; fingerprint: string; state: string; label: string;
  request: { name: string; action: string; backup: boolean; settings?: Record<string, unknown> };
  operation?: { id: string; status: string; message: string; error: string; progress: number } };

export default function AgentCommands({ commands, sessionId, onChanged }: { commands: AgentCommand[]; sessionId: string; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  async function confirm(command: AgentCommand) {
    setBusy(command.command_id); setNotice("");
    try {
      await agentRequest(`sessions/${sessionId}/commands/${command.command_id}/confirm`, { fingerprint: command.fingerprint, confirmed: true, confirmation_name: names[command.command_id] });
      await onChanged();
    } catch (error) { setNotice(error instanceof Error ? error.message : "操作结果待确认，请刷新；不会自动重放"); }
    finally { setBusy(""); }
  }
  if (!commands?.length) return null;
  return <section aria-label="虚拟机操作计划">
    {commands.map((command) => <article className="agent-question" key={command.command_id}>
      <h3>{command.label} · {command.request.name}</h3>
      {!!Object.keys(command.request.settings || {}).length && <pre>{JSON.stringify(command.request.settings, null, 2)}</pre>}
      {command.operation ? <><p>{command.operation.message || "查看操作进度"} · {command.operation.progress}%</p>
        <p>{command.operation.status === "completed" ? "已完成" : command.operation.status === "failed" ? "操作失败，现场已保留" : command.operation.status === "waiting_user" ? "等待你处理，请打开设备页" : command.operation.status === "cancelled" ? "已取消" : "操作进行中"}</p>
        {command.operation.error && <p role="alert">{command.operation.error}</p>}<a href="/devices">查看设备与操作记录</a></> : command.state === "awaiting_confirmation" ? <>
          {command.request.action === "delete" && <><p role="alert">删除可能导致账号和数据不可恢复。{command.request.backup ? "本次先备份再删除。" : "本次不创建备份。"}</p>
            <label>输入完整名称确认<input value={names[command.command_id] || ""} onChange={(e) => setNames((old) => ({ ...old, [command.command_id]: e.target.value }))}/></label></>}
          <button type="button" className="primary" disabled={!!busy || (command.request.action === "delete" && names[command.command_id] !== command.request.name)} onClick={() => void confirm(command)}>{busy === command.command_id ? "正在提交…" : `确认${command.label}这台虚拟机`}</button>
        </> : <p>此计划已取消或过期，可让助手重新规划。</p>}
    </article>)}
    {notice && <p role="status" className="agent-notice">{notice}</p>}
  </section>;
}
