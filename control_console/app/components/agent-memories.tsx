"use client";

import { useState } from "react";
import { agentRequest } from "../lib/agent-api";

type Memory = { id: string; version: number; title: string; body: string; enabled: boolean; source: string };

export default function AgentMemories() {
  const [items, setItems] = useState<Memory[]>([]);
  const [editing, setEditing] = useState<Memory | null>(null);
  const [history, setHistory] = useState<Memory[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  async function refresh() {
    setBusy(true);
    try { setItems((await agentRequest<{ memories: Memory[] }>("memories")).memories); }
    catch (error) { setNotice(error instanceof Error ? error.message : "记忆读取失败"); }
    finally { setBusy(false); }
  }
  async function edit(item: Memory) {
    setEditing(item); setTitle(item.title); setBody(item.body); setHistory([]); setBusy(true);
    try { setHistory((await agentRequest<{ versions: Memory[] }>(`memories/${item.id}/history`)).versions); }
    catch (error) { setNotice(error instanceof Error ? error.message : "历史读取失败"); }
    finally { setBusy(false); }
  }
  async function save(change: object, path = "memories") {
    setBusy(true); setNotice("");
    try {
      const result = await agentRequest<{ message: string }>(path, change);
      setNotice(result.message); setEditing(null); setTitle(""); setBody(""); setHistory([]);
      await refresh();
    } catch (error) { setNotice(error instanceof Error ? error.message : "记忆保存失败"); }
    finally { setBusy(false); }
  }
  return <details className="agent-memory" onToggle={(event) => { if (event.currentTarget.open) void refresh(); }}>
    <summary>本机记忆与偏好</summary>
    <p>可在对话中要求助手记住偏好，也可在这里编辑。记忆不会改变操作权限或替你确认任务。</p>
    <button type="button" className="secondary" disabled={busy} onClick={() => void refresh()}>刷新记忆</button>
    {items.map((item) => <article className="agent-memory-item" key={item.id}>
      <h4>{item.title} · v{item.version}{!item.enabled && " · 已禁用"}</h4><p>{item.body}</p>
      <div className="agent-toolbar">
        <button type="button" className="secondary" disabled={busy} onClick={() => void edit(item)}>编辑与历史</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => void save({ ...item, expected_version: item.version, enabled: !item.enabled })}>{item.enabled ? "禁用" : "启用"}</button>
      </div>
    </article>)}
    <form onSubmit={(event) => { event.preventDefault(); void save({ id: editing?.id, expected_version: editing?.version || 0, title, body, enabled: editing?.enabled ?? true }); }}>
      <h4>{editing ? "编辑记忆" : "新增记忆"}</h4>
      <label>标题<input value={title} maxLength={120} required disabled={busy} onChange={(event) => setTitle(event.target.value)}/></label>
      <label>内容<textarea value={body} maxLength={8000} required rows={3} disabled={busy} onChange={(event) => setBody(event.target.value)}/></label>
      <div className="agent-toolbar"><button type="submit" className="primary" disabled={busy}>保存记忆</button>
        {editing && <button type="button" className="secondary" disabled={busy} onClick={() => { setEditing(null); setTitle(""); setBody(""); setHistory([]); }}>取消编辑</button>}
      </div>
    </form>
    {editing && history.map((version) => <article key={version.version} className="agent-memory-item">
      <h4>v{version.version} · {version.title}</h4><p>{version.body}</p>
      <button type="button" className="secondary" disabled={busy || version.version === editing.version}
        onClick={() => void save({ version: version.version, expected_version: editing.version }, `memories/${editing.id}/restore`)}>恢复此版本</button>
    </article>)}
    {notice && <p role="status" className="agent-notice">{notice}</p>}
  </details>;
}
