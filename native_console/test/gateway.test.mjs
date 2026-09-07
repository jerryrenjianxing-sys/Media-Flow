import { test } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import net from 'node:net'
import { mkdtemp, writeFile, mkdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { createGateway } from '../gateway.mjs'

const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`)))
const close = server => new Promise(resolve => { server.close(resolve); server.closeAllConnections?.() })

test('bootstrap rejects stale IPC and requests idempotent engine startup',async()=>{
  const root=await mkdtemp(path.join(tmpdir(),'mf-gateway-'))
  let starts=0
  const api=http.createServer((req,res)=>{starts++;assert.equal(req.url,'/api/agent/start');res.end('{}')})
  const url=await listen(api), file=path.join(root,'connection.json')
  const dead=http.createServer();const deadUrl=await listen(dead);await close(dead)
  await writeFile(file,JSON.stringify({url:deadUrl,username:'opencode',password:'fixture',workspace:'fixture'}))
  const gateway=createGateway({connectionFile:file,nativeRoot:root,apiUrl:url})
  try {
    const base=await listen(gateway), response=await fetch(base+'/mediaflow/bootstrap')
    assert.equal(response.status,503)
    assert.equal(starts,1)
    assert.doesNotMatch(await response.text(),/fixture/)
  } finally {await close(gateway);await close(api);await rm(root,{recursive:true})}
})

test('native API proxy preserves status, bytes, query and streaming; injects only server authentication', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'mf-gateway-'))
  const upstream = http.createServer((req, res) => {
    assert.equal(req.headers.authorization, 'Basic '+Buffer.from('opencode:fixture').toString('base64'))
    assert.equal(req.url, '/session/missing?directory=C%3A%2Ffixture')
    res.writeHead(404, {'content-type': 'application/json'})
    res.end('{"name":"NotFoundError","data":{"message":"Session missing"}}')
  })
  const url = await listen(upstream)
  const file = path.join(root, 'connection.json')
  await writeFile(file, JSON.stringify({url, username:'opencode', password:'fixture', workspace:'C:/fixture'}))
  const gateway = createGateway({connectionFile:file, nativeRoot:root, apiUrl:url, legacyUrl:url})
  try {
    const base = await listen(gateway)
    const result = await fetch(base+'/opencode/session/missing?directory=C%3A%2Ffixture')
    assert.equal(result.status,404)
    assert.equal(await result.text(),'{"name":"NotFoundError","data":{"message":"Session missing"}}')
  } finally { await close(gateway); await close(upstream); await rm(root,{recursive:true}) }
})

test('SSE first event is forwarded before upstream finishes', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'mf-gateway-'))
  const upstream = http.createServer((req,res) => {
    res.writeHead(200,{'content-type':'text/event-stream'})
    res.write('data: {"type":"server.connected"}\n\n')
  })
  const url = await listen(upstream)
  const file = path.join(root,'connection.json')
  await writeFile(file,JSON.stringify({url,username:'opencode',password:'fixture'}))
  const gateway = createGateway({connectionFile:file,nativeRoot:root,apiUrl:url,legacyUrl:url})
  try {
    const base = await listen(gateway)
    const response = await fetch(base+'/opencode/event', {signal:AbortSignal.timeout(2000)})
    const reader = response.body.getReader()
    assert.match(new TextDecoder().decode((await reader.read()).value), /server.connected/)
    await reader.cancel()
  } finally { await close(gateway); await close(upstream); await rm(root,{recursive:true}) }
})

test('interrupted upstream SSE terminates downstream so native reconnect can run', async () => {
  const root=await mkdtemp(path.join(tmpdir(),'mf-gateway-'))
  let stream
  const upstream=http.createServer((req,res)=>{stream=res;res.writeHead(200,{'content-type':'text/event-stream'});res.write('data: connected\n\n')})
  const url=await listen(upstream),file=path.join(root,'connection.json')
  await writeFile(file,JSON.stringify({url,username:'opencode',password:'fixture'}))
  const gateway=createGateway({connectionFile:file,nativeRoot:root})
  try {
    const base=await listen(gateway),response=await fetch(base+'/opencode/event')
    const reader=response.body.getReader()
    assert.match(new TextDecoder().decode((await reader.read()).value),/connected/)
    stream.destroy()
    let timer
    const result=await Promise.race([reader.read().then(value=>value.done?'closed':'data',()=> 'closed'),new Promise(resolve=>{timer=setTimeout(()=>resolve('hung'),700)})])
    clearTimeout(timer)
    assert.equal(result,'closed')
  } finally {await close(gateway);await close(upstream);await rm(root,{recursive:true})}
})

test('one origin routes management separately and never serves private connection file', async () => {
  const root = await mkdtemp(path.join(tmpdir(),'mf-gateway-'))
  await mkdir(path.join(root,'dist'))
  await writeFile(path.join(root,'dist/index.html'),'<title>native</title>')
  await writeFile(path.join(root,'connection.json'),'secret')
  const legacy = http.createServer((req,res) => res.end('legacy:'+req.url))
  const url=await listen(legacy)
  const gateway=createGateway({nativeRoot:path.join(root,'dist'), connectionFile:path.join(root,'connection.json'),apiUrl:url,legacyUrl:url})
  try {
    const base=await listen(gateway)
    for(const route of ['/manage','/devices?device=test','/settings?tab=about','/guide']) {
      const redirect=await fetch(base+route,{redirect:'manual'})
      assert.equal(redirect.status,307)
      assert.equal(redirect.headers.get('location'),url+route)
      assert.equal(await (await fetch(base+route)).text(),'legacy:'+route)
    }
    assert.equal(await (await fetch(base+'/api/status')).text(),'legacy:/api/status')
    assert.match(await (await fetch(base+'/',{headers:{accept:'text/html'}})).text(), /native/)
    assert.equal((await fetch(base+'/_native/connection.json')).status,404)
    assert.equal((await fetch(base+'/_native/%2e%2e%2fconnection.json')).status,400)
    assert.equal((await fetch(base+'/opencode/session',{method:'POST',headers:{origin:'https://unrelated.test','content-type':'application/json'},body:'{}'})).status,403)
  } finally { await close(gateway);await close(legacy);await rm(root,{recursive:true}) }
})

test('native terminal upgrade tunnels bytes and basic authentication', async () => {
  const root = await mkdtemp(path.join(tmpdir(),'mf-gateway-'))
  const upstream=http.createServer()
  upstream.on('upgrade',(req,socket,head)=>{
    assert.match(req.headers.authorization,/^Basic /)
    assert.equal(req.url,'/pty/pty_fixture/connect?cursor=0')
    socket.write('HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\nterminal-fixture')
    socket.end()
  })
  const url=await listen(upstream), file=path.join(root,'connection.json')
  await writeFile(file,JSON.stringify({url,username:'opencode',password:'fixture'}))
  const gateway=createGateway({connectionFile:file,nativeRoot:root,apiUrl:url,legacyUrl:url})
  try {
    const base=await listen(gateway)
    const response=await new Promise((resolve,reject)=>{
      const socket=net.connect(Number(new URL(base).port),'127.0.0.1')
      let data=''; socket.on('error',reject);socket.on('data',chunk=>data+=chunk);socket.on('end',()=>resolve(data))
      socket.on('connect',()=>socket.write(`GET /opencode/pty/pty_fixture/connect?cursor=0 HTTP/1.1\r\nHost: ${new URL(base).host}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n`))
      socket.setTimeout(2000,()=>socket.destroy(new Error('timeout')))
    })
    assert.match(response,/101 Switching Protocols/)
    assert.match(response,/terminal-fixture/)
  } finally {await close(gateway);await close(upstream);await rm(root,{recursive:true})}
})
