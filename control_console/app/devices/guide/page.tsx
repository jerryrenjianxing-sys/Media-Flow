const steps = [
  ["01", "锁定当前手机", "核对友好名称、型号和 ADB 序列号，确认没有其他任务占用。"],
  ["02", "处理手机侧确认", "安装、安全、登录或身份验证由你在对应手机上完成；页面会停在“等待你操作”。"],
  ["03", "只读校准页面", "固定程序检查主页、搜索、视频结果、沉浸流和互动控件，不点赞、不收藏、不评论。"],
  ["04", "复验评论入口", "只允许打开评论区和输入入口；出现编辑焦点或键盘才算通过，不输入内容。"],
  ["05", "保存版本化档案", "固定程序复验通过后保存设备、抖音版本、显示签名和适配器版本。"],
  ["06", "运行3条零写入冒烟", "点赞、收藏、评论概率均为0；完成后才把这台真机交付为已就绪。"],
];

export default function RealDeviceInitializationGuide() {
  return <main className="app-shell prompt-guide-page"><div className="page-shell">
    <section className="records-hero prompt-guide-hero" data-motion><div><p className="eyebrow">REAL DEVICE INITIALIZATION</p><h1>真机 Agent 辅助初始化</h1><p>真机保留人工断点，固定程序负责复验和留证；默认不产生任何账号写入。</p></div><a className="secondary" href="/devices">返回设备</a></section>
    <div className="workbench-callout"><strong>开始前提</strong><span>USB 调试和 ADB 已授权、屏幕已解锁、抖音已安装并登录。开发者选项和首次授权不包含在初始化里。</span></div>
    <div className="prompt-guide-grid">{steps.map(([index, title, detail]) => <article className="panel prompt-guide-card" data-motion key={index}><header><span>{index}</span><div><h2>{title}</h2><p>{detail}</p></div></header></article>)}</div>
    <section className="panel prompt-guide-card" data-motion><h2>状态怎么理解</h2><ul><li>“等待你操作”是安装确认、登录或安全验证的人工断点，不是普通程序失败。</li><li>已有截图和 UI 树能解释的问题直接修固定规则；只有真正未知页面才由 MBH 只读探索。</li><li>失败或等待不会覆盖旧的已验证档案，未知写入结果也不会自动重放。</li></ul></section>
  </div></main>;
}
