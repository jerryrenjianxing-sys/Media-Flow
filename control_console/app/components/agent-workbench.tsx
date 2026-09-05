"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { agentRequest, type AgentProvider } from "../lib/agent-api";
import AgentProviderSettings from "./agent-provider-settings";
import AgentMemories from "./agent-memories";
import AgentPlans, { type AgentPlan } from "./agent-plans";
import AgentCommands, { type AgentCommand } from "./agent-commands";
import AgentRepairs, { type AgentRepair } from "./agent-repairs";
import AgentInbox from "./agent-inbox";
import AgentUsage from "./agent-usage";

type EngineStatus = { state: string; message: string; notice?: string; pending_capabilities?: string[] };
type Provider = AgentProvider;
type Session = { id: string; title: string; provider: string; model: string };
type Message = { id: string; role: string; error?: string; parts: { type: string; text?: string; tool?: string; status?: string }[] };
type Question = { id: string; questions: { question: string; options?: { label: string }[] }[] };
type Conversation = { messages: Message[]; state: string; questions: Question[]; plans?: AgentPlan[]; commands?: AgentCommand[]; repairs?: AgentRepair[]; notice?: string };

export default function AgentWorkbench() {
  const [status, setStatus] = useState<EngineStatus>({ state: "unknown", message: "" });
  const [providers, setProviders] = useState<Provider[]>([]);
  const [provider, setProvider] = useState("mediaflow-qwen-token-plan");
  const [model, setModel] = useState("qwen3.8-flash");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [conversation, setConversation] = useState<Conversation>({ messages: [], state: "idle", questions: [] });
  const [text, setText] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState(true);
  const inFlight = useRef(false);
  const failures = useRef(0);
  const startDeadline = useRef(0);
  const requestId = useRef<string | null>(null);
  const selectedSession = useRef(sessionId);
  useEffect(() => { selectedSession.current = sessionId; }, [sessionId]);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const next = await agentRequest<EngineStatus>("status");
      setStatus(next);
      if (next.state === "starting" && startDeadline.current && Date.now() > startDeadline.current) {
        setNotice("启动等待已结束，请刷新状态；不会重复启动或执行任务。");
        setPolling(false);
      }
      if (next.state === "ready") {
        const list = await agentRequest<{ sessions: Session[] }>("sessions");
        setSessions(list.sessions);
        if (sessionId) {
          const nextConversation = await agentRequest<Conversation>(`sessions/${sessionId}/messages`);
          if (selectedSession.current === sessionId) setConversation(nextConversation);
        }
      }
      failures.current = 0;
    } catch (error) {
      failures.current += 1;
      setNotice(error instanceof Error ? error.message : "无法读取对话状态");
      if (failures.current >= 3) setPolling(false);
    } finally { inFlight.current = false; }
  }, [sessionId]);

  useEffect(() => {
    if (!polling) return;
    const first = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { window.clearTimeout(first); window.clearInterval(timer); };
  }, [polling, refresh]);

  async function connect() {
    setBusy(true); setNotice("");
    try { startDeadline.current = Date.now() + 65_000; setStatus(await agentRequest<EngineStatus>("start", {})); failures.current = 0; setPolling(true); }
    catch (error) { setNotice(error instanceof Error ? error.message : "连接失败"); }
    finally { setBusy(false); }
  }
  const loadProviders = useCallback(async () => {
    setBusy(true);
    try { setProviders((await agentRequest<{ providers: Provider[] }>("providers")).providers); }
    catch (error) { setNotice(error instanceof Error ? error.message : "目录读取失败"); }
    finally { setBusy(false); }
  }, []);
  async function create() {
    setBusy(true); setNotice("");
    try {
      const result = await agentRequest<{ session: Session }>("sessions", { provider, model });
      setSessionId(result.session.id); setConversation({ messages: [], state: "idle", questions: [] });
      setSessions((current) => [result.session, ...current]); setPolling(true);
    } catch (error) { setNotice(error instanceof Error ? error.message : "会话创建失败"); }
    finally { setBusy(false); }
  }
  async function send() {
    if (!text.trim() || !sessionId) return;
    setBusy(true); setNotice("");
    const id = requestId.current || crypto.randomUUID(); requestId.current = id;
    try {
      const result = await agentRequest<{ state: string }>(`sessions/${sessionId}/messages`, { text, request_id: id });
      if (result.state === "unknown" || result.state === "dispatching") {
        setNotice("发送结果待确认，请刷新原会话，不要重复发送。");
      } else { setText(""); requestId.current = null; }
      setPolling(true); await refresh();
    } catch (error) { setNotice(error instanceof Error ? error.message : "发送失败"); }
    finally { setBusy(false); }
  }
  async function stop() {
    setBusy(true);
    try { const result = await agentRequest<{ message: string }>(`sessions/${sessionId}/stop`, {}); setNotice(result.message); await refresh(); }
    catch (error) { setNotice(error instanceof Error ? error.message : "停止结果待确认，请刷新状态"); }
    finally { setBusy(false); }
  }
  async function answer(question: Question, form: HTMLFormElement) {
    setBusy(true);
    const data = new FormData(form);
    try { await agentRequest(`sessions/${sessionId}/answer`, { question_id: question.id, answers: question.questions.map((_, i) => [String(data.get(`answer-${i}`) || "")]) }); await refresh(); }
    catch (error) { setNotice(error instanceof Error ? error.message : "回答未保存，请重试"); }
    finally { setBusy(false); }
  }

  const active = conversation.state === "busy" || conversation.state === "retry";
  const currentProvider = providers.find((entry) => entry.id === provider);
  return <section className="panel agent-workbench" aria-labelledby="agent-heading">
    <header className="agent-heading"><div><p className="eyebrow">OPENCODE · 本机助手</p><h2 id="agent-heading">告诉助手你要做什么</h2></div>
      <span className="agent-state">{status.state === "ready" ? "引擎已连接" : status.state === "starting" ? "正在连接引擎" : "引擎未连接"}</span>
    </header>
    <p className="agent-scope">{status.notice || "对话接入验证中。原有任务和设置保留在下方。"}</p>
    <div className="agent-toolbar">
      {status.state !== "ready" && <button type="button" className="primary" disabled={busy || status.state === "starting" || status.state === "unknown"} onClick={() => void connect()}>连接助手</button>}
      <button type="button" className="secondary" disabled={busy} onClick={() => { failures.current = 0; setPolling(true); void refresh(); }}>刷新状态</button>
      <button type="button" className="secondary" disabled={busy || status.state !== "ready"} onClick={() => void loadProviders()}>服务商与模型</button>
      <a href="/content" className="secondary">模型配置</a>
    </div>
    {!!providers.length && <div className="agent-model-row">
      <label htmlFor="agent-provider">服务商<select id="agent-provider" value={provider} onChange={(event) => { setProvider(event.target.value); setModel(providers.find((p) => p.id === event.target.value)?.models[0]?.id || ""); }}>
        {providers.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}{entry.connected ? " · 已连接" : " · 未连接"}</option>)}
      </select></label>
      <label htmlFor="agent-model">模型<select id="agent-model" value={model} onChange={(event) => setModel(event.target.value)}>
        {currentProvider?.models.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}
      </select></label><button type="button" className="secondary" disabled={busy} onClick={() => void create()}>以此模型新建对话</button>
    </div>}
    {currentProvider && <AgentProviderSettings key={currentProvider.id} provider={currentProvider} onChanged={loadProviders}/>}
    <div className="agent-toolbar"><label htmlFor="agent-session">对话<select id="agent-session" value={sessionId} onChange={(event) => { setSessionId(event.target.value); setConversation({ messages: [], state: "idle", questions: [] }); requestId.current = null; setText(""); setNotice(""); setPolling(true); }}>
      <option value="">选择或新建对话</option>{sessions.map((session) => <option key={session.id} value={session.id}>{session.title}</option>)}
    </select></label><button type="button" className="secondary" disabled={busy || status.state !== "ready"} onClick={() => void create()}>新建对话</button></div>
    <div className="agent-messages" role="log" aria-label="助手对话记录">
      {!conversation.messages.length && <p>可以先问：“检查当前服务和任务状态”。实际操作入口会按接入进度开放，不会假装执行成功。</p>}
      {conversation.messages.map((message) => <article key={message.id} className={`agent-message ${message.role}`}><strong>{message.role === "user" ? "你" : "MediaFlow助手"}</strong>
        {message.parts.map((part, i) => part.type === "text" ? <p key={i}>{part.text}</p> : <div className="agent-tool" key={i}>{part.tool} · {part.status === "completed" ? "已完成" : part.status === "error" ? "失败" : "处理中"}</div>)}
        {message.error && <p role="alert">{message.error}</p>}
      </article>)}
    </div>
    <AgentPlans key={sessionId} sessionId={sessionId} plans={conversation.plans || []} onChanged={refresh}/>
    <AgentCommands key={`commands-${sessionId}`} sessionId={sessionId} commands={conversation.commands || []} onChanged={refresh}/>
    <AgentRepairs key={`repairs-${sessionId}`} sessionId={sessionId} repairs={conversation.repairs || []} onChanged={refresh}/>
    {conversation.questions.map((question) => <form key={question.id} className="agent-question" onSubmit={(event) => { event.preventDefault(); void answer(question, event.currentTarget); }}>
      {question.questions.map((item, i) => <label key={i} htmlFor={`${question.id}-${i}`}>{item.question}<input id={`${question.id}-${i}`} name={`answer-${i}`} required placeholder={item.options?.map((x) => x.label).join(" / ")}/></label>)}
      <button className="primary" disabled={busy} type="submit">回答并继续</button>
    </form>)}
    <label htmlFor="agent-input" className="sr-only">给助手的消息</label>
    <textarea id="agent-input" rows={3} maxLength={12000} value={text} onChange={(event) => { setText(event.target.value); requestId.current = null; }} placeholder="描述你的目标，缺少的关键参数由助手再问你。请勿在聊天中输入API Key。"/>
    <div className="agent-toolbar"><button type="button" className="primary" disabled={busy || !sessionId || active || status.state !== "ready" || !text.trim()} onClick={() => void send()}>{busy ? "正在处理…" : "发送"}</button>
      <button type="button" className="secondary" disabled={busy || !sessionId || status.state !== "ready"} onClick={() => void stop()}>停止对话及本次任务</button>
      <span>{conversation.state === "waiting_user" ? "等待你回答" : active ? "助手正在处理" : ""}</span>
    </div>
    {(notice || status.message) && <p className="agent-notice" role="status">{notice || status.message}</p>}
    {conversation.notice && <p role="status">{conversation.notice}</p>}
    {!polling && <p className="agent-notice">连续读取失败，已停止等待。请点击“刷新状态”；不会自动重复执行。</p>}
    <AgentMemories/>
    <AgentInbox sessionId={sessionId} onChanged={refresh}/>
    <AgentUsage/>
    {!!status.pending_capabilities?.length && <details><summary>尚未完成的接入</summary><p>{status.pending_capabilities.join("、")}</p></details>}
  </section>;
}
