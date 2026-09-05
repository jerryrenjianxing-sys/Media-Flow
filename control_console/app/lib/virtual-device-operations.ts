import { fetchLocalApi } from "./local-api";

export type VirtualOperation = {
  id: string;
  status: "queued" | "running" | "waiting_user" | "completed" | "failed" | "cancelled";
  stage: string;
  progress: number;
  result?: { adb_endpoint?: string | null; name?: string; initialization_id?: string } | null;
  error?: string | null;
  message?: string;
  retryable?: boolean;
  deadline_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type InitializationOperation = {
  id: string;
  status: "queued" | "running" | "waiting_user" | "ready" | "failed" | "cancelled";
  stage: string;
  progress_current: number;
  progress_total: number;
  message: string;
  error?: string | null;
};

export const virtualOperationIsActive = (operation?: VirtualOperation | null) =>
  operation?.status === "queued" || operation?.status === "running";

export const virtualOperationStageLabel = (stage?: string) => ({
  queued: "等待处理",
  creating: "正在创建虚拟机",
  template_apks: "正在校验抖音安装文件",
  template_creating: "正在创建干净模板",
  template_installing: "正在预装软件与控制组件",
  template_sealing: "正在停止并封存模板",
  template_verifying: "正在检查模板完整性",
  starting_mumu: "正在启动MuMu",
  waiting_android: "正在等待Android启动",
  connecting_adb: "正在连接ADB",
  verifying_identity: "正在核对设备身份",
  adb_ready: "ADB已连接",
  waiting_app_install: "等待安装抖音",
  initialization_queued: "设备准备已排队",
  adopted_requires_verification: "已接管，等待复验",
  applying_settings: "正在写入并回读配置",
  cloning: "正在克隆虚拟机",
  backing_up: "正在备份虚拟机",
  backup_verified: "备份校验完成",
  restoring: "正在恢复为新虚拟机",
  deleting: "正在删除虚拟机",
  stopping: "正在停止虚拟机",
  restarting: "正在重启虚拟机",
  unknown_result_after_restart: "操作结果等待确认",
  engine_unavailable_after_restart: "MuMu引擎不可用",
  interrupted_before_start: "启动已中断",
  adb_unavailable_after_restart: "ADB连接失败",
  completed: "操作已完成",
  ready: "已就绪",
  failed: "操作失败",
}[stage || ""] || stage || "处理中");

export async function waitForVirtualOperation(
  api: string,
  initial: VirtualOperation,
  onUpdate?: (operation: VirtualOperation) => void,
  options: { intervalMs?: number; maxAttempts?: number } = {},
) {
  const intervalMs = options.intervalMs ?? 1000;
  const deadline = initial.deadline_at ? Date.parse(initial.deadline_at) : Date.now() + 240_000;
  const maxAttempts = options.maxAttempts ?? Math.max(1, Math.min(1800, Math.ceil((deadline - Date.now()) / intervalMs)));
  let operation = initial;
  let failures = 0;
  for (let attempt = 0; attempt < maxAttempts && virtualOperationIsActive(operation); attempt += 1) {
    if (Date.now() >= deadline) throw new Error("操作已达到等待期限，请查看保留的现场；不会自动重复创建");
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
    try {
      const response = await fetchLocalApi(
        `${api}/api/virtual-device-operations/${encodeURIComponent(operation.id)}`,
        { cache: "no-store" },
        8_000,
      );
      const payload = await response.json() as { operation?: VirtualOperation; error?: string };
      if (!response.ok || !payload.operation) throw new Error(payload.error || "操作状态读取失败");
      operation = payload.operation;
      failures = 0;
      onUpdate?.(operation);
    } catch (error) {
      failures += 1;
      if (failures >= 3) throw error;
    }
  }
  if (virtualOperationIsActive(operation)) {
    throw new Error("操作仍未收口，请刷新状态；MediaFlow不会重复发送启动命令");
  }
  return operation;
}

export async function waitForInitialization(
  api: string,
  deviceId: string,
  initial: InitializationOperation,
  onUpdate?: (operation: InitializationOperation) => void,
  options: { intervalMs?: number; maxAttempts?: number } = {},
) {
  const intervalMs = options.intervalMs ?? 2000;
  const maxAttempts = options.maxAttempts ?? 360;
  let operation = initial;
  let failures = 0;
  const isActive = () => operation.status === "queued" || operation.status === "running";
  for (let attempt = 0; attempt < maxAttempts && isActive(); attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
    try {
      const response = await fetchLocalApi(
        `${api}/api/devices/${encodeURIComponent(deviceId)}/initialization`,
        { cache: "no-store" },
        8_000,
      );
      const payload = await response.json() as { initialization?: InitializationOperation; error?: string };
      if (!response.ok || !payload.initialization) throw new Error(payload.error || "初始化状态读取失败");
      operation = payload.initialization;
      failures = 0;
      onUpdate?.(operation);
    } catch (error) {
      failures += 1;
      if (failures >= 3) throw error;
    }
  }
  if (isActive()) throw new Error("初始化等待超时，按钮已恢复；刷新后可根据真实状态继续");
  return operation;
}
