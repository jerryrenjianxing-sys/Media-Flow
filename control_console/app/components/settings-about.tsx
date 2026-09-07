"use client";
import {useEffect,useState} from "react";
import {fetchLocalApi} from "../lib/local-api";
import {BRAND} from "../brand";

export function AboutSettings(){
  const [version,setVersion]=useState("正在读取本机版本…");
  useEffect(()=>{let active=true;void fetchLocalApi("http://127.0.0.1:48138/api/status",{},5000).then(r=>r.json()).then(s=>{if(active)setVersion(s.product_version?.display_version||"版本信息暂不可用");}).catch(()=>{if(active)setVersion("本机服务未连接，版本信息暂不可用");});return()=>{active=false;};},[]);
  return <section className="settings-section"><p className="eyebrow">ABOUT</p><h3>{BRAND.name}</h3><p>{BRAND.tagline}</p><code>{version}</code><p>本机开发测试版本。通过外部 Agent 加载 MediaFlow Skill；执行情况以任务回执和证据为准。</p><details><summary>关于与开源许可</summary><p>管理界面：React（MIT）、Vinext（MIT）。历史 Pi、pi-web-ui、OpenCode 组件及其 MIT 许可和原会话数据保留，不再作为平台内置聊天入口。第三方组件保留各自版权与许可。</p></details><details><summary>使用说明与免责声明</summary><p>请仅操作你有权管理的设备、账号与内容。模型建议可能有误，实际动作仍由固定执行器与设备独占机制约束。实验功能不代表已完成稳定性验收。</p></details></section>;
}
export function PreferenceSettings(){
  const [theme,setTheme]=useState("system");
  useEffect(()=>{const sync=window.setTimeout(()=>setTheme(localStorage.getItem("mediaflow-theme")||"system"),0);return()=>clearTimeout(sync);},[]);
  function change(value:string){setTheme(value);if(value==="system")localStorage.removeItem("mediaflow-theme");else localStorage.setItem("mediaflow-theme",value);document.documentElement.dataset.theme=value==="system"?(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"):value;window.dispatchEvent(new Event("mediaflow-theme-change"));}
  return <section className="settings-section"><h3>外观</h3><label htmlFor="theme-preference">主题<select id="theme-preference" value={theme} onChange={e=>change(e.target.value)}><option value="system">跟随系统</option><option value="light">冷白 · 浅色</option><option value="dark">石墨 · 深色</option></select></label><p>动态效果遵循系统的“减少动画”偏好。外部 Agent 的输入快捷键由其自身设置决定。</p></section>;
}
