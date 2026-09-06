import { createStore } from "solid-js/store"
import { Show } from "solid-js"
import { useLanguage } from "@/context/language"
import { stopAutomation } from "./stop"
import "./mediaflow.css"

export function MediaFlowBar() {
  const language = useLanguage()
  const [state, setState] = createStore({ busy: false, status: "" })
  const stop = async () => {
    if (state.busy || !window.confirm(language.t("mediaflow.confirmStop"))) return
    setState({ busy: true, status: language.t("mediaflow.stopping") })
    try {
      await stopAutomation()
      setState("status", language.t("mediaflow.stopped"))
    } catch {
      setState("status", language.t("mediaflow.stopFailed"))
    } finally {
      setState("busy", false)
    }
  }
  return <div class="mediaflow-bar">
    <div class="mediaflow-brand">
      <img src="/_native/mediaflow-icon.svg" alt="" width="24" height="24" />
      <span>{language.t("mediaflow.brand")}</span>
      <span class="mediaflow-tagline">· {language.t("mediaflow.tagline")}</span>
    </div>
    <div class="mediaflow-actions">
      <Show when={state.status}><span role="status" class="mediaflow-status">{state.status}</span></Show>
      <a href="/manage">{language.t("mediaflow.manage")}</a>
      <button type="button" class="mediaflow-stop" disabled={state.busy} onClick={stop}>{language.t("mediaflow.stop")}</button>
    </div>
  </div>
}
