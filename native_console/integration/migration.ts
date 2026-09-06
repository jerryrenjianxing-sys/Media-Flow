/** One-way integration receipt, not a second conversation or draft store. */
type Storage = Pick<globalThis.Storage, 'length'|'key'|'getItem'|'setItem'>
type Drafts = {getItem(key:string): Promise<string|null|undefined>; setItem(key:string,value:string):Promise<unknown>}
type Session = {id:string; directory:string}
const receiptKey='mediaflow-native-drafts-dev24'

export async function migrateDrafts(storage:Storage,drafts:Drafts,
  session:(id:string)=>Promise<Session>,key:(session:Session)=>string) {
  let receipt:Record<string,string>={}
  try {receipt=JSON.parse(storage.getItem(receiptKey)??'{}')} catch { /* keep original values */ }
  const names=Array.from({length:storage.length},(_,i)=>storage.key(i)).filter((s):s is string=>!!s?.startsWith('mediaflow-agent-ui:'))
  for(const name of names) {
    const id=name.slice('mediaflow-agent-ui:'.length)
    if(!/^ses_[A-Za-z0-9_-]+$/.test(id)||receipt[id]==='migrated')continue
    try {
      const value=JSON.parse(storage.getItem(name)??'{}')
      if(typeof value.text!=='string'||!value.text.trim())continue
      const destination=key(await session(id))
      const current=await drafts.getItem(destination)
      if(current) {
        const parsed=JSON.parse(current)
        const parts=parsed.prompt??[]
        const staged=!!parsed.context?.items?.length||!!parsed.model
        if(staged||parts.some((part:{type?:string;content?:string})=>part.type!=='text'||part.content?.trim())) {
          // Native wins. No silent overwrite or concatenation of distinct drafts.
          receipt[id]=!staged&&parts.length===1&&parts[0].content===value.text?'migrated':'conflict'
          storage.setItem(receiptKey,JSON.stringify(receipt));continue
        }
      }
      await drafts.setItem(destination,JSON.stringify({prompt:[{type:'text',content:value.text,start:0,end:value.text.length}],
        cursor:value.text.length,context:{items:[]}}))
      receipt[id]='migrated'
    } catch {receipt[id]='retryable'}
    storage.setItem(receiptKey,JSON.stringify(receipt))
  }
  return receipt
}
