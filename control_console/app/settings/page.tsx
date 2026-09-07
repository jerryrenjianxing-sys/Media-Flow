import {NATIVE_CHAT_URL} from "../brand";
import {AboutSettings,PreferenceSettings} from "../components/settings-about";

export default function ManagementSettingsPage(){
  return <main className="page-shell management-page management-settings">
    <header className="management-heading"><div><h1>设置与关于</h1><p>平台设置与聊天设置独立，互不改变会话。</p></div><a href="/manage">返回管理中心</a></header>
    <section className="settings-section"><h2>模型与使用指南</h2><p><a href={NATIVE_CHAT_URL}>打开 Agent，使用原生设置管理聊天模型 →</a></p><p><a href="/content">业务视觉模型与内容配置 →</a></p><p><a href="/devices/guide">设备指南</a> · <a href="/content/guide">内容与任务指南</a> · <a href="/governance">数据与备份</a></p></section>
    <PreferenceSettings/>
    <AboutSettings/>
  </main>;
}
