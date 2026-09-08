"use client";

import { useCallback, useEffect, useState } from "react";
import PromptGuideLink from "../components/prompt-guide-link";
import { fetchLocalApi } from "../lib/local-api";

const API = "http://127.0.0.1:48138";

type PoolItem = { id: string; text: string; enabled: boolean };
type PlanTheme = { id: string; name: string; topic_prompt: string; search_query: string; comment_template: string; comment_pool: PoolItem[]; enabled: boolean };
type ContentPlan = { plan_id: string; revision_id: string; revision_number: number; archived: boolean; document: { name: string; comment_template: string; common_comment_pool: PoolItem[]; themes: PlanTheme[] } };
type ThemeDraft = { id?: string; name: string; topic_prompt: string; search_query: string; comment_template: string; poolText: string; enabled: boolean };
type Draft = { planId?: string; name: string; comment_template: string; commonPoolText: string; themes: ThemeDraft[] };
type Preset = { name: string; builtin: boolean; config: Record<string, unknown> };
type StorageStatus = { data_root: string; configured: boolean; categories: string[]; pending_migration?: { target?: string } | null; last_migration?: { applied?: boolean; message?: string; target?: string } | null };
type ModelStatus = { provider: string; model: string; key_configured: boolean; storage_status: "empty" | "pending" | "stored" | "unreadable"; auth_status: string; model_test_status: string; last_verified_at?: string | null; last_model_test_at?: string | null; last_model_latency_ms?: number | null; message: string; model_ready: boolean; has_pending_key: boolean; active_provider?: string; config_version?: number; requests_used?: number; requests_remaining?: number | null; can_enable?: boolean };

const blankTheme = (): ThemeDraft => ({ name: "", topic_prompt: "", search_query: "", comment_template: "", poolText: "", enabled: true });
const blankDraft = (): Draft => ({ name: "", comment_template: "", commonPoolText: "", themes: [blankTheme()] });
const poolLines = (items: PoolItem[]) => items.filter((item) => item.enabled).map((item) => item.text).join("\n");

export default function ContentAssetsPage() {
  const [plans, setPlans] = useState<ContentPlan[]>([]);
  const [draft, setDraft] = useState<Draft>(blankDraft);
  const [selectedRevision, setSelectedRevision] = useState("");
  const [expandedTheme, setExpandedTheme] = useState(0);
  const [notice, setNotice] = useState("正在读取内容资产…");
  const [busy, setBusy] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [provider, setProvider] = useState("openrouter");
  const [uploadConsent, setUploadConsent] = useState(false);
  const isQwen = provider === "qwen_token_plan";
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [storage, setStorage] = useState<StorageStatus | null>(null);
  const [storageTarget, setStorageTarget] = useState("");
  const [desktopBridge, setDesktopBridge] = useState(false);
  const [modelAction, setModelAction] = useState<"" | "saving" | "verifying" | "testing" | "enabling">("");

  async function chooseProvider(value: string) {
    setProvider(value); setApiKey(""); setUploadConsent(false); setModel(null); setBusy(true);
    try {
      const response = await fetchLocalApi(`${API}/api/model?provider=${value}`, { cache: "no-store" });
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "读取模型配置失败");
      setModel(result);
    } catch (error) { setNotice(error instanceof Error ? error.message : "读取模型配置失败"); }
    finally { setBusy(false); }
  }

  async function enableModel() {
    setBusy(true); setModelAction("enabling");
    try {
      const response = await fetchLocalApi(`${API}/api/model`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider }) }, 10_000);
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "启用失败");
      setModel(result); setNotice(result.message);
    } catch (error) {
      const message = error instanceof Error ? error.message : "启用失败，原配置未改变";
      setNotice(message); setModel((current) => current ? { ...current, message } : current);
    } finally { setBusy(false); setModelAction(""); }
  }

  const refresh = useCallback(async () => {
    try {
      const response = await fetchLocalApi(`${API}/api/content-plans`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "读取失败");
      setPlans(result.content_plans || []);
      setNotice(`${(result.content_plans || []).length} 个可用内容计划`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "本机服务未启动");
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    const modelTimer = window.setTimeout(() => {
      const requested = new URLSearchParams(window.location.search).get("model_provider");
      const selected = requested === "qwen_token_plan" || requested === "openrouter" ? requested : null;
      void fetchLocalApi(`${API}/api/model${selected ? `?provider=${selected}` : ""}`, { cache: "no-store" }).then(async (response) => {
        if (response.ok) { const value = await response.json(); setModel(value); setProvider(selected || value.active_provider || "openrouter"); }
      }).catch(() => setNotice("模型连接状态读取超时，请确认本机服务正在运行"));
    }, 0);
    const presetTimer = window.setTimeout(() => { void fetchLocalApi(`${API}/api/presets`, { cache: "no-store" }).then(async (response) => { if (response.ok) setPresets(((await response.json()).presets || []) as Preset[]); }).catch(() => undefined); }, 0);
    const storageTimer = window.setTimeout(() => { void fetchLocalApi(`${API}/api/system/storage`, { cache: "no-store" }).then(async (response) => { if (response.ok) setStorage(await response.json()); }).catch(() => undefined); }, 0);
    const host = (window as unknown as { chrome?: { webview?: { postMessage(message: string): void; addEventListener(type: string, listener: (event: MessageEvent) => void): void; removeEventListener(type: string, listener: (event: MessageEvent) => void): void } } }).chrome?.webview;
    const bridgeTimer = window.setTimeout(() => setDesktopBridge(Boolean(host)), 0);
    const receive = (event: MessageEvent) => {
      const message = event.data as { type?: string; path?: string } | null;
      if (message?.type === "folder-selected" && message.path) setStorageTarget(message.path);
    };
    host?.addEventListener("message", receive);
    return () => { window.clearTimeout(timer); window.clearTimeout(modelTimer); window.clearTimeout(presetTimer); window.clearTimeout(storageTimer); window.clearTimeout(bridgeTimer); host?.removeEventListener("message", receive); };
  }, [refresh]);

  async function scheduleStorageMigration() {
    if (!storageTarget.trim()) return;
    setBusy(true);
    try {
      const response = await fetchLocalApi(`${API}/api/system/storage/schedule`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data_root: storageTarget.trim() }) }, 20_000);
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "数据目录安排失败");
      const statusResponse = await fetchLocalApi(`${API}/api/system/storage`, { cache: "no-store" });
      if (statusResponse.ok) setStorage(await statusResponse.json());
      setNotice(result.message || "已安排数据目录迁移");
    } catch (error) { setNotice(error instanceof Error ? error.message : "数据目录安排失败"); }
    finally { setBusy(false); }
  }

  async function saveModelKey() {
    setBusy(true);
    setModelAction("saving");
    try {
      const response = await fetchLocalApi(`${API}/api/model-key`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: apiKey, provider }) }, 35_000);
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "模型 Key 保存失败");
      setModel(result); if (result.accepted) setApiKey(""); setNotice(result.message || "模型连接状态已更新");
    } catch (error) {
      const message = error instanceof Error ? error.message : "模型 Key 保存失败";
      setNotice(message);
      setModel((current) => current ? { ...current, message: `${message}；可以安全重试，未确认的新 Key 不会覆盖原配置` } : current);
    }
    finally { setModelAction(""); setBusy(false); }
  }

  async function verifyModelKey() {
    setBusy(true);
    setModelAction("verifying");
    try {
      const response = await fetchLocalApi(`${API}/api/model/verify`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider }) }, 20_000);
      const result = await response.json(); if (!response.ok) throw new Error(result.error || "鉴权失败");
      setModel(result); setNotice(result.message || "鉴权状态已更新");
    } catch (error) { setNotice(error instanceof Error ? error.message : "鉴权失败"); }
    finally { setModelAction(""); setBusy(false); }
  }

  async function testModel() {
    setBusy(true);
    setModelAction("testing");
    try {
      const response = await fetchLocalApi(`${API}/api/model/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider, upload_consent: uploadConsent }) }, 45_000);
      const result = await response.json(); if (result.model) setModel(result);
      if (!response.ok || !result.ok) throw new Error(result.message || "模型测试失败");
      setNotice(result.message || "当前模型测试成功");
    } catch (error) { const message = error instanceof Error ? error.message : "模型测试失败"; setNotice(message); setModel((current) => current ? { ...current, message } : current); }
    finally { setModelAction(""); setBusy(false); }
  }

  async function saveCurrentDraftAsPreset() {
    const name = presetName.trim();
    if (!name) return;
    setBusy(true);
    try {
      const draftResponse = await fetchLocalApi(`${API}/api/workbench/draft`, { cache: "no-store" });
      const currentDraft = await draftResponse.json();
      if (!draftResponse.ok) throw new Error(currentDraft.error || "任务草稿读取失败");
      const response = await fetchLocalApi(`${API}/api/presets`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, config: currentDraft.draft.config }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "预设保存失败");
      setPresets(result.presets || []); setPresetName(""); setNotice(`已把当前任务草稿保存为“${name}”`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "预设保存失败"); }
    finally { setBusy(false); }
  }

  async function deletePreset(name: string) {
    if (!window.confirm(`删除预设“${name}”？历史任务不会受影响。`)) return;
    setBusy(true);
    try {
      const response = await fetchLocalApi(`${API}/api/presets/delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "预设删除失败");
      setPresets(result.presets || []); setNotice(`已删除预设“${name}”`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "预设删除失败"); }
    finally { setBusy(false); }
  }

  function startNewPlan() {
    setDraft(blankDraft());
    setSelectedRevision("");
    setExpandedTheme(0);
    setNotice("正在新建内容计划");
  }

  function edit(plan: ContentPlan) {
    setSelectedRevision(plan.revision_id);
    setExpandedTheme(0);
    setDraft({
      planId: plan.plan_id,
      name: plan.document.name,
      comment_template: plan.document.comment_template || "",
      commonPoolText: poolLines(plan.document.common_comment_pool || []),
      themes: plan.document.themes.map((theme) => ({
        id: theme.id,
        name: theme.name,
        topic_prompt: theme.topic_prompt,
        search_query: theme.search_query,
        comment_template: theme.comment_template || "",
        poolText: poolLines(theme.comment_pool || []),
        enabled: theme.enabled,
      })),
    });
    setNotice(`正在编辑“${plan.document.name}”v${plan.revision_number}；保存会创建新版本`);
  }

  function updateTheme(index: number, patch: Partial<ThemeDraft>) {
    setDraft((current) => ({ ...current, themes: current.themes.map((theme, i) => i === index ? { ...theme, ...patch } : theme) }));
  }

  function moveTheme(index: number, direction: -1 | 1) {
    const next = index + direction;
    if (next < 0 || next >= draft.themes.length) return;
    setDraft((current) => {
      const themes = [...current.themes];
      [themes[index], themes[next]] = [themes[next], themes[index]];
      return { ...current, themes };
    });
    setExpandedTheme((current) => current === index ? next : current === next ? index : current);
  }

  function removeTheme(index: number) {
    if (draft.themes.length <= 1) {
      setNotice("内容计划至少需要一个主题");
      return;
    }
    setDraft((current) => ({ ...current, themes: current.themes.filter((_, i) => i !== index) }));
    setExpandedTheme((current) => current === index ? Math.max(0, index - 1) : current > index ? current - 1 : current);
  }

  function addTheme() {
    const nextIndex = draft.themes.length;
    setDraft((current) => ({ ...current, themes: [...current.themes, blankTheme()] }));
    setExpandedTheme(nextIndex);
  }

  async function save() {
    setBusy(true);
    setNotice("正在保存新版本…");
    try {
      const document = {
        name: draft.name,
        comment_template: draft.comment_template,
        common_comment_pool: draft.commonPoolText.split(/\r?\n/).map((text) => text.trim()).filter(Boolean),
        themes: draft.themes.map((theme) => ({
          id: theme.id,
          name: theme.name,
          topic_prompt: theme.topic_prompt,
          search_query: theme.search_query,
          comment_template: theme.comment_template,
          comment_pool: theme.poolText.split(/\r?\n/).map((text) => text.trim()).filter(Boolean),
          enabled: theme.enabled,
        })),
      };
      const response = await fetchLocalApi(`${API}/api/content-plans`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: draft.planId, document }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "保存失败");
      setSelectedRevision(result.content_plan.revision_id);
      setDraft((current) => ({ ...current, planId: result.content_plan.plan_id }));
      setNotice(`已保存“${result.content_plan.document.name}”v${result.content_plan.revision_number}`);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function archive() {
    if (!draft.planId || !window.confirm(`归档“${draft.name}”？历史任务和预设仍可读取。`)) return;
    setBusy(true);
    try {
      const response = await fetchLocalApi(`${API}/api/content-plans/archive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: draft.planId }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "归档失败");
      startNewPlan();
      setNotice("内容计划已归档，历史版本保留");
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "归档失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app-shell content-assets-page">
      <div className="page-shell">
        <section className="records-hero content-assets-hero" data-motion>
          <div>
            <p className="eyebrow">ASSETS &amp; SETTINGS</p>
            <h1>资产与设置</h1>
            <p>集中维护内容计划、评论资产、模型连接和质量评测；运行参数留在任务台。</p>
            <p className="workspace-page-notice" role="status">{notice}</p>
          </div>
          <button type="button" className="primary" onClick={startNewPlan}>新建计划</button>
        </section>
        <nav className="workspace-tabs asset-tabs" aria-label="资产与设置分类"><a className="active" href="#content-plans">内容计划</a><a href="/content/guide">提示词规范</a><a href="#presets">参数预设</a><a href="#model-settings">模型连接</a><a href="#storage-settings">数据存储</a><a href="/governance">评测与证据</a></nav>

        <section id="presets" className="panel preset-settings-strip" data-motion>
          <div><p className="section-index">PARAMETER PRESETS</p><h2>参数预设</h2><p>把任务台当前草稿保存为可复用参数；预设不会改写历史任务。</p></div>
          <div className="preset-assets-list">{presets.map((preset) => <span key={preset.name}><b>{preset.name}</b>{preset.builtin ? <small>内置</small> : <button type="button" disabled={busy} onClick={() => void deletePreset(preset.name)}>删除</button>}</span>)}</div>
          <div className="preset-assets-create"><input value={presetName} maxLength={50} onChange={(event) => setPresetName(event.target.value)} placeholder="新预设名称"/><button type="button" className="secondary" disabled={busy || !presetName.trim()} onClick={() => void saveCurrentDraftAsPreset()}>保存当前任务草稿</button></div>
        </section>

        <section id="model-settings" className="panel model-connection-card" data-motion>
          <div className="model-connection-head"><div><p className="section-index">MODEL CONNECTION</p><h2>全平台模型连接</h2><p>{model ? `${model.provider} · ${model.model}` : "正在读取模型设置…"}</p><p>正在使用：{model?.active_provider === "qwen_token_plan" ? "千问 Token Plan（实验性）" : "OpenRouter"} · 配置版本 {model?.config_version ?? 0}</p></div><span className={`key-state ${model?.model_ready ? "ready" : ""}`}>{modelAction ? "正在处理" : model?.model_test_status === "passed" ? "测试已通过" : model?.key_configured ? "已保存，待验证" : "未配置"}</span></div>
          <label>服务商 <select aria-label="模型服务商" value={provider} disabled={busy} onChange={(event) => void chooseProvider(event.target.value)}><option value="openrouter">OpenRouter</option><option value="qwen_token_plan">千问AI平台 · Token Plan（实验性）</option></select></label>
          {isQwen && <div className="model-connection-feedback">
            <p>模型固定为 qwen3.8-flash；地址由软件预设。Token Plan专属Key与OpenRouter分别加密保存。</p>
            <p>个人套餐官方FAQ限制后台自动化使用。本接入仅为实验性验证，不代表符合生产后台使用要求。不会自动切换到按量接口或其他服务商。</p>
            <p>累计请求：{model?.requests_used ?? 0} 次（失败、超时也计入；重启不重置）。平台不设次数或金额上限；Credits以千问工作台为准，不等同于零费用。</p>
            <p><a href="https://platform.qianwenai.com/docs/token-plan/personal/token-plan-personal-faq" target="_blank" rel="noreferrer">官方使用与数据条款</a></p>
            <label><input type="checkbox" checked={uploadConsent} disabled={busy} onChange={(event) => setUploadConsent(event.target.checked)}/> 我确认：测试图及后续明确授权的完整应用截图将发送到千问Token Plan服务并消耗套餐额度；个人版输入输出可能用于服务及模型改进。不上传电脑桌面、配置或Key。</label>
          </div>}
          <div className="model-connection-body"><div className="key-entry"><input aria-label={isQwen ? "千问 Token Plan API Key" : "OpenRouter API Key"} type="password" autoComplete="off" value={apiKey} disabled={busy} onChange={(event) => setApiKey(event.target.value)} placeholder={isQwen ? "粘贴 Token Plan 专属 sk-sp- Key" : "粘贴 OpenRouter Key"}/><button type="button" className="secondary" disabled={busy || !apiKey.trim()} onClick={() => void saveModelKey()}>{isQwen ? "安全保存" : "安全保存并鉴权"}</button></div>
            <div className="model-actions">{!isQwen && (model?.has_pending_key || (model?.key_configured && model.auth_status !== "authenticated")) && <button type="button" className="secondary" disabled={busy} onClick={() => void verifyModelKey()}>重新验证 Key</button>}
              <button type="button" className="secondary" disabled={busy || !model?.key_configured || (isQwen ? !uploadConsent : model.auth_status !== "authenticated")} onClick={() => void testModel()}>{isQwen ? "测试图片与结构化响应" : "测试当前模型（少量计费）"}</button>
              <button type="button" className="primary" disabled={busy || !model?.model_ready || (isQwen && !model.can_enable)} onClick={() => void enableModel()}>启用为全平台模型</button><a className="secondary" href="/governance">打开评测与证据</a>
            </div></div>
          <div className={`model-connection-feedback ${model?.model_test_status === "failed" ? "danger" : ""}`} role="status"><strong>{modelAction === "saving" ? isQwen ? "正在本机加密和解密回读，不发送联网请求…" : "正在本机加密并进行免费鉴权，最长约35秒…" : modelAction === "verifying" ? "正在重新验证Key，最长约20秒…" : modelAction === "testing" ? isQwen ? "正在验证文本、图片与JSON，20秒后收口，不自动重试…" : "正在测试当前模型，最长约45秒…" : modelAction === "enabling" ? "正在核对任务和分析调用是否空闲…" : model?.message || "请选择服务商并保存独立Key。"}</strong>{model?.last_model_test_at && <small>最近测试：{new Date(model.last_model_test_at).toLocaleString()}{model.last_model_latency_ms ? ` · ${model.last_model_latency_ms} ms` : ""}</small>}</div>
        </section>

        <section id="storage-settings" className="panel storage-settings-strip" data-motion>
          <div><p className="section-index">DATA STORAGE</p><h2>数据存储位置</h2><p>数据库、任务截图、日志、设备档案和虚拟机备份统一保存在这里。</p></div>
          <div className="storage-current"><small>当前目录</small><strong>{storage?.data_root || "正在读取…"}</strong>{storage?.pending_migration?.target && <em>已安排迁移到：{storage.pending_migration.target}</em>}</div>
          <div className="storage-change"><input aria-label="新的数据目录" value={storageTarget} onChange={(event) => setStorageTarget(event.target.value)} placeholder="选择一个空的本地固定磁盘目录"/>{desktopBridge && <button type="button" className="secondary" onClick={() => (window as unknown as { chrome: { webview: { postMessage(message: string): void } } }).chrome.webview.postMessage("mediaflow:choose-folder")}>选择目录</button>}<button type="button" className="secondary" disabled={busy || !storageTarget.trim()} onClick={() => void scheduleStorageMigration()}>校验并安排迁移</button></div>
          <p className="storage-note">迁移只会复制并校验数据，不删除旧目录。安排后请从托盘安全停止后台，再重新打开 MediaFlow；若校验失败会继续使用原目录。</p>
        </section>

        <div id="content-plans" className="content-assets-layout">
          <aside className="panel content-plan-list" data-motion>
            <div className="panel-heading">
              <div><p className="section-index">PLANS</p><h2>可用计划</h2></div>
              <span className="tag quiet">{plans.length} 个</span>
            </div>
            <div className="content-plan-items">
              {plans.map((plan) => (
                <button key={plan.revision_id} type="button" className={selectedRevision === plan.revision_id ? "selected" : ""} onClick={() => edit(plan)}>
                  <strong>{plan.document.name}</strong>
                  <small>v{plan.revision_number} · {plan.document.themes.filter((theme) => theme.enabled).length} 个启用主题</small>
                </button>
              ))}
              {!plans.length && <div className="content-plan-empty"><span>暂无计划</span><p>先创建计划，再按顺序加入主题。保存后可在新建任务页直接选择。</p><button type="button" className="secondary" onClick={startNewPlan}>创建第一个计划</button></div>}
            </div>
            <div className="content-plan-guide">
              <span>使用方式</span>
              <ol><li>建立统一评论口径</li><li>按轮次排列主题</li><li>保存为不可变版本</li></ol>
            </div>
          </aside>

          <section className="content-plan-editor" data-motion>
            <div className="panel asset-editor-heading">
              <div>
                <p className="section-index">PLAN EDITOR</p>
                <h2>{draft.planId ? "编辑并保存新版本" : "新建内容计划"}</h2>
                <p>{draft.planId ? "当前草稿基于已保存版本，保存后生成新的不可变版本。" : "先定义计划级评论口径，再配置逐轮循环的主题。"}</p>
              </div>
              {draft.planId && <button type="button" className="preset-delete" disabled={busy} onClick={() => void archive()}>归档计划</button>}
            </div>

            <section className="panel asset-section">
              <header className="asset-section-heading"><span>01</span><div><h3>计划设置</h3><p>这些内容会应用到计划中的所有主题。</p></div></header>
              <div className="asset-basics">
                <label className="field asset-plan-name"><span>计划名称</span><input maxLength={80} value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} placeholder="例如：产业主题轮换"/></label>
                <label className="field"><span className="field-title">全局评论写作模板<PromptGuideLink section="comment-template"/></span><textarea maxLength={1000} rows={4} value={draft.comment_template} onChange={(event) => setDraft((current) => ({ ...current, comment_template: event.target.value }))} placeholder="例如：专业、简洁、基于画面，不使用夸张语气"/><small>只补充语气和表达，不能覆盖固定安全规则。</small></label>
                <label className="field"><span className="field-title">通用评论词池<PromptGuideLink section="comment-pool"/></span><textarea rows={4} value={draft.commonPoolText} onChange={(event) => setDraft((current) => ({ ...current, commonPoolText: event.target.value }))} placeholder={"每行一条候选表达\n主题词池不足时用于补足"}/><small>主题专属词池不足时，最多取 5 条近期未使用候选。</small></label>
              </div>
            </section>

            <section className="panel asset-section asset-theme-section">
              <div className="asset-theme-head">
                <header className="asset-section-heading"><span>02</span><div><h3>主题队列</h3><p>按顺序逐轮循环；同一轮所有设备使用同一主题。</p></div></header>
                <button type="button" className="secondary" disabled={draft.themes.length >= 20} onClick={addTheme}>添加主题</button>
              </div>
              <div className="asset-theme-list">
                {draft.themes.map((theme, index) => {
                  const expanded = expandedTheme === index;
                  return (
                    <article key={theme.id || index} className={`${theme.enabled ? "asset-theme-card" : "asset-theme-card disabled"}${expanded ? " expanded" : ""}`}>
                      <div className="asset-theme-card-head">
                        <b>{String(index + 1).padStart(2, "0")}</b>
                        <button type="button" className="asset-theme-summary" onClick={() => setExpandedTheme(expanded ? -1 : index)} aria-expanded={expanded}>
                          <strong>{theme.name || `未命名主题 ${index + 1}`}</strong>
                          <small>{theme.search_query ? `搜索：${theme.search_query}` : "尚未填写搜索词"}</small>
                        </button>
                        <label className="switch-row"><span>{theme.enabled ? "启用" : "停用"}</span><input type="checkbox" checked={theme.enabled} onChange={(event) => updateTheme(index, { enabled: event.target.checked })}/></label>
                        <div className="asset-theme-actions">
                          <button type="button" onClick={() => moveTheme(index, -1)} disabled={index === 0} aria-label="上移主题">↑</button>
                          <button type="button" onClick={() => moveTheme(index, 1)} disabled={index === draft.themes.length - 1} aria-label="下移主题">↓</button>
                          <button type="button" onClick={() => removeTheme(index)}>删除</button>
                          <button type="button" className="asset-theme-expand" onClick={() => setExpandedTheme(expanded ? -1 : index)}>{expanded ? "收起" : "展开编辑"}</button>
                        </div>
                      </div>
                      {expanded && <div className="asset-theme-grid">
                        <label className="field"><span>主题名称</span><input maxLength={80} value={theme.name} onChange={(event) => updateTheme(index, { name: event.target.value })}/></label>
                        <label className="field"><span className="field-title">搜索词<PromptGuideLink section="search"/></span><input maxLength={80} value={theme.search_query} onChange={(event) => updateTheme(index, { search_query: event.target.value })}/></label>
                        <label className="field full"><span className="field-title">主题判定标准<PromptGuideLink section="topic"/></span><textarea maxLength={800} rows={3} value={theme.topic_prompt} onChange={(event) => updateTheme(index, { topic_prompt: event.target.value })}/><small>说明哪些画面和语义算精确匹配，避免只堆关键词。</small></label>
                        <label className="field"><span className="field-title">模板补充要求<PromptGuideLink section="theme-template"/></span><textarea maxLength={1000} rows={4} value={theme.comment_template} onChange={(event) => updateTheme(index, { comment_template: event.target.value })}/></label>
                        <label className="field"><span className="field-title">主题专属评论词池<PromptGuideLink section="comment-pool"/></span><textarea rows={4} value={theme.poolText} onChange={(event) => updateTheme(index, { poolText: event.target.value })} placeholder="每行一条，优先于通用词池"/></label>
                      </div>}
                    </article>
                  );
                })}
              </div>
            </section>

            <div className="setup-finish asset-save-bar ready">
              <div><strong>{draft.planId ? "保存后生成不可变新版本" : "创建第一个版本"}</strong><small>已排队任务继续使用提交时冻结的旧快照。</small></div>
              <button type="button" className="primary" disabled={busy} onClick={() => void save()}>{busy ? "处理中…" : "保存内容计划"}</button>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
