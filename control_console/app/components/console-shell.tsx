"use client";
/* eslint-disable @next/next/no-img-element */
import {usePathname} from "next/navigation";
import {createContext,useCallback,useContext,useEffect,useState} from "react";
import {fetchLocalApi} from "../lib/local-api";
import {BRAND,PLATFORM_HOME_URL} from "../brand";
import {API} from "./workbench-types";
import type {PlatformSnapshot} from "../lib/platform-home-state.mjs";
import InteractionAlertBanner from "./interaction-alert-banner";

const PlatformStatusContext=createContext<{status:PlatformSnapshot|null;error:boolean;refresh:()=>Promise<void>}>({status:null,error:false,refresh:async()=>{}});
export const usePlatformStatus=()=>useContext(PlatformStatusContext);
const links=[["/manage","总览"],["/devices","设备"],["/workbench","任务台"],["/run","运行"],["/records","结果与证据"],["/interactions","消息巡检"],["/content","资产配置"],["/governance","数据治理"]];
export default function ConsoleShell({children}:{children:React.ReactNode}){
  const path=usePathname();
  const [status,setStatus]=useState<PlatformSnapshot|null>(null),[error,setError]=useState(false),[dark,setDark]=useState(false);
  const refresh=useCallback(async()=>{try{const r=await fetchLocalApi(`${API}/api/status`,{cache:"no-store"},5000);if(!r.ok)throw Error();setStatus(await r.json());setError(false);}catch{setError(true);}},[]);
  useEffect(()=>{if(path==='/')return;const first=setTimeout(()=>void refresh(),0),timer=setInterval(()=>void refresh(),5000);return()=>{clearTimeout(first);clearInterval(timer);};},[path,refresh]);
  useEffect(()=>{const sync=()=>setDark(document.documentElement.dataset.theme==="dark");const initial=setTimeout(sync,0);const system=matchMedia("(prefers-color-scheme: dark)");const changed=()=>{if(!localStorage.getItem("mediaflow-theme"))document.documentElement.dataset.theme=system.matches?"dark":"light";sync();};system.addEventListener("change",changed);window.addEventListener("mediaflow-theme-change",sync);return()=>{clearTimeout(initial);system.removeEventListener("change",changed);window.removeEventListener("mediaflow-theme-change",sync);};},[]);
  const online=status?.devices?.filter(d=>d.state==="device").length||0,issues=status?.virtualization?.issues?.length||0,running=status?.task_summary?.running||0,pending=status?.task_summary?.pending||0;
  if(path==='/')return <><a className="skip-link" href="#main-content">跳到主要内容</a>{children}</>;
  return <div className="mf-shell mf-management-shell">
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    <header className="mf-topbar">
      <a className="mf-brand" href={PLATFORM_HOME_URL} aria-label={BRAND.fullName}><img src="/favicon.svg" alt=""/><strong>MediaFlow</strong><span>平台首页</span></a>
      <div className="mf-global-actions">
        {error?<button className="mf-status mf-error" onClick={()=>void refresh()}>服务未连接 · 重试</button>:<a className="mf-status" href="/run" title={status?`${online} 台在线，${running} 个运行，${pending} 个排队${status.paused?"，队列暂停":""}`:"正在连接本机服务"}><i className={`mf-status-dot ${online?"is-online":""}`}/><span>{status?`${online} 台在线`:"连接中"}</span><b>{!status?"未确认":running?`${running} 运行`:status.paused?"已暂停":"待命"}</b></a>}
        {!!issues&&<a className="mf-issues" href="/devices#device-issues" aria-label={`${issues} 项问题待处理`} title="查看具体问题及处理入口"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2 19 18H1Z"/><path d="M10 7v5m0 2v1"/></svg><span>{issues}</span></a>}
        <a className="mf-stop" href="/run" title="暂停队列或安全停止任务">停止入口</a>
        <button className="theme-toggle" aria-label={dark?"切换到浅色主题":"切换到深色主题"} onClick={()=>{const next=dark?"light":"dark";document.documentElement.dataset.theme=next;localStorage.setItem("mediaflow-theme",next);setDark(!dark);}}><span className={`theme-icon ${dark?"sun":"moon"}`} aria-hidden="true"/></button>
      </div>
    </header>
    <nav className="mf-management-nav" aria-label="管理工作区"><a className="mf-back-agent" href={PLATFORM_HOME_URL} aria-current={path==="/"?"page":undefined}>平台首页</a><div>{links.map(([href,label])=><a key={href} href={href} aria-current={path===href?"page":undefined}>{label}</a>)}</div><a href="/settings">设置</a></nav>
    <InteractionAlertBanner/>
    <PlatformStatusContext.Provider value={{status,error,refresh}}><div id="main-content" className="workspace-main mf-content" tabIndex={-1}>{children}</div></PlatformStatusContext.Provider>
  </div>;
}
