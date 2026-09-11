export const navigationFeedback: Record<string, string> = {
  worker_interrupted: "执行者中断，进度已保存；这不代表设备离线。核对后台与设备连接后，可恢复原任务，不重放未知动作。",
  visual_standard_mumu_required: "视觉增强仅适用于已登记的900×1600、320 DPI MuMu；请刷新库存并复核显示配置。",
  visual_navigation_unsafe_page: "当前是会话、个人主页或受保护页面，已停止识别点击；请查看异常现场并恢复安全主页。",
  model_network: "模型网络连接失败；请检查网络后重试，ADB连接和看屏不受影响。",
  visual_navigation_unavailable: "固定规则未能识别该页；请配置视觉模型后重新检查。本次未点击未知目标。",
  visual_deadline_exceeded: "视觉识别超过20秒，已停止等待；请检查模型连接后重试，迟到坐标不会执行。",
  engagement_deadline_exceeded: "巡检已达到时间上限，扫描结果未确认完整；请查看异常现场后重试。",
  visual_candidate_stale: "识别期间页面发生变化，旧坐标已丢弃；请稳定页面后重试。",
  visual_target_not_allowed: "识别目标不属于允许的导航范围，已阻止点击；请查看异常现场。",
  visual_region_invalid: "模型返回的位置无效，已阻止点击；请查看异常现场。",
  visual_page_mismatch: "模型识别的页面与当前流程不一致，已停止；请查看异常现场。",
  visual_response_invalid: "模型结果格式不可靠，未继续操作；请检查模型后重试。",
  visual_activity_items_incomplete: "该屏互动记录无法完整读取，本次不视为无新互动；请查看现场。",
  visual_boundary_evidence_missing: "模型没有提供已读或到底的依据，本次不视为无新互动。",
  visual_screenshot_invalid: "截图尺寸或方向不正确；请检查900×1600显示环境后重试。",
  navigation_lock_required: "未取得设备独占权限，未执行导航；请等待占用任务结束。",
  model_authentication: "模型鉴权失败；请在模型设置中检查Key。设备连接和看屏不受影响。",
  model_balance: "模型余额不足；请充值后重试，设备连接和看屏不受影响。",
  model_invalid_response: "模型返回内容不完整或格式错误；请查看现场并重新测试模型。",
  model_provider_failure: "模型服务暂时不可用；请稍后测试连接，本次没有继续点击。",
  model_timeout: "模型连接超时；请检查网络后重试，设备连接和看屏不受影响。",
  model_permanent_rejection: "模型服务拒绝该请求；请检查账号权限和服务限制，不会自动更换服务绕过拒绝。",
  model_rate_limited: "模型服务限流；请稍后重试，本次未继续猜测点击。",
  model_budget_exhausted: "历史本地预算限制记录；当前版本已取消此限制，请查看当前模型状态。",
  model_budget_price_unavailable: "历史价格查询失败记录；当前版本不再依赖价格查询放行请求。",
};

export function navigationReason(reason?: string | null): string | undefined {
  if (!reason) return undefined;
  if (reason.includes("密钥不可用")) return "视觉模型尚未配置；请前往模型设置，固定规则和设备连接仍可使用。";
  return navigationFeedback[reason];
}
