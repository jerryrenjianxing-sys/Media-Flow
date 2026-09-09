"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchLocalApi } from "../lib/local-api";
import { homeBadgeAlertPresentation, type HomeBadge } from "../lib/inspection-display.mjs";

const API = "http://127.0.0.1:48138";

type Alert = {
  id: string;
  inspection_id: string;
  device_name: string;
  sources?: string[];
  summary: {
    conclusion?: string;
    evidence_count?: number;
    home_badge?: HomeBadge;
    confirmed?: boolean;
    last_checked_at?: string;
    last_check_message?: string;
    sources?: Record<string, { unread_count?: number; indicator?: string }>;
  };
};

const sourceLabels: Record<string, string> = {
  home_badge: "首页消息",
  private_messages: "私信",
  received_likes: "点赞与收藏",
  comment_danmaku: "评论与弹幕",
  profile_visitors: "主页访客",
};

function formatAlertTime(value?: string) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "";
}

export default function InteractionAlertBanner() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const response = await fetchLocalApi(`${API}/api/interaction-alerts?status=unread&limit=100`, { cache: "no-store" }, 5_000);
      if (!response.ok) throw new Error("提醒服务暂时不可用");
      const payload = await response.json() as { alerts?: Alert[] };
      setAlerts(payload.alerts || []);
      setError("");
    } catch {
      setError("提醒同步失败，稍后自动重试");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [refresh]);

  async function acknowledgeAndOpen() {
    if (busy || alerts.length === 0) return;
    setBusy(true);
    try {
      const response = await fetchLocalApi(`${API}/api/interaction-alerts/acknowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_ids: alerts.map((alert) => alert.id) }),
      }, 12_000);
      const payload = await response.json() as { acknowledged_ids?: string[]; error?: string };
      if (!response.ok) throw new Error(payload.error || "确认失败");
      const focused = payload.acknowledged_ids?.length ? payload.acknowledged_ids : alerts.map((alert) => alert.id);
      window.location.href = `/interactions?view=all&focus=${encodeURIComponent(focused.join(","))}`;
    } catch {
      setError("确认失败，提醒仍保留在这里");
      setBusy(false);
    }
  }

  if (alerts.length === 0 && !error) return null;
  if (alerts.length === 0) return <div className="interaction-banner banner-error" role="status">{error}</div>;

  const homeAlerts = alerts.filter((alert) => alert.summary?.home_badge);
  const homeDetails = homeAlerts.map((alert) => `${alert.device_name}${homeBadgeAlertPresentation(alert.summary, formatAlertTime(alert.summary.last_checked_at)).message}`);
  const legacyAlerts = alerts.filter((alert) => alert.sources?.some((source) => source !== "home_badge"));
  const sourceDetails = Array.from(new Set(legacyAlerts.flatMap((alert) => alert.sources || []).filter((name) => name !== "home_badge"))).map((name) => {
    const counts = alerts.map((alert) => alert.summary?.sources?.[name]?.unread_count).filter((value): value is number => typeof value === "number" && value > 0);
    const count = counts.length ? Math.max(...counts) : null;
    return `${sourceLabels[name] || name}${count === null ? "" : count >= 99 ? "99+" : count}`;
  }).join("｜");
  const legacyDevices = Array.from(new Set(legacyAlerts.map((alert) => alert.device_name))).join("、");
  const bannerDetails = [...homeDetails, ...(sourceDetails ? [`${legacyDevices}｜${sourceDetails}`] : [])].join("｜") || "有待查看的平台提醒";
  return (
    <button className="interaction-banner" type="button" onClick={acknowledgeAndOpen} disabled={busy} aria-label={`查看 ${alerts.length} 条消息或互动提醒`}>
      <span className="interaction-banner-label">消息提醒 {alerts.length}</span>
      <span className="interaction-banner-track"><span>{bannerDetails}｜共 {alerts.length} 条未查看平台提醒｜点击查看截图证据｜平台确认不会清除抖音角标</span></span>
      {error ? <span className="interaction-banner-error">{error}</span> : null}
    </button>
  );
}
