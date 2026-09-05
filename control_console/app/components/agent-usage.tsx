"use client";
import { useState } from "react";
import { agentRequest } from "../lib/agent-api";

type Usage = { message: string; counts?: Record<string, number>; calls: { id: string; status: string; usage: Record<string, number> | null }[] };
export default function AgentUsage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  async function action(reset = false) {
    setBusy(true); setNotice("");
    try {
      if (reset) setNotice((await agentRequest<{ message: string }>("model-policy/reset", {})).message);
      setUsage(await agentRequest<Usage>("usage"));
    } catch (error) { setNotice(error instanceof Error ? error.message : "模型状态读取失败"); }
    finally { setBusy(false); }
  }
  return <details><summary>助手用量与模型故障恢复</summary>
    <p>处理好Key、套餐额度或权限后，可重置故障保护。此操作不会自动重试消息、切换服务商或清除用量记录。</p>
    <div className="agent-toolbar"><button className="secondary" type="button" disabled={busy} onClick={() => void action()}>读取用量</button>
      <button className="secondary" type="button" disabled={busy} onClick={() => void action(true)}>重置千问故障保护</button></div>
    {usage && <><p>{usage.message}</p><p>记录请求：{Object.values(usage.counts || {}).reduce((sum, value) => sum + value, 0)} 次</p>
      <ul>{usage.calls.slice(0, 10).map((call) => <li key={call.id}>{({ completed: "完成", failed: "失败", running: "请求中", cancelled: "已取消", interrupted: "已中断", timed_out: "超时" } as Record<string, string>)[call.status] || "结果待确认"} · {call.usage ? `用量 ${JSON.stringify(call.usage)}` : "用量未返回，不代表免费"}</li>)}</ul></>}
    {notice && <p role="status">{notice}</p>}
  </details>;
}
