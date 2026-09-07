import type { DraftStore } from '../utils/draft-store'
import { Persist } from '../utils/persist'
import { legacySessionHref } from '../utils/session-route'
import { migrateDrafts } from './migration'

const request = async(path:string) => {
  const response=await fetch(path,{signal:AbortSignal.timeout(5000)})
  if(!response.ok)throw new Error('local_connection_unavailable')
  return response.json()
}

function startupNotice(failed=false) {
  const root=document.getElementById('root')
  if(!root)return
  root.replaceChildren()
  const panel=document.createElement('section')
  panel.style.cssText='max-width:520px;margin:18vh auto;padding:32px;font:16px/1.7 system-ui;color:inherit'
  const title=document.createElement('h1');title.textContent='MediaFlow'
  const message=document.createElement('p');message.textContent=failed?'助手尚未连接。会话和任务仍保留，请重试或进入管理中心。':'正在连接原生助手…'
  panel.append(title,message)
  if(failed){const retry=document.createElement('button');retry.textContent='重试连接';retry.onclick=()=>location.reload();panel.append(retry)}
  const manage=document.createElement('a');manage.href='/manage';manage.target='_blank';manage.rel='external noopener noreferrer';manage.textContent='打开管理中心';manage.style.marginLeft='16px';panel.append(manage)
  root.append(panel)
}

export async function prepareNative(drafts:DraftStore) {
  startupNotice()
  let connected=false
  const deadline=Date.now()+60000
  while(Date.now()<deadline) {
    try {await request('/mediaflow/bootstrap');connected=true;break} catch {await new Promise(resolve=>setTimeout(resolve,1000))}
  }
  if(!connected){startupNotice(true);return new Promise<void>(()=>{})}
  const session=async(id:string)=>request('/opencode/session/'+encodeURIComponent(id))
  try {
    await migrateDrafts(localStorage,drafts,session,s=>{
      const target=Persist.session(s.directory,s.id,'prompt')
      return `${target.storage??'default'}:${target.key}`
    })
  } catch { /* Original browser drafts remain untouched; reload retries. */ }
  const old=new URLSearchParams(location.search).get('session')
  if(old&&/^ses_[A-Za-z0-9_-]+$/.test(old)) {
    try {
      const current=await session(old)
      history.replaceState(null,'',legacySessionHref(current.directory,current.id))
    } catch {
      // Keep the URL to retry, without inventing an empty replacement session.
      startupNotice(true);return new Promise<void>(()=>{})
    }
  }
  document.getElementById('root')?.replaceChildren()
}
