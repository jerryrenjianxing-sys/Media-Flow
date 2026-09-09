"use client";
/* Vinext client navigation can fail after hot updates; local page links use hard navigation. */
import { useCallback, useEffect, useRef, useState } from "react";
import { TaskGroupDetail, TaskGroupList, type TaskGroup } from "../components/task-groups";
import { fetchLocalApi } from "../lib/local-api";

const API = "http://127.0.0.1:48138";
const PAGE_SIZE = 50;
const SUMMARY_SIZE = 5;

type IncidentAnalysis = { summary?: string; suggested_rule?: string; risk?: string; auto_applicable?: false };
type Incident = { id: string; device_id: string; video_index: number|null; stage: string; error_type: string; error_message: string; outcome: "recovered"|"skipped"|"device_fatal"|"model_failed"|"model_circuit_open"; has_screenshot: boolean; has_ui_tree: boolean; analysis_status: string; analysis?: IncidentAnalysis|null; created_at: string };
type Page<T> = { items: T[]; total: number; limit: number; offset: number };

const emptyPage = <T,>(): Page<T> => ({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 });
const incidentOutcome = (incident: Incident) => incident.outcome === "recovered" ? "页面已恢复并继续" : incident.outcome === "device_fatal" ? "页面需处理" : incident.outcome === "model_circuit_open" ? "模型通道已熔断" : incident.outcome === "model_failed" ? "模型调用失败" : incident.video_index === null || incident.stage === "task" ? "任务已安全结束" : "已跳过当前视频";
const incidentAnalysisText = (incident: Incident) => incident.analysis_status === "completed" ? `只读建议：${incident.analysis?.summary || "已完成分析"}` : incident.analysis_status === "failed" ? incident.analysis?.summary || "只读分析暂时失败，证据已经保留" : incident.analysis_status === "analyzing" ? "正在生成只读建议" : "等待只读分析";

export default function RecordsPage() {
  const [taskGroups, setTaskGroups] = useState<Page<TaskGroup>>(emptyPage);
  const [incidents, setIncidents] = useState<Page<Incident>>(emptyPage);
  const [taskPage, setTaskPage] = useState(0);
  const [incidentPage, setIncidentPage] = useState(0);
  const [tasksExpanded, setTasksExpanded] = useState(false);
  const [incidentsExpanded, setIncidentsExpanded] = useState(false);
  const [selectedGroup, setSelectedGroup] = useState<TaskGroup | null>(null);
  const [now, setNow] = useState(0);
  const [notice, setNotice] = useState("正在读取本机记录…");
  const [loaded, setLoaded] = useState(false), [readError, setReadError] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const openedFromLink = useRef("");
  const closeTaskDetail = useCallback(() => {setSelectedGroup(null);const url=new URL(window.location.href);url.searchParams.delete('task_id');window.history.replaceState(null,'',url);}, []);

  const refresh = useCallback(async () => {
    try {
      const requestedTask = new URLSearchParams(window.location.search).get('task_id') || '';
      const [taskResponse, incidentResponse] = await Promise.all([
        fetchLocalApi(`${API}/api/records/task-groups?limit=${tasksExpanded ? PAGE_SIZE : SUMMARY_SIZE}&offset=${requestedTask ? 0 : tasksExpanded ? taskPage * PAGE_SIZE : 0}${requestedTask ? `&task_id=${encodeURIComponent(requestedTask)}` : ''}`, { cache: "no-store" }),
        fetchLocalApi(`${API}/api/records/incidents?limit=${incidentsExpanded ? PAGE_SIZE : SUMMARY_SIZE}&offset=${incidentsExpanded ? incidentPage * PAGE_SIZE : 0}`, { cache: "no-store" }),
      ]);
      if (!taskResponse.ok || !incidentResponse.ok) throw new Error("记录读取失败");
      const groups = await taskResponse.json() as Page<TaskGroup>;
      setTaskGroups(groups);
      if(requestedTask && openedFromLink.current !== requestedTask && groups.items.length){openedFromLink.current=requestedTask;setSelectedGroup(groups.items[0]);}
      setIncidents(await incidentResponse.json() as Page<Incident>);
      setNow(Date.now());
      setLoaded(true); setReadError(false); setNotice("记录已同步");
    } catch { setReadError(true); setNotice("记录读取失败；已显示的记录为上次观察，请重新读取。"); }
  }, [taskPage, incidentPage, tasksExpanded, incidentsExpanded]);

  const retryAnalysis = async () => {
    setRetrying(true);
    try {
      const response = await fetchLocalApi(`${API}/api/incidents/retry-analysis`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }, 20_000);
      const payload = await response.json() as { message?: string; error?: string };
      if (!response.ok) throw new Error(payload.error || "重新分析失败");
      setNotice(payload.message || "已重新加入只读分析队列");
      await refresh();
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "重新分析失败");
    } finally {
      setRetrying(false);
    }
  };

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  return <main className="app-shell records-page">
    <div className="page-shell">
      <section className="records-hero" data-motion><div><p className="eyebrow">RESULT CENTER</p><h1>结果</h1><p>任务结果、纠错过程和互动凭证使用同一套可追溯记录。</p><p className="workspace-page-notice" role="status">{notice}</p>{readError && <button type="button" className="secondary" onClick={() => void refresh()}>重新读取</button>}</div><a className="secondary back-link" href="/run">返回运行</a></section>
      <nav className="workspace-tabs result-tabs" aria-label="结果分类"><a className="active" href="#tasks">任务</a><a href="#incidents">纠错记录</a><a href="/interactions">互动凭证</a></nav>

      <section id="tasks" className="panel records-full-panel"><div className="panel-heading"><div><p className="section-index">DEVICE RUNS</p><h2>全部任务</h2><small className="section-note">共 {loaded ? taskGroups.total : "—"} 个设备任务{tasksExpanded ? ` · 第 ${taskPage + 1} 页` : " · 当前显示 5 个"}</small></div><button type="button" className="secondary record-expand" onClick={() => { setTaskPage(0); setTasksExpanded((value) => !value); }}>{tasksExpanded ? "收起" : "展开全部"}</button></div><div className="task-list">{loaded ? <TaskGroupList groups={taskGroups.items} now={now} onOpen={setSelectedGroup}/> : <p className="empty">{readError ? "任务记录未读取" : "正在读取任务记录…"}</p>}</div>{tasksExpanded && <Pagination page={taskPage} total={taskGroups.total} onChange={setTaskPage}/>}</section>

      <section id="incidents" className="panel records-full-panel"><div className="panel-heading"><div><p className="section-index">ALL RECOVERY EVENTS</p><h2>全部纠错记录</h2><small className="section-note">共 {loaded ? incidents.total : "—"} 条{incidentsExpanded ? ` · 第 ${incidentPage + 1} 页` : " · 当前显示 5 条"} · AI只读分析不会操作设备</small></div><div className="incident-actions"><button type="button" className="secondary" disabled={!loaded || readError || retrying} onClick={() => void retryAnalysis()}>{retrying ? "正在重新加入…" : "重试待分析记录"}</button><button type="button" className="secondary record-expand" onClick={() => { setIncidentPage(0); setIncidentsExpanded((value) => !value); }}>{incidentsExpanded ? "收起" : "展开全部"}</button></div></div><div className="incident-list">{incidents.items.map((incident) => <article key={incident.id}><span className={`incident-dot ${incident.outcome}`}/><div><strong>第 {incident.video_index ?? "-"} 条 · {incident.stage}</strong><small>设备 {incident.device_id.slice(-6)} · {new Date(incident.created_at).toLocaleString("zh-CN", { hour12: false })}</small><em>{incident.error_type}: {incident.error_message}</em><small className={`incident-analysis ${incident.analysis_status}`}>{incidentAnalysisText(incident)}</small>{incident.has_ui_tree && <small>UI结构已与截图配对保存，仅供受限分析使用。</small>}{incident.analysis?.suggested_rule && <small className="incident-rule">候选规则：{incident.analysis.suggested_rule}</small>}</div><div className="incident-actions"><b className={incident.outcome}>{incidentOutcome(incident)}</b>{incident.has_screenshot && <a href={`${API}/api/incident-image?id=${incident.id}`} target="_blank" rel="noreferrer">查看异常现场</a>}</div></article>)}{loaded && !incidents.items.length && <p className="empty">暂无纠错记录。</p>}</div>{incidentsExpanded && <Pagination page={incidentPage} total={incidents.total} onChange={setIncidentPage}/>}</section>
    </div>
    <TaskGroupDetail group={selectedGroup} onClose={closeTaskDetail}/>
  </main>;
}

function Pagination({ page, total, onChange }: { page: number; total: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return <div className="pagination"><button type="button" className="secondary" disabled={page <= 0} onClick={() => onChange(page - 1)}>上一页</button><span>第 {page + 1} / {pages} 页</span><button type="button" className="secondary" disabled={page + 1 >= pages} onClick={() => onChange(page + 1)}>下一页</button></div>;
}
