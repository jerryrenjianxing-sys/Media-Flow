export function formatTrialBudget(value) {
  if(value.limit==='unlimited') return `已用 ${value.used} 次（不限次数）。${value.message}`;
  return `已用 ${value.used}/${value.limit} 次。${value.message}`;
}

export default {
  mount(container) {
    const root=document.createElement('section');
    root.style.cssText='padding:24px;max-width:720px;margin:auto;line-height:1.7';
    const title=document.createElement('h2');title.textContent='MediaFlow 管理中心';
    const note=document.createElement('p');note.textContent='平台后台独立运行。打开管理中心不会新建或清空当前对话；关闭管理标签即可返回这里。';
    const link=document.createElement('a');link.href='http://127.0.0.1:3001/manage';link.target='_blank';link.rel='noopener';link.textContent='打开管理中心 ↗';
    link.style.cssText='display:inline-block;padding:10px 18px;border:1px solid currentColor;border-radius:8px;color:inherit';
    const info=document.createElement('p');info.textContent='本次为Pi小范围试用。Token Plan套餐Credits以千问工作台为准，界面美元估算不代表套餐实际消耗。';
    const download=document.createElement('a');download.href='/plugins-api/mediaflow/skill';download.textContent='下载 MediaFlow Skill（外部Agent可用）';download.style.cssText='display:block;margin-top:18px;color:inherit';
    const budget=document.createElement('p');budget.textContent='正在读取试用额度…';
    const controller=new AbortController();
    fetch('/plugins-api/mediaflow/trial',{signal:controller.signal}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(value=>{budget.textContent=formatTrialBudget(value);}).catch(()=>{if(!controller.signal.aborted)budget.textContent='额度记录暂不可读，请检查独立Agent服务。';});
    root.append(title,note,link,download,budget,info);container.append(root);
    return ()=>{controller.abort();root.remove();};
  }
};
