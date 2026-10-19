"use client";

import {useDesktopState, type DesktopState} from "../lib/desktop-bridge";

const labels = {
  idle: "尚未检查", checking: "正在检查更新", available: "发现新版本",
  downloading: "正在下载更新", verifying: "正在校验安装包", waiting_idle: "等待任务与设备操作空闲",
  countdown: "即将重启安装", installing: "正在打开安装程序", current: "已是最新版本", error: "更新未完成",
};
const activePhases = new Set(["checking", "downloading", "verifying", "waiting_idle", "countdown", "installing"]);
const megabytes = (value: number) => `${(value / 1048576).toFixed(1)} MB`;
function updateMessage(value: unknown, fallback = "更新未完成，请查看日志后重试。") {
  if (typeof value !== "string" || value.length > 160 || !/[\u3400-\u9fff]/.test(value) || /[\r\n\u2028\u2029]|https?:\/\/|[a-z]:[\\/]|\\\\|(?:HttpError|Error:|Headers:|authorization|Bearer|api[_-]?key|password|secret|token=)/i.test(value)) return fallback;
  return value;
}

export function UpdateStatus({update}: {update: DesktopState["update"]}) {
  const percent = Math.max(0, Math.min(100, Number(update.percent) || 0));
  return <div className="desktop-update-status" aria-live="polite">
    <strong>{labels[update.phase]}{update.version ? ` · ${update.version}` : ""}</strong>
    {update.message && <p>{updateMessage(update.message)}</p>}
    {update.phase === "downloading" && <>
      <progress max={100} value={percent} aria-label="更新下载进度"/>
      <span>{percent.toFixed(0)}%{update.total ? ` · ${megabytes(update.transferred || 0)} / ${megabytes(update.total)}` : ""}{update.bytesPerSecond ? ` · ${megabytes(update.bytesPerSecond)}/s` : ""}</span>
    </>}
    {update.phase === "countdown" && typeof update.countdownSeconds === "number" && <p>{update.countdownSeconds} 秒后重启安装，可以取消。</p>}
    {update.differential === "full" && <p>本次使用完整安装包下载。</p>}
    {!!update.blockers?.length && <ul>{update.blockers.map((blocker, index) => <li key={`${blocker.kind}-${blocker.id || index}`}>{updateMessage(blocker.message, "后台仍有操作占用，请等待或查看日志。")}</li>)}</ul>}
  </div>;
}

export function DesktopUpdateBanner() {
  const {state, error, invoke} = useDesktopState();
  if (!state || ["idle", "checking", "current"].includes(state.update.phase)) return null;
  const update = state.update;
  const canStart = update.phase === "available" || (update.phase === "error" && !!update.version);
  return <aside className="desktop-update-banner" aria-label="软件更新">
    <UpdateStatus update={update}/>
    <div className="desktop-update-actions">
      {!!update.releaseNotes && <details><summary>更新说明</summary><p className="desktop-release-notes">{update.releaseNotes}</p></details>}
      {canStart && <button onClick={() => void invoke("startUpdate")}>{update.phase === "error" ? "重试更新" : "更新"}</button>}
      {update.phase === "available" && <button className="desktop-update-secondary" onClick={() => void invoke("deferUpdate")}>稍后</button>}
      {update.canCancel && <button className="desktop-update-secondary" onClick={() => void invoke("cancelUpdate")}>取消更新</button>}
      {update.phase === "error" && !update.version && <button onClick={() => void invoke("checkForUpdates")}>重新检查</button>}
      {update.phase === "error" && <button className="desktop-update-secondary" onClick={() => void invoke("openLogs")}>查看日志</button>}
    </div>
    {error && <p role="alert">{updateMessage(error)}</p>}
  </aside>;
}

export function DesktopUpdateSettings() {
  const {state, error, invoke} = useDesktopState();
  if (!state) return null;
  return <section className="settings-section desktop-update-settings">
    <h3>软件更新</h3>
    <p>桌面版本 {state.desktopVersion}{state.sourceRevision ? ` · ${state.sourceRevision.slice(0, 12)}` : ""}</p>
    <p>{state.packaged ? "启动时自动检查 GitHub 新版；点击更新后才会下载，安装过程会显示进度。" : "当前为源码开发环境，安装版提供 GitHub 更新。"}</p>
    <UpdateStatus update={state.update}/>
    <div className="desktop-update-actions">
      <button disabled={!state.packaged || activePhases.has(state.update.phase)} onClick={() => void invoke("checkForUpdates")}>检查更新</button>
      {state.update.phase === "available" && <button onClick={() => void invoke("startUpdate")}>更新</button>}
      {state.update.canCancel && <button className="desktop-update-secondary" onClick={() => void invoke("cancelUpdate")}>取消更新</button>}
      <button className="desktop-update-secondary" onClick={() => void invoke("openLogs")}>查看日志</button>
    </div>
    {error && <p role="alert">{updateMessage(error)}</p>}
  </section>;
}
