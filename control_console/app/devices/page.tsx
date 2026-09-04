"use client";
/* Vinext client navigation can fail after hot updates; local page links use hard navigation. */
/* eslint-disable @next/next/no-html-link-for-pages */
/* eslint-disable @next/next/no-img-element -- images are live local device frames */
/* Form labels wrap their controls; compact JSX is not recognized by the static a11y rule. */
/* eslint-disable jsx-a11y/label-has-associated-control */

import { useCallback, useEffect, useMemo, useState } from "react";
import { DeviceOnboardingDialog } from "../components/device-onboarding-dialog";
import { DeviceLiveView } from "../components/device-live-view";
import type { VirtualDevice, VirtualDeviceIssue } from "../components/workbench-types";
import { fetchLocalApi } from "../lib/local-api";
import { virtualOperationIsActive, virtualOperationStageLabel, waitForVirtualOperation } from "../lib/virtual-device-operations";

const API = "http://127.0.0.1:48138";

type Config = { device_id: string; device_ids: string[]; [key: string]: unknown };
type InitializationStatus = {
  id: string; status: "queued" | "running" | "waiting_user" | "ready" | "stale" | "failed" | "cancelled";
  stage: string; progress_current: number; progress_total: number; message: string; report_path?: string | null;
  write_acceptance?: boolean; error?: string | null;
};
type DeviceStatus = { device_id: string; state: string; device_type?: "physical" | "virtual"; friendly_name?: string; profile_verified?: boolean; model?: string; initialization_status?: string; initialization?: InitializationStatus | null };
type Status = {
  devices: DeviceStatus[];
  paused: boolean;
  stop_requested_device_ids: string[];
  initialization_summary?: Record<string, number>;
  active_tasks?: Array<{ device_id: string; status: string }>;
  virtualization?: { devices: VirtualDevice[]; issues?: VirtualDeviceIssue[] };
  device_preferences?: { physical_devices_enabled: boolean };
};
type VirtualSettings = { cpu: number; memory_gb: number; fps: number; muted: boolean };
type VirtualBackup = { id: string; virtual_device_id?: string | null; source_name: string; size_bytes: number; sha256: string; status: string; created_at: string };
type PoolPlan = { mode: "supplement" | "reset"; target_count: number; standard_count: number; create_count: number; deletion_items: Array<{ provider_instance_id: string; name: string; state: string; managed: boolean }>; confirmation_phrase?: string | null; no_backup: boolean };

const initializationLabels: Record<string, string> = {
  uninitialized: "未初始化", legacy: "旧档案", queued: "排队中", running: "初始化中", waiting_user: "等待你操作",
  ready: "已就绪", stale: "需要复验", failed: "初始化失败", cancelled: "已取消",
};

export default function DevicesPage() {
  const [config, setConfig] = useState<Config | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [notice, setNotice] = useState("正在连接本机设备服务…");
  const [scanning, setScanning] = useState(false);
  const [addDeviceOpen, setAddDeviceOpen] = useState(false);
  const [imageStamp, setImageStamp] = useState(0);
  const [failedImages, setFailedImages] = useState<Set<string>>(new Set());
  const [initializing, setInitializing] = useState<Set<string>>(new Set());
  const [writeAcceptance, setWriteAcceptance] = useState<Record<string, boolean>>({});
  const [focused, setFocused] = useState<{ device: DeviceStatus; mode: "read_only" | "control" } | null>(null);
  const [virtualBusy, setVirtualBusy] = useState<Set<string>>(new Set());
  const [unmanagedVirtuals, setUnmanagedVirtuals] = useState<Array<{ provider_instance_id: string; name: string; state: string }>>([]);
  const [settingsTarget, setSettingsTarget] = useState<VirtualDevice | null>(null);
  const [backups, setBackups] = useState<VirtualBackup[]>([]);
  const [selectedVirtuals, setSelectedVirtuals] = useState<Set<string>>(new Set());
  const [settingsForm, setSettingsForm] = useState<VirtualSettings>({ cpu: 2, memory_gb: 1.75, fps: 30, muted: true });
  const [activeTab, setActiveTab] = useState<"virtual" | "physical">("virtual");
  const [deviceSettingsOpen, setDeviceSettingsOpen] = useState(false);
  const [poolTarget, setPoolTarget] = useState(2);
  const [poolMode, setPoolMode] = useState<"supplement" | "reset">("supplement");
  const [poolPlan, setPoolPlan] = useState<PoolPlan | null>(null);
  const [poolConfirmation, setPoolConfirmation] = useState("");
  const [poolBusy, setPoolBusy] = useState(false);

  const readStatus = useCallback(async (selectAll = false) => {
    const response = await fetchLocalApi(`${API}/api/status`, { cache: "no-store" }, 8_000);
    if (!response.ok) throw new Error("设备状态读取失败");
    const next = await response.json() as Status;
    const onlineIds = next.devices.filter((device) => device.state === "device").map((device) => device.device_id);
    setStatus(next);
    if (!next.device_preferences?.physical_devices_enabled) {
      setActiveTab((current) => current === "physical" ? "virtual" : current);
    }
    if (selectAll) setSelected(onlineIds);
    setNotice(onlineIds.length ? `${onlineIds.length} 台设备已连接` : "当前没有在线ADB设备；已停止的虚拟机仍会保留");
    setFailedImages(new Set());
    setImageStamp(Date.now());
    return next;
  }, []);

  const readBackups = useCallback(async () => {
    const response = await fetchLocalApi(`${API}/api/virtual-device-backups`, { cache: "no-store" }, 8_000);
    if (!response.ok) return;
    const payload = await response.json() as { backups?: VirtualBackup[] };
    setBackups(payload.backups || []);
  }, []);

  const readUnmanaged = useCallback(async () => {
    const response = await fetchLocalApi(`${API}/api/virtual-devices/unmanaged`, { cache: "no-store" }, 8_000);
    if (!response.ok) return;
    const payload = await response.json() as { instances?: Array<{ provider_instance_id: string; name: string; state: string }> };
    setUnmanagedVirtuals(payload.instances || []);
  }, []);

  useEffect(() => {
    const load = async () => {
      try {
        const configResponse = await fetchLocalApi(`${API}/api/config`, { cache: "no-store" });
        if (!configResponse.ok) throw new Error("当前方案读取失败");
        const nextConfig = await configResponse.json() as Config;
        setConfig(nextConfig); setSelected(nextConfig.device_ids || []);
        await Promise.all([readStatus(false), readBackups(), readUnmanaged()]);
      } catch (error) { setNotice(error instanceof Error ? error.message : "本机控制服务未启动"); }
    };
    const initial = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => { void readStatus(false).catch(() => setNotice("本机控制服务未启动")); }, 5000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [readBackups, readStatus, readUnmanaged]);

  const online = useMemo(() => status?.devices.filter((device) => device.state === "device") || [], [status]);
  const physicalEnabled = Boolean(status?.device_preferences?.physical_devices_enabled);
  const displayTab = physicalEnabled ? activeTab : "virtual";
  const visibleOnline = useMemo(() => online.filter((device) => (device.device_type || "physical") === displayTab), [displayTab, online]);
  const visibleDevices = useMemo(() => (status?.devices || []).filter((device) => (device.device_type || "physical") === displayTab), [displayTab, status]);
  const selectedOnline = selected.filter((deviceId) => visibleOnline.some((device) => device.device_id === deviceId));
  const virtualByAdb = useMemo(() => new Map((status?.virtualization?.devices || []).filter((device) => device.adb_endpoint).map((device) => [device.adb_endpoint!, device])), [status]);
  const runningDeviceIds = useMemo(() => new Set((status?.active_tasks || []).filter((task) => task.status === "running").map((task) => task.device_id)), [status]);

  const scan = async () => {
    setScanning(true); setNotice("正在核对本机 MuMu 实例和 ADB 设备…");
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-devices/reconcile`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, 20_000);
      const result = await response.json() as { error?: string };
      if (!response.ok) throw new Error(result.error || "虚拟机库存刷新失败");
      await readUnmanaged();
      await readStatus(true);
      await readBackups();
      setNotice("本机虚拟机和真机状态已刷新");
    } catch (error) { setNotice(error instanceof Error ? error.message : "设备检索失败"); }
    finally { setScanning(false); }
  };

  const batchVirtual = async (action: "start" | "stop") => {
    const virtualDeviceIds = [...selectedVirtuals];
    if (!virtualDeviceIds.length) return;
    if (action === "stop" && !window.confirm(`确认停止选中的 ${virtualDeviceIds.length} 台虚拟机？正在执行任务的设备会被单独拒绝。`)) return;
    setVirtualBusy((current) => new Set([...current, ...virtualDeviceIds]));
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-devices/batch`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, virtual_device_ids: virtualDeviceIds, idempotency_key: crypto.randomUUID() }),
      }, 20_000);
      const payload = await response.json() as { operations?: Array<{ operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string }>; error?: string };
      if (!response.ok || !payload.operations) throw new Error(payload.error || "批量操作提交失败");
      const accepted = payload.operations.filter((item) => item.operation).map((item) => item.operation!);
      const rejected = payload.operations.filter((item) => item.error).length;
      const terminal = await Promise.all(accepted.map((operation) => waitForVirtualOperation(API, operation, undefined, { maxAttempts: 900 })));
      const completed = terminal.filter((operation) => operation.status === "completed").length;
      const failed = terminal.length - completed + rejected;
      setNotice(`批量${action === "start" ? "启动" : "停止"}完成：${completed} 台成功${failed ? `，${failed} 台未完成` : ""}`);
      setSelectedVirtuals(new Set());
      await scan();
    } catch (error) { setNotice(error instanceof Error ? error.message : "批量操作失败"); }
    finally { setVirtualBusy((current) => { const next = new Set(current); virtualDeviceIds.forEach((id) => next.delete(id)); return next; }); }
  };

  const restoreBackup = async (backup: VirtualBackup) => {
    const busyKey = `backup:${backup.id}`;
    setVirtualBusy((current) => new Set(current).add(busyKey));
    setNotice(`正在从 ${backup.source_name} 的备份恢复新虚拟机…`);
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-device-backups/${encodeURIComponent(backup.id)}/restore`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      }, 20_000);
      const payload = await response.json() as { operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string };
      if (!response.ok || !payload.operation) throw new Error(payload.error || "恢复请求失败");
      const operation = await waitForVirtualOperation(API, payload.operation, (next) => setNotice(`${virtualOperationStageLabel(next.stage)} · ${next.progress}%`), { maxAttempts: 900 });
      if (operation.status !== "completed") throw new Error(operation.error || "备份恢复未完成");
      setNotice(`${operation.result?.name || "新虚拟机"} 已恢复，需要启动并复验`);
      await scan();
    } catch (error) { setNotice(error instanceof Error ? error.message : "备份恢复失败"); }
    finally { setVirtualBusy((current) => { const next = new Set(current); next.delete(busyKey); return next; }); }
  };

  const adoptVirtual = async (providerInstanceId: string) => {
    setVirtualBusy((current) => new Set(current).add(`adopt:${providerInstanceId}`));
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-devices/unmanaged/${encodeURIComponent(providerInstanceId)}/adopt`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      }, 20_000);
      const payload = await response.json() as { operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string };
      if (!response.ok || !payload.operation) throw new Error(payload.error || "接管虚拟机失败");
      const operation = await waitForVirtualOperation(API, payload.operation, (next) => setNotice(`${virtualOperationStageLabel(next.stage)} · ${next.progress}%`));
      if (operation.status === "failed") throw new Error(operation.error || "接管虚拟机失败");
      setNotice(`${operation.result?.name || "虚拟机"} 已接管，需要启动并完成复验`);
      await scan();
    } catch (error) { setNotice(error instanceof Error ? error.message : "接管虚拟机失败"); }
    finally { setVirtualBusy((current) => { const next = new Set(current); next.delete(`adopt:${providerInstanceId}`); return next; }); }
  };

  const startVirtual = async (virtualDevice: VirtualDevice) => {
    setVirtualBusy((current) => new Set(current).add(virtualDevice.virtual_device_id));
    setNotice(`正在启动 ${virtualDevice.name}…`);
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-devices/${encodeURIComponent(virtualDevice.virtual_device_id)}/operations`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "start", idempotency_key: crypto.randomUUID() }),
      }, 20_000);
      const created = await response.json() as { operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string };
      if (!response.ok || !created.operation) throw new Error(created.error || "虚拟机启动失败");
      const operation = await waitForVirtualOperation(API, created.operation, (next) => {
        setNotice(`${virtualDevice.name} · ${virtualOperationStageLabel(next.stage)} · ${next.progress}%`);
      });
      const next = await readStatus(false);
      if (["failed", "cancelled"].includes(operation.status)) throw new Error(operation.error || "虚拟机启动失败");
      if (operation.status === "waiting_user") {
        setNotice(operation.error || "虚拟机需要你确认后才能继续");
        return;
      }
      const endpoint = operation.result?.adb_endpoint;
      const device = endpoint ? next.devices.find((item) => item.device_id === endpoint) : undefined;
      setNotice(device ? `${virtualDevice.name} 已启动，正在打开平台内画面` : `${virtualDevice.name} 已启动，正在完成初始化复验`);
      if (device) setFocused({ device, mode: "read_only" });
    } catch (error) { setNotice(error instanceof Error ? error.message : "虚拟机启动失败"); }
    finally { setVirtualBusy((current) => { const next = new Set(current); next.delete(virtualDevice.virtual_device_id); return next; }); }
  };

  const continueVirtualOnboarding = async (virtualDevice: VirtualDevice) => {
    const operation = virtualDevice.active_operation;
    if (!operation || operation.status !== "waiting_user" || operation.stage !== "waiting_app_install") {
      const endpoint = virtualDevice.adb_endpoint;
      if (endpoint) await initializationRequest(endpoint, "start");
      return;
    }
    setVirtualBusy((current) => new Set(current).add(virtualDevice.virtual_device_id));
    setNotice(`${virtualDevice.name} · 正在检查抖音安装状态…`);
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-device-operations/${encodeURIComponent(operation.id)}/continue`, { method: "POST" }, 20_000);
      const payload = await response.json() as { operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string };
      if (!response.ok || !payload.operation) throw new Error(payload.error || "继续接入失败");
      const resumed = { ...payload.operation, status: "running" as const, stage: "checking_app_install", progress: 75, error: null };
      const completed = await waitForVirtualOperation(API, resumed, (next) => setNotice(`${virtualDevice.name} · ${virtualOperationStageLabel(next.stage)} · ${next.progress}%`));
      if (completed.status === "failed") throw new Error(completed.error || "继续接入失败");
      setNotice(completed.status === "waiting_user" ? completed.error || "仍需完成当前人工步骤" : "抖音安装检查已完成，正在进入初始化");
      await readStatus(false);
    } catch (error) { setNotice(error instanceof Error ? error.message : "继续接入失败"); }
    finally { setVirtualBusy((current) => { const next = new Set(current); next.delete(virtualDevice.virtual_device_id); return next; }); }
  };

  const operateVirtual = async (virtualDevice: VirtualDevice, action: "stop" | "restart" | "clone" | "backup" | "repair_standard") => {
    setVirtualBusy((current) => new Set(current).add(virtualDevice.virtual_device_id));
    const labels = { stop: "停止", restart: "重启", clone: "克隆", backup: "备份", repair_standard: "恢复标准配置" };
    setNotice(`正在${labels[action]} ${virtualDevice.name}…`);
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-devices/${encodeURIComponent(virtualDevice.virtual_device_id)}/operations`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, idempotency_key: crypto.randomUUID() }),
      }, 20_000);
      const payload = await response.json() as { operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string };
      if (!response.ok || !payload.operation) throw new Error(payload.error || `${labels[action]}失败`);
      const operation = await waitForVirtualOperation(API, payload.operation, (next) => setNotice(`${virtualDevice.name} · ${virtualOperationStageLabel(next.stage)} · ${next.progress}%`), { maxAttempts: action === "backup" || action === "clone" ? 900 : 360 });
      if (operation.status !== "completed") throw new Error(operation.error || `${labels[action]}未完成`);
      setNotice(`${virtualDevice.name} · ${labels[action]}已完成`);
      await scan();
    } catch (error) { setNotice(error instanceof Error ? error.message : `${labels[action]}失败`); }
    finally { setVirtualBusy((current) => { const next = new Set(current); next.delete(virtualDevice.virtual_device_id); return next; }); }
  };

  const saveVirtualSettings = async () => {
    if (!settingsTarget) return;
    setVirtualBusy((current) => new Set(current).add(settingsTarget.virtual_device_id));
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-devices/${encodeURIComponent(settingsTarget.virtual_device_id)}/settings`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings: settingsForm, idempotency_key: crypto.randomUUID() }),
      }, 20_000);
      const payload = await response.json() as { operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string };
      if (!response.ok || !payload.operation) throw new Error(payload.error || "配置保存失败");
      const operation = await waitForVirtualOperation(API, payload.operation, (next) => setNotice(`${settingsTarget.name} · ${virtualOperationStageLabel(next.stage)} · ${next.progress}%`));
      if (operation.status !== "completed") throw new Error(operation.error || "配置保存失败");
      setSettingsTarget(null); setNotice("虚拟机配置已回读确认"); await scan();
    } catch (error) { setNotice(error instanceof Error ? error.message : "配置保存失败"); }
    finally { setVirtualBusy((current) => { const next = new Set(current); if (settingsTarget) next.delete(settingsTarget.virtual_device_id); return next; }); }
  };

  const deleteVirtual = async (virtualDevice: VirtualDevice) => {
    const confirmation = window.prompt(`删除前会先备份。请输入完整名称“${virtualDevice.name}”确认：`, "");
    if (confirmation === null) return;
    setVirtualBusy((current) => new Set(current).add(virtualDevice.virtual_device_id));
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-devices/${encodeURIComponent(virtualDevice.virtual_device_id)}`, {
        method: "DELETE", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation_name: confirmation, backup: true, idempotency_key: crypto.randomUUID() }),
      }, 20_000);
      const payload = await response.json() as { operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string };
      if (!response.ok || !payload.operation) throw new Error(payload.error || "删除请求失败");
      const operation = await waitForVirtualOperation(API, payload.operation, (next) => setNotice(`${virtualDevice.name} · ${virtualOperationStageLabel(next.stage)} · ${next.progress}%`), { maxAttempts: 900 });
      if (operation.status !== "completed") throw new Error(operation.error || "删除失败");
      setNotice(`${virtualDevice.name} 已备份并删除，历史记录仍保留`); await scan();
    } catch (error) { setNotice(error instanceof Error ? error.message : "删除失败"); }
    finally { setVirtualBusy((current) => { const next = new Set(current); next.delete(virtualDevice.virtual_device_id); return next; }); }
  };

  const initializationRequest = async (deviceId: string, action: "start" | "continue" | "cancel") => {
    setInitializing((current) => new Set(current).add(deviceId));
    try {
      const write = Boolean(writeAcceptance[deviceId]);
      const path = action === "start"
        ? `${API}/api/devices/${encodeURIComponent(deviceId)}/initializations`
        : `${API}/api/devices/${encodeURIComponent(deviceId)}/initialization/${action}`;
      const body = action === "start" ? {
        search_query: String(config?.search_query || config?.topic_prompt || "人工智能").slice(0, 80),
        write_acceptance: write,
        ...(write ? { confirmation: "ENABLE_WRITE_ACCEPTANCE" } : {}),
      } : {};
      const response = await fetchLocalApi(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, 20_000);
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "初始化操作失败");
      setNotice(action === "cancel" ? "已请求安全取消初始化" : "初始化任务已交给设备 Worker");
      await readStatus(false);
    } catch (error) { setNotice(error instanceof Error ? error.message : "初始化操作失败"); }
    finally { setInitializing((current) => { const next = new Set(current); next.delete(deviceId); return next; }); }
  };

  const openNativeWindow = async (virtualDeviceId: string) => {
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-devices/${encodeURIComponent(virtualDeviceId)}/window`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visible: true }),
      }, 15_000);
      const result = await response.json() as { error?: string };
      if (!response.ok) throw new Error(result.error || "单机窗口打开失败");
      setNotice("已打开该台 MuMu 单机窗口，没有打开管理大厅");
    } catch (error) { setNotice(error instanceof Error ? error.message : "单机窗口打开失败"); }
  };

  const savePhysicalPreference = async (enabled: boolean) => {
    try {
      const response = await fetchLocalApi(`${API}/api/device-preferences`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ physical_devices_enabled: enabled }),
      }, 10_000);
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "设备设置保存失败");
      if (!enabled) setActiveTab("virtual");
      setNotice(enabled ? "真机支持已启用；现在可以切换到真机板块" : "真机支持已隐藏；档案和历史数据仍保留");
      await readStatus(false);
    } catch (error) { setNotice(error instanceof Error ? error.message : "设备设置保存失败"); }
  };

  const previewPool = async () => {
    setPoolBusy(true);
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-device-pools/preview`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: poolMode, target_count: poolTarget }),
      }, 20_000);
      const payload = await response.json() as { plan?: PoolPlan; error?: string };
      if (!response.ok || !payload.plan) throw new Error(payload.error || "标准池预览失败");
      setPoolPlan(payload.plan); setPoolConfirmation("");
      setNotice(poolMode === "supplement" ? `当前达标 ${payload.plan.standard_count} 台，需要新建 ${payload.plan.create_count} 台` : `已列出 ${payload.plan.deletion_items.length} 台将删除实例，请核对后确认`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "标准池预览失败"); }
    finally { setPoolBusy(false); }
  };

  const applyPool = async () => {
    if (!poolPlan) return;
    setPoolBusy(true);
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-device-pools`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: poolPlan.mode, target_count: poolPlan.target_count, confirmation: poolConfirmation, idempotency_key: crypto.randomUUID() }),
      }, 20_000);
      const payload = await response.json() as { operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string };
      if (!response.ok || !payload.operation) throw new Error(payload.error || "标准池操作提交失败");
      const operation = await waitForVirtualOperation(API, payload.operation, (next) => setNotice(`${virtualOperationStageLabel(next.stage)} · ${next.progress}%`), { maxAttempts: 1800 });
      if (operation.status !== "completed") throw new Error(operation.error || "标准池操作未完成");
      setNotice(operation.message || "标准池操作完成"); setPoolPlan(null); setPoolConfirmation(""); await scan();
    } catch (error) { setNotice(error instanceof Error ? error.message : "标准池操作失败"); }
    finally { setPoolBusy(false); }
  };

  const operateUnmanaged = async (item: { provider_instance_id: string; name: string; state: string }, action: "start" | "stop" | "delete") => {
    const key = `unmanaged:${item.provider_instance_id}`;
    const confirmation = action === "delete" ? window.prompt(`该实例不受MediaFlow托管。请输入完整名称“${item.name}”确认永久删除：`, "") : "";
    if (action === "delete" && confirmation === null) return;
    setVirtualBusy((current) => new Set(current).add(key));
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-devices/unmanaged/${encodeURIComponent(item.provider_instance_id)}/operations`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, confirmation_name: confirmation, idempotency_key: crypto.randomUUID() }),
      }, 20_000);
      const payload = await response.json() as { operation?: import("../lib/virtual-device-operations").VirtualOperation; error?: string };
      if (!response.ok || !payload.operation) throw new Error(payload.error || "虚拟机操作失败");
      const operation = await waitForVirtualOperation(API, payload.operation);
      if (operation.status !== "completed") throw new Error(operation.error || "虚拟机操作失败");
      setNotice(`未托管虚拟机${action === "start" ? "已启动" : action === "stop" ? "已停止" : "已删除"}`); await scan();
    } catch (error) { setNotice(error instanceof Error ? error.message : "虚拟机操作失败"); }
    finally { setVirtualBusy((current) => { const next = new Set(current); next.delete(key); return next; }); }
  };

  return <main className="app-shell devices-page">
    <div className="page-shell">
      <section className="records-hero devices-hero"><div><p className="eyebrow">STANDARD DEVICE POOL</p><h1>标准虚拟机管理</h1><p>统一管理900×1600、320 DPI的MediaFlow虚拟机；真机支持默认隐藏。</p><p className="workspace-page-notice" role="status">{notice}</p></div><div className="hero-actions"><button type="button" className="primary" onClick={() => setAddDeviceOpen(true)}>添加虚拟机</button><button type="button" className="secondary" disabled={scanning} onClick={() => void scan()}>{scanning ? "正在检索…" : "刷新虚拟机"}</button><button type="button" className="secondary" onClick={() => { setFailedImages(new Set()); setImageStamp(Date.now()); }}>刷新全部画面</button><a className="secondary" href="/">返回任务台</a><div className="device-settings-anchor"><button type="button" className="secondary" aria-expanded={deviceSettingsOpen} onClick={() => setDeviceSettingsOpen((value) => !value)}>设备设置</button>{deviceSettingsOpen && <div className="device-settings-popover"><strong>可选设备类型</strong><label className="capsule-switch"><span><b>启用真机支持</b><small>默认关闭；开启后显示真机板块和任务候选</small></span><input type="checkbox" checked={physicalEnabled} onChange={(event) => void savePhysicalPreference(event.target.checked)}/><i aria-hidden="true"/></label><small>关闭不会删除真机档案或历史记录。</small></div>}</div></div></section>

      <nav className="device-type-tabs" aria-label="设备类型"><button type="button" className={activeTab === "virtual" ? "active" : ""} onClick={() => setActiveTab("virtual")}>虚拟机</button>{physicalEnabled && <button type="button" className={activeTab === "physical" ? "active" : ""} onClick={() => setActiveTab("physical")}>真机</button>}</nav>

      {activeTab === "virtual" && <section className="panel standard-pool-panel"><div><p className="section-index">STANDARD POOL</p><h2>一键配置标准虚拟机池</h2><p>显示环境固定为 Android 15、900×1600、320 DPI、竖屏和AdbKeyboard。CPU、内存、帧率与静音可单独调整。</p></div><div className="standard-pool-controls"><label><span>目标数量</span><input type="number" min="1" max="50" value={poolTarget} onChange={(event) => { setPoolTarget(Math.max(1, Math.min(50, Number(event.target.value)))); setPoolPlan(null); }}/></label><label><span>配置方式</span><select value={poolMode} onChange={(event) => { setPoolMode(event.target.value as "supplement" | "reset"); setPoolPlan(null); }}><option value="supplement">补齐标准池（推荐）</option><option value="reset">删除全部并重建</option></select></label><button type="button" className="primary" disabled={poolBusy} onClick={() => void previewPool()}>{poolBusy ? "正在核对…" : "预览操作"}</button></div>{poolPlan && <div className={`standard-pool-preview ${poolPlan.mode}`}><strong>{poolPlan.mode === "supplement" ? `已有 ${poolPlan.standard_count} 台达标，将新建 ${poolPlan.create_count} 台` : `将永久删除 ${poolPlan.deletion_items.length} 台MuMu实例，再创建 ${poolPlan.create_count} 台`}</strong>{poolPlan.mode === "reset" && <><p className="danger-copy">不创建备份；这些实例中的账号、应用和数据不可恢复。任一删除结果不明确时会停止，不开始创建。</p><ul>{poolPlan.deletion_items.map((item) => <li key={item.provider_instance_id}><span>{item.name}</span><small>实例 {item.provider_instance_id} · {item.state === "stopped" ? "已停止" : "运行中"} · {item.managed ? "MediaFlow托管" : "未托管"}</small></li>)}</ul><label><span>输入“{poolPlan.confirmation_phrase}”确认</span><input value={poolConfirmation} onChange={(event) => setPoolConfirmation(event.target.value)}/></label></>}<button type="button" className={poolPlan.mode === "reset" ? "danger" : "primary"} disabled={poolBusy || (poolPlan.mode === "reset" && poolConfirmation !== poolPlan.confirmation_phrase)} onClick={() => void applyPool()}>{poolPlan.mode === "reset" ? "确认删除并重建" : poolPlan.create_count ? `创建 ${poolPlan.create_count} 台` : "确认当前数量"}</button></div>}</section>}

      {activeTab === "virtual" && !!status?.virtualization?.issues?.length && <section id="device-issues" className="panel device-issue-summary" aria-label="问题与待办">
        <div><p className="section-index">ISSUES & NEXT STEPS</p><h2>问题与待办</h2><small>每一项都说明卡在哪里、为什么没有继续，以及下一步怎么处理。</small></div>
        <div>{status.virtualization.issues.map((issue) => <a key={`${issue.virtual_device_id}-${issue.reason_code}`} href={`#virtual-${issue.virtual_device_id}`} className={`device-issue ${issue.issue_status || "waiting_user"}`}><span><strong>{issue.name}</strong><em>{issue.user_message}</em></span><small>{issue.suggested_action}</small></a>)}</div>
      </section>}

      {activeTab === "virtual" && !!status?.virtualization?.devices.length && <section className="panel virtual-inventory-panel">
        <div className="panel-heading"><div><p className="section-index">LOCAL VIRTUAL DEVICES</p><h2>本机虚拟机</h2><small className="section-note">MuMu关闭后仍会保留在这里；启动不会打开管理大厅。</small></div><div className="virtual-batch-actions"><span className="tag quiet">{status.virtualization.devices.length} 台已记录</span><button type="button" className="secondary" disabled={!selectedVirtuals.size} onClick={() => void batchVirtual("start")}>批量启动</button><button type="button" className="secondary" disabled={!selectedVirtuals.size} onClick={() => void batchVirtual("stop")}>批量停止</button></div></div>
        <div className="virtual-inventory-grid">{status.virtualization.devices.filter((item) => item.state !== "retired" || item.presence_status === "identity_conflict").map((virtualDevice) => {
          const onlineDevice = (virtualDevice.connected_device as DeviceStatus | null | undefined)
            || (virtualDevice.adb_endpoint ? online.find((item) => item.device_id === virtualDevice.adb_endpoint) : undefined);
          const busy = virtualBusy.has(virtualDevice.virtual_device_id) || virtualOperationIsActive(virtualDevice.active_operation);
          const unavailable = ["missing", "engine_unavailable", "identity_conflict"].includes(virtualDevice.presence_status || "");
          const stateLabel = unavailable ? virtualDevice.presence_status === "identity_conflict" ? "身份待确认" : virtualDevice.presence_status === "missing" ? "实例暂未找到" : "MuMu不可用" : onlineDevice ? virtualDevice.reason_code === "douyin_not_installed" ? "在线 · 等待安装抖音" : virtualDevice.task_ready ? "已就绪" : "在线 · 待复验" : busy ? virtualOperationStageLabel(virtualDevice.active_operation?.stage) : virtualDevice.state === "stopped" ? "已停止" : ["running", "starting", "adb_ready", "waiting_app"].includes(virtualDevice.state) ? "ADB未连接 · 可重试" : "未连接";
          const canControl = Boolean(onlineDevice && virtualDevice.available_actions?.includes("manual_control") && !runningDeviceIds.has(onlineDevice.device_id));
          return <article id={`virtual-${virtualDevice.virtual_device_id}`} className={`virtual-inventory-card ${unavailable ? "unavailable" : ""}`} key={virtualDevice.virtual_device_id}>
            <div><input className="virtual-select" type="checkbox" aria-label={`选择 ${virtualDevice.name}`} checked={selectedVirtuals.has(virtualDevice.virtual_device_id)} disabled={busy || unavailable} onChange={(event) => setSelectedVirtuals((current) => { const next = new Set(current); if (event.target.checked) next.add(virtualDevice.virtual_device_id); else next.delete(virtualDevice.virtual_device_id); return next; })}/><span className={`dot ${onlineDevice ? "online" : "paused"}`}/><span><strong>{virtualDevice.name}</strong><small>MuMu 实例 {virtualDevice.provider_instance_id} · {virtualDevice.discovery_source === "mediaflow_adopted" ? "已接管" : "MediaFlow创建"}</small></span><em>{stateLabel}</em></div>
            <div className={`virtual-guidance ${virtualDevice.issue_status || "normal"}`}><strong>{virtualDevice.user_message || virtualDevice.last_error || "正在读取设备状态"}</strong><span>{virtualDevice.suggested_action || (virtualDevice.last_connected_at ? `上次连接 ${new Date(virtualDevice.last_connected_at).toLocaleString()}` : "稍后刷新状态")}</span></div>
            <div className="virtual-inventory-actions">{onlineDevice ? <><button type="button" className="primary" onClick={() => setFocused({ device: onlineDevice, mode: "read_only" })}>打开画面</button>{virtualDevice.available_actions?.includes("continue_onboarding") && <button type="button" className="primary" disabled={busy} onClick={() => void continueVirtualOnboarding(virtualDevice)}>安装完成，继续检查</button>}{virtualDevice.available_actions?.includes("continue_initialization") && <button type="button" className="primary" disabled={busy} onClick={() => void initializationRequest(onlineDevice.device_id, onlineDevice.initialization?.status === "waiting_user" ? "continue" : "start")}>继续复验</button>}<button type="button" className="secondary" disabled={!canControl} title={canControl ? "取得设备独占锁后操作" : "任务或初始化期间只能观看"} onClick={() => setFocused({ device: onlineDevice, mode: "control" })}>{canControl ? "人工接管" : "当前只能观看"}</button>{virtualDevice.available_actions?.includes("configure_model") && <a className="secondary" href="/content#model-settings">前往配置模型</a>}</> : <button type="button" className="primary" disabled={busy || unavailable || virtualDevice.can_start === false} onClick={() => void startVirtual(virtualDevice)}>{busy ? `${virtualOperationStageLabel(virtualDevice.active_operation?.stage)}…` : ["running", "starting", "adb_ready", "waiting_app"].includes(virtualDevice.state) ? "重试连接" : virtualDevice.profile_status === "ready" ? "启动" : "启动并接入"}</button>}</div>
            {!!virtualDevice.readiness_steps?.length && <details className="virtual-readiness"><summary>查看就绪检查（{virtualDevice.readiness_steps.filter((step) => step.status === "ready").length}/{virtualDevice.readiness_steps.length}）</summary><ol>{virtualDevice.readiness_steps.map((step) => <li className={step.status} key={step.id}><span>{step.status === "ready" ? "✓" : step.status === "blocked" ? "!" : "·"}</span><p><strong>{step.label}</strong><small>{step.message}</small></p></li>)}</ol><footer>诊断编号 {virtualDevice.diagnostic_id || "—"}</footer></details>}
            <details className="virtual-more-actions"><summary>更多操作</summary><div>{virtualDevice.available_actions?.includes("repair_standard") && <button type="button" disabled={busy || virtualDevice.state !== "stopped"} onClick={() => void operateVirtual(virtualDevice, "repair_standard")}>恢复标准配置</button>}{onlineDevice || ["running", "starting", "adb_ready"].includes(virtualDevice.state) ? <><button type="button" disabled={busy} onClick={() => void operateVirtual(virtualDevice, "stop")}>停止</button><button type="button" disabled={busy} onClick={() => void operateVirtual(virtualDevice, "restart")}>重启</button></> : <><button type="button" disabled={busy || virtualDevice.state !== "stopped"} onClick={() => { const recipe = virtualDevice.recipe || {}; setSettingsForm({ cpu: Number(recipe.cpu) || 2, memory_gb: Number(recipe.memory_gb) || 1.75, fps: Number(recipe.fps) || 30, muted: recipe.muted !== false }); setSettingsTarget(virtualDevice); }}>修改性能</button><button type="button" disabled={busy || virtualDevice.state !== "stopped"} onClick={() => void operateVirtual(virtualDevice, "clone")}>克隆</button><button type="button" disabled={busy || virtualDevice.state !== "stopped"} onClick={() => void operateVirtual(virtualDevice, "backup")}>备份</button><button type="button" className="danger" disabled={busy || virtualDevice.state !== "stopped"} onClick={() => void deleteVirtual(virtualDevice)}>删除</button></>}</div></details>
          </article>;
        })}</div>
      </section>}

      {activeTab === "virtual" && !!backups.length && <details className="panel virtual-backups-panel"><summary>本机虚拟机备份（{backups.length}）</summary><p className="section-note">恢复会创建一台新的MediaFlow虚拟机，不覆盖原实例。</p><div className="virtual-backup-list">{backups.map((backup) => <div key={backup.id}><span><strong>{backup.source_name}</strong><small>{new Date(backup.created_at).toLocaleString()} · {(backup.size_bytes / 1024 / 1024).toFixed(1)} MB · 校验值 {backup.sha256.slice(0, 12)}…</small></span><button type="button" className="secondary" disabled={backup.status !== "completed" || virtualBusy.has(`backup:${backup.id}`)} onClick={() => void restoreBackup(backup)}>{virtualBusy.has(`backup:${backup.id}`) ? "正在恢复…" : "恢复为新虚拟机"}</button></div>)}</div></details>}

      {activeTab === "virtual" && !!unmanagedVirtuals.length && <details className="panel virtual-unmanaged-panel">
        <summary>发现 {unmanagedVirtuals.length} 台未托管 MuMu 虚拟机</summary>
        <p className="section-note">默认虚拟机和其他软件创建的实例不会进入任务。只有你明确接管后，MediaFlow才会改名并要求重新复验。</p>
        <div className="virtual-inventory-grid">{unmanagedVirtuals.map((item) => <article className="virtual-inventory-card" key={item.provider_instance_id}>
          <div><span className="dot paused"/><span><strong>{item.name || `MuMu实例${item.provider_instance_id}`}</strong><small>MuMu 实例 {item.provider_instance_id} · 未托管</small></span><em>{item.state === "stopped" ? "已停止" : "运行中"}</em></div>
          <div className="virtual-inventory-actions"><button type="button" className="secondary" disabled={virtualBusy.has(`unmanaged:${item.provider_instance_id}`)} onClick={() => void operateUnmanaged(item, item.state === "stopped" ? "start" : "stop")}>{item.state === "stopped" ? "启动" : "停止"}</button><button type="button" className="secondary" disabled={virtualBusy.has(`adopt:${item.provider_instance_id}`)} onClick={() => void adoptVirtual(item.provider_instance_id)}>{virtualBusy.has(`adopt:${item.provider_instance_id}`) ? "正在接管…" : "接管到MediaFlow"}</button><button type="button" className="danger" disabled={virtualBusy.has(`unmanaged:${item.provider_instance_id}`) || item.state !== "stopped"} onClick={() => void operateUnmanaged(item, "delete")}>删除</button></div>
        </article>)}</div>
      </details>}

      <section className="device-page-layout">
        <aside className="panel device-selector-panel"><div className="panel-heading"><div><p className="section-index">DEVICE HEALTH</p><h2>设备状态</h2><small className="section-note">这里只检查设备；当前草稿已选设备以标记显示。</small></div><span className="tag quiet device-selection-count">草稿已选 {selectedOnline.length} 台</span></div>
          <div className="device-options">{visibleDevices.map((device) => { const available = device.state === "device"; return <div key={device.device_id} className={available ? "device-option online" : "device-option unavailable"}><span className={available && selected.includes(device.device_id) ? "device-draft-check selected" : "device-draft-check"}>{available && selected.includes(device.device_id) ? "✓" : ""}</span><span><strong>{device.friendly_name || device.device_id}</strong><small>{device.model ? `${device.model} · ` : ""}{device.device_id}</small><small>{available ? device.profile_verified ? "在线 · 档案已验证" : "在线 · 档案待验证" : device.state === "unauthorized" ? "等待手机确认 USB 调试" : "设备离线"}</small></span><em>{available ? "可用" : "不可用"}</em></div>; })}{status && !visibleDevices.length && <div className="empty-device-action"><strong>{activeTab === "virtual" ? "暂无可执行的标准虚拟机" : "暂无已连接真机"}</strong><span>{activeTab === "virtual" ? "已停止或待修复的虚拟机仍保留在上方，可直接处理。" : "连接并初始化真机后会显示在这里。"}</span>{activeTab === "virtual" && !(status.virtualization?.devices.length) && <button type="button" className="primary" onClick={() => setAddDeviceOpen(true)}>添加虚拟机</button>}</div>}</div>
          <div className="device-page-actions"><a className="primary" href="/">在任务台选择设备</a></div>
        </aside>

        <section className="device-screen-section">
          <div className="device-screen-heading"><div><p className="section-index">LIVE DEVICE FRAMES</p><h2>当前设备画面与初始化</h2></div><span>{visibleOnline.length} 台在线</span></div>
          <div className="device-screen-grid">{visibleOnline.map((device) => {
            const init = device.initialization;
            const state = init?.status || device.initialization_status || "uninitialized";
            const busy = initializing.has(device.device_id);
            const progress = init ? Math.round((init.progress_current / Math.max(init.progress_total, 1)) * 100) : 0;
            const virtualDevice = virtualByAdb.get(device.device_id);
            const isMuMu = virtualDevice?.provider === "mumu";
            const taskRunning = runningDeviceIds.has(device.device_id);
            const initializationBusy = state === "queued" || state === "running" || state === "waiting_user";
            const canControl = isMuMu && !taskRunning && !initializationBusy;
            const displayName = device.friendly_name || virtualDevice?.name || device.device_id;
            return <article className="device-screen-card" key={device.device_id}>
              <div className="device-screen-title"><div><strong>{displayName}</strong><small>{device.model || "Android 设备"}</small></div><span className={`initialization-chip ${state}`}>{initializationLabels[state] || state}</span></div>
              {isMuMu && focused?.device.device_id !== device.device_id
                ? <DeviceLiveView deviceId={device.device_id} deviceName={displayName} profile="wall" mode="read_only" onOpenNativeWindow={() => void openNativeWindow(virtualDevice.virtual_device_id)}/>
                : !isMuMu && <div className="device-screen-shell">{!failedImages.has(device.device_id) && <img src={`${API}/api/device-image?device_id=${encodeURIComponent(device.device_id)}&t=${imageStamp}`} alt={`${displayName} 当前画面`} onLoad={() => setFailedImages((current) => { const next = new Set(current); next.delete(device.device_id); return next; })} onError={() => setFailedImages((current) => new Set(current).add(device.device_id))}/>}<div className="device-screen-placeholder"><span>等待设备画面</span><small>{failedImages.has(device.device_id) ? "截图暂不可用，可点击刷新重试" : "正在读取…"}</small></div></div>}
              {isMuMu && <div className="device-view-actions"><button type="button" className="secondary" onClick={() => setFocused({ device, mode: "read_only" })}>打开画面</button><button type="button" className="primary" disabled={!canControl} title={canControl ? "取得设备独占锁后操作" : "任务或初始化期间只能观看"} onClick={() => setFocused({ device, mode: "control" })}>{canControl ? "人工接管" : "当前只能观看"}</button></div>}
              <div className="device-screen-meta"><span>{device.device_id}</span><b>{isMuMu ? "MuMu · 实时/截图自动切换" : "真机 · 每5秒截图"}</b></div>
              <div className="initialization-panel">{init && <><div className="initialization-progress"><span style={{ width: `${progress}%` }}/></div><p><strong>{initializationLabels[state] || state}</strong><small>{init.message}</small></p>{init.error && state !== "waiting_user" && <em>{init.error}</em>}</>}<label className="write-acceptance-toggle"><input type="checkbox" checked={Boolean(writeAcceptance[device.device_id])} disabled={state === "queued" || state === "running"} onChange={(event) => setWriteAcceptance((current) => ({ ...current, [device.device_id]: event.target.checked }))}/><span>完整写入验收</span><small>默认关闭；开启会发送 1 条真实测试评论且不会自动删除</small></label><div className="initialization-actions">{!isMuMu && <a className="secondary" href="/devices/guide">查看Agent初始化指南</a>}{state === "waiting_user" ? <button type="button" className="primary" disabled={busy} onClick={() => void initializationRequest(device.device_id, "continue")}>{isMuMu ? "继续初始化" : "继续人工步骤"}</button> : state === "queued" || state === "running" ? <button type="button" className="secondary" disabled={busy} onClick={() => void initializationRequest(device.device_id, "cancel")}>安全取消</button> : <button type="button" className="primary" disabled={busy} onClick={() => void initializationRequest(device.device_id, "start")}>{state === "ready" ? "重新校准" : isMuMu ? "开始初始化" : "按指南开始初始化"}</button>}{init?.report_path && <a className="secondary" href={`${API}/api/devices/${encodeURIComponent(device.device_id)}/initialization/report`} target="_blank" rel="noreferrer">查看报告</a>}</div></div>
            </article>;
          })}{!visibleOnline.length && <div className="empty-screen-grid"><strong>{activeTab === "virtual" ? "还没有在线标准虚拟机" : "还没有在线真机"}</strong><span>{activeTab === "virtual" ? "可在上方启动或创建MediaFlow标准虚拟机。" : "连接并授权真机后点击刷新。"}</span></div>}</div>
        </section>
      </section>
      <footer><span>MediaFlow · 设备工作区</span><span>只读画面 · 不触发设备动作</span></footer>
      <DeviceOnboardingDialog open={addDeviceOpen} source="devices" onClose={() => { setAddDeviceOpen(false); void readStatus(false); }}/>
      {settingsTarget && <div className="device-focus-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSettingsTarget(null); }}><form className="virtual-settings-dialog" onSubmit={(event) => { event.preventDefault(); void saveVirtualSettings(); }}><header><div><p className="section-index">VIRTUAL DEVICE PERFORMANCE</p><h2>调整 {settingsTarget.name} 性能</h2><small>只有停止状态可以修改；保存后会从MuMu完整回读确认。</small></div><button type="button" className="icon-button" aria-label="关闭配置" onClick={() => setSettingsTarget(null)}>×</button></header><div className="locked-standard-summary"><strong>显示环境已锁定</strong><span>Android 15 · 900×1600 · 320 DPI · 竖屏 · Root · AdbKeyboard</span></div><div className="virtual-settings-grid">{([['cpu','CPU核心'],['memory_gb','内存 GB'],['fps','帧率']] as const).map(([key, label]) => <label key={key}><span>{label}</span><input type="number" step={key === 'memory_gb' ? '0.25' : '1'} value={settingsForm[key]} onChange={(event) => setSettingsForm((current) => ({ ...current, [key]: Number(event.target.value) }))}/></label>)}</div><div className="virtual-settings-toggles"><label><input type="checkbox" checked={settingsForm.muted} onChange={(event) => setSettingsForm((current) => ({ ...current, muted: event.target.checked }))}/>静音</label></div><footer><button type="button" className="secondary" onClick={() => setSettingsTarget(null)}>取消</button><button type="submit" className="primary" disabled={virtualBusy.has(settingsTarget.virtual_device_id)}>保存并回读</button></footer></form></div>}
      {focused && <div className="device-focus-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setFocused(null); }}><section className="device-focus-dialog" role="dialog" aria-modal="true" aria-label={`${focused.device.friendly_name || focused.device.device_id} 设备画面`}><header><div><p className="section-index">MUMU LIVE VIEW</p><h2>{focused.device.friendly_name || focused.device.device_id}</h2><small>{focused.mode === "control" ? "人工接管已取得设备独占锁" : "只读观看，不会向设备发送输入"}</small></div><button type="button" className="icon-button" aria-label="关闭画面" onClick={() => setFocused(null)}>×</button></header><DeviceLiveView deviceId={focused.device.device_id} deviceName={focused.device.friendly_name || focused.device.device_id} profile="focus" mode={focused.mode} onOpenNativeWindow={() => { const virtualDevice = virtualByAdb.get(focused.device.device_id); if (virtualDevice) void openNativeWindow(virtualDevice.virtual_device_id); }}/></section></div>}
    </div>
  </main>;
}
