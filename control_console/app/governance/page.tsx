"use client";
/* Evidence images are served by the local API and cannot use the framework image optimizer. */
/* eslint-disable @next/next/no-img-element */

import { useCallback, useEffect, useState } from "react";
import { fetchLocalApi } from "../lib/local-api";

const API = "http://127.0.0.1:48138";
type Relevance = "exact" | "adjacent" | "unrelated" | "uncertain";
type Review = { sample_id: string; candidate: { relevance?: Relevance; note?: string }; riskflow: { relevance?: Relevance; reason?: string }; human_relevance?: Relevance|null; human_note?: string; confirmed_at?: string|null };
type Evaluation = { total_samples: number; confirmed_samples: number; pending_review: string[]; agreement_rate: number|null; result_status: string; coverage: { complete: boolean; missing_relevance: string[]; missing_hard_negatives: string[] } };
type ReviewPayload = { items: Review[]; evaluation: Evaluation };
type Inventory = { file_count: number; total_bytes: number; database_bytes: number; oldest_at?: string|null; newest_at?: string|null; policy: { retention_days: number; auto_delete: false } };
type EvidencePayload = { inventory: Inventory; backups: { name: string; bytes: number; created_at: string }[] };

const labels: Record<Relevance, string> = { exact: "精准匹配", adjacent: "相关边界", unrelated: "不相关", uncertain: "不确定" };
const emptyEvaluation: Evaluation = { total_samples: 0, confirmed_samples: 0, pending_review: [], agreement_rate: null, result_status: "provisional", coverage: { complete: false, missing_relevance: [], missing_hard_negatives: [] } };
const formatBytes = (value: number) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB` : value >= 1024 ** 2 ? `${(value / 1024 ** 2).toFixed(1)} MB` : value >= 1024 ? `${(value / 1024).toFixed(1)} KB` : `${value} B`;

export default function GovernancePage() {
  const [reviews, setReviews] = useState<Review[]>([]);
  const [evaluation, setEvaluation] = useState<Evaluation>(emptyEvaluation);
  const [evidence, setEvidence] = useState<EvidencePayload|null>(null);
  const [drafts, setDrafts] = useState<Record<string, { relevance: Relevance; note: string }>>({});
  const [notice, setNotice] = useState("正在读取本机评测与证据…");
  const [busy, setBusy] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [reviewResponse, evidenceResponse] = await Promise.all([fetchLocalApi(`${API}/api/topic-reviews`, { cache: "no-store" }), fetchLocalApi(`${API}/api/evidence/status`, { cache: "no-store" })]);
      if (!reviewResponse.ok || !evidenceResponse.ok) throw new Error("读取失败");
      const reviewPayload = await reviewResponse.json() as ReviewPayload;
      setReviews(reviewPayload.items); setEvaluation(reviewPayload.evaluation); setEvidence(await evidenceResponse.json() as EvidencePayload);
      setDrafts((current) => Object.fromEntries(reviewPayload.items.map((item) => [item.sample_id, current[item.sample_id] || { relevance: item.human_relevance || item.candidate.relevance || "uncertain", note: item.human_note || "" }])));
      setNotice("本机数据已同步");
    } catch { setNotice("本机控制服务未启动，暂时无法读取数据"); }
  }, []);
  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(initial);
  }, [refresh]);

  const confirm = async (review: Review) => {
    const draft = drafts[review.sample_id]; if (!draft) return; setBusy(review.sample_id);
    try { const response = await fetchLocalApi(`${API}/api/topic-reviews/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sample_id: review.sample_id, ...draft }) }); const result = await response.json() as { error?: string }; if (!response.ok) throw new Error(result.error || "保存失败"); await refresh(); setNotice(`样本 ${review.sample_id} 已人工确认`); }
    catch (error) { setNotice(error instanceof Error ? error.message : "保存失败"); } finally { setBusy(""); }
  };
  const savePolicy = async () => {
    if (!evidence) return; setBusy("policy");
    try { const response = await fetchLocalApi(`${API}/api/evidence/policy`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ retention_days: evidence.inventory.policy.retention_days }) }); if (!response.ok) throw new Error("政策保存失败"); await refresh(); setNotice("证据保留政策已记录，不会自动删除文件"); }
    catch (error) { setNotice(error instanceof Error ? error.message : "保存失败"); } finally { setBusy(""); }
  };
  const backup = async () => {
    setBusy("backup");
    try { const response = await fetchLocalApi(`${API}/api/evidence/backup`, { method: "POST" }, 30_000); if (!response.ok) throw new Error("备份失败"); await refresh(); setNotice("已创建新的本地数据库备份"); }
    catch (error) { setNotice(error instanceof Error ? error.message : "备份失败"); } finally { setBusy(""); }
  };

  return <main className="app-shell governance-page"><div className="page-shell">
    <section className="records-hero"><div><p className="eyebrow">ASSETS &amp; SETTINGS · QUALITY</p><h1>评测与证据</h1><p>人工真值、模型判断和运行证据分开管理，准确率口径可追溯。</p><p className="workspace-page-notice" role="status">{notice}</p></div><a className="secondary back-link" href="/content">返回资产与设置</a></section>
    <nav className="workspace-tabs asset-tabs" aria-label="资产与设置分类"><a href="/content#content-plans">内容计划</a><a href="/content#model-settings">模型连接</a><a className="active" href="/governance">评测与证据</a></nav>
    <section className="governance-summary"><article><span>评测样本</span><b>{evaluation.total_samples}</b><small>{evaluation.confirmed_samples} 条已人工确认</small></article><article><span>待复核</span><b>{evaluation.pending_review.length}</b><small>不计入正式准确率</small></article><article><span>正式一致率</span><b>{evaluation.agreement_rate === null ? "待建立" : `${Math.round(evaluation.agreement_rate * 100)}%`}</b><small>{evaluation.result_status === "complete" ? "覆盖完整" : "暂定结果"}</small></article><article><span>证据库存</span><b>{evidence ? formatBytes(evidence.inventory.total_bytes) : "—"}</b><small>{evidence?.inventory.file_count || 0} 个文件</small></article></section>
    <section className="governance-grid"><section className="panel review-panel"><div className="panel-heading"><div><p className="section-index">HUMAN REVIEW</p><h2>主题人工复核</h2><small className="section-note">候选标签仅供参考；点击确认后才进入正式准确率。</small></div><span className="tag quiet">人工真值独立保存</span></div>
      {!evaluation.coverage.complete && <div className="coverage-warning"><strong>当前覆盖仍不完整</strong><span>缺少类别：{evaluation.coverage.missing_relevance.map((value) => labels[value as Relevance]).join("、") || "无"}</span><span>缺少困难负样本：{evaluation.coverage.missing_hard_negatives.join("、") || "无"}</span></div>}
      <div className="review-list">{reviews.map((review) => { const draft = drafts[review.sample_id] || { relevance: "uncertain" as Relevance, note: "" }; return <article key={review.sample_id} className={review.confirmed_at ? "confirmed" : ""}><a className="review-image" href={`${API}/api/topic-review-image?id=${encodeURIComponent(review.sample_id)}`} target="_blank" rel="noreferrer"><img src={`${API}/api/topic-review-image?id=${encodeURIComponent(review.sample_id)}`} alt={`评测样本 ${review.sample_id}`}/></a><div className="review-content"><div className="review-title"><strong>{review.sample_id}</strong><span>{review.confirmed_at ? "已人工确认" : "待人工复核"}</span></div><div className="decision-compare"><p><span>候选标签</span><b>{labels[(review.candidate.relevance || "uncertain") as Relevance]}</b><small>{review.candidate.note || "无候选说明"}</small></p><p><span>MediaFlow 判断</span><b>{labels[(review.riskflow.relevance || "uncertain") as Relevance]}</b><small>{review.riskflow.reason || "无模型说明"}</small></p></div><div className="review-form"><label className="field"><span>人工结论</span><select value={draft.relevance} onChange={(event) => setDrafts((current) => ({ ...current, [review.sample_id]: { ...draft, relevance: event.target.value as Relevance } }))}>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="field"><span>复核说明（可选）</span><input value={draft.note} maxLength={500} onChange={(event) => setDrafts((current) => ({ ...current, [review.sample_id]: { ...draft, note: event.target.value } }))} placeholder="记录判断依据"/></label><button type="button" className="primary" disabled={busy === review.sample_id} onClick={() => void confirm(review)}>{busy === review.sample_id ? "保存中…" : review.confirmed_at ? "更新确认" : "确认结论"}</button></div></div></article>; })}</div>
    </section><aside className="governance-side"><section className="panel evidence-panel"><div className="panel-heading"><div><p className="section-index">EVIDENCE POLICY</p><h2>证据治理</h2><small className="section-note">只盘点和备份，不自动删除。</small></div></div>{evidence && <><dl className="evidence-stats"><div><dt>证据文件</dt><dd>{evidence.inventory.file_count} 个</dd></div><div><dt>数据库大小</dt><dd>{formatBytes(evidence.inventory.database_bytes)}</dd></div><div><dt>最早证据</dt><dd>{evidence.inventory.oldest_at ? new Date(evidence.inventory.oldest_at).toLocaleDateString("zh-CN") : "—"}</dd></div><div><dt>最近证据</dt><dd>{evidence.inventory.newest_at ? new Date(evidence.inventory.newest_at).toLocaleDateString("zh-CN") : "—"}</dd></div></dl><label className="field retention-field"><span>证据保留政策</span><div><input type="number" min={1} max={3650} value={evidence.inventory.policy.retention_days} onChange={(event) => setEvidence({ ...evidence, inventory: { ...evidence.inventory, policy: { ...evidence.inventory.policy, retention_days: Number(event.target.value) } } })}/><em>天</em></div><small>只记录政策，不自动删除任何证据。</small></label><div className="evidence-actions"><button type="button" className="secondary" disabled={busy === "policy"} onClick={() => void savePolicy()}>保存政策</button><button type="button" className="primary" disabled={busy === "backup"} onClick={() => void backup()}>创建数据库备份</button><a href={`${API}/api/evidence/manifest`} target="_blank" rel="noreferrer">查看库存清单</a></div><div className="backup-list"><strong>最近备份</strong>{evidence.backups.slice(0, 5).map((item) => <small key={item.name}>{item.name} · {formatBytes(item.bytes)}</small>)}{!evidence.backups.length && <small>尚未创建备份</small>}</div></>}
    </section></aside></section>
  </div></main>;
}
