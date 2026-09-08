export function platformSummary(snapshot, error) {
  if (error) return {state:'error', message:'平台状态暂不可读，当前设备与任务状态未确认。请重试，或进入管理中心检查。', metrics:null};
  if (!snapshot) return {state:'loading', message:'正在读取平台状态…', metrics:null};
  return {
    state:'ready',
    message:'平台服务已连接。设备是否可用、任务是否完成，以实时状态和执行回执为准。',
    metrics:{
      online:Array.isArray(snapshot.devices)?snapshot.devices.filter(device=>device.state==='device').length:null,
      running:typeof snapshot.task_summary?.running==='number'?snapshot.task_summary.running:null,
      pending:typeof snapshot.task_summary?.pending==='number'?snapshot.task_summary.pending:null,
      queue:typeof snapshot.paused==='boolean'?(snapshot.paused?'已暂停':'未暂停'):'未读取',
    },
  };
}

export async function loadSkillArchive(request) {
  const response=await request();
  if(!response.ok || !response.headers.get('content-type')?.toLowerCase().startsWith('application/zip')) throw new Error('skill_download_failed');
  const archive=await response.blob();
  if(!archive.size) throw new Error('skill_download_empty');
  return archive;
}

export async function loadSkillMarkdown(request) {
  const response=await request();
  if(!response.ok || !response.headers.get('content-type')?.toLowerCase().startsWith('text/markdown')) throw new Error('skill_copy_failed');
  const document=await response.text();
  if(!document.trim()) throw new Error('skill_copy_empty');
  return document;
}
