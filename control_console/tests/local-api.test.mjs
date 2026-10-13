import assert from "node:assert/strict";
import {after, test} from "node:test";
import {createServer} from "vite";

const server = await createServer({configFile:false,cacheDir:"node_modules/.vite-local-api-tests",server:{middlewareMode:true,hmr:false},appType:"custom"});
const {fetchLocalApi, LocalApiTimeoutError} = await server.ssrLoadModule("/app/lib/local-api.ts");
const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
after(async()=>{
  globalThis.fetch=originalFetch;
  if(originalWindow===undefined)delete globalThis.window;else globalThis.window=originalWindow;
  await server.close();
});
const instance = {id:"acceptance-local",acceptance:true,apiUrl:"http://127.0.0.1:49338",uiUrl:"http://127.0.0.1:31301/"};

test("desktop acceptance resolves API before fetch so no production request or CORS redirect occurs",async()=>{
  const requests=[];
  globalThis.window={mediaflowDesktop:{getInstance:()=>instance}};
  globalThis.fetch=async(input,init)=>{requests.push({url:String(input),init});return new Response('{"ok":true}',{headers:{"content-type":"application/json"}});};
  const result=await fetchLocalApi("http://127.0.0.1:48138/api/status?detail=1",{cache:"no-store"});
  assert.deepEqual(await result.json(),{ok:true});
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,"http://127.0.0.1:49338/api/status?detail=1");
  assert.equal(requests[0].init.cache,"no-store");
  assert.ok(requests[0].init.signal instanceof AbortSignal);
});

test("Request and URL inputs preserve method, headers and body when scoped to the desktop API",async()=>{
  globalThis.window={mediaflowDesktop:{getInstance:()=>instance}};
  const requests=[];
  globalThis.fetch=async input=>{requests.push(input);return new Response("ok");};
  const request=new Request("http://localhost:48138/api/config",{method:"POST",headers:{"content-type":"application/json"},body:'{"test":true}'});
  await fetchLocalApi(request);
  assert.equal(requests[0].url,"http://127.0.0.1:49338/api/config");
  assert.equal(requests[0].method,"POST");
  assert.equal(requests[0].headers.get("content-type"),"application/json");
  assert.equal(await requests[0].text(),'{"test":true}');
  await fetchLocalApi(new URL("http://127.0.0.1:48138/api/interaction-alerts?status=unread"));
  assert.equal(String(requests[1]),"http://127.0.0.1:49338/api/interaction-alerts?status=unread");
});

test("ordinary browser and production desktop preserve existing API behavior",async()=>{
  const requests=[];
  globalThis.fetch=async input=>{requests.push(String(input));return new Response("ok");};
  delete globalThis.window;
  await fetchLocalApi("http://127.0.0.1:48138/api/status");
  globalThis.window={};
  await fetchLocalApi("http://127.0.0.1:48138/api/status");
  globalThis.window={mediaflowDesktop:{getInstance:()=>({...instance,id:"production",acceptance:false,apiUrl:"http://127.0.0.1:48138"})}};
  await fetchLocalApi("http://127.0.0.1:48138/api/status");
  assert.deepEqual(requests,Array(3).fill("http://127.0.0.1:48138/api/status"));
});

test("invalid or unavailable acceptance config fails before any production network access",async()=>{
  let requests=0;
  globalThis.fetch=async()=>{requests++;return new Response("unexpected");};
  for(const value of [null,{...instance,apiUrl:"http://127.0.0.1:48138"},{...instance,apiUrl:"https://example.org"},{...instance,apiUrl:"http://user:secret@127.0.0.1:49338"}]){
    globalThis.window={mediaflowDesktop:{getInstance:()=>value}};
    await assert.rejects(fetchLocalApi("http://127.0.0.1:48138/api/status"),/桌面实例/);
  }
  for(const bridge of [{},{getInstance:()=>{throw new Error("bridge unavailable");}}]){
    globalThis.window={mediaflowDesktop:bridge};
    await assert.rejects(fetchLocalApi("http://127.0.0.1:48138/api/status"),/桌面实例/);
  }
  assert.equal(requests,0);
});

test("relative, external and already isolated URLs are not rewritten",async()=>{
  const requests=[];
  globalThis.window={mediaflowDesktop:{getInstance:()=>{throw new Error("must not need instance lookup");}}};
  globalThis.fetch=async input=>{requests.push(String(input));return new Response("ok");};
  const urls=["/api/status","https://example.org:48138/api/status","http://127.0.0.1:49338/api/status"];
  for(const url of urls)await fetchLocalApi(url);
  globalThis.window={mediaflowDesktop:{}};
  for(const url of urls)await fetchLocalApi(url);
  assert.deepEqual(requests,[...urls,...urls]);
});

test("mapped desktop requests retain bounded timeout and cancellation behavior",async()=>{
  globalThis.window={mediaflowDesktop:{getInstance:()=>instance}};
  globalThis.fetch=(_input,init)=>new Promise((_resolve,reject)=>{init.signal.addEventListener("abort",()=>reject(init.signal.reason),{once:true});});
  await assert.rejects(fetchLocalApi("http://127.0.0.1:48138/api/status",{},5),error=>error instanceof LocalApiTimeoutError&&error.timeoutMs===5);
  const controller=new AbortController();
  const request=fetchLocalApi("http://127.0.0.1:48138/api/status",{signal:controller.signal},1000);
  controller.abort(new Error("caller cancelled"));
  await assert.rejects(request);
});
