"use client";

import { useEffect, useState } from "react";
import { agentRequest, type AgentProvider } from "../lib/agent-api";

type Flow = { id: string; provider: string; state: string; message: string; url?: string; callback_method?: string; deadline: number };
const terminal = new Set(["completed", "failed", "cancelled", "expired", "interrupted"]);

export default function AgentProviderSettings({ provider, onChanged }: { provider: AgentProvider; onChanged: () => void }) {
  const [method, setMethod] = useState(0);
  const [key, setKey] = useState("");
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [flow, setFlow] = useState<Flow | null>(null);
  const [polling, setPolling] = useState(true);
  const methods = provider.auth_methods || [{ type: "api", label: "API Key" }];
  const selected = methods[method] || methods[0];
  const flowId = flow?.id;
  const flowState = flow?.state;

  useEffect(() => {
    let cancelled = false;
    void agentRequest<{ flows: Flow[] }>("auth-flows").then((result) => {
      if (!cancelled) setFlow(result.flows.find((entry) => entry.provider === provider.id) || null);
    }).catch(() => { /* The explicit refresh button can recover the status. */ });
    return () => { cancelled = true; };
  }, [provider.id]);

  useEffect(() => {
    if (!flowId || !flowState || terminal.has(flowState) || !polling) return;
    let cancelled = false, reading = false, failures = 0;
    const timer = window.setInterval(async () => {
      if (reading) return;
      reading = true;
      try {
        const next = await agentRequest<Flow>(`auth-flows/${flowId}`);
        if (!cancelled) {
          setFlow(next);
          if (terminal.has(next.state)) onChanged();
        }
        failures = 0;
      } catch (error) {
        if (!cancelled && ++failures >= 3) {
          setPolling(false);
          setNotice(error instanceof Error ? error.message : "登录状态读取失败，请刷新；不会重复授权");
        }
      } finally { reading = false; }
    }, 2500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [flowId, flowState, polling, onChanged]);

  async function perform(path: string, body: object, isFlow = false) {
    setBusy(true); setNotice("");
    try {
      const result = await agentRequest<Flow & { message: string }>(path, body);
      if (isFlow) { setFlow(result); setPolling(true); }
      else { setNotice(result.message); onChanged(); }
      setKey(""); setCode("");
    } catch (error) { setNotice(error instanceof Error ? error.message : "设置失败，请重试"); }
    finally { setBusy(false); }
  }

  const pending = !!flow && !terminal.has(flow.state);
  const visible = (selected.prompts || []).filter((prompt) => !prompt.when || (prompt.when.op === "eq"
    ? inputs[prompt.when.key] === prompt.when.value : inputs[prompt.when.key] !== prompt.when.value));
  const payload = { method, inputs: Object.fromEntries(visible.map((prompt) => [prompt.key, inputs[prompt.key] || ""])) };
  return <div className="agent-auth" aria-label="助手服务商登录">
    <h3>{provider.name} · 登录设置</h3>
    <p>使用 OpenCode 原生登录方式，仅保存在这台电脑。保存不等于模型调用已验证。</p>
    {provider.id === "mediaflow-qwen-token-plan" && <p>Token Plan 为实验性接入，请使用套餐专属 Key；额度与使用限制以千问工作台为准，不会自动切换到按量接口。</p>}
    <label>登录方式<select value={method} disabled={busy || pending} onChange={(event) => { setMethod(Number(event.target.value)); setInputs({}); setKey(""); }}>
      {methods.map((entry, index) => <option key={index} value={index}>{entry.label}</option>)}
    </select></label>
    {visible.map((prompt) => <label key={prompt.key}>{prompt.message}{prompt.type === "select"
      ? <select value={inputs[prompt.key] || ""} disabled={busy || pending} onChange={(event) => setInputs({ ...inputs, [prompt.key]: event.target.value })}>
          <option value="">请选择</option>{prompt.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      : <input value={inputs[prompt.key] || ""} placeholder={prompt.placeholder} disabled={busy || pending} maxLength={2000}
          onChange={(event) => setInputs({ ...inputs, [prompt.key]: event.target.value })}/>}</label>)}
    {selected.type === "api" && <label>API Key<input type="password" autoComplete="new-password" value={key} disabled={busy || pending}
      onChange={(event) => setKey(event.target.value)} placeholder="直接输入此服务商的 Key，不会发送到聊天" maxLength={16000}/></label>}
    <div className="agent-toolbar">
      <button type="button" className="primary" disabled={busy || pending || (selected.type === "api" && !key.trim())}
        onClick={() => void perform(`providers/${encodeURIComponent(provider.id)}/${selected.type === "api" ? "key" : "authorize"}`,
          selected.type === "api" ? { ...payload, key } : payload, selected.type === "oauth")}>
        {busy ? "正在处理…" : selected.type === "api" ? "保存 Key" : "开始网页登录"}
      </button>
      {provider.id === "mediaflow-qwen-token-plan" && <button type="button" className="secondary" disabled={busy || pending}
        onClick={() => void perform("providers/reference", {})}>使用现有 MediaFlow 千问配置</button>}
    </div>
    {flow && <div className="agent-auth-flow" role="status">
      <p>{flow.message}</p>
      {flow.state === "waiting_user" && <>
        {flow.url && <a className="secondary" href={flow.url} target="_blank" rel="noreferrer">打开服务商登录页面</a>}
        {flow.callback_method === "code" && <label>授权码<input type="password" autoComplete="off" value={code} onChange={(event) => setCode(event.target.value)}/></label>}
        <button type="button" className="primary" disabled={busy} onClick={() => void perform(`auth-flows/${flow.id}/callback`, { code }, true)}>已完成登录，继续</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => void perform(`auth-flows/${flow.id}/cancel`, {}, true)}>取消登录</button>
      </>}
      <button type="button" className="secondary" disabled={busy} onClick={async () => {
        setBusy(true);
        try { setFlow(await agentRequest<Flow>(`auth-flows/${flow.id}`)); setPolling(true); onChanged(); }
        catch (error) { setNotice(error instanceof Error ? error.message : "读取失败"); }
        finally { setBusy(false); }
      }}>刷新登录状态</button>
    </div>}
    {notice && <p className="agent-notice" role="status">{notice}</p>}
  </div>;
}
