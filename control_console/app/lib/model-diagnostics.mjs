const stages = { connect_or_headers: '连接或等待响应头', request_write: '上传请求', response_read: '读取响应', request: '请求模型', parse: '解析响应', schema: '验证响应字段' };
const kinds = { transient_network: '临时网络故障', authentication: '鉴权失败', balance: '上游额度不足', rate_limited: '上游限流', permanent_rejection: '上游拒绝', invalid_request: '请求不可用', invalid_response: '响应格式错误', provider_failure: '上游服务故障' };
export function modelDiagnosticText(error) {
  if (!error || !kinds[error.kind]) return '';
  const details = error.diagnostics || {};
  const parts = [kinds[error.kind], stages[details.stage] || '失败阶段未记录'];
  if (Number.isInteger(details.elapsed_ms) && details.elapsed_ms >= 0) parts.push(`耗时 ${(details.elapsed_ms / 1000).toFixed(1)} 秒`);
  if (Number.isInteger(error.attempts) && error.attempts > 0) parts.push(`请求 ${error.attempts} 次（重试 ${error.attempts - 1} 次）`);
  return parts.join(' · ');
}
