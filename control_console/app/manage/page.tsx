/* eslint-disable @next/next/no-html-link-for-pages */
const entries=[
  ["/workbench","任务台","完整任务参数、草稿和预览"],
  ["/devices","设备管理","虚拟机、实时画面与设备问题"],
  ["/run","运行与停止","查看队列、暂停和安全停止任务"],
  ["/records","结果与证据","历史任务、纠错记录和互动凭证"],
  ["/content","资产与设置","模型、内容计划和数据存储"],
  ["/governance","数据治理","证据保留、备份与检查"],
];
export default function ManagementPage(){return <main className="page-shell management-page"><header><p className="eyebrow">MEDIAFLOW</p><h1>管理中心</h1><p>完整功能保留在这里。日常操作可以回到助手对话。</p><a className="secondary" href="/">返回助手</a></header><div className="management-grid">{entries.map(([href,title,description])=><a className="panel" key={href} href={href}><h2>{title} ↗</h2><p>{description}</p></a>)}</div><section className="panel" id="licenses"><h2>关于与开源许可</h2><p>MediaFlow · 本机媒体自动化平台</p><p>对话引擎：OpenCode（MIT）。界面：React（MIT）、Vinext（MIT）。第三方组件保留各自版权与许可；安装版附带完整第三方声明。</p></section></main>;}
