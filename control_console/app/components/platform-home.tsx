"use client";
/* eslint-disable @next/next/no-img-element */
import {useEffect,useRef,useState,useSyncExternalStore} from "react";
import Link from "next/link";
import {API} from "./workbench-types";
import {fetchLocalApi} from "../lib/local-api";
import {connectionFilename,loadSkillConnection} from "../lib/platform-home-state.mjs";

const agents=[['claude-code','Claude Code'],['cursor','Cursor'],['codex','Codex'],['copilot','GitHub Copilot'],['windsurf','Windsurf'],['gemini','Gemini'],['cline','Cline'],['amp','Amp'],['antigravity','Antigravity'],['openclaw','OpenClaw'],['droid','Droid'],['goose','Goose'],['kilo','Kilo'],['kiro-cli','Kiro CLI'],['nous-research','Hermes'],['opencode','OpenCode'],['roo','Roo'],['trae','Trae'],['vscode','VS Code'],['zed','Zed']];
type ActionState='idle'|'loading'|'done'|'error';
const subscribeDesktop=()=>()=>{};

function Icon({kind}:{kind:'download'|'copy'|'arrow'|'grid'|'pause'|'play'}){
  const paths={download:<><path d="M12 3v12m-4-4 4 4 4-4"/><path d="M4 16v4h16v-4"/></>,copy:<><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V3H3v13h5"/></>,arrow:<path d="M5 12h14m-5-5 5 5-5 5"/>,grid:<><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,pause:<path d="M8 5v14M16 5v14"/>,play:<path d="m8 4 12 8-12 8Z"/>};
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[kind]}</svg>;
}

export default function PlatformHome({legacySession=false}:{legacySession?:boolean}){
  const [download,setDownload]=useState<ActionState>('idle'),[copy,setCopy]=useState<ActionState>('idle');
  const [paused,setPaused]=useState(false),[dark,setDark]=useState(false);
  const desktop=useSyncExternalStore(subscribeDesktop,()=>typeof window.mediaflowDesktop?.copyConnectionFile==='function',()=>false);
  const [filename,setFilename]=useState('MediaFlow连接说明.md'),[fileId,setFileId]=useState(''),[notice,setNotice]=useState('');
  const downloading=useRef(false),copying=useRef(false);
  useEffect(()=>{const sync=()=>setDark(document.documentElement.dataset.theme==='dark');sync();const observer=new MutationObserver(sync);observer.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});return()=>observer.disconnect();},[]);
  async function downloadSkill(){
    if(downloading.current)return;
    downloading.current=true;setDownload('loading');setCopy('idle');setNotice('');
    try{
      const name=connectionFilename(filename);
      const documentText=await loadSkillConnection(()=>fetchLocalApi(`${API}/api/platform-skill?format=connect`,{cache:'no-store'},15000));
      const url=URL.createObjectURL(new Blob([documentText],{type:'text/markdown;charset=utf-8'})),link=document.createElement('a');
      link.href=url;link.download=name;document.body.append(link);link.click();link.remove();
      window.setTimeout(()=>URL.revokeObjectURL(url),1000);setDownload('done');setNotice('已发起MD下载，可将文件拖入或添加到本机Agent。');
    }catch{setDownload('error');setNotice('MD下载未完成，请检查文件名与平台连接后重试。');}finally{downloading.current=false;}
  }
  async function copySkill(){
    if(copying.current)return;
    copying.current=true;setCopy('loading');setDownload('idle');setNotice('');
    try{
      const name=connectionFilename(filename);
      const bridge=window.mediaflowDesktop;
      if(!bridge?.copyConnectionFile)throw new Error('desktop_required');
      const result=await bridge.copyConnectionFile({filename:name});
      setFileId(result.fileId);setCopy(result.copied?'done':'error');
      setNotice(result.copied?'MD文件已复制。请粘贴到支持文件附件的本机Agent；不能粘贴时可打开文件位置后拖入。':'文件复制未完成，可打开文件位置后拖入或选择此MD。');
    }catch{setCopy('error');setNotice('MD文件复制未完成，请检查文件名与平台连接后重试。');}finally{copying.current=false;}
  }
  async function revealFile(){try{await window.mediaflowDesktop?.revealConnectionFile?.({fileId});}catch{setNotice('文件位置暂不可用，请重新生成MD文件后重试。');}}
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
      <p className="skill-description">把设备、任务与工作流，交给你熟悉的 Agent。<br/>一份 Markdown，直接连接本地平台。</p>
      <div className="skill-actions">
        <button className="skill-button skill-button-primary" type="button" disabled={download==='loading'} onClick={()=>void downloadSkill()}><Icon kind="download"/>{download==='loading'?'正在准备…':download==='error'?'重试下载 MD':'下载 MD'}</button>
        <button className="skill-button skill-button-secondary" type="button" disabled={!desktop||copy==='loading'} title={desktop?'复制真正的MD文件':'文件复制需使用MediaFlow桌面版，可先下载MD'} onClick={()=>void copySkill()}><Icon kind="copy"/>{copy==='loading'?'正在准备…':copy==='done'?'MD文件已复制':'复制 MD 文件'}</button>
      </div>
      <p className="skill-format">仅提供 .md 文件 · {!desktop?'文件复制需桌面版 · ':''}完整 Skill 已随软件安装</p>
      <details className="skill-file-options"><summary>文件名</summary><label>MD文件名 <input aria-label="MD文件名" maxLength={100} value={filename} onChange={event=>setFilename(event.target.value)}/></label></details>
      <div className="skill-feedback" aria-live="polite" aria-atomic="true">
        {notice?<p className={copy==='error'||download==='error'?'skill-error':undefined}>{notice}</p>:null}
        {fileId&&desktop?<button type="button" className="text-button" onClick={()=>void revealFile()}>打开文件位置</button>:null}
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
  </main>;
}
