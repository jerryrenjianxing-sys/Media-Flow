"use client";
/* eslint-disable @next/next/no-img-element -- local changing screenshots bypass optimization */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { gsap } from "gsap";

const API = "http://127.0.0.1:48138";

type Config = {
  device_id: string; device_ids: string[]; video_count: number; round_count: number;
  round_interval_minutes: number; dwell_min: number; dwell_max: number;
  like_probability: number; favorite_probability: number; comment_probability: number;
  topic_prompt: string; topic_filter_enabled: boolean; topic_confidence: number;
  like_only_on_match: boolean; engagement_requires_topic: boolean;
  comment_requires_topic: boolean; preview_only: boolean; seed: number;
  max_gate_skips: number; max_likes: number; max_favorites: number; max_comments: number;
};
type Task = { id: string; device_id: string; task_type: string; status: "pending"|"running"|"completed"|"failed"; created_at: string; started_at?: string|null; finished_at?: string|null; result?: Record<string, unknown>|null; error?: string|null };
type DeviceStatus = { device_id: string; state: string };
type WorkerStatus = { device_id: string; running: boolean; pid: number|null };
type Incident = { id: string; task_id: string; device_id: string; video_index: number|null; stage: string; error_type: string; error_message: string; outcome: "recovered"|"skipped"|"device_fatal"; recovery_action?: string|null; screenshot_path?: string|null; analysis_status: string; created_at: string };
type IncidentSummary = { total: number; queued: number; recovered: number; skipped: number; device_fatal: number };
type TaskSummary = { pending: number; running: number; completed: number; failed: number };
type Status = { device: DeviceStatus; devices: DeviceStatus[]; worker: WorkerStatus; workers: WorkerStatus[]; paused: boolean; task_summary: TaskSummary; tasks: Task[]; incidents: Incident[]; incident_summary: IncidentSummary };
type ModelStatus = { provider: string; model: string; key_configured: boolean };

const fallback: Config = {
  device_id: "emulator-5556", device_ids: ["emulator-5556", "127.0.0.1:16448", "127.0.0.1:16480", "127.0.0.1:16512", "127.0.0.1:16544"],
  video_count: 20, round_count: 1, round_interval_minutes: 0, dwell_min: 6, dwell_max: 15,
  like_probability: .2, favorite_probability: .1, comment_probability: .05,
  topic_prompt: "不限主题", topic_filter_enabled: false, topic_confidence: .78,
  like_only_on_match: false, engagement_requires_topic: false, comment_requires_topic: false,
  preview_only: true, seed: 20260821, max_gate_skips: 6, max_likes: 4,
  max_favorites: 2, max_comments: 1,
};

const percent = (value: number) => `${Math.round(value * 100)}%`;
const statusText = (task: Task) => {
  if (task.status === "pending") return "等待执行";
  if (task.status === "running") return "正在执行";
  if (task.status === "completed") return "已结束 · 成功";
  return task.error?.includes("worker_interrupted") ? "已结束 · 中断" : "已结束 · 失败";
};
const incidentOutcome = (value: Incident["outcome"]) => ({ recovered: "已恢复", skipped: "已跳过", device_fatal: "需处理" })[value];

function taskDuration(task: Task, now: number) {
  if (!task.started_at) return "尚未开始";
  const start = new Date(task.started_at).getTime();
  const end = task.finished_at ? new Date(task.finished_at).getTime() : now;
  const total = Math.max(0, Math.round((end - start) / 1000));
  const hours = Math.floor(total / 3600), minutes = Math.floor((total % 3600) / 60), seconds = total % 60;
  const value = hours ? `${hours}时${minutes}分` : minutes ? `${minutes}分${seconds}秒` : `${seconds}秒`;
  return task.status === "running" ? `已运行 ${value}` : `总耗时 ${value}`;
}

function planDuration(minutes: number) {
  const value = Math.max(1, Math.round(minutes));
  if (value < 60) return `约 ${value} 分钟`;
  const hours = Math.floor(value / 60), rest = value % 60;
  return rest ? `约 ${hours} 小时 ${rest} 分` : `约 ${hours} 小时`;
}

function Probability({ label, hint, value, onChange }: { label: string; hint: string; value: number; onChange: (value: number) => void }) {
  return <label className="probability-control"><span className="label-row"><strong>{label}</strong><b>{percent(value)}</b></span><input aria-label={label} type="range" min="0" max="1" step="0.05" value={value} onChange={(event) => onChange(Number(event.target.value))}/><small>{hint}</small></label>;
}

function TopicPolicy({ title, description, required, onChange }: { title: string; description: string; required: boolean; onChange: (value: boolean) => void }) {
  return <div className="policy-card"><div><strong>{title}</strong><small>{description}</small></div><div className="segmented" role="group" aria-label={`${title}主题范围`}><button type="button" className={!required ? "active" : ""} aria-pressed={!required} onClick={() => onChange(false)}>全部安全内容</button><button type="button" className={required ? "active" : ""} aria-pressed={required} onClick={() => onChange(true)}>仅匹配主题</button></div></div>;
}

export default function Home() {
  const [config, setConfig] = useState<Config>(fallback);
  const [status, setStatus] = useState<Status|null>(null);
  const [model, setModel] = useState<ModelStatus|null>(null);
  const [apiKey, setApiKey] = useState("");
  const [keyNotice, setKeyNotice] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [notice, setNotice] = useState("正在连接本机服务…");
  const [busy, setBusy] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [imageStamp, setImageStamp] = useState(0);
  const [now, setNow] = useState(0);
  const [theme, setTheme] = useState<"dark"|"light">("dark");
  const themeVeilRef = useRef<HTMLDivElement>(null);
  const themeIconRef = useRef<HTMLSpanElement>(null);
  const themeAnimatingRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/status`, { cache: "no-store" });
      if (!response.ok) throw new Error();
      const next = await response.json() as Status;
      const refreshedAt = Date.now();
      const online = next.devices.filter((device) => device.state === "device").length;
      setStatus(next); setImageStamp(refreshedAt); setNow(refreshedAt);
      setNotice(next.paused ? "所有任务已暂停" : online ? `${online} 台手机已连接，可以提交测试` : "未检测到已授权的安卓手机");
    } catch { setNotice("本机控制服务未启动，请双击启动器"); }
  }, []);

  useEffect(() => {
    fetch(`${API}/api/config`, { cache: "no-store" }).then((response) => response.json()).then((value) => setConfig(value as Config)).catch(() => undefined);
    fetch(`${API}/api/model`, { cache: "no-store" }).then((response) => response.json()).then((value) => setModel(value as ModelStatus)).catch(() => undefined);
    const initialTimer = window.setTimeout(() => {
      setTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");
      void refresh();
    }, 0), timer = window.setInterval(refresh, 3500);
    return () => { window.clearTimeout(initialTimer); window.clearInterval(timer); };
  }, [refresh]);

  const selectedCount = Math.max(1, config.device_ids.length);
  const onlineCount = status?.devices.filter((device) => device.state === "device").length || 0;
  const runningCount = status?.task_summary?.running ?? status?.tasks.filter((task) => task.status === "running").length ?? 0;
  const pendingCount = status?.task_summary?.pending ?? status?.tasks.filter((task) => task.status === "pending").length ?? 0;
  const anyTopicRule = config.engagement_requires_topic || config.comment_requires_topic;
  const strategyReady = !anyTopicRule || Boolean(config.topic_prompt.trim());
  const estimate = useMemo(() => ((config.dwell_min + config.dwell_max) / 2 * config.video_count * config.round_count / 60) + config.round_interval_minutes * Math.max(0, config.round_count - 1), [config]);

  function set<K extends keyof Config>(key: K, value: Config[K]) { setConfig((current) => ({ ...current, [key]: value })); }
  function setTopicPolicy(key: "engagement_requires_topic"|"comment_requires_topic", value: boolean) {
    setConfig((current) => { const next = { ...current, [key]: value, topic_prompt: value && current.topic_prompt.trim() === "不限主题" ? "" : current.topic_prompt }; const enabled = next.engagement_requires_topic || next.comment_requires_topic; return { ...next, topic_filter_enabled: enabled, like_only_on_match: next.engagement_requires_topic }; });
  }
  async function save(run: boolean) {
    setBusy(true); setNotice(run ? "正在提交任务…" : "正在保存方案…");
    try {
      const response = await fetch(`${API}${run ? "/api/run" : "/api/config"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "操作失败");
      await refresh(); setNotice(run ? `${result.count || 1} 个任务已进入队列，首个编号 ${String(result.task_id).slice(0, 8)}` : "方案已保存");
    } catch (error) { setNotice(error instanceof Error ? error.message : "操作失败"); } finally { setBusy(false); }
  }
  async function saveModelKey() {
    setSavingKey(true); setKeyNotice("正在安全保存…");
    try {
      const response = await fetch(`${API}/api/model-key`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: apiKey }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "保存失败");
      setApiKey(""); setModel(result as ModelStatus); setKeyNotice("已加密保存在本机");
    } catch (error) { setKeyNotice(error instanceof Error ? error.message : "保存失败"); } finally { setSavingKey(false); }
  }
  async function clearAllTasks() {
    setClearing(true);
    try {
      const response = await fetch(`${API}/api/tasks/clear`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation: "CLEAR_ALL_TASKS" }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "清空失败");
      setConfirmClear(false); await refresh(); setNotice(`已清空 ${result.tasks_deleted || 0} 条任务记录`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "清空失败"); } finally { setClearing(false); }
  }
  function toggleDevice(deviceId: string) {
    setConfig((current) => { const selected = current.device_ids.includes(deviceId); if (selected && current.device_ids.length === 1) return current; const device_ids = selected ? current.device_ids.filter((id) => id !== deviceId) : [...current.device_ids, deviceId]; return { ...current, device_ids, device_id: device_ids[0] }; });
  }
  function selectOnlineDevices() { const device_ids = status?.devices.filter((device) => device.state === "device").map((device) => device.device_id) || []; if (device_ids.length) setConfig((current) => ({ ...current, device_ids, device_id: device_ids[0] })); }
  async function togglePause() {
    setPausing(true);
    try {
      const pause = !status?.paused; const response = await fetch(`${API}${pause ? "/api/pause" : "/api/resume"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "操作失败"); await refresh(); setNotice(pause ? "所有任务将在当前安全步骤后暂停" : "所有任务已恢复执行");
    } catch (error) { setNotice(error instanceof Error ? error.message : "操作失败"); } finally { setPausing(false); }
  }

  function toggleTheme(event: React.MouseEvent<HTMLButtonElement>) {
    if (themeAnimatingRef.current) return;
    const nextTheme = theme === "dark" ? "light" : "dark";
    const applyTheme = () => {
      document.documentElement.dataset.theme = nextTheme;
      window.localStorage.setItem("riskflow-theme", nextTheme);
      setTheme(nextTheme);
    };
    const veil = themeVeilRef.current;
    const icon = themeIconRef.current;
    if (!veil || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      applyTheme();
      return;
    }

    themeAnimatingRef.current = true;
    const bounds = event.currentTarget.getBoundingClientRect();
    const originX = Math.round(bounds.left + bounds.width / 2);
    const originY = Math.round(bounds.top + bounds.height / 2);
    const radius = Math.ceil(Math.hypot(
      Math.max(originX, window.innerWidth - originX),
      Math.max(originY, window.innerHeight - originY),
    ));
    gsap.killTweensOf([veil, icon]);
    gsap.set(veil, {
      display: "block",
      opacity: 1,
      backgroundColor: nextTheme === "light" ? "#f5f6f7" : "#010102",
      clipPath: `circle(0px at ${originX}px ${originY}px)`,
    });
    const timeline = gsap.timeline({
      defaults: { ease: "power3.inOut" },
      onComplete: () => {
        gsap.set(veil, { display: "none", clearProps: "opacity,clipPath,backgroundColor" });
        themeAnimatingRef.current = false;
      },
    });
    timeline
      .to(veil, { clipPath: `circle(${radius}px at ${originX}px ${originY}px)`, duration: .42 })
      .call(applyTheme)
      .to(veil, { opacity: 0, duration: .2 })
      .fromTo(icon, { rotation: -55, scale: .65 }, { rotation: 0, scale: 1, duration: .38, ease: "back.out(1.8)" }, "<-.15");
  }

  return <main className="app-shell">
    <div ref={themeVeilRef} className="theme-transition-layer" aria-hidden="true"/>
    <header className="topbar"><div className="brand"><span className="brand-mark">R</span><div><strong>RiskFlow</strong><small>CONTROL LAB</small></div></div><nav aria-label="页面导航"><a className="active" href="#strategy">策略</a><a href="#devices">设备</a><a href="#records">运行记录</a></nav><div className="system-status"><span className={status?.paused ? "dot paused" : onlineCount ? "dot online" : "dot"}/><span>{notice}</span></div><button type="button" className="theme-toggle" aria-label={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"} aria-pressed={theme === "light"} title={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"} onClick={toggleTheme}><span ref={themeIconRef} className={`theme-icon ${theme === "dark" ? "sun" : "moon"}`} aria-hidden="true"/></button><span className="environment">本机 · 内部测试</span></header>
    <div className="page-shell">
      <section className="page-intro"><div><p className="eyebrow">SOCIAL RISK AUTOMATION</p><h1>策略控制台</h1><p>配置内容判断、交互概率与设备调度，所有执行结果可追踪、可纠错。</p></div><div className="overview-strip" aria-label="运行概览"><div><span>已选设备</span><b>{config.device_ids.length}</b><small>{onlineCount} 台在线</small></div><div><span>本次任务</span><b>{config.video_count * config.round_count * selectedCount}</b><small>条视频计划</small></div><div><span>预计耗时</span><b>{planDuration(estimate)}</b><small>设备并行</small></div><div><span>队列状态</span><b>{runningCount + pendingCount}</b><small>{runningCount} 执行 / {pendingCount} 排队</small></div></div></section>
      <div className="dashboard-grid"><div className="main-column">
        <section id="strategy" className="panel strategy-panel"><div className="panel-heading"><div><p className="section-index">01 · CONTENT POLICY</p><h2>内容与主题策略</h2></div><span className="tag">逐视频 AI 判断</span></div>
          <div className="topic-field-grid"><label className={`field topic-field ${anyTopicRule ? "" : "muted-field"}`}><span>目标主题</span><textarea disabled={!anyTopicRule} value={config.topic_prompt} onChange={(event) => set("topic_prompt", event.target.value)} rows={3} placeholder="例如：宠物日常、露营装备评测、职场沟通"/><small>{anyTopicRule ? "至少一类行为启用了主题约束；描述越明确，判断越稳定。" : "当前两类行为均面向全部安全内容，不需要填写主题。"}</small></label><label className={`field confidence-field ${anyTopicRule ? "" : "muted-field"}`}><span>主题判断阈值</span><div className="confidence-value"><b>{percent(config.topic_confidence)}</b><small>置信度</small></div><input aria-label="主题判断阈值" disabled={!anyTopicRule} type="range" min="0.5" max="0.99" step="0.01" value={config.topic_confidence} onChange={(event) => set("topic_confidence", Number(event.target.value))}/></label></div>
          <div className="policy-grid"><TopicPolicy title="点赞与收藏" description="轻互动归为同一组，使用同一主题门槛。" required={config.engagement_requires_topic} onChange={(value) => setTopicPolicy("engagement_requires_topic", value)}/><TopicPolicy title="评论" description="内容生成单独控制，仍保留安全检查。" required={config.comment_requires_topic} onChange={(value) => setTopicPolicy("comment_requires_topic", value)}/></div>
          <div className="policy-summary"><span>当前规则</span><b>点赞/收藏：{config.engagement_requires_topic ? "仅匹配主题" : "全部安全内容"}</b><b>评论：{config.comment_requires_topic ? "仅匹配主题" : "全部安全内容"}</b></div>{!strategyReady && <p className="inline-error">选择“仅匹配主题”后，需要先填写目标主题才能保存或提交。</p>}
        </section>
        <section className="panel behavior-panel"><div className="panel-heading"><div><p className="section-index">02 · BEHAVIOR MODEL</p><h2>节奏与行为参数</h2></div><span className="tag quiet">种子 {config.seed}</span></div>
          <div className="pace-grid"><label className="field"><span>每轮视频数</span><div className="number-with-unit"><input type="number" min="1" max="200" value={config.video_count} onChange={(event) => set("video_count", Number(event.target.value))}/><em>条</em></div></label><label className="field"><span>最短观看</span><div className="number-with-unit"><input type="number" min="0" max="120" value={config.dwell_min} onChange={(event) => set("dwell_min", Number(event.target.value))}/><em>秒</em></div></label><label className="field"><span>最长观看</span><div className="number-with-unit"><input type="number" min="0" max="120" value={config.dwell_max} onChange={(event) => set("dwell_max", Number(event.target.value))}/><em>秒</em></div></label></div>
          <div className="action-groups"><div className="action-group"><div className="action-group-title"><div><span className="group-icon">↗</span><div><strong>轻互动</strong><small>点赞与收藏共享主题策略</small></div></div><span>{config.engagement_requires_topic ? "主题限定" : "安全内容"}</span></div><div className="probability-grid two"><Probability label="点赞概率" hint={`每轮最多 ${config.max_likes} 次`} value={config.like_probability} onChange={(value) => set("like_probability", value)}/><Probability label="收藏概率" hint={`每轮最多 ${config.max_favorites} 次`} value={config.favorite_probability} onChange={(value) => set("favorite_probability", value)}/></div><div className="cap-grid two"><label className="field"><span>点赞上限</span><input type="number" min="0" max="200" value={config.max_likes} onChange={(event) => set("max_likes", Number(event.target.value))}/></label><label className="field"><span>收藏上限</span><input type="number" min="0" max="200" value={config.max_favorites} onChange={(event) => set("max_favorites", Number(event.target.value))}/></label></div></div>
            <div className="action-group"><div className="action-group-title"><div><span className="group-icon">Aa</span><div><strong>评论生成</strong><small>独立主题策略与发送开关</small></div></div><span>{config.comment_requires_topic ? "主题限定" : "安全内容"}</span></div><Probability label="评论概率" hint={`每轮最多 ${config.max_comments} 次`} value={config.comment_probability} onChange={(value) => set("comment_probability", value)}/><label className="field cap-single"><span>评论上限</span><input type="number" min="0" max="200" value={config.max_comments} onChange={(event) => set("max_comments", Number(event.target.value))}/></label></div></div>
          <div className="advanced-row"><label className="field"><span>随机种子</span><input type="number" min="0" value={config.seed} onChange={(event) => set("seed", Number(event.target.value))}/></label><label className="field"><span>异常页面最多跳过</span><input type="number" min="0" max="20" value={config.max_gate_skips} onChange={(event) => set("max_gate_skips", Number(event.target.value))}/></label><label className="field"><span>连续轮数</span><input type="number" min="1" max="20" value={config.round_count} onChange={(event) => set("round_count", Number(event.target.value))}/></label><label className="field"><span>轮次间隔（分钟）</span><input type="number" min="0" max="1440" value={config.round_interval_minutes} onChange={(event) => set("round_interval_minutes", Number(event.target.value))}/></label></div>
        </section>
        <section id="devices" className="panel device-panel"><div className="panel-heading"><div><p className="section-index">03 · EXECUTION POOL</p><h2>设备与模型</h2></div><button type="button" className="text-button" onClick={selectOnlineDevices}>选择全部在线设备</button></div><div className="device-layout"><div className="device-pool"><div className="subheading"><strong>设备池</strong><small>设备之间并行，同一设备独占执行</small></div><div className="device-options">{status?.devices.map((device) => <label key={device.device_id} className={device.state === "device" ? "device-option online" : "device-option"}><input type="checkbox" checked={config.device_ids.includes(device.device_id)} onChange={() => toggleDevice(device.device_id)}/><span><strong>{device.device_id}</strong><small>{device.state === "device" ? "ADB 已连接" : device.state === "unauthorized" ? "等待确认 ADB 调试授权" : "设备离线"}</small></span><em>{device.state === "device" ? "在线" : "离线"}</em></label>)}{!status?.devices.length && <p className="empty-device">还没有检测到安卓设备</p>}</div></div>
          <div className="model-key-box"><div className="model-title"><div><strong>视觉模型</strong><small>{model ? `${model.provider} · ${model.model}` : "正在读取模型设置…"}</small></div><span className={model?.key_configured ? "key-state ready" : "key-state"}>{model?.key_configured ? "已配置" : "未配置"}</span></div><div className="key-entry"><input aria-label="OpenRouter API Key" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={model?.key_configured ? "输入新 Key 可替换当前配置" : "粘贴 OpenRouter Key"}/><button type="button" className="secondary" disabled={savingKey || !apiKey.trim()} onClick={() => void saveModelKey()}>{savingKey ? "保存中…" : "安全保存"}</button></div>{keyNotice && <em>{keyNotice}</em>}<small className="key-help">Key 只传给本机接口并以 Windows 用户加密形式保存，网页不会回显原值。</small></div></div></section>
      </div><aside className="side-column"><section className="panel preview-panel"><div className="panel-heading compact-heading"><div><p className="section-index">LIVE VIEW</p><h2>设备画面</h2></div><button className="icon-button" aria-label="刷新设备画面" onClick={() => setImageStamp(Date.now())}>↻</button></div><div className="phone-preview"><img src={`${API}/api/latest-image?t=${imageStamp}`} alt="最近一次设备执行截图" onError={(event) => { event.currentTarget.style.opacity = "0"; }}/><span>等待设备画面</span></div><div className="preview-meta"><span>{config.device_ids.length} 台已选</span><b>{onlineCount} 台 ADB 在线</b></div></section>
        <section className="panel run-panel"><div className="run-status-row"><div><span className={status?.paused ? "dot paused" : "dot online"}/><strong>{status?.paused ? "调度已暂停" : "调度器待命"}</strong></div><small>{runningCount} 执行中 · {pendingCount} 排队</small></div><label className="switch-row important"><div><strong>仅预览评论</strong><small>生成内容但不实际发送</small></div><input aria-label="仅预览评论，不实际发送" type="checkbox" checked={config.preview_only} onChange={(event) => set("preview_only", event.target.checked)}/></label>{!config.preview_only && <div className="warning">实际发送已开启。通过规则与安全检查的评论会发送到内部测试页面。</div>}<button type="button" className={status?.paused ? "resume-all" : "pause-all"} disabled={pausing} onClick={() => void togglePause()}>{pausing ? "处理中…" : status?.paused ? "恢复所有任务" : "暂停所有任务"}</button><div className="launch-actions"><button type="button" className="secondary" disabled={busy || !strategyReady} onClick={() => void save(false)}>保存方案</button><button type="button" className="primary" disabled={busy || !config.device_ids.length || !strategyReady} onClick={() => void save(true)}>{busy ? "处理中…" : `提交 ${config.round_count * selectedCount} 个任务`}</button></div><p className="fine-print">执行会在安全步骤间响应暂停；失败任务不会自动重试。</p></section></aside></div>
        <section id="records" className="records-grid"><section className="panel history-panel"><div className="panel-heading"><div><p className="section-index">AUDIT LOG</p><h2>最近任务</h2></div><button type="button" className="danger-button" disabled={!status?.tasks.length} onClick={() => setConfirmClear(true)}>清空全部任务</button></div><div className="task-list">{status?.tasks.slice(0, 10).map((task) => <article key={task.id}><span className={`task-dot ${task.status}`}/><div><strong>{task.task_type === "douyin_topic_session" ? "内容策略测试" : task.task_type}</strong><small>{task.id.slice(0, 8)} · 设备 {task.device_id.slice(-6)} · {new Date(task.created_at).toLocaleString("zh-CN", { hour12: false })}</small><small className="task-duration">{taskDuration(task, now)}</small>{Number(task.result?.video_errors || 0) > 0 && <small className="task-correction">纠错 {String(task.result?.video_errors)} 条 · 恢复 {String(task.result?.recovered_videos || 0)} 条</small>}{task.error && <em>{task.error.includes("worker_interrupted") ? "执行进程已结束，任务已自动收口" : task.error}</em>}</div><b className={`task-status ${task.status}`}>{statusText(task)}</b></article>)}{!status?.tasks.length && <p className="empty">暂无任务记录。配置策略后提交第一轮测试。</p>}</div></section>
        <section className="panel correction-panel"><div className="panel-heading"><div><p className="section-index">RECOVERY MONITOR</p><h2>纠错监控</h2></div><span className="tag quiet">异常自动留档</span></div><div className="correction-summary"><div><span>已恢复</span><b>{status?.incident_summary.recovered || 0}</b></div><div><span>已跳过</span><b>{status?.incident_summary.skipped || 0}</b></div><div className={(status?.incident_summary.device_fatal || 0) > 0 ? "danger" : ""}><span>需处理</span><b>{status?.incident_summary.device_fatal || 0}</b></div></div><div className="incident-list">{status?.incidents.slice(0, 6).map((incident) => <article key={incident.id}><span className={`incident-dot ${incident.outcome}`}/><div><strong>第 {incident.video_index ?? "-"} 条 · {incident.stage}</strong><small>设备 {incident.device_id.slice(-6)} · {new Date(incident.created_at).toLocaleString("zh-CN", { hour12: false })}</small><em>{incident.error_type}: {incident.error_message}</em></div><div className="incident-actions"><b className={incident.outcome}>{incidentOutcome(incident.outcome)}</b>{incident.screenshot_path && <a href={`${API}/api/incident-image?id=${incident.id}`} target="_blank" rel="noreferrer">查看截图</a>}</div></article>)}{!status?.incidents.length && <p className="empty">暂无异常记录，固定程序运行正常。</p>}</div></section></section>
      <footer><span>RiskFlow · 社媒风控实验台</span><span>本机数据 · 内部测试 · 审计记录保留</span></footer>
    </div>
    {confirmClear && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !clearing) setConfirmClear(false); }}><div className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="clear-title"><span className="danger-mark">!</span><p className="section-index">DESTRUCTIVE ACTION</p><h2 id="clear-title">清空全部任务记录？</h2><p>将删除任务列表和对应纠错记录；已保存方案、API Key 与本地截图文件不会删除。</p>{runningCount > 0 && <div className="modal-warning">当前还有 {runningCount} 个任务正在执行。请先暂停并等待它们结束，再清空记录。</div>}<div className="dialog-stats"><span>任务记录 <b>{status?.tasks.length || 0}</b></span><span>纠错记录 <b>{status?.incident_summary.total || 0}</b></span></div><div className="dialog-actions"><button type="button" className="secondary" disabled={clearing} onClick={() => setConfirmClear(false)}>取消</button><button type="button" className="danger-confirm" disabled={clearing || runningCount > 0} onClick={() => void clearAllTasks()}>{clearing ? "正在清空…" : "确认清空"}</button></div></div></div>}
  </main>;
}
