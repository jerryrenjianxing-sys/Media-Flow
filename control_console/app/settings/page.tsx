import {PLATFORM_HOME_URL} from "../brand";
import {AboutSettings,PreferenceSettings} from "../components/settings-about";

export default function ManagementSettingsPage(){
  return <main className="page-shell management-page management-settings">
    <header className="management-heading"><div><h1>设置与关于</h1><p>这里管理平台设置；外部 Agent 的模型与会话由其自身管理。</p></div><a href="/manage">返回管理中心</a></header>
    <section className="settings-section"><h2>Skill 与使用指南</h2><p><a href={PLATFORM_HOME_URL}>返回平台首页，下载外部 Agent 使用的 MediaFlow Skill →</a></p><p><a href="/content">业务视觉模型与内容配置 →</a></p><p><a href="/devices/guide">设备指南</a> · <a href="/content/guide">内容与任务指南</a> · <a href="/governance">数据与备份</a></p></section>
    <PreferenceSettings/>
    <AboutSettings/>
  </main>;
}
