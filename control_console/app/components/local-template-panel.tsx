"use client";
import { useCallback, useEffect, useState } from "react";
import { fetchLocalApi } from "../lib/local-api";
import { type VirtualOperation, waitForVirtualOperation } from "../lib/virtual-device-operations";

const API = "http://127.0.0.1:48138";
type TemplateState = { status?: string; message?: string; app_version?: string; template_version?: string; operation?: VirtualOperation; source?: string; sha256?: string; private_data_possible?: boolean };

export function LocalTemplatePanel() {
  const [state, setState] = useState<TemplateState>({});
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [directory, setDirectory] = useState("");
  const [privateManifest, setPrivateManifest] = useState("");
  const [resumeInstanceId, setResumeInstanceId] = useState("");
  const refresh = useCallback(async () => {
    const response = await fetchLocalApi(`${API}/api/virtual-device-template`, { cache: "no-store" }, 8000);
    if (!response.ok) throw new Error("模板状态读取失败，请刷新重试");
    setState(await response.json() as TemplateState);
  }, []);
  useEffect(() => {
    let cancelled = false, failures = 0;
    let timer: number | undefined;
    const poll = async () => {
      try { await refresh(); failures = 0; }
      catch (error) { failures += 1; if (!cancelled) setNotice(error instanceof Error ? error.message : "读取失败"); }
      if (!cancelled && failures < 3) timer = window.setTimeout(() => { void poll(); }, 5000);
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [refresh]);
  const prepare = async () => {
    const rebuild = Boolean(state.status) && !privateManifest.trim();
    if (privateManifest.trim() && !window.confirm("私人快照可能包含缓存和账号标识，仅供私人测试。验证成功后切换默认模板，保留旧模板和全部实例。继续吗？")) return;
    if (rebuild && !window.confirm("将新建一个干净模板，旧模板与现有虚拟机全部保留。继续吗？")) return;
    setBusy(true); setNotice("正在提交模板准备…");
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-device-template`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID(), rebuild,
          confirmation: privateManifest.trim() ? "导入私人快照，保留旧模板和实例" : "新建干净模板，保留旧实例", import_directory: directory.trim() || undefined,
          private_manifest: privateManifest.trim() || undefined, resume_instance_id: resumeInstanceId.trim() || undefined }),
      }, 15000);
      const result = await response.json() as { operation?: VirtualOperation; error?: string };
      if (!response.ok || !result.operation) throw new Error(result.error || "无法准备模板");
      const operation = await waitForVirtualOperation(API, result.operation,
        (value) => setNotice(`${value.message || "正在准备"} · ${value.progress}%`), { maxAttempts: 1800 });
      if (operation.status !== "completed") throw new Error(operation.error || operation.message || "模板未完成，现场已保留");
      setNotice("模板已完成；以后新增虚拟机自动复制，请在新实例中自行登录。");
      await refresh();
    } catch (error) { setNotice(error instanceof Error ? error.message : "模板操作失败"); }
    finally { setBusy(false); }
  };
  const stopAll = async (stopped: boolean) => {
    try {
      const response = await fetchLocalApi(`${API}/api/automation-stop`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stopped }) }, 15000);
      const result = await response.json() as { error?: string };
      if (!response.ok) throw new Error(result.error || "操作失败");
      setNotice(stopped ? "已请求安全停止全部自动操作；执行者将在安全检查点停止。" : "设备维护已恢复，业务队列仍保持暂停。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "操作失败"); }
  };
  const cancel = async () => {
    try {
      const response = await fetchLocalApi(`${API}/api/virtual-device-template/cancel`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operation_id: state.operation?.id }) });
      const result = await response.json() as { error?: string };
      if (!response.ok) throw new Error(result.error || "取消请求失败");
      setNotice("已请求安全取消，当前命令完成后停止；保留已创建的实例，请勿重复创建。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "取消请求失败"); }
  };
  return <section className="panel"><h2>本机标准模板</h2>
    <p>{state.message || "首次添加时自动建立模板：只复制抖音安装文件，不复制账号和应用数据。"}</p>
    {state.app_version && <p>抖音 {state.app_version} · 模板 {state.template_version?.slice(0, 8)}</p>}
    {state.source === "private_snapshot" && <p role="note">私人快照 · 可能包含缓存及账号标识，不是公共干净模板。版本 {state.template_version} · SHA-256 {state.sha256?.slice(0, 12)}…</p>}
    {state.operation && <p>{state.operation.message} · {state.operation.progress}%（关闭页面后可回来查看）</p>}
    {state.operation && ["queued", "running"].includes(state.operation.status) && <button type="button" className="secondary" onClick={() => void cancel()}>安全取消，保留现场</button>}
    <p role="status">{notice}</p>
    <button type="button" className="primary" disabled={busy || Boolean(state.operation)} onClick={() => void prepare()}>{busy ? "正在准备…" : state.status ? "重建本机模板" : "准备本机模板"}</button>
    <button type="button" className="secondary" onClick={() => void refresh().catch(() => setNotice("读取失败，请检查后台"))}>刷新模板状态</button>
    <details><summary>导入完整安装包</summary><label htmlFor="template-apk-directory">本机APK目录（包括所有必要分包）</label><input id="template-apk-directory" value={directory} onChange={(event) => setDirectory(event.target.value)}/><p>填好后点击准备或重建模板；只读取此目录中的APK。</p></details>
    <details><summary>导入私人快照</summary><label htmlFor="private-template-manifest">私人安装包提取的 manifest.json 完整路径</label><input id="private-template-manifest" value={privateManifest} onChange={(event) => setPrivateManifest(event.target.value)}/><p>载荷必须与清单在同一目录。验证成功才切换默认；失败保留原默认和现场，不会重复导入。</p><label htmlFor="private-template-resume">接续核验实例号（通常留空；只核验上次导入现场，不重新导入）</label><input id="private-template-resume" inputMode="numeric" value={resumeInstanceId} onChange={(event) => setResumeInstanceId(event.target.value)}/><button type="button" disabled={busy || Boolean(state.operation) || !privateManifest.trim()} onClick={() => void prepare()}>验证并设为默认模板</button></details>
    <details><summary>自动操作开关</summary><p>暂停任务只暂停业务；停止所有自动操作同时停止维护。恢复后业务仍暂停。</p><button type="button" onClick={() => void stopAll(true)}>停止所有自动操作</button><button type="button" onClick={() => void stopAll(false)}>恢复设备维护</button></details>
  </section>;
}
