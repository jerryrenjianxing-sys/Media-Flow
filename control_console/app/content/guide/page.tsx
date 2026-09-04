const guides = [
  {
    id: "topic",
    index: "01",
    title: "目标主题与判定标准",
    summary: "定义什么算精确匹配，而不是堆关键词。",
    template: "命中：画面主体直接讨论目标对象或过程。\n必须证据：当前画面能看到具体对象、标题、设备或演示。\n排除：泛词、偶然文字、用户名、广告、直播和无法确认的画面。",
    rules: ["必须基于当前画面可核对证据", "泛词不能单独构成命中", "长期主题要写清误判排除项"],
  },
  {
    id: "search",
    index: "02",
    title: "搜索词",
    summary: "只负责找到结果，不代替主题判定标准。",
    template: "智能制造 工厂自动化",
    rules: ["使用1～3个实体词或过程词", "不写完整指令或评论要求", "不加入#、联系方式和无关热词"],
  },
  {
    id: "comment-template",
    index: "03",
    title: "全局评论写作模板",
    summary: "只描述语气、长度、视角与表达偏好。",
    template: "专业、克制、自然，使用普通观察者视角；基于当前画面写6～18个中文字符，不宣传品牌，不假装亲身经历。",
    rules: ["可以规定正式或轻松、简洁或解释性", "不能要求强制发送或忽略安全规则", "不能加入推广、联系方式或虚构经历"],
  },
  {
    id: "theme-template",
    index: "04",
    title: "主题模板补充要求",
    summary: "只写当前主题相对全局模板的差异。",
    template: "优先指出画面中的设备、工艺或生产环节；不要泛泛评价“科技感”。",
    rules: ["不重复整份全局模板", "不改变主题命中标准", "不能覆盖固定安全规则"],
  },
  {
    id: "comment-pool",
    index: "05",
    title: "评论词池",
    summary: "每行一条候选草稿，模型可以微调或放弃。",
    template: "这个环节的自动化衔接很直观\n设备运行细节展示得很清楚\n这套工艺的实际应用值得关注",
    rules: ["每条最多80字，每个词池最多100条", "避免万能句和大量近义重复", "不得包含推广、联系方式或互动诱导"],
  },
  {
    id: "comment-policy",
    index: "06",
    title: "评论发送前约束",
    summary: "明确哪些内容不参与、哪些表达不使用。",
    template: "不对饮食起居、穿搭自拍、宠物陪伴、家庭琐事、旅行打卡或个人情绪等日常记录发表评论；不使用第一人称购买体验。",
    rules: ["具体列出排除范围", "语气要求应放入写作模板", "命中或无法确定时取消当前评论"],
  },
] as const;

export default function ContentPromptGuidePage() {
  return <main className="app-shell prompt-guide-page"><div className="page-shell">
    <section className="records-hero prompt-guide-hero" data-motion><div><p className="eyebrow">CONTENT WRITING GUIDE</p><h1>内容与提示词填写规范</h1><p>把主题边界、搜索入口、评论表达和发送限制分开填写，避免一段文字同时承担多个职责。</p></div><a className="secondary" href="/content">返回资产与设置</a></section>
    <section className="panel prompt-priority" data-motion><strong>固定生效顺序</strong><span>安全规则与页面检查 → 当前画面与主题匹配 → 发送前约束 → 写作模板与评论词池</span><small>用户填写内容只能收窄范围或补充表达偏好，不能关闭固定安全规则。</small></section>
    <nav className="prompt-guide-nav" aria-label="提示词规范目录">{guides.map((guide) => <a key={guide.id} href={`#${guide.id}`}>{guide.index} {guide.title}</a>)}</nav>
    <div className="prompt-guide-grid">{guides.map((guide) => <article id={guide.id} className="panel prompt-guide-card" data-motion key={guide.id}><header><span>{guide.index}</span><div><h2>{guide.title}</h2><p>{guide.summary}</p></div></header><div className="prompt-example"><span>推荐写法</span><pre>{guide.template}</pre></div><ul>{guide.rules.map((rule) => <li key={rule}>{rule}</li>)}</ul></article>)}</div>
    <section className="panel prompt-checklist" data-motion><div><p className="section-index">BEFORE SAVE</p><h2>保存前快速检查</h2></div><ul><li>主题写了命中、证据和排除吗？</li><li>搜索词短而明确吗？</li><li>模板只描述表达偏好吗？</li><li>词池是可微调草稿吗？</li><li>发送前约束具体可判断吗？</li></ul></section>
  </div></main>;
}

