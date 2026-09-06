"use client";
/* eslint-disable @next/next/no-html-link-for-pages, @next/next/no-img-element */
import { useCallback, useEffect, useRef, useState } from "react";
import { AgentRequestError, agentRequest, type AgentProvider } from "../lib/agent-api";
import { chooseSession, loadSessionUi, rememberSession, saveSessionUi, resizeComposer, type SessionUi } from "../lib/agent-workspace-state.mjs";
import AgentProviderSettings from "./agent-provider-settings";
import AgentMemories from "./agent-memories";
import AgentPlans, { type AgentPlan } from "./agent-plans";
import AgentCommands, { type AgentCommand } from "./agent-commands";
import AgentRepairs, { type AgentRepair } from "./agent-repairs";
import AgentInbox from "./agent-inbox";
import AgentUsage from "./agent-usage";
import AgentMarkdown from "./agent-markdown";

type EngineStatus = { state:string; message:string; notice?:string; pending_capabilities?:string[] };
type Session = {id:string;title:string;provider:string;model:string};
type Question = {id:string;questions:{question:string;options?:{label:string}[]}[]};
type Conversation = {
  permission?:{level:string;label:string;revision:number};
  updates?:{id:string;status:string;stage:string;message:string}[];
  messages:{id:string;role:string;error?:string;parts:{type:string;text?:string;tool?:string;status?:string}[]}[];
  state:string;questions:Question[];plans?:AgentPlan[];commands?:AgentCommand[];repairs?:AgentRepair[];notice?:string;
  turns?:{id:string;state:string;message_id:string}[];
};
const emptyConversation = ():Conversation => ({messages:[],state:"idle",questions:[]});
const emptyUi = ():SessionUi => ({text:"",requestId:null,notice:"",answers:{}});
export default function AgentWorkbench() {
  const [status,setStatus]=useState<EngineStatus>({state:"unknown",message:"正在连接本机状态…"});
  const [providers,setProviders]=useState<AgentProvider[]>([]);
  const [provider,setProvider]=useState("mediaflow-qwen-token-plan"), [model,setModel]=useState("qwen3.8-flash");
  const [sessions,setSessions]=useState<Session[]>([]), [sessionId,setSessionId]=useState("");
  const [conversation,setConversation]=useState<Conversation>(emptyConversation);
  const [ui,setUi]=useState<SessionUi>(emptyUi);
  const [busy,setBusy]=useState(false),[polling,setPolling]=useState(true),[loading,setLoading]=useState(true);
  const [readError,setReadError]=useState(""),[panel,setPanel]=useState<""|"execution"|"settings">("");
  const selectedSession=useRef(""), uiRef=useRef<SessionUi>(emptyUi()), restored=useRef(false);
  const inFlight=useRef(new Set<string>()), failures=useRef(0), startDeadline=useRef(0);
  const logRef=useRef<HTMLDivElement>(null),stickBottom=useRef(true);
  const inputRef=useRef<HTMLTextAreaElement>(null);
  const updateUi=useCallback((patch:Partial<SessionUi>)=>{
    const value={...uiRef.current,...patch};uiRef.current=value;setUi(value);
    if(selectedSession.current) saveSessionUi(window.localStorage,selectedSession.current,value);
  },[]);
  const select=useCallback((id:string,replace=false)=>{
    selectedSession.current=id;setSessionId(id);restored.current=true;
    const value=loadSessionUi(window.localStorage,id);uiRef.current=value;setUi(value);
    setConversation(emptyConversation());setLoading(Boolean(id));setReadError("");setPolling(true);failures.current=0;stickBottom.current=true;
    rememberSession(window.localStorage,id);
    const url=new URL(window.location.href);if(id)url.searchParams.set("session",id);else url.searchParams.delete("session");
    if(replace)window.history.replaceState(null,"",url);else window.history.pushState(null,"",url);
  },[]);
  const refresh=useCallback(async()=>{
    const target=selectedSession.current;
    if(inFlight.current.has(target))return;
    inFlight.current.add(target);
    try{
      const [next,list]=await Promise.all([agentRequest<EngineStatus>("status"),agentRequest<{sessions:Session[]}>("sessions")]);
      setStatus(next);setSessions(list.sessions);
      if(!restored.current){
        const id=chooseSession(window.location.search,window.localStorage,list.sessions.map(s=>s.id));
        select(id,true);if(!id)setLoading(false);return;
      }
      if(next.state==="starting" && startDeadline.current && Date.now()>startDeadline.current){
        updateUi({notice:"连接等待已结束，请刷新状态；不会重复执行任务。"});setPolling(false);
      }
      if(target && selectedSession.current===target){
        if(next.state!=="ready"){setReadError("助手服务尚未连接，历史会话仍保留。连接后继续读取。");setLoading(false);}
        else{
          const result=await agentRequest<Conversation>(`sessions/${target}/messages`);
          if(selectedSession.current===target){
            setConversation(result);setLoading(false);setReadError("");
            const pending=uiRef.current.requestId;
            const turn=result.turns?.find(t=>t.id===pending);
            if(turn && !["unknown","dispatching"].includes(turn.state))updateUi({text:"",requestId:null,notice:""});
          }
        }
      }
      failures.current=0;
    }catch(error){
      if(selectedSession.current===target){failures.current+=1;setReadError(error instanceof Error?error.message:"读取会话失败");setLoading(false);if(failures.current>=3)setPolling(false);}
    }finally{inFlight.current.delete(target);}
  },[select,updateUi]);
  useEffect(()=>{
    if(!polling)return;
    const first=window.setTimeout(()=>void refresh(),0),timer=window.setInterval(()=>void refresh(),2000);
    return()=>{window.clearTimeout(first);window.clearInterval(timer);};
  },[polling,refresh,sessionId]);
  useEffect(()=>{
    const back=()=>select(new URLSearchParams(window.location.search).get("session")||"",true);
    window.addEventListener("popstate",back);return()=>window.removeEventListener("popstate",back);
  },[select]);
  useEffect(()=>{if(stickBottom.current && logRef.current)logRef.current.scrollTop=logRef.current.scrollHeight;},[conversation]);
  useEffect(()=>{
    resizeComposer(inputRef.current);
  },[ui.text,sessionId]);
  async function permission(level:string){
    const target=selectedSession.current;if(!target)return;setBusy(true);
    try{
      const result=await agentRequest<NonNullable<Conversation["permission"]>>(`sessions/${target}/permissions`,{level});
      if(selectedSession.current===target){setConversation(v=>({...v,permission:result}));updateUi({notice:`当前会话已切换为${result.label}；新会话仍为操作模式。`});}
    }catch(e){if(selectedSession.current===target)updateUi({notice:e instanceof Error?e.message:"权限设置失败"});}
    finally{setBusy(false);}
  }
  async function connect(){
    setBusy(true);updateUi({notice:""});
    try{startDeadline.current=Date.now()+65000;setStatus(await agentRequest<EngineStatus>("start",{}));failures.current=0;setPolling(true);await refresh();}
    catch(e){updateUi({notice:e instanceof Error?e.message:"连接失败"});}finally{setBusy(false);}
  }
  const loadProviders=useCallback(async()=>{
    setBusy(true);
    try{setProviders((await agentRequest<{providers:AgentProvider[]}>("providers")).providers);}
    catch(e){updateUi({notice:e instanceof Error?e.message:"读取服务商失败"});}finally{setBusy(false);}
  },[updateUi]);
  async function create(){
    setBusy(true);
    try{const result=await agentRequest<{session:Session}>("sessions",{provider,model});setSessions(v=>[result.session,...v]);select(result.session.id);setPanel("");}
    catch(e){updateUi({notice:e instanceof Error?e.message:"新建失败"});}finally{setBusy(false);}
  }
  async function send(){
    const target=selectedSession.current,text=uiRef.current.text.trim();
    if(!target||!text)return;
    const id=uiRef.current.requestId||crypto.randomUUID();updateUi({requestId:id,notice:"正在发送…"});setBusy(true);
    try{
      const result=await agentRequest<{state:string}>(`sessions/${target}/messages`,{text,request_id:id});
      if(selectedSession.current!==target)return;
      if(["unknown","dispatching"].includes(result.state))updateUi({notice:"发送结果待确认。请刷新原会话，不会重复发送。"});
      else updateUi({text:"",requestId:null,notice:""});
      setPolling(true);await refresh();
    }catch(e){if(selectedSession.current===target)updateUi({notice:e instanceof Error?e.message:"发送失败，请刷新原会话确认结果",...(e instanceof AgentRequestError&&e.status<500&&e.reasonCode!=="dispatch_unknown"?{requestId:null}:{})});}
    finally{setBusy(false);}
  }
  async function stop(){
    const target=selectedSession.current;setBusy(true);
    try{const result=await agentRequest<{message:string}>(`sessions/${target}/stop`,{});if(target===selectedSession.current)updateUi({notice:result.message});await refresh();}
    catch(e){if(target===selectedSession.current)updateUi({notice:e instanceof Error?e.message:"停止结果待确认"});}finally{setBusy(false);}
  }
  async function answer(question:Question){
    const target=selectedSession.current;setBusy(true);
    try{
      await agentRequest(`sessions/${target}/answer`,{question_id:question.id,answers:question.questions.map((_,i)=>[uiRef.current.answers[`${question.id}-${i}`]||""])});
      if(target===selectedSession.current)updateUi({notice:"回答已提交，请等待助手继续。"});
      setPolling(true);await refresh();
    }catch(e){if(target===selectedSession.current)updateUi({notice:e instanceof Error?e.message:"回答结果待确认，请刷新原问题"});}finally{setBusy(false);}
  }
  const active=["busy","retry"].includes(conversation.state);
  const currentProvider=providers.find(p=>p.id===provider);
  const selected=sessions.find(s=>s.id===sessionId);
  const planCount=conversation.plans?.length||0;
  return <section className={`agent-studio ${panel?"with-details":""}`} aria-label="MediaFlow 助手">
    <aside className="agent-history" aria-label="会话历史">
      <a className="agent-brand" href="/"><img src="/favicon.svg" alt=""/><strong>MediaFlow</strong></a>
      <button type="button" className="primary" disabled={busy||status.state!=="ready"} onClick={()=>void create()}>＋ 新建对话</button>
      <div className="agent-session-list">{sessions.map(s=><button type="button" key={s.id} className={s.id===sessionId?"selected":""} aria-current={s.id===sessionId?"page":undefined} disabled={busy} onClick={()=>select(s.id)}><span>{s.title}</span><small>{s.model}</small></button>)}
        {!sessions.length&&<p>{readError?"会话列表暂时无法读取":loading?"正在读取会话…":"还没有会话"}</p>}
      </div>
      <div className="agent-history-footer">
        <button className="secondary" type="button" onClick={()=>{setPanel(panel==="settings"?"":"settings");if(status.state==="ready")void loadProviders();}}>助手设置</button>
        <a className="secondary" href="/manage">管理中心 ↗</a>
      </div>
    </aside>
    <div className="agent-chat">
      <header className="agent-chat-head"><div><h1>{selected?.title||"MediaFlow 助手"}</h1><small>{selected?`${selected.provider} · ${selected.model}`:"告诉助手你要做什么"}</small></div>
        {sessionId&&<button type="button" className="secondary agent-permission-badge" onClick={()=>setPanel("settings")}>{conversation.permission?.label||"操作模式"}</button>}
        <button type="button" className="secondary" aria-expanded={panel==="execution"} onClick={()=>setPanel(panel==="execution"?"":"execution")}>计划与执行{planCount?` · ${planCount}`:""}</button>
      </header>
      <div className="agent-connection">
        <span><i className={status.state==="ready"?"dot online":"dot"}/>{status.state==="ready"?"助手已连接":status.state==="starting"?"正在连接助手":status.state==="unknown"?"正在读取状态":"助手未连接"}</span>
        {status.state!=="ready"&&<button className="secondary" type="button" disabled={busy||status.state==="starting"||status.state==="unknown"} onClick={()=>void connect()}>连接助手</button>}
        <button type="button" className="secondary" onClick={()=>{failures.current=0;setPolling(true);void refresh();}}>刷新状态</button>
      </div>
      {(readError||!polling||status.state==="starting")&&<div className="agent-notice" role="status">{readError||status.message}{!polling&&" 连续读取失败，已停止等待。点击刷新状态恢复，不会重复执行。"}</div>}
      <div className="agent-messages" ref={logRef} role="log" aria-label="助手对话记录" aria-busy={loading} onScroll={()=>{const e=logRef.current;if(e)stickBottom.current=e.scrollHeight-e.scrollTop-e.clientHeight<120;}}>
        {loading&&!conversation.messages.length?<p className="agent-empty">正在恢复会话记录…</p>:!sessionId?<div className="agent-empty"><h2>今天需要做什么？</h2><p>新建对话，描述设备和目标。缺少的关键参数由助手再问你。</p></div>:!readError&&!conversation.messages.length?<p className="agent-empty">这是一个新会话。可以先问“检查当前服务和任务状态”。</p>:null}
        {conversation.messages.map(message=><article key={message.id} data-message-id={message.id} className={`agent-message ${message.role}`}><strong>{message.role==="user"?"你":"MediaFlow"}</strong>
          {message.parts.map((part,i)=>part.type==="text"?<AgentMarkdown key={i} text={part.text||""}/>:<details className="agent-tool" key={i}><summary>{part.tool||"工具"} · {part.status==="completed"?"已完成":part.status==="error"?"失败":"处理中"}</summary><p>实际结果请查看计划与执行回执；工具完成不代表业务任务已成功。</p></details>)}
          {message.error&&<p role="alert">{message.error}</p>}
        </article>)}
        {conversation.questions.map(q=><form key={q.id} className="agent-question" onSubmit={e=>{e.preventDefault();void answer(q);}}>
          <h3>需要你补充</h3>{q.questions.map((item,i)=><label key={i} htmlFor={`${q.id}-${i}`}>{item.question}<input id={`${q.id}-${i}`} required value={ui.answers[`${q.id}-${i}`]||""} onChange={e=>updateUi({answers:{...uiRef.current.answers,[`${q.id}-${i}`]:e.target.value}})} placeholder={item.options?.map(x=>x.label).join(" / ")}/></label>)}
          <button className="primary" disabled={busy||status.state!=="ready"} type="submit">回答并继续</button>
        </form>)}
        {!!planCount&&<button type="button" className="agent-plan-shortcut" onClick={()=>setPanel("execution")}>查看已保存的计划和执行回执 →</button>}
      </div>
      <div className="agent-composer">
        {ui.notice&&<p className="agent-notice" role="status">{ui.notice}</p>}
        {ui.requestId&&<p role="status">正在核对原消息发送状态；输入已保留，不自动重发。<button type="button" className="secondary" onClick={()=>void refresh()}>核对原请求</button><button type="button" className="secondary" disabled={busy||active||status.state!=="ready"} onClick={()=>void send()}>用原编号重试</button></p>}
        <label className="sr-only" htmlFor="agent-input">给助手的消息</label>
        <textarea ref={inputRef} id="agent-input" rows={1} maxLength={12000} value={ui.text} disabled={!!ui.requestId} onChange={e=>updateUi({text:e.target.value})} placeholder="描述你的目标…（API Key 请在助手设置中填写）"/>
        <div className="agent-toolbar"><small>{conversation.state==="waiting_user"?"等待你回答":active?"助手正在处理…":"说清目标即可运行 · 缺少参数时助手会询问"}</small>
          <button type="button" className="secondary" disabled={busy||!sessionId||status.state!=="ready"} onClick={()=>void stop()}>停止对话及本次任务</button>
          <button type="button" className="primary" disabled={busy||!sessionId||active||!!ui.requestId||status.state!=="ready"||!ui.text.trim()} onClick={()=>void send()}>发送 ↑</button>
        </div>
      </div>
    </div>
    {panel&&<aside className="agent-details" aria-label={panel==="settings"?"助手设置":"计划与执行"}>
      <header><h2>{panel==="settings"?"助手设置":"计划与执行"}</h2><button className="secondary" type="button" onClick={()=>setPanel("")} aria-label="关闭详情">×</button></header>
      {panel==="execution"?<>
        {!planCount&&<p>尚无任务计划。告诉助手需要运行的设备和目标。</p>}
        <AgentPlans key={sessionId} sessionId={sessionId} plans={conversation.plans||[]} onChanged={refresh} onNotice={notice=>updateUi({notice})}/>
        <AgentCommands key={`commands-${sessionId}`} sessionId={sessionId} commands={conversation.commands||[]} onChanged={refresh}/>
        <AgentRepairs key={`repairs-${sessionId}`} sessionId={sessionId} repairs={conversation.repairs||[]} onChanged={refresh}/>
        {conversation.updates?.map(operation=><section key={operation.id} className="agent-notice"><h3>修复更新 · {({queued:"排队中",running:"正在更新",waiting_user:"等待空闲或处理",completed:"已生效",failed:"未完成",cancelled:"已取消"} as Record<string,string>)[operation.status]||"等待核对"}</h3><p>{operation.message}</p><small>{operation.id}</small>
          {["queued","running","waiting_user"].includes(operation.status)&&<button type="button" className="secondary" onClick={()=>{void agentRequest(`sessions/${sessionId}/updates/${operation.id}/cancel`,{}).then(()=>refresh()).catch(e=>updateUi({notice:e.message}));}}>取消更新</button>}
        </section>)}
        <a href="/devices">查看设备与问题 ↗</a><a href="/records">查看任务与证据 ↗</a>
      </>:<>
        <section aria-label="当前会话权限">
          <h3>当前会话权限</h3>
          <label htmlFor="agent-permission">权限等级<select id="agent-permission" disabled={busy||!sessionId} value={conversation.permission?.level||"operate"} onChange={e=>void permission(e.target.value)}>
            <option value="operate">操作模式 · 默认，可直接启动任务</option>
            <option value="maintain">维护模式 · 管理虚拟机和运行配置</option>
            <option value="develop">开发模式 · 修改项目和运行测试</option>
          </select></label>
          <p>仅对当前会话持续有效，刷新或重启后保留。开发范围仅 MediaFlow 项目；修改生效前会在聊天中请你确认。</p>
          <button className="secondary" type="button" disabled={busy||!sessionId||!conversation.permission||conversation.permission.level==="operate"} onClick={()=>void permission("operate")}>撤销高权限</button>
        </section>
        <button className="secondary" type="button" disabled={busy||status.state!=="ready"} onClick={()=>void loadProviders()}>刷新服务商与模型</button>
        {!!providers.length&&<div className="agent-model-row"><label htmlFor="agent-provider">服务商<select id="agent-provider" value={provider} onChange={e=>{setProvider(e.target.value);setModel(providers.find(p=>p.id===e.target.value)?.models[0]?.id||"");}}>{providers.map(p=><option key={p.id} value={p.id}>{p.name}{p.connected?" · 已连接":""}</option>)}</select></label>
          <label htmlFor="agent-model">模型<select id="agent-model" value={model} onChange={e=>setModel(e.target.value)}>{currentProvider?.models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
          <button className="primary" type="button" disabled={busy} onClick={()=>void create()}>以此模型新建对话</button>
        </div>}
        {currentProvider&&<AgentProviderSettings key={currentProvider.id} provider={currentProvider} onChanged={loadProviders}/>}
        <AgentMemories/><AgentInbox sessionId={sessionId} onChanged={refresh}/><AgentUsage/>
        <details><summary>关于与开源许可</summary><p>MediaFlow 使用 OpenCode 引擎。保留原生服务商和认证方式。</p><a href="/manage#licenses">查看组件与许可说明</a></details>
        {!!status.pending_capabilities?.length&&<details><summary>尚未完成的接入</summary><p>{status.pending_capabilities.join("、")}</p></details>}
      </>}
    </aside>}
  </section>;
}
