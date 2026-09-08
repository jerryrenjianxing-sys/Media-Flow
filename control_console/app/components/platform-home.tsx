"use client";
/* eslint-disable @next/next/no-img-element */
import {useEffect,useRef,useState} from "react";
import Link from "next/link";
import {API} from "./workbench-types";
import {fetchLocalApi} from "../lib/local-api";
import {loadSkillArchive,loadSkillMarkdown} from "../lib/platform-home-state.mjs";

const agents=[['claude-code','Claude Code'],['cursor','Cursor'],['codex','Codex'],['copilot','GitHub Copilot'],['windsurf','Windsurf'],['gemini','Gemini'],['cline','Cline'],['amp','Amp'],['antigravity','Antigravity'],['openclaw','OpenClaw'],['droid','Droid'],['goose','Goose'],['kilo','Kilo'],['kiro-cli','Kiro CLI'],['nous-research','Hermes'],['opencode','OpenCode'],['roo','Roo'],['trae','Trae'],['vscode','VS Code'],['zed','Zed']];
type ActionState='idle'|'loading'|'done'|'error';

function Icon({kind}:{kind:'download'|'copy'|'arrow'|'grid'|'pause'|'play'}){
  const paths={download:<><path d="M12 3v12m-4-4 4 4 4-4"/><path d="M4 16v4h16v-4"/></>,copy:<><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V3H3v13h5"/></>,arrow:<path d="M5 12h14m-5-5 5 5-5 5"/>,grid:<><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,pause:<path d="M8 5v14M16 5v14"/>,play:<path d="m8 4 12 8-12 8Z"/>};
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[kind]}</svg>;
}

export default function PlatformHome({legacySession=false}:{legacySession?:boolean}){
  const [download,setDownload]=useState<ActionState>('idle'),[copy,setCopy]=useState<ActionState>('idle');
  const [manual,setManual]=useState(''),[paused,setPaused]=useState(false),[dark,setDark]=useState(false);
  const downloading=useRef(false),copying=useRef(false),dialog=useRef<HTMLDialogElement>(null),manualText=useRef<HTMLTextAreaElement>(null);
  useEffect(()=>{const sync=()=>setDark(document.documentElement.dataset.theme==='dark');sync();const observer=new MutationObserver(sync);observer.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});return()=>observer.disconnect();},[]);
  useEffect(()=>{if(manual){dialog.current?.showModal();manualText.current?.focus();manualText.current?.select();}},[manual]);
  async function downloadSkill(){
    if(downloading.current)return;
    downloading.current=true;setDownload('loading');
    try{
      const archive=await loadSkillArchive(()=>fetchLocalApi(`${API}/api/platform-skill`,{cache:'no-store'},15000));
      const url=URL.createObjectURL(archive),link=document.createElement('a');
      link.href=url;link.download='MediaFlow-Skill.zip';document.body.append(link);link.click();link.remove();
      window.setTimeout(()=>URL.revokeObjectURL(url),1000);setDownload('done');
    }catch{setDownload('error');}finally{downloading.current=false;}
  }
  async function copySkill(){
    if(copying.current)return;
    copying.current=true;setCopy('loading');
    try{
      const markdown=await loadSkillMarkdown(()=>fetchLocalApi(`${API}/api/platform-skill?format=markdown`,{cache:'no-store'},15000));
      try{await navigator.clipboard.writeText(markdown);setCopy('done');}
      catch{setCopy('idle');setManual(markdown);}
    }catch{setCopy('error');}finally{copying.current=false;}
  }
  function toggleTheme(){const theme=dark?'light':'dark';document.documentElement.dataset.theme=theme;localStorage.setItem('mediaflow-theme',theme);window.dispatchEvent(new Event('mediaflow-theme-change'));}
  return <main className="skill-home" id="main-content">
    <header className="skill-header">
      <Link href="/" className="skill-brand" aria-label="MediaFlow 首页"><img src="/favicon.svg" alt="" width="28" height="28"/><span>MediaFlow</span></Link>
      <span className="skill-header-caption">你的 Agent，你的工作流。</span>
      <button type="button" className="skill-theme" aria-label={dark?'切换到浅色主题':'切换到深色主题'} onClick={toggleTheme}><span className={`theme-icon ${dark?'sun':'moon'}`} aria-hidden="true"/></button>
    </header>
    <section className="skill-hero" aria-labelledby="skill-title">
      <p className="skill-eyebrow"><span/>为你的 Agent，接入行动力</p>
      <h1 id="skill-title">MediaFlow<span className="skill-title-dot">.</span></h1>
      <h2>一份 Skill，<span>一站式掌控媒体自动化。</span></h2>
      <p className="skill-description">把设备、任务与工作流，交给你熟悉的 Agent。<br/>复制或下载，让想法直接连接本地平台。</p>
      <div className="skill-actions">
        <button className="skill-button skill-button-primary" type="button" disabled={download==='loading'} onClick={()=>void downloadSkill()}><Icon kind="download"/>{download==='loading'?'正在准备…':download==='error'?'重试下载':'下载 Skill'}</button>
        <button className="skill-button skill-button-secondary" type="button" disabled={copy==='loading'} onClick={()=>void copySkill()}><Icon kind="copy"/>{copy==='loading'?'正在读取…':copy==='done'?'已复制':'复制 Skill'}</button>
      </div>
      <p className="skill-format">Markdown 格式 · 包含操作说明与完整脚本</p>
      <div className="skill-feedback" aria-live="polite" aria-atomic="true">
        {download==='error'?<p className="skill-error">下载暂不可用，请重试或进入任务台检查本机服务。</p>:download==='done'?<p>已发起下载，请查看浏览器下载列表。</p>:null}
        {copy==='error'?<p className="skill-error">读取失败。点击“复制 Skill”重试，或到任务台检查本机服务。</p>:copy==='done'?<p>完整 Markdown 已复制，粘贴给你的 Agent 即可。</p>:null}
      </div>
      {legacySession?<p className="skill-legacy">旧会话数据仍保留；此入口已改为外部 Skill，不会删除历史记录。</p>:null}
    </section>
    <section className={`skill-agents ${paused?'is-paused':''}`} aria-labelledby="skill-agents-heading">
      <div className="skill-agents-heading"><h2 id="skill-agents-heading">与你熟悉的 Agent 协作</h2><button className="skill-marquee-toggle" type="button" onClick={()=>setPaused(!paused)} aria-label={paused?'继续图标滚动':'暂停图标滚动'} aria-pressed={paused}><Icon kind={paused?'play':'pause'}/></button></div>
      <div className="skill-marquee"><div className="skill-marquee-track">
        {[0,1].map(group=><ul className="skill-agent-group" key={group} aria-hidden={group===1?true:undefined}>{agents.map(([id,name])=><li key={id}><img src={`/agents/${id}.svg`} alt="" width="28" height="28"/><span>{name}</span></li>)}</ul>)}
      </div></div>
      <p className="skill-agents-note">适用于支持 Skill、文件与本地命令的 Agent；具体接入能力以所用工具为准。</p>
    </section>
    <footer className="skill-footer"><span>本地运行 · 自由连接</span><a className="skill-workbench" href="/manage"><Icon kind="grid"/>任务台<Icon kind="arrow"/></a></footer>
    <dialog ref={dialog} className="skill-copy-dialog" onClose={()=>setManual('')}>
      <h2>手动复制 Skill</h2><p>浏览器未允许直接复制。下方已选中完整 Markdown，请按 Ctrl+C（Mac 为 ⌘C）。</p>
      <textarea ref={manualText} aria-label="完整 Skill Markdown" readOnly value={manual}/>
      <button type="button" className="skill-button skill-button-primary" onClick={()=>dialog.current?.close()}>完成</button>
    </dialog>
  </main>;
}
