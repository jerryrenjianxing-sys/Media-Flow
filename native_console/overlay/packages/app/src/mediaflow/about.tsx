import { onMount, Show } from "solid-js"
import { createStore } from "solid-js/store"
import { useLanguage } from "@/context/language"
import license from "./opencode-MIT.txt?raw"
import { migrationStatus, retryMigration, type Migration } from "./status"
import "./mediaflow.css"

export function MediaFlowAbout() {
  const language = useLanguage()
  const [state, setState] = createStore<{ busy: boolean; failed: boolean; receipt?: Migration }>({ busy: true, failed: false })
  const load = async (retry = false) => {
    setState({ busy: true, failed: false })
    try {
      setState("receipt", await (retry ? retryMigration() : migrationStatus()))
    } catch {
      setState("failed", true)
    } finally {
      setState("busy", false)
    }
  }
  onMount(() => void load())
  const drafts = () => {
    try {
      return /conflict|retryable/.test(localStorage.getItem("mediaflow-native-drafts-dev24") ?? "")
    } catch { return false }
  }
  return <section class="mediaflow-about">
    <img src="/_native/mediaflow-icon.svg" alt="" width="48" height="48" />
    <h2>{language.t("mediaflow.brand")} · {language.t("mediaflow.tagline")}</h2>
    <p>OpenCode 1.18.29 · MediaFlow 版本见管理中心</p>
    <p>{language.t("mediaflow.disclaimer")}</p>
    <h3>{language.t("mediaflow.migration")}</h3>
    <p role="status">{state.failed ? language.t("mediaflow.statusFailed") : state.busy ? language.t("mediaflow.statusLoading") : state.receipt?.message}</p>
    <Show when={!state.failed && state.receipt?.retryable}>
      <button type="button" disabled={state.busy} onClick={() => void load(true)}>{language.t("mediaflow.retryMigration")}</button>
    </Show>
    <Show when={state.failed}>
      <button type="button" disabled={state.busy} onClick={() => void load()}>{language.t("mediaflow.refreshStatus")}</button>
    </Show>
    <Show when={drafts()}>
      <p role="status">{language.t("mediaflow.drafts")}</p>
      <button type="button" onClick={() => location.reload()}>{language.t("mediaflow.reload")}</button>
    </Show>
    <details>
      <summary>{language.t("mediaflow.license")}</summary>
      <pre>{license}</pre>
    </details>
    <a href="/manage" target="_blank" rel="external noopener noreferrer">{language.t("mediaflow.manage")}</a>
  </section>
}
