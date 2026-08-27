"use client";

import { useCallback, useEffect, useState } from "react";
import type { MouseEvent } from "react";

const API = "http://127.0.0.1:48138";
const PAGE_SIZE = 50;
const SUMMARY_SIZE = 5;

type Task = { id: string; device_id: string; task_type: string; status: "pending"|"running"|"completed"|"failed"|"stopped"|"cancelled"; created_at: string; started_at?: string|null; finished_at?: string|null; result?: Record<string, unknown>|null; error?: string|null };
type IncidentAnalysis = { summary?: string; suggested_rule?: string; risk?: string; auto_applicable?: false };
type Incident = { id: string; device_id: string; video_index: number|null; stage: string; error_type: string; error_message: string; outcome: "recovered"|"skipped"|"device_fatal"; screenshot_path?: string|null; analysis_status: string; analysis?: IncidentAnalysis|null; created_at: string };
type Page<T> = { items: T[]; total: number; limit: number; offset: number };
type CommentScreenshot = { video_index: number };

const emptyPage = <T,>(): Page<T> => ({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 });
const statusText = (task: Task) => task.status === "pending" ? "等待执行" : task.status === "running" ? "正在执行" : task.status === "completed" ? "已结束 · 成功" : task.status === "stopped" ? "已结束 · 安全停止" : task.status === "cancelled" ? "已结束 · 已取消" : task.error?.includes("worker_interrupted") ? "已结束 · 中断" : "已结束 · 失败";
const incidentOutcome = (value: Incident["outcome"]) => ({ recovered: "已恢复", skipped: "已跳过", device_fatal: "需处理" })[value];
const incidentAnalysisText = (incident: Incident) => incident.analysis_status === "completed" ? `只读建议：${incident.analysis?.summary || "已完成分析"}` : incident.analysis_status === "failed" ? "只读分析暂时失败" : incident.analysis_status === "analyzing" ? "正在生成只读建议" : "等待只读分析";
const commentScreenshots = (task: Task): CommentScreenshot[] => Array.isArray(task.result?.comment_screenshots) ? task.result.comment_screenshots.filter((item): item is CommentScreenshot => Boolean(item && typeof item === "object" && Number.isInteger((item as Record<string, unknown>).video_index))) : [];

function taskDuration(task: Task, now: number) {
  if (!task.started_at) return "尚未开始";
  const total = Math.max(0, Math.round(((task.finished_at ? new Date(task.finished_at).getTime() : now) - new Date(task.started_at).getTime()) / 1000));
  const hours = Math.floor(total / 3600), minutes = Math.floor((total % 3600) / 60), seconds = total % 60;
  const value = hours ? `${hours}时${minutes}分` : minutes ? `${minutes}分${seconds}秒` : `${seconds}秒`;
  return task.status === "running" ? `已运行 ${value}` : `总耗时 ${value}`;
}

export default function RecordsPage() {
  const [tasks, setTasks] = useState<Page<Task>>(emptyPage);
  const [incidents, setIncidents] = useState<Page<Incident>>(emptyPage);
  const [taskPage, setTaskPage] = useState(0);
  const [incidentPage, setIncidentPage] = useState(0);
  const [tasksExpanded, setTasksExpanded] = useState(false);
  const [incidentsExpanded, setIncidentsExpanded] = useState(false);
  const [now, setNow] = useState(0);
  const [notice, setNotice] = useState("正在读取本机记录…");
  const goHome = (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    window.location.assign("/");
  };

  const refresh = useCallback(async () => {
    try {
      const [taskResponse, incidentResponse] = await Promise.all([
        fetch(`${API}/api/records/tasks?limit=${tasksExpanded ? PAGE_SIZE : SUMMARY_SIZE}&offset=${tasksExpanded ? taskPage * PAGE_SIZE : 0}`, { cache: "no-store" }),
        fetch(`${API}/api/records/incidents?limit=${incidentsExpanded ? PAGE_SIZE : SUMMARY_SIZE}&offset=${incidentsExpanded ? incidentPage * PAGE_SIZE : 0}`, { cache: "no-store" }),
      ]);
      if (!taskResponse.ok || !incidentResponse.ok) throw new Error("记录读取失败");
      setTasks(await taskResponse.json() as Page<Task>);
      setIncidents(await incidentResponse.json() as Page<Incident>);
      setNow(Date.now());
      setNotice("记录已同步");
    } catch { setNotice("本机控制服务未启动，暂时无法读取记录"); }
  }, [taskPage, incidentPage, tasksExpanded, incidentsExpanded]);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  return <main className="app-shell records-page">
    <header className="topbar"><a className="brand" href="/" onClick={goHome}><span className="brand-mark">R</span><div><strong>RiskFlow</strong><small>CONTROL LAB</small></div></a><nav aria-label="页面导航"><a href="/" onClick={goHome}>策略控制台</a><a className="active" href="/records">运行记录</a><a href="/governance">评测与证据</a></nav><div className="system-status"><span className="dot online"/><span>{notice}</span></div><span className="environment">本机 · 内部测试</span></header>
    <div className="page-shell">
      <section className="records-hero"><div><p className="eyebrow">AUDIT &amp; RECOVERY</p><h1>全部任务与纠错记录</h1><p>任务与纠错默认各显示最近 5 条，需要时可分别展开完整记录。</p></div><a className="secondary back-link" href="/" onClick={goHome}>返回策略控制台</a></section>

      <section id="tasks" className="panel records-full-panel"><div className="panel-heading"><div><p className="section-index">ALL TASKS</p><h2>全部任务</h2><small className="section-note">共 {tasks.total} 条{tasksExpanded ? ` · 第 ${taskPage + 1} 页` : " · 当前显示 5 条"}</small></div><button type="button" className="secondary record-expand" onClick={() => { setTaskPage(0); setTasksExpanded((value) => !value); }}>{tasksExpanded ? "收起" : "展开全部"}</button></div><div className="task-list">{tasks.items.map((task) => <article key={task.id}><span className={`task-dot ${task.status}`}/><div><strong>{task.task_type === "douyin_topic_session" ? "内容策略测试" : task.task_type}</strong><small>{task.id.slice(0, 8)} · 设备 {task.device_id.slice(-6)} · {new Date(task.created_at).toLocaleString("zh-CN", { hour12: false })}</small><small className="task-duration">{taskDuration(task, now)}</small>{Number(task.result?.video_errors || 0) > 0 && <small className="task-correction">纠错 {String(task.result?.video_errors)} 条 · 恢复 {String(task.result?.recovered_videos || 0)} 条</small>}{commentScreenshots(task).length > 0 && <span className="task-evidence">{commentScreenshots(task).map((evidence) => <a key={evidence.video_index} href={`${API}/api/comment-image?task_id=${encodeURIComponent(task.id)}&video=${evidence.video_index}`} target="_blank" rel="noreferrer">评论截图 · 第 {evidence.video_index} 条</a>)}</span>}{task.error && <em>{task.error}</em>}</div><b className={`task-status ${task.status}`}>{statusText(task)}</b></article>)}{!tasks.items.length && <p className="empty">暂无任务记录。</p>}</div>{tasksExpanded && <Pagination page={taskPage} total={tasks.total} onChange={setTaskPage}/>}</section>

      <section id="incidents" className="panel records-full-panel"><div className="panel-heading"><div><p className="section-index">ALL RECOVERY EVENTS</p><h2>全部纠错记录</h2><small className="section-note">共 {incidents.total} 条{incidentsExpanded ? ` · 第 ${incidentPage + 1} 页` : " · 当前显示 5 条"} · AI只读分析不会操作设备</small></div><button type="button" className="secondary record-expand" onClick={() => { setIncidentPage(0); setIncidentsExpanded((value) => !value); }}>{incidentsExpanded ? "收起" : "展开全部"}</button></div><div className="incident-list">{incidents.items.map((incident) => <article key={incident.id}><span className={`incident-dot ${incident.outcome}`}/><div><strong>第 {incident.video_index ?? "-"} 条 · {incident.stage}</strong><small>设备 {incident.device_id.slice(-6)} · {new Date(incident.created_at).toLocaleString("zh-CN", { hour12: false })}</small><em>{incident.error_type}: {incident.error_message}</em><small className={`incident-analysis ${incident.analysis_status}`}>{incidentAnalysisText(incident)}</small>{incident.analysis?.suggested_rule && <small className="incident-rule">候选规则：{incident.analysis.suggested_rule}</small>}</div><div className="incident-actions"><b className={incident.outcome}>{incidentOutcome(incident.outcome)}</b>{incident.screenshot_path && <a href={`${API}/api/incident-image?id=${incident.id}`} target="_blank" rel="noreferrer">查看截图</a>}</div></article>)}{!incidents.items.length && <p className="empty">暂无纠错记录。</p>}</div>{incidentsExpanded && <Pagination page={incidentPage} total={incidents.total} onChange={setIncidentPage}/>}</section>
    </div>
  </main>;
}

function Pagination({ page, total, onChange }: { page: number; total: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return <div className="pagination"><button type="button" className="secondary" disabled={page <= 0} onClick={() => onChange(page - 1)}>上一页</button><span>第 {page + 1} / {pages} 页</span><button type="button" className="secondary" disabled={page + 1 >= pages} onClick={() => onChange(page + 1)}>下一页</button></div>;
}
