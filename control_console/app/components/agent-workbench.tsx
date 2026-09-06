"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { AgentRequestError, agentRequest, type AgentProvider } from "../lib/agent-api";
import { chooseSession, loadSessionUi, rememberSession, saveSessionUi, resizeComposer, shouldSendKey, type SessionUi } from "../lib/agent-workspace-state.mjs";
import {AboutSettings,PreferenceSettings} from "./settings-about";
import AgentProviderSettings from "./agent-provider-settings";
import AgentMemories from "./agent-memories";
import AgentPlans, { type AgentPlan } from "./agent-plans";
import AgentCommands, { type AgentCommand } from "./agent-commands";
import AgentRepairs, { type AgentRepair } from "./agent-repairs";
import AgentInbox from "./agent-inbox";
import AgentUsage from "./agent-usage";
import AgentMarkdown from "./agent-markdown";

type EngineStatus = { state:string; message:string; notice?:string; pending_capabilities?:string[] };
type Session = {id:string;title:string;provider:string;model:string;created?:number};
type Question = {id:string;questions:{question:string;multiple?:boolean;options?:{label:string;description?:string}[]}[]};
type Conversation = {
  response?:{phase:string;message:string;reason_code:string;recovery?:{state:string;attempts:number}};
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
  const [settingsTab,setSettingsTab]=useState("models"),[historyOpen,setHistoryOpen]=useState(false),[away,setAway]=useState(false);
  const [guide,setGuide]=useState(""),[guideError,setGuideError]=useState(""),[guideLoading,setGuideLoading]=useState(false);
  const loadGuide=useCallback(async()=>{setGuideLoading(true);setGuideError("");try{const value=await agentRequest<{guide:string}>("guide");setGuide(value.guide);}catch(error){setGuideError(error instanceof Error?error.message:"操作指南读取失败");}finally{setGuideLoading(false);}},[]);
  useEffect(()=>{if(panel!=="settings"||settingsTab!=="guide"||guide)return;const timer=setTimeout(()=>void loadGuide(),0);return()=>clearTimeout(timer);},[panel,settingsTab,guide,loadGuide]);
  const submitLock=useRef(false), composing=useRef(false);
  const selectedSession=useRef(""), uiRef=useRef<SessionUi>(emptyUi()), restored=useRef(false);
  const inFlight=useRef(new Set<string>()), failures=useRef(0), startDeadline=useRef(0);
  const logRef=useRef<HTMLDivElement>(null),stickBottom=useRef(true);
  const inputRef=useRef<HTMLTextAreaElement>(null);
  const detailsRef=useRef<HTMLElement>(null),historyRef=useRef<HTMLElement>(null);
  const updateUi=useCallback((patch:Partial<SessionUi>)=>{
    const value={...uiRef.current,...patch};uiRef.current=value;setUi(value);
    if(selectedSession.current) saveSessionUi(window.localStorage,selectedSession.current,value);
  },[]);
  const select=useCallback((id:string,replace=false)=>{
    selectedSession.current=id;setSessionId(id);restored.current=true;
    const value=loadSessionUi(window.localStorage,id);uiRef.current=value;setUi(value);
    setHistoryOpen(false);
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
            if(turn && !["unknown","dispatching"].includes(turn.state))updateUi({text:uiRef.current.text.trim()===uiRef.current.sentText?"":uiRef.current.text,requestId:null,sentText:"",notice:""});
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
    const settings=()=>{setPanel("settings");setSettingsTab(new URLSearchParams(window.location.search).get("settings")||"models");};
    if(new URLSearchParams(window.location.search).has("settings"))window.setTimeout(settings,0);
    window.addEventListener("popstate",back);return()=>window.removeEventListener("popstate",back);
  },[select]);
  useEffect(()=>{const escape=(e:KeyboardEvent)=>{if(e.key==="Escape"){setPanel("");setHistoryOpen(false);}};window.addEventListener("keydown",escape);return()=>window.removeEventListener("keydown",escape);},[]);
  useEffect(()=>{
    const region=historyOpen?historyRef.current:panel?detailsRef.current:null;
    if(!region)return;
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
    const focusable=()=>Array.from(region.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]')).filter(e=>e.getClientRects().length);
    focusable()[0]?.focus();
    const trap=(e:KeyboardEvent)=>{
      const modal=matchMedia(historyOpen?"(max-width:700px)":"(max-width:1549px)").matches;
      if(e.key!=="Tab"||!modal)return;
      const items=focusable(),first=items[0],last=items[items.length-1];
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
    };
    region.addEventListener("keydown",trap);
    return()=>{region.removeEventListener("keydown",trap);if(previous?.isConnected)previous.focus();};
  },[panel,historyOpen]);
  useEffect(()=>{if(stickBottom.current && logRef.current)logRef.current.scrollTop=logRef.current.scrollHeight;},[conversation]);
  useEffect(()=>{
    resizeComposer(inputRef.current);
  },[ui.text,sessionId]);
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
    if(!target||!text||submitLock.current||busy||active||conversation.questions.length||status.state!=="ready")return;
    submitLock.current=true;
    const id=uiRef.current.requestId||crypto.randomUUID(),sent=uiRef.current.requestId?(uiRef.current.sentText||text):text;
    updateUi({requestId:id,sentText:sent,notice:"正在发送…"});setBusy(true);
    try{
      const result=await agentRequest<{state:string}>(`sessions/${target}/messages`,{text:sent,request_id:id});
      if(selectedSession.current!==target)return;
      if(["unknown","dispatching"].includes(result.state))updateUi({notice:"发送结果待确认。请刷新原会话，不会重复发送。"});
      else updateUi({text:uiRef.current.text.trim()===sent?"":uiRef.current.text,requestId:null,sentText:"",notice:""});
      setPolling(true);await refresh();
    }catch(e){if(selectedSession.current===target)updateUi({notice:e instanceof Error?e.message:"发送失败，请刷新原会话确认结果",...(e instanceof AgentRequestError&&e.status<500&&e.reasonCode!=="dispatch_unknown"?{requestId:null}:{})});}
    finally{submitLock.current=false;setBusy(false);}
  }
  async function stop(task=false){
    const target=selectedSession.current;setBusy(true);
    try{const result=await agentRequest<{message:string}>(`sessions/${target}/${task?"stop":"stop-response"}`,{});if(target===selectedSession.current)updateUi({notice:result.message});await refresh();}
    catch(e){if(target===selectedSession.current)updateUi({notice:e instanceof Error?e.message:"停止结果待确认"});}finally{setBusy(false);}
  }
  async function answer(question:Question){
    if(submitLock.current)return;submitLock.current=true;
    const target=selectedSession.current;setBusy(true);
    try{
      await agentRequest(`sessions/${target}/answer`,{question_id:question.id,answers:question.questions.map((_,i)=>{const value=uiRef.current.answers[`${question.id}-${i}`];return Array.isArray(value)?value:[value||""];})});
      if(target===selectedSession.current)updateUi({notice:"回答已提交，请等待助手继续。"});
      setPolling(true);await refresh();
    }catch(e){if(target===selectedSession.current)updateUi({notice:e instanceof Error?e.message:"回答结果待确认，请刷新原问题"});}finally{submitLock.current=false;setBusy(false);}
  }
  const active=["busy","retry"].includes(conversation.state)||conversation.response?.phase==="supplementing";
  const currentProvider=providers.find(p=>p.id===provider);
  const selected=sessions.find(s=>s.id===sessionId);
  const planCount=conversation.plans?.length||0;
  return <section className={`agent-studio ${panel?"with-details":""} ${historyOpen?"history-open":""}`} aria-label="MediaFlow 一站式媒体自动化Agent">
    {historyOpen&&<button className="agent-mobile-scrim" aria-label="关闭会话列表" onClick={()=>setHistoryOpen(false)}/>}
    <aside ref={historyRef} className="agent-history" aria-label="会话历史">
      <p className="agent-history-caption">工作空间 <span>本机</span></p>
      <button type="button" className="primary" disabled={busy||status.state!=="ready"} onClick={()=>void create()}>＋ 新建对话</button>
      <div className="agent-session-list">{sessions.map((s,index)=><button type="button" key={s.id} className={s.id===sessionId?"selected":""} aria-current={s.id===sessionId?"page":undefined} disabled={busy} onClick={()=>select(s.id)}><span className="session-title">{s.title?.trim()&&s.title!=="MediaFlow对话"?s.title:`对话 · ${s.created?new Date(s.created*1000).toLocaleString("zh-CN",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}):index+1}`}</span><small>{s.model}</small></button>)}
        {!sessions.length&&<p>{readError?"会话列表暂时无法读取":loading?"正在读取会话…":"还没有会话"}</p>}
      </div>
      <div className="agent-history-footer">
        <a className="secondary" href="/manage">管理中心 ↗</a>
        <button className="secondary" type="button" onClick={()=>{setPanel(panel==="settings"?"":"settings");if(status.state==="ready")void loadProviders();}}>设置</button>
      </div>
    </aside>
    <div className="agent-chat">
      <header className="agent-chat-head"><button className="secondary agent-history-toggle" aria-label="打开会话历史" onClick={()=>setHistoryOpen(true)}>☰</button><div><h1>{selected?.title||"开始一个新目标"}</h1><small><i className={status.state==="ready"?"dot online":"dot"}/>{status.state==="ready"?(conversation.response?.message||"已连接 · 随时就绪"):status.message}</small></div>
        <button type="button" className="secondary" aria-expanded={panel==="execution"} onClick={()=>setPanel(panel==="execution"?"":"execution")}>计划与执行{planCount?` · ${planCount}`:""}</button>
      </header>
      {status.state!=="ready"&&<div className="agent-connection">
        <span><i className={status.state==="ready"?"dot online":"dot"}/>{status.state==="ready"?"助手已连接":status.state==="starting"?"正在连接助手":status.state==="unknown"?"正在读取状态":"助手未连接"}</span>
        {status.state!=="ready"&&<button className="secondary" type="button" disabled={busy||status.state==="starting"||status.state==="unknown"} onClick={()=>void connect()}>连接助手</button>}
        <button type="button" className="secondary" onClick={()=>{failures.current=0;setPolling(true);void refresh();}}>刷新状态</button>
      </div>}
      {(readError||!polling||status.state==="starting")&&<div className="agent-notice" role="status">{readError||status.message}{!polling&&" 连续读取失败，已停止等待，不会重复执行。"}{(readError||!polling)&&<button type="button" className="secondary" onClick={()=>{failures.current=0;setPolling(true);void refresh();}}>重新读取</button>}</div>}
      <div className="agent-messages" ref={logRef} role="log" aria-label="助手对话记录" aria-busy={loading} onScroll={()=>{const e=logRef.current;if(e){stickBottom.current=e.scrollHeight-e.scrollTop-e.clientHeight<120;setAway(!stickBottom.current);}}}>
        {loading&&!conversation.messages.length?<p className="agent-empty">正在恢复会话记录…</p>:!sessionId?<div className="agent-empty"><h2>今天需要做什么？</h2><p>新建对话，描述设备和目标。缺少的关键参数由助手再问你。</p></div>:!readError&&!conversation.messages.length?<p className="agent-empty">这是一个新会话。可以先问“检查当前服务和任务状态”。</p>:null}
        {conversation.messages.map(message=><article key={message.id} data-message-id={message.id} className={`agent-message ${message.role}`}><strong>{message.role==="user"?"你":"MediaFlow"}</strong>
          {message.parts.map((part,i)=>part.type==="text"?<AgentMarkdown key={i} text={part.text||""}/>:<details className="agent-tool" key={i}><summary>{part.tool||"工具"} · {part.status==="completed"?"已完成":part.status==="error"?"失败":"处理中"}</summary><p>实际结果请查看计划与执行回执；工具完成不代表业务任务已成功。</p></details>)}
          {message.error&&<p role="alert">{message.error}</p>}
        </article>)}
        {conversation.questions.map(q=><form key={q.id} className="agent-question" onSubmit={e=>{e.preventDefault();void answer(q);}}>
          <h3>补充参数后继续</h3>{q.questions.map((item,i)=>{const key=`${q.id}-${i}`,raw=ui.answers[key],chosen=Array.isArray(raw)?raw:raw?[raw]:[],labels=item.options?.map(o=>o.label)||[],custom=chosen.filter(v=>!labels.includes(v)).join(" ");return <fieldset key={key}><legend>{item.question}{item.multiple?"（可多选）":""}</legend><div className="agent-question-options">{item.options?.map(option=><label className="agent-choice" key={option.label}><input type={item.multiple?"checkbox":"radio"} name={key} checked={chosen.includes(option.label)} onChange={e=>updateUi({answers:{...uiRef.current.answers,[key]:item.multiple?(e.target.checked?[...chosen,option.label]:chosen.filter(v=>v!==option.label)):[option.label]}})}/><span>{option.label}{option.description&&<small>{option.description}</small>}</span></label>)}</div><label htmlFor={key}>{labels.length?"自定义回答":"你的回答"}<input id={key} value={custom} required={!chosen.length} onChange={e=>updateUi({answers:{...uiRef.current.answers,[key]:item.multiple?[...chosen.filter(v=>labels.includes(v)),...(e.target.value?[e.target.value]:[])]:e.target.value?[e.target.value]:[]}})} placeholder="填写你的参数或补充说明"/></label></fieldset>;})}
          <button className="primary" disabled={busy||status.state!=="ready"} type="submit">回答并继续</button>
        </form>)}
        {!!planCount&&<button type="button" className="agent-plan-shortcut" onClick={()=>setPanel("execution")}>查看已保存的计划和执行回执 →</button>}
      </div>
      {away&&<button type="button" className="agent-latest" onClick={()=>{if(logRef.current)logRef.current.scrollTop=logRef.current.scrollHeight;stickBottom.current=true;setAway(false);}}>↓ 回到最新</button>}
      {conversation.response?.phase==="incomplete"&&<div className="agent-recovery" role="status"><span>{conversation.response.message}</span><button className="secondary" disabled={busy} onClick={()=>{const target=sessionId;setBusy(true);void agentRequest(`sessions/${target}/recover`,{}).then(()=>refresh()).catch(e=>{if(target===selectedSession.current)updateUi({notice:e.message});}).finally(()=>setBusy(false));}}>重新补齐</button><button className="secondary" onClick={()=>inputRef.current?.focus()}>直接补充参数</button></div>}
      <div className="agent-composer">
        {ui.notice&&<p className="agent-notice" role="status">{ui.notice}</p>}
        {ui.requestId&&<p role="status">正在核对原消息发送状态；输入已保留，不自动重发。<button type="button" className="secondary" onClick={()=>void refresh()}>核对原请求</button><button type="button" className="secondary" disabled={busy||active||status.state!=="ready"} onClick={()=>void send()}>用原编号重试</button></p>}
        <label className="sr-only" htmlFor="agent-input">给助手的消息</label>
        <textarea ref={inputRef} id="agent-input" rows={1} maxLength={12000} value={ui.text} onCompositionStart={()=>{composing.current=true;}} onCompositionEnd={()=>{composing.current=false;}} onKeyDown={e=>{if(shouldSendKey({...e,isComposing:composing.current||e.nativeEvent.isComposing,keyCode:e.nativeEvent.keyCode})){e.preventDefault();if(!ui.requestId)void send();}else if(e.key==="Enter"&&e.repeat&&!e.shiftKey&&!composing.current)e.preventDefault();}} onChange={e=>updateUi({text:e.target.value})} placeholder="告诉我你的目标，剩下的交给 Agent…"/>
        <div className="agent-toolbar"><small>{conversation.state==="waiting_user"?"请在上方回答问题":active?"可以编辑下一条消息，不会自动发送":"Enter 发送 · Shift + Enter 换行"}</small>
          {(active||!!conversation.questions.length)&&<button type="button" className="secondary" disabled={busy||!sessionId||status.state!=="ready"} title="停止当前回答或追问，不停止已提交的设备任务" onClick={()=>void stop()}>{conversation.questions.length?"取消追问":"停止回答"}</button>}
          <button type="button" className="primary" disabled={busy||!sessionId||active||!!conversation.questions.length||!!ui.requestId||status.state!=="ready"||!ui.text.trim()} onClick={()=>void send()}>发送 ↑</button>
        </div>
      </div>
    </div>
    {panel&&<aside ref={detailsRef} className="agent-details" aria-label={panel==="settings"?"助手设置":"计划与执行"}>
      <header><h2>{panel==="settings"?"助手设置":"计划与执行"}</h2><button className="secondary" type="button" onClick={()=>setPanel("")} aria-label="关闭详情">×</button></header>
      {panel==="execution"?<>
        {!planCount&&<p>尚无任务计划。告诉助手需要运行的设备和目标。</p>}
        <button className="secondary" disabled={busy||!sessionId} title="停止本会话的设备业务任务，并停止回答" onClick={()=>void stop(true)}>停止本次任务</button>
        <AgentPlans key={sessionId} sessionId={sessionId} plans={conversation.plans||[]} onChanged={refresh} onNotice={notice=>updateUi({notice})}/>
        <AgentCommands key={`commands-${sessionId}`} sessionId={sessionId} commands={conversation.commands||[]} onChanged={refresh}/>
        <AgentRepairs key={`repairs-${sessionId}`} sessionId={sessionId} repairs={conversation.repairs||[]} onChanged={refresh}/>
        {conversation.updates?.map(operation=><section key={operation.id} className="agent-notice"><h3>修复更新 · {({queued:"排队中",running:"正在更新",waiting_user:"等待空闲或处理",completed:"已生效",failed:"未完成",cancelled:"已取消"} as Record<string,string>)[operation.status]||"等待核对"}</h3><p>{operation.message}</p><small>{operation.id}</small>
          {["queued","running","waiting_user"].includes(operation.status)&&<button type="button" className="secondary" onClick={()=>{void agentRequest(`sessions/${sessionId}/updates/${operation.id}/cancel`,{}).then(()=>refresh()).catch(e=>updateUi({notice:e.message}));}}>取消更新</button>}
        </section>)}
        <a href="/devices">查看设备与问题 ↗</a><a href="/records">查看任务与证据 ↗</a>
      </>:<>
        <nav className="settings-tabs" aria-label="设置分类">{[["models","模型与连接"],["guide","操作指南"],["preferences","偏好"],["data","数据"],["about","关于"]].map(([id,label])=><button key={id} type="button" aria-pressed={settingsTab===id} onClick={()=>setSettingsTab(id)}>{label}</button>)}</nav>
        {settingsTab==="guide"&&<section className="settings-section"><h3>平台操作指南</h3><p>Agent 已可设置、启动、维护和修复 MediaFlow，无需选择权限等级。它会按内置 Skill 理解目标、调用工具、核对结果。</p><p>你只需说明要做什么。设备占用、服务故障或任务结果不明确时，它会查明原因并给出处理方法。</p>{guideLoading&&<p role="status">正在读取内置指南…</p>}{guideError&&<p role="alert">{guideError}<button className="secondary" type="button" onClick={()=>void loadGuide()}>重新读取</button></p>}{guide&&<details><summary>查看完整内置 Skill</summary><AgentMarkdown text={guide}/></details>}</section>}
        {settingsTab==="models"&&<>
        {selected&&<p className="settings-current-model">当前对话：{selected.provider} · {selected.model}</p>}
        <button className="secondary" type="button" disabled={busy||status.state!=="ready"} onClick={()=>void loadProviders()}>刷新服务商与模型</button>
        {!!providers.length&&<div className="agent-model-row"><label htmlFor="agent-provider">服务商<select id="agent-provider" value={provider} onChange={e=>{setProvider(e.target.value);setModel(providers.find(p=>p.id===e.target.value)?.models[0]?.id||"");}}>{providers.map(p=><option key={p.id} value={p.id}>{p.name}{p.connected?" · 已连接":""}</option>)}</select></label>
          <label htmlFor="agent-model">模型<select id="agent-model" value={model} onChange={e=>setModel(e.target.value)}>{currentProvider?.models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
          <button className="primary" type="button" disabled={busy} onClick={()=>void create()}>以此模型新建对话</button>
        </div>}
        {currentProvider&&<AgentProviderSettings key={currentProvider.id} provider={currentProvider} onChanged={loadProviders}/>}
        <AgentUsage/></>}
        {settingsTab==="preferences"&&<PreferenceSettings/>}
        {settingsTab==="data"&&<><a className="secondary" href="/governance">数据保留与备份 ↗</a><AgentMemories/><AgentInbox sessionId={sessionId} onChanged={refresh}/></>}
        {settingsTab==="about"&&<><AboutSettings/>{!!status.pending_capabilities?.length&&<details><summary>尚未完成的接入</summary><p>{status.pending_capabilities.join("、")}</p></details>}</>}
      </>}
    </aside>}
  </section>;
}
