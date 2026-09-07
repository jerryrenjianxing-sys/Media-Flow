import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import {spawn} from 'node:child_process';
import { createBudgetFetch } from './pi-request-budget.mjs';

const endpoint = 'https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions';
test('concurrent, failed and restarted requests share ten durable slots', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'mediaflow-budget-'));
  let sent = 0;
  const upstream = async () => { sent++; throw Error('network failure'); };
  const guarded = createBudgetFetch({ directory, endpoint, fetch: upstream });
  await Promise.allSettled(Array.from({length: 14}, () => guarded(endpoint, {method:'POST'})));
  assert.equal(sent, 10);
  const restarted = createBudgetFetch({ directory, endpoint, fetch: upstream });
  await assert.rejects(restarted(endpoint, {method:'POST'}), /10/);
  assert.equal(sent, 10);
  assert.equal(readdirSync(directory).length, 10);
});
test('guard preserves streaming response identity and never stores request contents', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'mediaflow-budget-'));
  const response = new Response('data: first\n\n', {headers:{'content-type':'text/event-stream'}});
  const guarded = createBudgetFetch({directory, endpoint, fetch:async()=>response});
  assert.equal(await guarded(new Request(endpoint,{method:'POST',body:'secret'})), response);
  assert.equal(await response.text(), 'data: first\n\n');
});
test('other URLs are untouched and a missing ledger fails closed', async () => {
  let sent = 0;
  const guarded = createBudgetFetch({directory:'', endpoint, fetch:async()=>{sent++;return new Response('ok');}});
  await guarded('http://127.0.0.1:48138/api/status');
  await assert.rejects(guarded(endpoint,{method:'POST'}), /ledger/);
  assert.equal(sent, 1);
});
test('independent Node processes cannot reserve more than ten requests',async()=>{
  const directory=mkdtempSync(join(tmpdir(),'mediaflow-process-budget-'));
  const worker=`import {createBudgetFetch} from ${JSON.stringify(new URL('./pi-request-budget.mjs',import.meta.url).href)};let count=0;const f=createBudgetFetch({directory:${JSON.stringify(directory)},endpoint:${JSON.stringify(endpoint)},fetch:async()=>{count++;return new Response('ok');}});for(let i=0;i<10;i++){try{await f(${JSON.stringify(endpoint)},{method:'POST'});}catch{}}process.stdout.write(String(count));`;
  const counts=await Promise.all(Array.from({length:4},()=>new Promise((resolve,reject)=>{
    const child=spawn(process.execPath,['--input-type=module','-e',worker],{windowsHide:true});let output='';
    child.stdout.on('data',chunk=>output+=chunk);child.on('error',reject);child.on('exit',code=>code===0?resolve(Number(output)):reject(Error('worker failed')));
  })));
  assert.equal(counts.reduce((a,b)=>a+b,0),10);
});
test('unlimited requests preserve the first ten claims and continue monotonically across concurrency and restart',async()=>{
  const directory=mkdtempSync(join(tmpdir(),'mediaflow-unlimited-budget-'));
  for(let slot=1;slot<=10;slot++) writeFileSync(join(directory,`${slot}.claim`),'preserved');
  let sent=0;
  const upstream=async()=>{sent++;return new Response('ok');};
  const guarded=createBudgetFetch({directory,endpoint,limit:'unlimited',fetch:upstream});
  await Promise.all(Array.from({length:4},()=>guarded(endpoint,{method:'POST'})));
  const restarted=createBudgetFetch({directory,endpoint,limit:'unlimited',fetch:upstream});
  await restarted(endpoint,{method:'POST'});
  assert.equal(sent,5);
  assert.deepEqual(readdirSync(directory).sort((a,b)=>Number.parseInt(a)-Number.parseInt(b)),
                   Array.from({length:15},(_,index)=>`${index+1}.claim`));
});
test('invalid request limits fail closed before a wire request',async()=>{
  for(const limit of [0,-1,'0','10.5','forever',null]) {
    let sent=0;
    assert.throws(()=>createBudgetFetch({directory:mkdtempSync(join(tmpdir(),'mediaflow-invalid-budget-')),
                                        endpoint,limit,fetch:async()=>{sent++;return new Response('ok');}}),
                  /request limit/i);
    assert.equal(sent,0);
  }
});
test('guard import reads unlimited mode from the launch environment',async()=>{
  const directory=mkdtempSync(join(tmpdir(),'mediaflow-env-budget-'));
  for(let slot=1;slot<=10;slot++) writeFileSync(join(directory,`${slot}.claim`),'preserved');
  const originalFetch=globalThis.fetch;
  const originalDirectory=process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR;
  const originalLimit=process.env.MEDIAFLOW_PI_REQUEST_LIMIT;
  let sent=0;
  globalThis.fetch=async()=>{sent++;return new Response('ok');};
  process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR=directory;
  process.env.MEDIAFLOW_PI_REQUEST_LIMIT='unlimited';
  try {
    await import(`./pi-request-budget.mjs?environment-test=${Date.now()}`);
    await globalThis.fetch(endpoint,{method:'POST'});
    assert.equal(sent,1);
    assert.equal(readdirSync(directory).includes('11.claim'),true);
  } finally {
    globalThis.fetch=originalFetch;
    if(originalDirectory===undefined) delete process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR;
    else process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR=originalDirectory;
    if(originalLimit===undefined) delete process.env.MEDIAFLOW_PI_REQUEST_LIMIT;
    else process.env.MEDIAFLOW_PI_REQUEST_LIMIT=originalLimit;
  }
});
test('locked Pi SDK uses guarded fetch before each real wire request', {skip:!process.env.MEDIAFLOW_PI_SOURCE}, async () => {
  const directory=mkdtempSync(join(tmpdir(),'mediaflow-sdk-budget-'));
  const original=globalThis.fetch;
  let sent=0;
  globalThis.fetch=createBudgetFetch({directory,endpoint,fetch:async(input,init)=>{
    sent++;
    assert.equal(JSON.parse(init.body).model,'qwen3.8-flash');
    return new Response('data: '+JSON.stringify({id:'local',object:'chat.completion.chunk',choices:[{index:0,delta:{content:'本地验证'},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
  }});
  try {
    const sdk=await import(pathToFileURL(join(process.env.MEDIAFLOW_PI_SOURCE,'node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js')));
    const model={id:'qwen3.8-flash',name:'Qwen',api:'openai-completions',provider:'qwen',baseUrl:endpoint.replace('/chat/completions',''),reasoning:false,input:['text'],contextWindow:128000,maxTokens:128,cost:{input:0,output:0,cacheRead:0,cacheWrite:0}};
    for(let n=0;n<11;n++) {
      const stream=sdk.streamSimple(model,{messages:[{role:'user',content:'local only',timestamp:1}]},{apiKey:'local-test-not-a-key',maxRetries:0});
      const result=await stream.result();
      assert.equal(result.stopReason,n<10?'stop':'error');
    }
    assert.equal(sent,10);
  } finally {globalThis.fetch=original;}
});
