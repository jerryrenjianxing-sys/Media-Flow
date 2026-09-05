"use client";
/* eslint-disable @next/next/no-img-element */

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchLocalApi } from "../lib/local-api";
import { navigationReason } from "../lib/navigation-feedback";

const API = "http://127.0.0.1:48138";
const sectionLabels: Record<string, string> = {
  unified_activity: "互动消息聚合页",
  private_messages: "私信消息",
  received_likes: "点赞与收藏",
  comment_danmaku: "评论与弹幕",
  profile_visitors: "主页访客",
};
const resultLabels = { alert: "发现新互动", clear: "无新互动", incomplete: "检查未完成" } as const;
const reasonLabels: Record<string, string> = {
  not_checked: "该分区尚未检查",
  list_incomplete: "列表未能完整检查",
  visitor_history_disabled: "访客记录当前不可用",
  visitor_page_not_recognized: "访客页面未能确认",
  visitor_entry_not_found: "当前主页没有可识别的访客入口",
  calibrated_entry_not_available: "该版本已校准确认没有此互动入口",
  list_boundary_not_confirmed: "未找到已读边界，也无法确认列表已经到底；本次不会误报为无新互动",
  interaction_entry_not_found: "消息页中没有找到可确认的互动消息入口",
  interaction_entry_ambiguous: "消息页出现多个互动消息候选，已停止以避免点错",
  unified_activity_page_not_recognized: "点击后没有进入可确认的互动消息聚合页",
  unified_activity_page_changed: "滚动后页面结构发生变化，无法继续安全读取",
  v3_standard_display_required: "设备不是900×1600、320 DPI的标准虚拟机",
  v3_calibration_missing: "这台标准虚拟机尚未建立互动巡检v3档案",
  v3_calibration_unstable: "互动巡检v3档案尚未完成三次稳定复验",
};

type Entry = { display_name?: string; content?: string; time?: string; unread_count?: number; subsection?: string };
type Section = { status: string; complete: boolean; scroll_count: number; indicator?: string; unread_count?: number; item_count?: number; items?: Entry[]; reason?: string; baseline_status?: string };
type Evidence = { id: string; label: string; section: string; captured_at: string; image_url?: string; ui_tree_url?: string };
type Inspection = {
  id: string; device_name: string; device_id: string; workflow_version: string; status: string;
  result_kind: "alert" | "clear" | "incomplete"; restored: boolean; started_at: string; finished_at: string;
  evidence_count: number; evidence?: Evidence[]; failure_reason?: string;
  summary: { conclusion: string; evidence_count?: number; sections?: Record<string, Section>; visitor_change?: Record<string, unknown>; unified_activity?: { complete?: boolean; read_boundary?: string | null; scroll_count?: number; unread_item_count?: number | null; categories?: string[]; reason_code?: string | null } };
};
type Alert = {
  id: string; inspection_id?: string; device_name: string; sources: string[]; status: "unread" | "viewed"; detected_at: string;
  summary: { conclusion?: string; source_count: number; evidence_count?: number; sources: Record<string, { unread_count?: number; items?: Entry[] }> };
};

function formatTime(value: string) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "";
}
function reasonText(value?: string) { return value ? navigationReason(value) || reasonLabels[value] || "页面检查未完成，请查看异常现场" : ""; }
function receiptState(item: Inspection) {
  if (item.status === "completed") return resultLabels[item.result_kind];
  return item.result_kind === "alert" ? "发现互动 · 检查未完成" : "检查未完成";
}
function overviewState(item?: Inspection) {
  if (!item) return { label: "尚未巡检", kind: "empty" };
  const diagnostic = `${item.failure_reason || ""} ${item.summary?.conclusion || ""}`.toLowerCase();
  if (diagnostic.includes("calibrat") || diagnostic.includes("校准") || diagnostic.includes("signature") || diagnostic.includes("version")) {
    return { label: "校准异常", kind: "calibration" };
  }
  if (item.status !== "completed" || item.result_kind === "incomplete") return { label: "未完成", kind: "incomplete" };
  if (item.result_kind === "alert") return { label: "有互动", kind: "alert" };
  return { label: "无互动", kind: "clear" };
}

export default function InteractionsPage() {
  const [filter, setFilter] = useState<"all" | "alert" | "clear" | "incomplete">("all");
  const [inspections, setInspections] = useState<Inspection[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [selected, setSelected] = useState<Inspection | null>(null);
  const [focusIds] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    const focused = new URLSearchParams(window.location.search).get("focus") || "";
    return new Set(focused.split(",").filter(Boolean));
  });
  const [notice, setNotice] = useState("正在读取本地巡检回执…");

  const refresh = useCallback(async () => {
    try {
      const resultQuery = filter === "all" ? "" : `result=${filter}&`;
      const [inspectionResponse, alertResponse] = await Promise.all([
        fetchLocalApi(`${API}/api/interaction-inspections?${resultQuery}limit=100&offset=0`, { cache: "no-store" }),
        fetchLocalApi(`${API}/api/interaction-alerts?limit=100&offset=0`, { cache: "no-store" }),
      ]);
      const inspectionPayload = await inspectionResponse.json() as { inspections?: Inspection[]; error?: string };
      const alertPayload = await alertResponse.json() as { alerts?: Alert[]; error?: string };
      if (!inspectionResponse.ok) throw new Error(inspectionPayload.error || "巡检回执读取失败");
      if (!alertResponse.ok) throw new Error(alertPayload.error || "提醒读取失败");
      setInspections(inspectionPayload.inspections || []);
      setAlerts(alertPayload.alerts || []);
      setNotice("证据保存在本机；没有实际内容的字段不会显示");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "读取失败");
    }
  }, [filter]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const focusedInspectionIds = useMemo(() => new Set(alerts.filter((alert) => focusIds.has(alert.id) && alert.inspection_id).map((alert) => alert.inspection_id as string)), [alerts, focusIds]);
  const inspectionIds = useMemo(() => new Set(inspections.map((item) => item.id)), [inspections]);
  const legacyAlerts = alerts.filter((alert) => !alert.inspection_id || !inspectionIds.has(alert.inspection_id));
  const latest = inspections[0];
  const latestByDevice = useMemo(() => {
    const result = new Map<string, Inspection>();
    for (const inspection of inspections) {
      if (!result.has(inspection.device_id)) result.set(inspection.device_id, inspection);
    }
    return result;
  }, [inspections]);
  const inspectionDevices = useMemo(() => Array.from(latestByDevice.values()).map((item) => ({ id: item.device_id, name: item.device_name })), [latestByDevice]);

  async function openInspection(id: string) {
    const response = await fetchLocalApi(`${API}/api/interaction-inspections/${encodeURIComponent(id)}`, { cache: "no-store" });
    const payload = await response.json() as { inspection?: Inspection; error?: string };
    if (!response.ok || !payload.inspection) {
      setNotice(payload.error || "巡检详情读取失败");
      return;
    }
    setSelected(payload.inspection);
  }

  return <main className="app-shell interactions-page">
    <div className="page-shell interaction-shell">
      <section className="records-hero interaction-hero"><div><span className="eyebrow">RESULT CENTER · RECEIPTS</span><h1>互动凭证</h1><p>{notice}</p></div><div className="interaction-filter" role="group" aria-label="巡检筛选">{(["all", "alert", "clear", "incomplete"] as const).map((value) => <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{value === "all" ? "全部" : resultLabels[value]}</button>)}</div></section>
      <nav className="workspace-tabs result-tabs" aria-label="结果分类"><a href="/records#tasks">任务</a><a href="/records#incidents">纠错记录</a><a className="active" href="/interactions">互动凭证</a></nav>

      <section className="interaction-device-overview" aria-label="各设备最近巡检">
        <header><div><span className="section-index">LATEST BY DEVICE</span><h2>各虚拟机最近巡检</h2></div><p>每台只展示自己的最近回执与证据，不混用设备身份和复验档案。</p></header>
        <div>{inspectionDevices.map((device) => {
          const inspection = latestByDevice.get(device.id);
          const state = overviewState(inspection);
          return <button key={device.id} type="button" className={`interaction-device-card ${state.kind}`} disabled={!inspection} onClick={() => inspection ? void openInspection(inspection.id) : undefined}>
            <span>{device.name}</span><strong>{state.label}</strong>
            {inspection ? <small>{inspection.evidence_count > 0 ? `${inspection.evidence_count} 份证据` : "简明回执"} · {formatTime(inspection.finished_at)}</small> : <small>等待首次 v3 验收</small>}
          </button>;
        })}{!inspectionDevices.length ? <p className="interaction-empty">尚无标准虚拟机巡检回执</p> : null}</div>
      </section>

      {latest ? <section className={`interaction-latest ${latest.status === "completed" ? latest.result_kind : "incomplete"}`}><div><span>最近一次巡检</span><strong>{latest.status === "completed" ? latest.summary.conclusion : latest.result_kind === "alert" ? "检查未完成，已发现互动" : "检查未完成"}</strong><small>{latest.device_name} · {formatTime(latest.finished_at)}</small></div><button type="button" onClick={() => void openInspection(latest.id)}>查看 {latest.evidence_count} 份证据</button></section> : <section className="interaction-latest incomplete"><div><span>最近一次巡检</span><strong>尚无新版巡检回执</strong></div></section>}

      <section className="interaction-receipts" aria-live="polite">
        {inspections.map((inspection) => <article key={inspection.id} className={`interaction-receipt ${inspection.result_kind} ${focusedInspectionIds.has(inspection.id) ? "focused" : ""}`}>
          <div className="interaction-card-head"><div><strong>{inspection.device_name}</strong><span>{receiptState(inspection)}</span></div><time>{formatTime(inspection.finished_at)}</time></div>
          <h2>{inspection.summary.conclusion}</h2>
          {inspection.workflow_version === "v3" && inspection.summary.unified_activity ? <div className="interaction-metrics"><b>{inspection.summary.unified_activity.complete ? "边界已确认" : "扫描未完成"}</b><b>滑动 {inspection.summary.unified_activity.scroll_count || 0} 次</b>{typeof inspection.summary.unified_activity.unread_item_count === "number" ? <b>边界上方 {inspection.summary.unified_activity.unread_item_count} 条</b> : null}</div> : null}
          <div className="interaction-section-grid">{Object.entries(inspection.summary.sections || {}).map(([name, section]) => <section key={name} className={!section.complete ? "incomplete" : ""}>
            <header><strong>{sectionLabels[name] || name}</strong><span>{section.complete ? "已检查" : "未完成"}</span></header>
            <div className="interaction-metrics">{typeof section.unread_count === "number" && section.unread_count > 0 ? <b>角标 {section.unread_count >= 99 ? "99+" : section.unread_count}</b> : null}{typeof section.item_count === "number" && section.item_count > 0 ? <b>可见 {section.item_count} 条</b> : null}{section.scroll_count > 0 ? <b>滑动 {section.scroll_count} 次</b> : null}</div>
            {section.items?.length ? <ul>{section.items.slice(0, 3).map((item, index) => <li key={`${item.content || item.display_name}-${index}`}>{item.display_name ? <strong>{item.display_name}</strong> : null}{item.content ? <span>{item.content}</span> : null}{item.time ? <time>{item.time}</time> : null}</li>)}</ul> : null}
            {section.reason ? <p className="interaction-reason">{reasonText(section.reason)}</p> : null}
          </section>)}</div>
          <footer><span>{inspection.evidence_count > 0 ? `${inspection.evidence_count} 份本地证据` : "简明回执"}</span><button type="button" onClick={() => void openInspection(inspection.id)}>展开详情</button></footer>
        </article>)}
        {!inspections.length ? <article className="interaction-empty"><strong>当前筛选没有巡检回执</strong><span>没有内容时不会生成空字段或虚假记录。</span></article> : null}
      </section>

      {legacyAlerts.length ? <section className="legacy-alerts"><h2>旧版提醒</h2><p>旧记录只显示当时保存的摘要，已经删除的截图不会补造。</p>{legacyAlerts.map((alert) => <article key={alert.id} className={focusIds.has(alert.id) ? "focused" : ""}><strong>{alert.device_name} · {alert.summary.conclusion || "检测到新互动"}</strong><time>{formatTime(alert.detected_at)}</time><div>{alert.sources.map((source) => <span key={source}>{sectionLabels[source] || source}</span>)}</div></article>)}</section> : null}

      {selected ? <div className="interaction-detail-backdrop" role="button" tabIndex={0} aria-label="关闭巡检证据详情" onClick={(event) => { if (event.target === event.currentTarget) setSelected(null); }} onKeyDown={(event) => { if (event.key === "Escape" || event.key === "Enter") setSelected(null); }}><section className="interaction-detail" role="dialog" aria-modal="true" aria-label="巡检证据详情"><header><div><span>{selected.device_name}</span><h2>{selected.summary.conclusion}</h2><small>{formatTime(selected.finished_at)}</small></div><button type="button" onClick={() => setSelected(null)}>关闭</button></header>
        {selected.workflow_version === "v3" && selected.summary.unified_activity ? <div className="interaction-detail-sections"><section><h3>统一互动列表</h3><p>{selected.summary.unified_activity.complete ? `已确认边界 · ${selected.summary.unified_activity.read_boundary === "first_screen" ? "首屏发现已读" : selected.summary.unified_activity.read_boundary === "after_scroll" ? "滑动后发现已读" : "列表已结束"}` : reasonText(selected.summary.unified_activity.reason_code) || "扫描未完整完成"}</p></section></div> : null}
        <div className="interaction-detail-sections">{Object.entries(selected.summary.sections || {}).map(([name, section]) => <section key={name}><h3>{sectionLabels[name] || name}</h3>{section.items?.length ? <ul>{section.items.map((item, index) => <li key={`${item.content || item.display_name}-${index}`}>{item.display_name ? <strong>{item.display_name}</strong> : null}{item.content ? <span>{item.content}</span> : null}{item.time ? <time>{item.time}</time> : null}{typeof item.unread_count === "number" ? <em>{item.unread_count} 条未读</em> : null}</li>)}</ul> : <p>{section.complete ? "已检查，没有额外可读条目" : reasonText(section.reason) || "该分区未完成"}</p>}</section>)}</div>
        {selected.evidence?.length ? <div className="interaction-evidence-gallery">{selected.evidence.map((item) => <figure key={item.id}>{item.image_url ? <img src={`${API}${item.image_url}`} alt={`${sectionLabels[item.section] || item.section}：${item.label}`} loading="lazy"/> : null}<figcaption><strong>{item.label}</strong><span>{sectionLabels[item.section] || item.section}</span><time>{formatTime(item.captured_at)}</time></figcaption></figure>)}</div> : <p className="interaction-no-evidence">这条旧回执没有可恢复的截图证据。</p>}
      </section></div> : null}
    </div>
  </main>;
}
