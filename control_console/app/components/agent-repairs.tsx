"use client";
import { useState } from "react";
import { agentRequest } from "../lib/agent-api";
import { API } from "./workbench-types";

export type AgentRepair = { id: string; purpose: string; state: string; revision: string; error?: string };
type TestReceipt = { id: string; mode: string; target: string; state: string; output: string; exit_code?: number };

export default function AgentRepairs({ repairs, sessionId, onChanged }: { repairs: AgentRepair[]; sessionId: string; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [details, setDetails] = useState<Record<string, { patch: string; changed_files: string[]; tests: TestReceipt[] }>>({});
  const [downloads, setDownloads] = useState<Record<string, string>>({});
  async function inspect(id: string) {
    setBusy(id);
    try {
      const base = `sessions/${sessionId}/repairs/${id}`;
      const [diff, tests] = await Promise.all([agentRequest<{ patch: string; changed_files: string[] }>(`${base}/diff`), agentRequest<TestReceipt[]>(`${base}/tests`)]);
      setDetails((old) => ({ ...old, [id]: { ...diff, tests } }));
    } catch (error) { setNotice(error instanceof Error ? error.message : "修复结果读取失败"); }
    finally { setBusy(""); }
  }
  async function action(id: string, name: "close" | "export" | "cancel") {
    setBusy(id); setNotice("");
    try {
      const result = await agentRequest<{ message: string; download_url?: string }>(`sessions/${sessionId}/repairs/${id}/${name}`, {});
      if (result.download_url) setDownloads((old) => ({ ...old, [id]: `${API}${result.download_url}` }));
      setNotice(result.message); await onChanged();
    } catch (error) { setNotice(error instanceof Error ? error.message : "修复操作失败"); }
    finally { setBusy(""); }
  }
  if (!repairs?.length) return null;
  return <section aria-label="修复工作区与测试结果">
    {repairs.map((repair) => <article className="agent-question" key={repair.id}>
      <h3>候选修复 · {repair.purpose}</h3><p>基准：{repair.revision || "材料尚未准备完成"}</p>
      <p>{repair.state === "ready" ? "独立工作区，不会热改运行程序" : repair.state === "closed" ? "已关闭，材料保留" : repair.error || "正在准备材料"}</p>
      {repair.state === "ready" && <div className="agent-toolbar">
        <button type="button" className="secondary" disabled={!!busy} onClick={() => void inspect(repair.id)}>查看差异与测试</button>
        <button type="button" className="secondary" disabled={!!busy} onClick={() => void action(repair.id, "export")}>导出已测试候选补丁</button>
        <button type="button" className="secondary" disabled={!!busy} onClick={() => void action(repair.id, "cancel")}>停止本工作区测试</button>
        <button type="button" className="secondary" disabled={!!busy} onClick={() => void action(repair.id, "close")}>关闭候选并保留记录</button>
      </div>}
      {details[repair.id] && <details open><summary>{details[repair.id].changed_files.length}个文件有变更</summary>
        <pre>{details[repair.id].patch || "没有修改"}</pre>
        {details[repair.id].tests.map((test) => <div key={test.id}><p>{test.target} · {({ passed: "测试通过", failed: "测试失败", timeout: "超时", interrupted: "已中断", running: "测试中", queued: "已排队" } as Record<string, string>)[test.state] || test.state} · 退出码{test.exit_code ?? "待确认"}</p><pre>{test.output}</pre></div>)}
      </details>}
      {downloads[repair.id] && <a href={downloads[repair.id]}>下载候选补丁（尚未发布生效）</a>}
    </article>)}
    {notice && <p role="status" className="agent-notice">{notice}</p>}
  </section>;
}
