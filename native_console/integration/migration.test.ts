import { expect, test } from 'bun:test'
import { migrateDrafts } from './migration'

function fixture() {
  const legacy = new Map<string, string>([
    ['mediaflow-agent-ui:ses_one', JSON.stringify({text:'未发送草稿',sentText:'不得发送'})],
  ])
  const native = new Map<string,string>()
  const storage = {get length(){return legacy.size}, key:(i:number)=>[...legacy.keys()][i]??null,
    getItem:(k:string)=>legacy.get(k)??null, setItem:(k:string,v:string)=>{legacy.set(k,v)}}
  const drafts = {getItem:async(k:string)=>native.get(k)??null,setItem:async(k:string,v:string)=>{native.set(k,v)}}
  const session = async(id:string)=>({id,directory:'fixture'})
  return {legacy,native,storage,drafts,session}
}
test('moves only unsent text through native draft API once and preserves source', async()=>{
  const f=fixture()
  await migrateDrafts(f.storage,f.drafts,f.session,(s)=>s.id)
  await migrateDrafts(f.storage,f.drafts,f.session,(s)=>s.id)
  expect(f.native.size).toBe(1)
  expect(JSON.parse(f.native.get('ses_one')!).prompt[0].content).toBe('未发送草稿')
  expect(f.native.get('ses_one')).not.toContain('不得发送')
  expect(f.legacy.has('mediaflow-agent-ui:ses_one')).toBe(true)
})
test('does not overwrite native draft and retains explicit retry receipt',async()=>{
  const f=fixture(); f.native.set('ses_one',JSON.stringify({prompt:[{type:'text',content:'原生草稿'}]}))
  const result=await migrateDrafts(f.storage,f.drafts,f.session,s=>s.id)
  expect(result.ses_one).toBe('conflict')
  expect(f.native.get('ses_one')).toContain('原生草稿')
})
test('failed session lookup can retry without sending or duplicating',async()=>{
  const f=fixture()
  const failed=await migrateDrafts(f.storage,f.drafts,async()=>{throw Error('offline')},s=>s.id)
  expect(failed.ses_one).toBe('retryable')
  expect(f.native.size).toBe(0)
  expect((await migrateDrafts(f.storage,f.drafts,f.session,s=>s.id)).ses_one).toBe('migrated')
})
test('preserves staged native attachments and model even without prompt text',async()=>{
  const f=fixture()
  const current=JSON.stringify({prompt:[],context:{items:[{type:'file',path:'fixture.txt'}]},model:{modelID:'native-choice'}})
  f.native.set('ses_one',current)
  expect((await migrateDrafts(f.storage,f.drafts,f.session,s=>s.id)).ses_one).toBe('conflict')
  expect(f.native.get('ses_one')).toBe(current)
})
