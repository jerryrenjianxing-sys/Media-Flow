"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchLocalApi } from "../lib/local-api";

const API = "http://127.0.0.1:48138";

type Alert = {
  id: string;
  inspection_id: string;
  device_name: string;
  sources: string[];
  summary: {
    sources: Record<string, { unread_count?: number; indicator?: string }>;
  };
};

const sourceLabels: Record<string, string> = {
  private_messages: "私信",
  received_likes: "点赞与收藏",
  comment_danmaku: "评论与弹幕",
  profile_visitors: "主页访客",
};

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

  const devices = Array.from(new Set(alerts.map((alert) => alert.device_name))).join("、");
  const sourceDetails = Array.from(new Set(alerts.flatMap((alert) => alert.sources))).map((name) => {
    const counts = alerts.map((alert) => alert.summary?.sources?.[name]?.unread_count).filter((value): value is number => typeof value === "number" && value > 0);
    const count = counts.length ? Math.max(...counts) : null;
    return `${sourceLabels[name] || name}${count === null ? "" : count >= 99 ? "99+" : count}`;
  }).join("｜");
  return (
    <button className="interaction-banner" type="button" onClick={acknowledgeAndOpen} disabled={busy} aria-label={`查看 ${alerts.length} 条互动提醒`}>
      <span className="interaction-banner-label">互动提醒</span>
      <span className="interaction-banner-track"><span>{devices}｜{sourceDetails}｜共 {alerts.length} 条未查看｜点击查看完整证据</span></span>
      {error ? <span className="interaction-banner-error">{error}</span> : null}
    </button>
  );
}
