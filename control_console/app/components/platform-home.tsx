"use client";
import {useRef,useState} from "react";
import {usePlatformStatus} from "./console-shell";
import {API} from "./workbench-types";
import {fetchLocalApi} from "../lib/local-api";
import {loadSkillArchive,platformSummary} from "../lib/platform-home-state.mjs";

export default function PlatformHome({legacySession=false}:{legacySession?:boolean}){
  const {status,error,refresh}=usePlatformStatus();
  const summary=platformSummary(status,error);
  const [download,setDownload]=useState<"idle"|"loading"|"error"|"done">("idle");
  const downloading=useRef(false);
  async function downloadSkill(){
    if(downloading.current)return;
    downloading.current=true;
    setDownload("loading");
    try{
      const archive=await loadSkillArchive(()=>fetchLocalApi(`${API}/api/platform-skill`,{cache:"no-store"},15000));
      const url=URL.createObjectURL(archive),link=document.createElement("a");
      link.href=url;link.download="MediaFlow-Skill.zip";document.body.append(link);link.click();link.remove();
      window.setTimeout(()=>URL.revokeObjectURL(url),1000);
      setDownload("done");
    }catch{setDownload("error");}
    finally{downloading.current=false;}
  }
  return <main className="page-shell management-page management-settings">
    <header className="management-heading"><div><p className="eyebrow">PLATFORM</p><h1>平台首页</h1><p>查看平台状态，通过外部 Agent 使用 MediaFlow Skill，或直接进入管理中心。</p></div><a className="primary" href="/manage">进入管理中心 →</a></header>
    {legacySession?<p className="workspace-page-notice" role="status">这是旧会话书签。旧会话数据仍保留，本页不再打开内置聊天，也不会迁移或删除历史记录。</p>:null}
    <section className="settings-section" aria-labelledby="platform-status-heading">
      <h2 id="platform-status-heading">平台状态</h2>
      <p role="status">{summary.message}</p>
      {summary.metrics?<p>{summary.metrics.online??"未读取"} 台设备在线 · {summary.metrics.running??"未读取"} 个任务执行中 · {summary.metrics.pending??"未读取"} 个任务等待 · 队列{summary.metrics.queue}</p>:null}
      <div className="hero-actions"><button type="button" className="secondary" onClick={()=>void refresh()}>{summary.state==="error"?"重试读取状态":"刷新状态"}</button><a href="/run">查看运行与停止</a><a href="/devices">查看设备</a></div>
    </section>
    <section className="settings-section" aria-labelledby="platform-skill-heading">
      <h2 id="platform-skill-heading">使用外部 Agent</h2>
      <p>下载通用 Skill 包，在你选择的本机 Agent 中加载。平台不再内置聊天；模型和会话由外部 Agent 管理。</p>
      <button type="button" className="primary" disabled={download==="loading"} onClick={()=>void downloadSkill()}>{download==="loading"?"正在准备下载…":download==="error"?"重试下载 MediaFlow Skill":"下载 MediaFlow Skill"}</button>
      {download==="error"?<p role="alert">Skill 下载失败。请确认平台服务可用后重试；管理中心仍可打开。</p>:download==="done"?<p role="status">已发起下载。请在浏览器下载列表查看 MediaFlow-Skill.zip。</p>:null}
      <ol><li>解压下载包，让外部 Agent 读取 <code>SKILL.md</code>。</li><li>按包内说明配置本机 <code>config.json</code>，脚本通过配置连接 MediaFlow。不要把密钥粘贴到聊天。</li><li>先让 Agent 查询平台状态，再描述需要的操作；执行结果以平台回执为准。</li></ol>
      <p>设备操作仍需明确范围。普通咨询不会提交任务；未知结果先查询，不要重复执行。</p>
      <p><a href="/manage">管理中心</a> · <a href="/devices/guide">设备指南</a> · <a href="/content/guide">内容与任务指南</a> · <a href="/settings">设置与关于</a></p>
    </section>
  </main>;
}
