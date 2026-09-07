import {WorkspaceIcon,type WorkspaceIconName} from "../components/workspace-sidebar";
import {NATIVE_CHAT_URL} from "../brand";
const groups=[{title:"执行工作区",description:"从准备设备到查看结果，所有操作都有回执。",entries:[
  ["/devices","设备管理","画面、连接、虚拟机与当前待办","devices"],
  ["/workbench","任务台","编辑完整参数，预览并提交任务","create"],
  ["/run","运行与停止","查看真实进度、暂停及安全停止","run"],
  ["/records","结果与证据","追踪结果、异常现场和纠错记录","results"],
]},{title:"配置与知识",description:"低频管理集中在这里，日常操作交给 Agent。",entries:[
  ["/interactions","互动记录","巡检回执、聚合通知与现场证据","results"],
  ["/content","资产配置","模型、内容计划和任务预设","assets"],
  ["/governance","数据治理","证据保留、备份与数据检查","assets"],
  ["/settings","设置","模型连接、操作指南、偏好与关于","assets"],
]}];
export default function ManagementPage(){return <main className="page-shell management-page"><header className="management-heading"><div><p className="eyebrow">WORKSPACE</p><h1>管理中心</h1><p>掌握运行全貌，随时回到对话。</p></div><a className="primary" href={NATIVE_CHAT_URL}>回到 Agent →</a></header>{groups.map(group=><section className="management-section" key={group.title}><header><h2>{group.title}</h2><p>{group.description}</p></header><div className="management-grid">{group.entries.map(([href,title,description,icon])=><a key={href} href={href}><span className="management-icon"><WorkspaceIcon name={icon as WorkspaceIconName}/></span><div><h3>{title}</h3><p>{description}</p></div><span className="management-arrow">↗</span></a>)}</div></section>)}<footer className="management-help"><span>需要操作指引？</span><a href="/devices/guide">设备指南 ↗</a><a href="/content/guide">内容与任务指南 ↗</a></footer></main>;}
