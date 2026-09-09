const labels = { pending: '等待执行', running: '执行中', waiting_model: '等待模型', waiting_device: '等待设备', waiting_user: '等待用户', completed: '已完成', degraded: '完成，有异常', failed: '失败', stopped: '已停止', cancelled: '已取消' };
export function taskStatusLabel(status, task) { return status === 'stopped' && (task?.error === 'stopped_by_user' || task?.result?.stopped_by_user === true) ? '用户停止' : labels[status] || status; }
export function supportedTaskProgress(progress) { return progress?.schema_supported === true ? progress : undefined; }
export function taskControlActions(status, progress) { return status === 'pending' ? ['stop_task'] : isWaitingTask(status) ? (progress?.available_actions || []).filter((action) => ['resume_task', 'stop_task'].includes(action)) : []; }
export function isWaitingTask(status) { return ['waiting_model', 'waiting_device', 'waiting_user'].includes(status); }
export async function controlTask(transport, base, taskId, action, requestId) {
  if (!['resume_task', 'stop_task'].includes(action)) throw new Error('不支持的任务操作');
  const response = await transport(`${base}/api/tasks/${encodeURIComponent(taskId)}/${action === 'resume_task' ? 'resume' : 'stop'}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: requestId }),
  });
  const result = await response.json();
  if (!response.ok && !result.status) throw new Error(result.error || '任务操作失败');
  return result;
}
