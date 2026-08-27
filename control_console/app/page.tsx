"use client";
/* eslint-disable @next/next/no-img-element -- local changing screenshots bypass optimization */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { gsap } from "gsap";

const API = "http://127.0.0.1:48138";

type Config = {
  device_id: string; device_ids: string[]; video_count: number; round_count: number;
  round_interval_minutes: number; dwell_min: number; dwell_max: number;
  like_probability: number; favorite_probability: number; comment_probability: number;
  matched_like_probability: number; matched_favorite_probability: number; matched_comment_probability: number;
  content_mode: "general"|"mixed"|"search"; search_query: string;
  topic_prompt: string; topic_filter_enabled: boolean; topic_confidence: number;
  like_only_on_match: boolean; engagement_requires_topic: boolean;
  comment_requires_topic: boolean; preview_only: boolean; seed: number;
  max_gate_skips: number; max_likes: number; max_favorites: number; max_comments: number;
};
type TaskStatus = "pending"|"running"|"completed"|"failed"|"stopped"|"cancelled";
type Task = { id: string; device_id: string; task_type: string; status: TaskStatus; created_at: string; started_at?: string|null; finished_at?: string|null; result?: Record<string, unknown>|null; error?: string|null };
type DeviceStatus = { device_id: string; state: string; friendly_name?: string; profile_verified?: boolean; model?: string };
type WorkerStatus = { device_id: string; running: boolean; pid: number|null };
type IncidentAnalysis = { summary?: string; suggested_rule?: string; risk?: string; auto_applicable?: false };
type Incident = { id: string; task_id: string; device_id: string; video_index: number|null; stage: string; error_type: string; error_message: string; outcome: "recovered"|"skipped"|"device_fatal"; recovery_action?: string|null; screenshot_path?: string|null; analysis_status: string; analysis?: IncidentAnalysis|null; created_at: string };
type IncidentSummary = { total: number; queued: number; recovered: number; skipped: number; device_fatal: number; analysis_completed?: number; analysis_failed?: number };
type TaskSummary = Record<TaskStatus, number>;
type Status = { device: DeviceStatus; devices: DeviceStatus[]; worker: WorkerStatus; workers: WorkerStatus[]; paused: boolean; stop_requested_device_ids: string[]; task_summary: TaskSummary; tasks: Task[]; incidents: Incident[]; incident_summary: IncidentSummary };
type ModelStatus = { provider: string; model: string; key_configured: boolean };
type CommentScreenshot = { video_index: number };
type Preset = { name: string; builtin: boolean; config: Partial<Config> };

const fallback: Config = {
  device_id: "emulator-5556", device_ids: ["emulator-5556", "127.0.0.1:16448", "127.0.0.1:16480", "127.0.0.1:16512", "127.0.0.1:16544"],
  video_count: 20, round_count: 1, round_interval_minutes: 0, dwell_min: 6, dwell_max: 15,
  like_probability: .2, favorite_probability: .1, comment_probability: .05,
  matched_like_probability: .8, matched_favorite_probability: .7, matched_comment_probability: .5,
  content_mode: "general", search_query: "",
  topic_prompt: "不限主题", topic_filter_enabled: false, topic_confidence: .78,
  like_only_on_match: false, engagement_requires_topic: false, comment_requires_topic: false,
  preview_only: true, seed: 20260821, max_gate_skips: 6, max_likes: 20,
  max_favorites: 20, max_comments: 20,
};

const percent = (value: number) => `${Math.round(value * 100)}%`;
const statusText = (task: Task) => {
  if (task.status === "pending") return "等待执行";
  if (task.status === "running") return "正在执行";
  if (task.status === "completed") return "已结束 · 成功";
  if (task.status === "stopped") return "已结束 · 安全停止";
  if (task.status === "cancelled") return "已结束 · 已取消";
  return task.error?.includes("worker_interrupted") ? "已结束 · 中断" : "已结束 · 失败";
};
const incidentOutcome = (value: Incident["outcome"]) => ({ recovered: "已恢复", skipped: "已跳过", device_fatal: "需处理" })[value];
const incidentAnalysisText = (incident: Incident) => incident.analysis_status === "completed" ? `只读建议：${incident.analysis?.summary || "已完成分析"}` : incident.analysis_status === "failed" ? "只读分析暂时失败" : incident.analysis_status === "analyzing" ? "正在生成只读建议" : "等待只读分析";
const commentScreenshots = (task: Task): CommentScreenshot[] => {
  const value = task.result?.comment_screenshots;
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is CommentScreenshot => Boolean(item && typeof item === "object" && Number.isInteger((item as Record<string, unknown>).video_index)));
};

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

function ContentMode({ value, onChange }: { value: Config["content_mode"]; onChange: (value: Config["content_mode"]) => void }) {
  return <div className="segmented content-mode" role="group" aria-label="内容模式"><button type="button" className={value === "general" ? "active" : ""} aria-pressed={value === "general"} onClick={() => onChange("general")}>不限主题</button><button type="button" className={value === "mixed" ? "active" : ""} aria-pressed={value === "mixed"} onClick={() => onChange("mixed")}>混合主题</button><button type="button" className={value === "search" ? "active" : ""} aria-pressed={value === "search"} onClick={() => onChange("search")}>搜索主题</button></div>;
}

export default function Home() {
  const [config, setConfig] = useState<Config>(fallback);
  const [status, setStatus] = useState<Status|null>(null);
  const [model, setModel] = useState<ModelStatus|null>(null);
  const [apiKey, setApiKey] = useState("");
  const [keyNotice, setKeyNotice] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [selectedPreset, setSelectedPreset] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetNotice, setPresetNotice] = useState("");
  const [savingPreset, setSavingPreset] = useState(false);
  const [notice, setNotice] = useState("正在连接本机服务…");
  const [busy, setBusy] = useState(false);
  const [scanningDevices, setScanningDevices] = useState(false);
  const [deviceScanNotice, setDeviceScanNotice] = useState("");
  const [pausing, setPausing] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [restartingWorkers, setRestartingWorkers] = useState(false);
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
      setNotice(next.stop_requested_device_ids?.length ? `${next.stop_requested_device_ids.length} 台设备正在安全停止` : next.paused ? "所有任务已暂停领取" : online ? `${online} 台手机已连接，可以提交测试` : "未检测到已授权的安卓手机");
    } catch { setNotice("本机控制服务未启动，请双击启动器"); }
  }, []);

  useEffect(() => {
    fetch(`${API}/api/config`, { cache: "no-store" }).then((response) => response.json()).then((value) => setConfig(value as Config)).catch(() => undefined);
    fetch(`${API}/api/model`, { cache: "no-store" }).then((response) => response.json()).then((value) => setModel(value as ModelStatus)).catch(() => undefined);
    fetch(`${API}/api/presets`, { cache: "no-store" }).then((response) => response.json()).then((value: { presets?: Preset[] }) => { const next = value.presets || []; setPresets(next); setSelectedPreset((current) => current || next[1]?.name || next[0]?.name || ""); }).catch(() => undefined);
    const initialTimer = window.setTimeout(() => {
      setTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");
      void refresh();
    }, 0), timer = window.setInterval(refresh, 3500);
    return () => { window.clearTimeout(initialTimer); window.clearInterval(timer); };
  }, [refresh]);

  const availableDeviceIds = new Set(status?.devices.filter((device) => device.state === "device").map((device) => device.device_id) || []);
  const selectedDeviceIds = status ? config.device_ids.filter((deviceId) => availableDeviceIds.has(deviceId)) : [];
  const selectedCount = selectedDeviceIds.length;
  const onlineCount = status?.devices.filter((device) => device.state === "device").length || 0;
  const runningCount = status?.task_summary?.running ?? status?.tasks.filter((task) => task.status === "running").length ?? 0;
  const pendingCount = status?.task_summary?.pending ?? status?.tasks.filter((task) => task.status === "pending").length ?? 0;
  const anyTopicRule = config.content_mode !== "general";
  const strategyReady = !anyTopicRule || Boolean(config.topic_prompt.trim()) && (config.content_mode !== "search" || Boolean(config.search_query.trim()));
  const estimate = useMemo(() => ((config.dwell_min + config.dwell_max) / 2 * config.video_count * config.round_count / 60) + config.round_interval_minutes * Math.max(0, config.round_count - 1), [config]);

  function set<K extends keyof Config>(key: K, value: Config[K]) { setConfig((current) => ({ ...current, [key]: value })); }
  function setContentMode(value: Config["content_mode"]) {
    setConfig((current) => ({ ...current, content_mode: value, topic_filter_enabled: value !== "general", topic_prompt: value !== "general" && current.topic_prompt.trim() === "不限主题" ? "" : current.topic_prompt }));
  }
  async function save(run: boolean) {
    setBusy(true); setNotice(run ? "正在提交任务…" : "正在保存方案…");
    try {
      if (!selectedDeviceIds.length) throw new Error("请先检索并选择至少一台在线设备");
      const payload = { ...config, device_ids: selectedDeviceIds, device_id: selectedDeviceIds[0] };
      const response = await fetch(`${API}${run ? "/api/run" : "/api/config"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "操作失败");
      setConfig(payload); await refresh(); setNotice(run ? `${result.count || 1} 个任务已进入队列，首个编号 ${String(result.task_id).slice(0, 8)}` : "方案已保存");
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
  function applyPreset() {
    const preset = presets.find((item) => item.name === selectedPreset);
    if (!preset) { setPresetNotice("请先选择一个预设"); return; }
    setConfig((current) => ({
      ...current,
      ...preset.config,
      device_id: current.device_id,
      device_ids: current.device_ids,
      seed: current.seed,
      preview_only: current.preview_only,
    }));
    setPresetNotice(`已套用“${preset.name}”，设备、随机种子和评论发送开关保持不变`);
  }
  async function saveCurrentPreset() {
    setSavingPreset(true); setPresetNotice("正在保存预设…");
    try {
      const response = await fetch(`${API}/api/presets`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: presetName, config }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "保存失败");
      const next = (result.presets || []) as Preset[];
      const savedName = String(result.preset?.name || presetName).trim();
      setPresets(next); setSelectedPreset(savedName); setPresetName("");
      setPresetNotice(`已保存“${savedName}”；同名自定义预设会直接更新`);
    } catch (error) { setPresetNotice(error instanceof Error ? error.message : "保存失败"); } finally { setSavingPreset(false); }
  }
  async function removeSelectedPreset() {
    const preset = presets.find((item) => item.name === selectedPreset);
    if (!preset || preset.builtin) { setPresetNotice("内置预设不能删除"); return; }
    if (!window.confirm(`确定删除自定义预设“${preset.name}”吗？`)) return;
    setSavingPreset(true); setPresetNotice("正在删除预设…");
    try {
      const response = await fetch(`${API}/api/presets/delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: preset.name }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "删除失败");
      const next = (result.presets || []) as Preset[];
      setPresets(next); setSelectedPreset(next[1]?.name || next[0]?.name || "");
      setPresetNotice(`已删除“${preset.name}”`);
    } catch (error) { setPresetNotice(error instanceof Error ? error.message : "删除失败"); } finally { setSavingPreset(false); }
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
    if (!availableDeviceIds.has(deviceId)) return;
    setConfig((current) => { const selected = current.device_ids.includes(deviceId); if (selected && current.device_ids.length === 1) return current; const device_ids = selected ? current.device_ids.filter((id) => id !== deviceId) : [...current.device_ids, deviceId]; return { ...current, device_ids, device_id: device_ids[0] }; });
  }
  async function scanAvailableDevices() {
    setScanningDevices(true); setDeviceScanNotice("正在检索本机 ADB 设备…");
    try {
      const response = await fetch(`${API}/api/status`, { cache: "no-store" });
      if (!response.ok) throw new Error("设备状态读取失败");
      const next = await response.json() as Status;
      const refreshedAt = Date.now();
      const device_ids = next.devices.filter((device) => device.state === "device").map((device) => device.device_id);
      setStatus(next); setImageStamp(refreshedAt); setNow(refreshedAt);
      setConfig((current) => ({ ...current, device_ids, device_id: device_ids[0] || "" }));
      setDeviceScanNotice(device_ids.length ? `已检索到 ${device_ids.length} 台可用设备，已自动勾选` : "未检索到可用设备，请检查连接和 USB 调试授权");
      setNotice(device_ids.length ? `${device_ids.length} 台手机可用` : "未检测到已授权的安卓手机");
    } catch (error) {
      const message = error instanceof Error ? error.message : "设备检索失败";
      setDeviceScanNotice(message); setNotice(message);
    } finally { setScanningDevices(false); }
  }
  async function togglePause() {
    setPausing(true);
    try {
      const pause = !status?.paused; const response = await fetch(`${API}${pause ? "/api/pause" : "/api/resume"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "操作失败"); await refresh(); setNotice(pause ? "已暂停领取新任务；运行中的任务保持运行" : "已恢复领取任务，并清除安全停止请求");
    } catch (error) { setNotice(error instanceof Error ? error.message : "操作失败"); } finally { setPausing(false); }
  }

  async function requestSafeStop() {
    setStopping(true);
    try {
      const response = await fetch(`${API}/api/tasks/stop`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device_ids: selectedDeviceIds }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "安全停止失败");
      await refresh(); setNotice(result.running ? `已发送安全停止请求，${result.running} 个任务将在当前视频后结束` : "设备已停止领取新任务；当前没有运行任务");
    } catch (error) { setNotice(error instanceof Error ? error.message : "安全停止失败"); } finally { setStopping(false); }
  }

  async function cancelPendingTasks() {
    if (!window.confirm(`确定取消当前 ${pendingCount} 个等待任务吗？记录会保留。`)) return;
    setCancelling(true);
    try {
      const response = await fetch(`${API}/api/tasks/cancel-pending`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation: "CANCEL_PENDING_TASKS" }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "取消失败");
      await refresh(); setNotice(`已取消 ${result.cancelled || 0} 个等待任务，历史记录已保留`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "取消失败"); } finally { setCancelling(false); }
  }

  async function restartSelectedWorkers() {
    setRestartingWorkers(true);
    try {
      const response = await fetch(`${API}/api/workers/restart`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device_ids: selectedDeviceIds }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "Worker 重启失败");
      await refresh(); setNotice(`已精确重启 ${result.workers?.length || 0} 个设备 Worker`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Worker 重启失败"); } finally { setRestartingWorkers(false); }
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
    <header className="topbar"><div className="brand"><span className="brand-mark">R</span><div><strong>RiskFlow</strong><small>CONTROL LAB</small></div></div><nav aria-label="页面导航"><a className="active" href="#strategy">策略</a><a href="#devices">设备</a><a href="/records">运行记录</a></nav><div className="system-status"><span className={status?.paused ? "dot paused" : onlineCount ? "dot online" : "dot"}/><span>{notice}</span></div><button type="button" className="theme-toggle" aria-label={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"} aria-pressed={theme === "light"} title={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"} onClick={toggleTheme}><span ref={themeIconRef} className={`theme-icon ${theme === "dark" ? "sun" : "moon"}`} aria-hidden="true"/></button><span className="environment">本机 · 内部测试</span></header>
    <div className="page-shell">
      <section className="page-intro"><div><p className="eyebrow">SOCIAL RISK AUTOMATION</p><h1>策略控制台</h1><p>配置内容判断、交互概率与设备调度，所有执行结果可追踪、可纠错。</p></div><div className="overview-strip" aria-label="运行概览"><div><span>已选设备</span><b>{selectedCount}</b><small>{onlineCount} 台在线</small></div><div><span>本次任务</span><b>{config.video_count * config.round_count * selectedCount}</b><small>条视频计划</small></div><div><span>预计耗时</span><b>{planDuration(estimate)}</b><small>设备并行</small></div><div><span>队列状态</span><b>{runningCount + pendingCount}</b><small>{runningCount} 执行 / {pendingCount} 排队</small></div></div></section>
      <div className="dashboard-grid"><div className="main-column">
        <section id="strategy" className="panel strategy-panel"><div className="panel-heading"><div><p className="section-index">01 · CONTENT POLICY</p><h2>内容与主题策略</h2></div><span className="tag">证据式 AI 判断</span></div>
          <div className="mode-row"><div><strong>内容入口</strong><small>混合主题会逐条判断；搜索主题会先搜索并进入视频结果。</small></div><ContentMode value={config.content_mode} onChange={setContentMode}/></div>
          <div className="topic-field-grid"><label className={`field topic-field ${anyTopicRule ? "" : "muted-field"}`}><span>目标主题判定标准</span><textarea disabled={!anyTopicRule} value={config.topic_prompt} onChange={(event) => set("topic_prompt", event.target.value)} rows={4} placeholder={"【命中】主要内容必须是什么\n【必须证据】画面或文字要直接出现什么\n【排除】哪些相邻内容不算"}/><small>{anyTopicRule ? "按“命中 / 必须证据 / 排除”写；系统只认主要画面的直接证据，相邻或看不清均按未匹配。" : "不限主题模式仍执行安全检查，并使用“其他安全内容”概率。"}</small></label>{config.content_mode === "search" && <label className="field search-query-field"><span>先搜索什么</span><input value={config.search_query} maxLength={80} onChange={(event) => set("search_query", event.target.value)} placeholder="例如：人工智能 制造业"/><small>只负责进入搜索结果；进入后仍逐条进行主题判断。</small></label>}</div>
          <div className="policy-summary"><span>当前规则</span><b>{config.content_mode === "general" ? "不限主题 · 仅走通用概率" : config.content_mode === "mixed" ? "混合主题 · 两组概率并存" : "搜索主题 · 进入结果后两组概率并存"}</b><b>匹配必须带画面证据</b></div>{!strategyReady && <p className="inline-error">当前模式需要填写目标主题{config.content_mode === "search" ? "和搜索词" : ""}。</p>}
        </section>
        <section className="panel behavior-panel"><div className="panel-heading"><div><p className="section-index">02 · BEHAVIOR MODEL</p><h2>节奏与行为参数</h2></div><span className="tag quiet">种子 {config.seed}</span></div>
          <div className="preset-panel"><div className="preset-header"><div><strong>参数预设</strong><small>快速套用常用节奏，或保存当前参数供下次复用</small></div><span>{presets.filter((item) => !item.builtin).length} 个自定义</span></div><div className="preset-controls"><label className="field"><span>选择预设</span><select aria-label="选择参数预设" value={selectedPreset} onChange={(event) => { setSelectedPreset(event.target.value); setPresetNotice(""); }}><option value="" disabled>请选择</option>{presets.map((preset) => <option key={`${preset.builtin ? "builtin" : "custom"}-${preset.name}`} value={preset.name}>{preset.name}{preset.builtin ? " · 内置" : " · 自定义"}</option>)}</select></label><button type="button" className="secondary" disabled={!selectedPreset} onClick={applyPreset}>应用预设</button><label className="field preset-name"><span>保存为新预设</span><input aria-label="自定义预设名称" maxLength={40} value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder="例如：五机长时回归"/></label><button type="button" className="primary" disabled={savingPreset || !presetName.trim()} onClick={() => void saveCurrentPreset()}>{savingPreset ? "处理中…" : "保存当前参数"}</button><button type="button" className="preset-delete" disabled={savingPreset || !selectedPreset || Boolean(presets.find((item) => item.name === selectedPreset)?.builtin)} onClick={() => void removeSelectedPreset()}>删除预设</button></div>{presetNotice && <p className="preset-notice" role="status">{presetNotice}</p>}</div>
          <div className="pace-grid"><label className="field"><span>每轮视频总数</span><div className="number-with-unit"><input type="number" min="1" max="200" value={config.video_count} onChange={(event) => set("video_count", Number(event.target.value))}/><em>条</em></div></label><label className="field"><span>最短观看</span><div className="number-with-unit"><input type="number" min="0" max="120" value={config.dwell_min} onChange={(event) => set("dwell_min", Number(event.target.value))}/><em>秒</em></div></label><label className="field"><span>最长观看</span><div className="number-with-unit"><input type="number" min="0" max="120" value={config.dwell_max} onChange={(event) => set("dwell_max", Number(event.target.value))}/><em>秒</em></div></label></div>
          <div className="action-groups"><div className={`action-group ${anyTopicRule ? "" : "muted-field"}`}><div className="action-group-title"><div><span className="group-icon">◎</span><div><strong>匹配主题内容</strong><small>只有 exact 且存在画面证据时使用</small></div></div><span>主题分支</span></div><div className="probability-grid three"><Probability label="主题点赞概率" hint="匹配主题时" value={config.matched_like_probability} onChange={(value) => set("matched_like_probability", value)}/><Probability label="主题收藏概率" hint="匹配主题时" value={config.matched_favorite_probability} onChange={(value) => set("matched_favorite_probability", value)}/><Probability label="主题评论概率" hint="匹配主题时" value={config.matched_comment_probability} onChange={(value) => set("matched_comment_probability", value)}/></div></div>
            <div className="action-group"><div className="action-group-title"><div><span className="group-icon">↗</span><div><strong>其他安全内容</strong><small>不限主题或未匹配主题时使用</small></div></div><span>通用分支</span></div><div className="probability-grid three"><Probability label="通用点赞概率" hint="未匹配主题时" value={config.like_probability} onChange={(value) => set("like_probability", value)}/><Probability label="通用收藏概率" hint="未匹配主题时" value={config.favorite_probability} onChange={(value) => set("favorite_probability", value)}/><Probability label="通用评论概率" hint="未匹配主题时" value={config.comment_probability} onChange={(value) => set("comment_probability", value)}/></div></div></div>
          <div className="advanced-row"><label className="field"><span>随机种子</span><input type="number" min="0" value={config.seed} onChange={(event) => set("seed", Number(event.target.value))}/></label><label className="field"><span>连续异常停止阈值</span><input type="number" min="1" max="50" value={config.max_gate_skips} onChange={(event) => set("max_gate_skips", Number(event.target.value))}/><small>连续出现异常页面才计数；正常完成一条视频后自动清零。</small></label><label className="field"><span>连续轮数</span><input type="number" min="1" max="20" value={config.round_count} onChange={(event) => set("round_count", Number(event.target.value))}/></label><label className="field"><span>轮次间隔（分钟）</span><input type="number" min="0" max="1440" value={config.round_interval_minutes} onChange={(event) => set("round_interval_minutes", Number(event.target.value))}/></label></div>
        </section>
        <section id="devices" className="panel device-panel"><div className="panel-heading"><div><p className="section-index">03 · EXECUTION POOL</p><h2>设备与模型</h2></div><div className="panel-actions"><button type="button" className="text-button" disabled={restartingWorkers || !selectedCount || runningCount > 0} onClick={() => void restartSelectedWorkers()}>{restartingWorkers ? "正在重启…" : "精确重启已选 Worker"}</button><button type="button" className="text-button" disabled={scanningDevices} onClick={() => void scanAvailableDevices()}>{scanningDevices ? "正在检索…" : "检索所有可用设备"}</button></div></div><div className="device-layout"><div className="device-pool"><div className="subheading"><strong>设备池</strong><small>设备之间并行，同一设备独占执行</small></div>{deviceScanNotice && <p className="device-scan-notice">{deviceScanNotice}</p>}<div className="device-options">{status?.devices.map((device) => { const available = device.state === "device"; return <label key={device.device_id} className={available ? "device-option online" : "device-option unavailable"}><input type="checkbox" disabled={!available} checked={available && config.device_ids.includes(device.device_id)} onChange={() => toggleDevice(device.device_id)}/><span><strong>{device.friendly_name || device.device_id}</strong><small>{device.model ? `${device.model} · ` : ""}{device.device_id} · {available ? device.profile_verified ? "档案已验证" : "在线但档案待验证" : device.state === "unauthorized" ? "等待确认 ADB 调试授权" : device.state === "unknown" ? "无法读取设备状态" : "设备离线"}</small></span><em>{status?.stop_requested_device_ids?.includes(device.device_id) ? "停止中" : available ? "可用" : device.state === "unauthorized" ? "待授权" : "不可用"}</em></label>; })}{!status?.devices.length && <p className="empty-device">还没有检测到安卓设备</p>}</div></div>
          <div className="model-key-box"><div className="model-title"><div><strong>视觉模型</strong><small>{model ? `${model.provider} · ${model.model}` : "正在读取模型设置…"}</small></div><span className={model?.key_configured ? "key-state ready" : "key-state"}>{model?.key_configured ? "已配置" : "未配置"}</span></div><div className="key-entry"><input aria-label="OpenRouter API Key" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={model?.key_configured ? "输入新 Key 可替换当前配置" : "粘贴 OpenRouter Key"}/><button type="button" className="secondary" disabled={savingKey || !apiKey.trim()} onClick={() => void saveModelKey()}>{savingKey ? "保存中…" : "安全保存"}</button></div>{keyNotice && <em>{keyNotice}</em>}<small className="key-help">Key 只传给本机接口并以 Windows 用户加密形式保存，网页不会回显原值。</small></div></div></section>
      </div><aside className="side-column"><section className="panel preview-panel"><div className="panel-heading compact-heading"><div><p className="section-index">LIVE VIEW</p><h2>设备画面</h2></div><button className="icon-button" aria-label="刷新设备画面" onClick={() => setImageStamp(Date.now())}>↻</button></div><div className="phone-preview"><img src={`${API}/api/latest-image?t=${imageStamp}`} alt="最近一次设备执行截图" onError={(event) => { event.currentTarget.style.opacity = "0"; }}/><span>等待设备画面</span></div><div className="preview-meta"><span>{selectedCount} 台已选</span><b>{onlineCount} 台 ADB 在线</b></div></section>
        <section className="panel run-panel"><div className="run-status-row"><div><span className={status?.paused ? "dot paused" : "dot online"}/><strong>{status?.paused ? "已暂停领取" : "调度器待命"}</strong></div><small>{runningCount} 执行中 · {pendingCount} 排队</small></div><label className="switch-row important"><div><strong>仅预览评论</strong><small>生成内容但不实际发送</small></div><input aria-label="仅预览评论，不实际发送" type="checkbox" checked={config.preview_only} onChange={(event) => set("preview_only", event.target.checked)}/></label>{!config.preview_only && <div className="warning">实际发送已开启。通过规则与安全检查的评论会发送到内部测试页面。</div>}<div className="task-control-grid"><button type="button" className={status?.paused ? "resume-all" : "pause-all"} disabled={pausing} onClick={() => void togglePause()}>{pausing ? "处理中…" : status?.paused ? "恢复领取任务" : "暂停领取新任务"}</button><button type="button" className="safe-stop" disabled={stopping || !selectedCount} onClick={() => void requestSafeStop()}>{stopping ? "正在请求…" : "安全停止已选设备"}</button><button type="button" className="cancel-pending" disabled={cancelling || pendingCount <= 0} onClick={() => void cancelPendingTasks()}>{cancelling ? "正在取消…" : `取消 ${pendingCount} 个等待任务`}</button></div><div className="launch-actions"><button type="button" className="secondary" disabled={busy || !selectedCount || !strategyReady} onClick={() => void save(false)}>保存方案</button><button type="button" className="primary" disabled={busy || !selectedCount || !strategyReady} onClick={() => void save(true)}>{busy ? "处理中…" : `提交 ${config.round_count * selectedCount} 个任务`}</button></div><p className="fine-print">暂停不等于停止；安全停止会在当前视频结束后收口。失败和停止任务都不会自动重试。</p></section></aside></div>
        <section id="records" className="records-grid"><section className="panel history-panel"><div className="panel-heading"><div><p className="section-index">AUDIT LOG</p><h2>最近任务</h2><small className="section-note">首页固定显示最近 5 条 · 共 {Object.values(status?.task_summary || {}).reduce((sum, value) => sum + value, 0)} 条</small></div><div className="panel-actions"><a className="view-all-link" href="/records#tasks">查看所有</a><button type="button" className="danger-button" disabled={!status?.tasks.length} onClick={() => setConfirmClear(true)}>清空全部任务</button></div></div><div className="task-list">{status?.tasks.slice(0, 5).map((task) => <article key={task.id}><span className={`task-dot ${task.status}`}/><div><strong>{task.task_type === "douyin_topic_session" ? "内容策略测试" : task.task_type}</strong><small>{task.id.slice(0, 8)} · 设备 {task.device_id.slice(-6)} · {new Date(task.created_at).toLocaleString("zh-CN", { hour12: false })}</small><small className="task-duration">{taskDuration(task, now)}</small>{Number(task.result?.video_errors || 0) > 0 && <small className="task-correction">纠错 {String(task.result?.video_errors)} 条 · 恢复 {String(task.result?.recovered_videos || 0)} 条</small>}{commentScreenshots(task).length > 0 && <span className="task-evidence">{commentScreenshots(task).map((evidence) => <a key={evidence.video_index} href={`${API}/api/comment-image?task_id=${encodeURIComponent(task.id)}&video=${evidence.video_index}`} target="_blank" rel="noreferrer">评论截图 · 第 {evidence.video_index} 条</a>)}</span>}{task.error && <em>{task.error.includes("worker_interrupted") ? "执行进程已结束，任务已自动收口" : task.error}</em>}</div><b className={`task-status ${task.status}`}>{statusText(task)}</b></article>)}{!status?.tasks.length && <p className="empty">暂无任务记录。配置策略后提交第一轮测试。</p>}</div></section>
        <section className="panel correction-panel"><div className="panel-heading"><div><p className="section-index">RECOVERY MONITOR</p><h2>纠错监控</h2></div><div className="panel-actions"><a className="view-all-link" href="/records#incidents">查看所有</a><span className="tag quiet">异常自动留档 · AI只读分析</span></div></div><div className="correction-summary"><div><span>已恢复</span><b>{status?.incident_summary.recovered || 0}</b></div><div><span>已跳过</span><b>{status?.incident_summary.skipped || 0}</b></div><div className={(status?.incident_summary.device_fatal || 0) > 0 ? "danger" : ""}><span>需处理</span><b>{status?.incident_summary.device_fatal || 0}</b></div></div><div className="incident-list">{status?.incidents.slice(0, 5).map((incident) => <article key={incident.id}><span className={`incident-dot ${incident.outcome}`}/><div><strong>第 {incident.video_index ?? "-"} 条 · {incident.stage}</strong><small>设备 {incident.device_id.slice(-6)} · {new Date(incident.created_at).toLocaleString("zh-CN", { hour12: false })}</small><em>{incident.error_type}: {incident.error_message}</em><small className={`incident-analysis ${incident.analysis_status}`}>{incidentAnalysisText(incident)}</small>{incident.analysis?.suggested_rule && <small className="incident-rule">候选规则：{incident.analysis.suggested_rule}</small>}</div><div className="incident-actions"><b className={incident.outcome}>{incidentOutcome(incident.outcome)}</b>{incident.screenshot_path && <a href={`${API}/api/incident-image?id=${incident.id}`} target="_blank" rel="noreferrer">查看截图</a>}</div></article>)}{!status?.incidents.length && <p className="empty">暂无异常记录，固定程序运行正常。</p>}</div></section></section>
      <footer><span>RiskFlow · 社媒风控实验台</span><span>本机数据 · 内部测试 · 审计记录保留</span></footer>
    </div>
    {confirmClear && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !clearing) setConfirmClear(false); }}><div className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="clear-title"><span className="danger-mark">!</span><p className="section-index">DESTRUCTIVE ACTION</p><h2 id="clear-title">清空全部任务记录？</h2><p>将删除任务列表和对应纠错记录；已保存方案、API Key 与本地截图文件不会删除。</p>{runningCount > 0 && <div className="modal-warning">当前还有 {runningCount} 个任务正在执行。请先暂停并等待它们结束，再清空记录。</div>}<div className="dialog-stats"><span>任务记录 <b>{Object.values(status?.task_summary || {}).reduce((sum, value) => sum + value, 0)}</b></span><span>纠错记录 <b>{status?.incident_summary.total || 0}</b></span></div><div className="dialog-actions"><button type="button" className="secondary" disabled={clearing} onClick={() => setConfirmClear(false)}>取消</button><button type="button" className="danger-confirm" disabled={clearing || runningCount > 0} onClick={() => void clearAllTasks()}>{clearing ? "正在清空…" : "确认清空"}</button></div></div></div>}
  </main>;
}
