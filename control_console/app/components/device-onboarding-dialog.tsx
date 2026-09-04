"use client";
/* The backdrop deliberately handles outside-click dismissal; the dialog stops propagation. */
/* eslint-disable jsx-a11y/no-noninteractive-element-interactions */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchLocalApi } from "../lib/local-api";
import { virtualOperationIsActive, virtualOperationStageLabel, waitForInitialization, waitForVirtualOperation, type InitializationOperation, type VirtualOperation } from "../lib/virtual-device-operations";

const API = "http://127.0.0.1:48138";

type PhysicalDevice = {
  device_id: string;
  device_type: "physical";
  state: string;
  friendly_name?: string;
  model?: string;
  initialization?: { status: string; message: string } | null;
};

type Provider = {
  status: "missing" | "ready" | "incompatible" | "failed";
  compatible: boolean;
  version?: string;
  manager_path?: string;
  install_root?: string;
  detection_source?: string;
  compatibility_status?: string;
  action_required?: string;
  download_url: string;
  message: string;
};

type Snapshot = {
  physical_devices: PhysicalDevice[];
  virtual_devices: Array<{
    virtual_device_id: string;
    adb_endpoint?: string;
    name: string;
    state: string;
    initialization?: { status: string; message: string } | null;
  }>;
  agent_guide_url: string;
  agent_document: string;
  mumu: { provider: Provider; instances: Array<{ provider_instance_id: string; name: string; state: string }> ; recipe: Record<string, unknown> };
};

const detectionSourceLabel = (source?: string) => ({
  last_confirmed: "上次确认位置",
  user_selected: "手动选择",
  uninstall_registry: "Windows安装记录",
  app_paths: "Windows应用路径",
  start_menu: "开始菜单",
  running_process: "正在运行的程序",
  common_location: "常用安装位置",
  standard_location: "常用安装位置",
  fixed_drive_standard_location: "固定磁盘常用位置",
  configured: "已配置位置",
}[source || ""] || "自动检测");

export function DeviceOnboardingDialog({
  open,
  source,
  onClose,
  onDeviceReady,
}: {
  open: boolean;
  source: "workbench" | "devices";
  onClose: () => void;
  onDeviceReady?: (deviceId: string) => void | Promise<void>;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("正在读取本机设备状态…");
  const [mumuPath, setMumuPath] = useState("");
  const [desktopBridge, setDesktopBridge] = useState(false);
  const [mumuInstaller, setMumuInstaller] = useState("");
  const [operation, setOperation] = useState<VirtualOperation | null>(null);
  const [initializing, setInitializing] = useState<string | null>(null);
  const scanInFlight = useRef(false);

  const scan = useCallback(async (pathOverride = "") => {
    if (scanInFlight.current) return;
    scanInFlight.current = true;
    setLoading(true);
    try {
      const response = await fetchLocalApi(`${API}/api/device-onboarding/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mumu_path: pathOverride.trim() || undefined }),
      }, 20_000);
      const result = await response.json() as Snapshot & { error?: string };
      if (!response.ok) throw new Error(result.error || "设备检测失败");
      setSnapshot(result);
      setNotice(result.physical_devices.length ? `检测到 ${result.physical_devices.length} 台真机` : result.mumu.provider.message);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "本机设备服务不可用");
    } finally {
      scanInFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    const start = window.setTimeout(() => void scan(), 0);
    return () => window.clearTimeout(start);
  }, [open, scan]);

  useEffect(() => {
    if (!open) return;
    const host = (window as unknown as { chrome?: { webview?: { postMessage(message: string): void; addEventListener(type: string, listener: (event: MessageEvent) => void): void; removeEventListener(type: string, listener: (event: MessageEvent) => void): void } } }).chrome?.webview;
    const sync = window.setTimeout(() => setDesktopBridge(Boolean(host)), 0);
    if (!host) return () => window.clearTimeout(sync);
    const receive = (event: MessageEvent) => {
      const message = event.data as { type?: string; path?: string; error?: string } | null;
      if (!message) return;
      if (message.type === "folder-selected" && message.path) {
        setMumuPath(message.path);
        void scan(message.path);
      } else if (message.type === "mumu-installer-selected" && message.path) {
        setMumuInstaller(message.path);
        setNotice("已验证MuMu官方安装程序；点击运行安装程序后仍需完成Windows安装向导");
      } else if (message.type === "mumu-installer-started") {
        setNotice("MuMu安装程序已启动。安装完成后MediaFlow会自动重新检测");
      } else if (message.type === "mumu-installer-rejected") {
        setNotice(message.error || "所选文件未通过MuMu安装程序校验");
      }
    };
    host.addEventListener("message", receive);
    return () => {
      window.clearTimeout(sync);
      host.removeEventListener("message", receive);
    };
  }, [open, scan]);

  const postDesktop = (message: string) => {
    const host = (window as unknown as { chrome?: { webview?: { postMessage(value: string): void } } }).chrome?.webview;
    host?.postMessage(message);
  };

  useEffect(() => {
    if (!open || snapshot?.mumu.provider.status !== "missing") return;
    const timer = window.setInterval(() => void scan(mumuPath), 5000);
    return () => window.clearInterval(timer);
  }, [mumuPath, open, scan, snapshot?.mumu.provider.status]);

  useEffect(() => {
    if (!open || operation?.status !== "completed") return;
    const timer = window.setInterval(() => void scan(), 4000);
    return () => window.clearInterval(timer);
  }, [open, operation?.status, scan]);

  const physical = snapshot?.physical_devices || [];
  const provider = snapshot?.mumu.provider;
  const busy = loading || virtualOperationIsActive(operation);
  const operationLabel = operation ? virtualOperationStageLabel(operation.stage) : "";

  const startPhysical = async (deviceId: string) => {
    setInitializing(deviceId);
    try {
      const response = await fetchLocalApi(`${API}/api/devices/${encodeURIComponent(deviceId)}/initializations`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ search_query: "人工智能", write_acceptance: false }),
      }, 20_000);
      const result = await response.json() as { initialization?: InitializationOperation; error?: string };
      if (!response.ok || !result.initialization) throw new Error(result.error || "初始化启动失败");
      const completed = await waitForInitialization(API, deviceId, result.initialization, (next) => {
        setNotice(`${next.message || "正在初始化"} · ${next.progress_current}/${next.progress_total}`);
      });
      setNotice(completed.status === "ready" ? "初始化和零写入自检已完成" : completed.message || completed.error || "初始化已收口");
      await scan();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "初始化启动失败");
    } finally {
      setInitializing(null);
    }
  };

  const continuePhysical = async (deviceId: string) => {
    setInitializing(deviceId);
    try {
      const response = await fetchLocalApi(`${API}/api/devices/${encodeURIComponent(deviceId)}/initialization/continue`, { method: "POST" }, 20_000);
      const result = await response.json() as { initialization?: InitializationOperation; error?: string };
      if (!response.ok || !result.initialization) throw new Error(result.error || "初始化继续失败");
      const completed = await waitForInitialization(API, deviceId, result.initialization, (next) => {
        setNotice(`${next.message || "正在继续初始化"} · ${next.progress_current}/${next.progress_total}`);
      });
      setNotice(completed.status === "ready" ? "初始化和零写入自检已完成" : completed.message || completed.error || "初始化已收口");
      await scan();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "初始化继续失败");
    } finally {
      setInitializing(null);
    }
  };

  const physicalAction = async (device: PhysicalDevice) => {
    const status = device.initialization?.status;
    if (status === "ready") {
      await onDeviceReady?.(device.device_id);
      setNotice(source === "workbench" ? "设备已加入当前任务草稿；尚未提交任务" : "设备已就绪");
      return;
    }
    if (status === "waiting_user") {
      await continuePhysical(device.device_id);
      return;
    }
    await startPhysical(device.device_id);
  };

  const copyAgentDocument = async () => {
    if (!snapshot?.agent_document) return;
    await navigator.clipboard.writeText(snapshot.agent_document);
    setNotice("Agent初始化文档已复制");
  };

  const createVirtual = async () => {
    setLoading(true);
    try {
      const idempotencyKey = crypto.randomUUID();
      const response = await fetchLocalApi(`${API}/api/virtual-devices`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mumu_path: mumuPath.trim() || undefined, idempotency_key: idempotencyKey }),
      }, 30_000);
      const result = await response.json() as { operation?: VirtualOperation; error?: string };
      if (!response.ok || !result.operation) throw new Error(result.error || "虚拟机创建失败");
      setOperation(result.operation);
      setNotice("已开始创建；关闭弹窗也不会重复创建或提交正式任务");
      const completed = await waitForVirtualOperation(API, result.operation, (next) => {
        setOperation(next);
        setNotice(`${virtualOperationStageLabel(next.stage)} · ${next.progress}%`);
      });
      setOperation(completed);
      if (completed.status === "failed") throw new Error(completed.error || "虚拟机创建失败");
      setNotice(completed.status === "waiting_user" ? completed.error || "需要你完成当前步骤" : completed.result?.adb_endpoint ? "虚拟机已交给初始化流程；完成后即可加入任务" : "虚拟机已创建");
      await scan(mumuPath);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "虚拟机创建失败");
    } finally {
      setLoading(false);
    }
  };

  const continueVirtual = async () => {
    if (!operation) return;
    setLoading(true);
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-device-operations/${encodeURIComponent(operation.id)}/continue`, { method: "POST" }, 20_000);
      const result = await response.json() as { operation?: VirtualOperation; error?: string };
      if (!response.ok || !result.operation) throw new Error(result.error || "抖音安装状态检查失败");
      const resumed = { ...result.operation, status: "running" as const, stage: "checking_app_install", progress: 75, error: null };
      setOperation(resumed);
      const completed = await waitForVirtualOperation(API, resumed, (next) => {
        setOperation(next);
        setNotice(`${virtualOperationStageLabel(next.stage)} · ${next.progress}%`);
      });
      setOperation(completed);
      setNotice(completed.status === "failed" ? completed.error || "虚拟机接入失败" : completed.status === "waiting_user" ? completed.error || "需要你处理当前步骤" : "虚拟机接入操作已完成");
      await scan(mumuPath);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "抖音安装状态检查失败");
    } finally {
      setLoading(false);
    }
  };

  const virtualDevice = snapshot?.virtual_devices.find((device) => device.adb_endpoint === operation?.result?.adb_endpoint);
  const virtualInitialization = virtualDevice?.initialization;

  if (!open) return null;
  return <div className="device-onboarding-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="device-onboarding-dialog" role="dialog" aria-modal="true" aria-labelledby="add-device-title" onMouseDown={(event) => event.stopPropagation()}>
      <header className="device-onboarding-head">
        <div><p className="section-index">ADD DEVICE</p><h2 id="add-device-title">添加设备</h2><p>接入完成只会加入{source === "workbench" ? "当前任务草稿" : "设备列表"}，不会自动开始正式任务。</p></div>
        <button type="button" className="icon-button" aria-label="关闭" onClick={onClose}>×</button>
      </header>
      <div className="device-onboarding-choices">
        <article className="device-onboarding-choice">
          <span className="device-choice-icon">机</span><h3>连接真机</h3>
          <p>插入USB、解锁屏幕，并在对应手机上确认USB调试授权。</p>
          <ol><li>打开开发者选项和USB调试</li><li>保持屏幕解锁并确认授权</li><li>选择设备开始只读初始化</li></ol>
          <div className="onboarding-device-list">
            {physical.map((device) => {
              const initializationStatus = device.initialization?.status;
              const active = initializationStatus === "queued" || initializationStatus === "running";
              const label = initializing === device.device_id ? "处理中…" : initializationStatus === "ready" ? source === "workbench" ? "加入草稿" : "已就绪" : initializationStatus === "waiting_user" ? "继续" : active ? "初始化中" : "初始化";
              return <div key={device.device_id}><span><strong>{device.friendly_name || device.device_id}</strong><small>{device.state === "unauthorized" ? "等待手机确认USB调试" : device.state === "device" ? device.initialization?.message || "已授权，可开始初始化" : "设备离线"}</small></span><button type="button" className="secondary" disabled={device.state !== "device" || initializing === device.device_id || active || (initializationStatus === "ready" && !onDeviceReady)} onClick={() => void physicalAction(device)}>{label}</button></div>;
            })}
            {!physical.length && <p className="onboarding-empty">暂未检测到真机。插好后点击重新检测。</p>}
          </div>
          <div className="device-choice-actions"><button type="button" className="primary" disabled={loading} onClick={() => void scan(mumuPath)}>{loading ? "检测中…" : "开始检测真机"}</button><a className="secondary" href="/devices/guide">查看接入教程</a><button type="button" className="text-button" onClick={() => void copyAgentDocument()}>复制Agent初始化文档</button></div>
        </article>
        <div className="device-choice-or" aria-hidden="true"><span>或者</span></div>
        <article className="device-onboarding-choice virtual">
          <span className="device-choice-icon">虚</span><h3>添加虚拟机</h3>
          <p>MuMu安装和抖音登录需要你确认；创建、配置、ADB连接、初始化与自检由MediaFlow接手。</p>
          {!provider && <div className="onboarding-provider-state">正在检测MuMu…</div>}
          {provider?.status === "missing" && <><div className="onboarding-provider-state warning"><strong>尚未检测到已安装的MuMu</strong><small>下载安装包只是下载，仍需运行安装程序并完成安装。完成后这里每5秒自动检查一次。</small></div><a className="primary" href={provider.download_url} target="_blank" rel="noreferrer">前往MuMu官方下载</a>{desktopBridge && <><button type="button" className="secondary" onClick={() => postDesktop("mediaflow:choose-mumu-installer")}>选择已下载的安装程序</button>{mumuInstaller && <div className="onboarding-provider-state"><strong>安装程序已验证</strong><small>{mumuInstaller}</small><button type="button" className="primary" onClick={() => postDesktop("mediaflow:launch-mumu-installer")}>运行MuMu安装程序</button></div>}</>}{!desktopBridge && <small>浏览器不能直接运行本地安装程序。请在下载完成后双击安装，再回到这里重新检测。</small>}</>}
          {provider?.status === "incompatible" && <div className="onboarding-provider-state warning"><strong>当前版本暂不兼容</strong><small>{provider.message}</small></div>}
          {provider?.status === "failed" && <div className="onboarding-provider-state danger"><strong>MuMu连接失败</strong><small>{provider.message}</small></div>}
          {provider?.compatible && <div className="onboarding-provider-state ready"><strong>MuMu {provider.version || "新版"} 已连接</strong><small>{provider.compatibility_status === "capability_verified" ? "该版本尚未列入已测清单，但所需管理能力已通过复验。" : "版本与管理能力已验证。"} MediaFlow只管理由平台创建或你明确接管的实例。</small>{provider.install_root && <small>位置：{provider.install_root}（{detectionSourceLabel(provider.detection_source)}）</small>}</div>}
          <label className="field"><span>MuMu安装目录（自动检测不到时填写）</span><input value={mumuPath} onChange={(event) => setMumuPath(event.target.value)} placeholder="例如 D:\MuMuPlayer"/></label>
          {desktopBridge && <button type="button" className="text-button" onClick={() => postDesktop("mediaflow:choose-folder")}>选择MuMu安装目录</button>}
          <button type="button" className="secondary" disabled={loading} onClick={() => void scan(mumuPath)}>我已安装，立即检测</button>
          {provider?.compatible && <><p className="section-note">系统会按本机顺序命名为 MediaFlow虚拟机1、2、3……删除后编号也不会重复。</p><button type="button" className="primary" disabled={busy} onClick={() => void createVirtual()}>{busy ? "处理中…" : "添加虚拟机"}</button></>}
          {operation && <div className={`virtual-operation ${operation.status}`}><div><strong>{operationLabel}</strong><span>{operation.progress}%</span></div><i><span style={{ width: `${operation.progress}%` }}/></i>{operation.error && <small>{operation.error}</small>}{operation.status === "waiting_user" && <button type="button" className="primary" onClick={() => void continueVirtual()}>我已安装抖音，继续初始化</button>}{operation.status === "completed" && operation.result?.adb_endpoint && <>{virtualInitialization?.status === "waiting_user" ? <button type="button" className="primary" onClick={() => void continuePhysical(operation.result!.adb_endpoint!)}>处理完成，继续初始化</button> : virtualInitialization?.status === "ready" ? <button type="button" className="secondary" disabled={!onDeviceReady} onClick={() => void onDeviceReady?.(operation.result!.adb_endpoint!)}>{source === "workbench" ? "加入当前任务草稿" : "虚拟机已就绪"}</button> : <small>{virtualInitialization?.message || "初始化与3条零写入自检正在进行，完成后才可加入任务"}</small>}</>}</div>}
        </article>
      </div>
      <footer className="device-onboarding-footer"><span role="status">{notice}</span><button type="button" className="secondary" onClick={onClose}>完成</button></footer>
    </section>
  </div>;
}
