const steps = [
  ["01", "锁定当前手机", "核对友好名称、型号和 ADB 序列号，确认没有其他任务占用。"],
  ["02", "处理手机侧确认", "安装、安全、登录或身份验证由你在对应手机上完成；页面会停在“等待你操作”。"],
  ["03", "按任务检查页面", "看屏检查连接和截图；浏览再确认主页与基本导航，不要求先完成整套初始化。"],
  ["04", "需要输入时再验证", "搜索或评论才准备中文输入，必须实际写入测试文字并读回；仅启用输入法不算通过。"],
  ["05", "保留已通过的能力", "每项准备成功立即保存，失败只处理受影响步骤，不重复检查已有结果。"],
  ["06", "选择任务或继续准备", "未登录不阻断管理和参数准备；实际遇到登录或安全验证时由你处理。消息巡检只看首页角标，不进入消息页。"],
];

export default function RealDeviceInitializationGuide() {
  return <main className="app-shell prompt-guide-page"><div className="page-shell">
    <section className="records-hero prompt-guide-hero" data-motion><div><p className="eyebrow">DEVICE PREPARATION</p><h1>真机按需准备</h1><p>默认使用标准 MuMu；明确选择真机时复用已有能力。普通准备不点赞、不收藏、不评论。</p></div><a className="secondary" href="/devices">返回设备</a></section>
    <div className="workbench-callout"><strong>先连接，再按需准备</strong><span>在手机上开启 USB 调试并确认 ADB 授权，核对实际设备与占用。抖音安装、登录、输入和业务模型按任务需要分别处理，不是统一的管理门槛。</span></div>
    <div className="prompt-guide-grid">{steps.map(([index, title, detail]) => <article className="panel prompt-guide-card" data-motion key={index}><header><span>{index}</span><div><h2>{title}</h2><p>{detail}</p></div></header></article>)}</div>
    <section className="panel prompt-guide-card" data-motion><h2>状态怎么理解</h2><ul><li>“等待你操作”是当前步骤需要安装确认、登录或安全验证；不意味着设备管理不可用。</li><li>三次规则验证是开发验收，不是每台设备的使用门槛。开发 MBH 带测需与 Worker 交接设备锁。</li><li>失败或等待不会覆盖旧的已验证档案，未知写入结果也不会自动重放。</li></ul></section>
  </div></main>;
}
