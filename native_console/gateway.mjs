/** Same-origin transport only. OpenCode remains the sole owner of chat state. */
import http from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import { createReadStream } from 'node:fs'
import path from 'node:path'

const loopback = new Set(['127.0.0.1','::1','::ffff:127.0.0.1'])
const management = /^\/(manage|workbench|devices|run|records|interactions|content|governance|settings|guide|guides)(\/|$)/
const mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon','.woff2':'font/woff2','.wasm':'application/wasm','.json':'application/json'}
const error = (res,status,message) => {res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify({error:message}))}

function localRequest(req) {
  try {
    const url=new URL('http://'+req.headers.host)
    return ['127.0.0.1','localhost','[::1]'].includes(url.hostname) && loopback.has(req.socket.remoteAddress)
      && (!req.headers.origin || new URL(req.headers.origin).host===url.host)
  } catch {return false}
}

export function createGateway({connectionFile,nativeRoot,apiUrl='http://127.0.0.1:48138',legacyUrl='http://127.0.0.1:3001',baseline=false}) {
  const sockets=new Set()
  const connection=async()=>{
    const config=JSON.parse(await readFile(connectionFile,'utf8'))
    const url=new URL(config.url)
    if(url.protocol!=='http:' || !['127.0.0.1','localhost','[::1]'].includes(url.hostname)) throw new Error('invalid engine endpoint')
    return config
  }
  const destination=async(req)=>{
    if(req.url.startsWith('/opencode/') || baseline) {
      const config=await connection()
      return {url:new URL(config.url),route:req.url.startsWith('/opencode/')?req.url.slice('/opencode'.length):req.url,authorization:'Basic '+Buffer.from(`${config.username}:${config.password}`).toString('base64')}
    }
    return {url:new URL(req.url.startsWith('/api/')?apiUrl:legacyUrl),route:req.url}
  }
  function forward(req,res,target) {
    const headers={...req.headers,host:target.url.host}
    if(target.authorization) headers.authorization=target.authorization
    const upstream=http.request({hostname:target.url.hostname,port:target.url.port,method:req.method,path:target.route,headers},response=>{
      // Byte-for-byte streaming: no model, message, tool or status rewriting.
      res.writeHead(response.statusCode,response.headers)
      response.on('aborted',()=>res.destroy())
      response.on('error',()=>res.destroy())
      response.pipe(res)
    })
    upstream.on('error',()=>{if(!res.headersSent)error(res,502,'本地服务暂未连接，请重试；原记录仍保留');else res.destroy()})
    req.on('aborted',()=>upstream.destroy())
    res.on('close',()=>upstream.destroy())
    req.pipe(upstream)
  }
  const server=http.createServer(async(req,res)=>{
    if(!localRequest(req))return error(res,403,'仅支持本机页面连接')
    try {
      const pathname=new URL(req.url,'http://localhost').pathname
      // Management has its own router/assets. External anchors bypass the
      // native client router; this compatibility redirect reaches that origin.
      if(management.test(pathname)) {
        res.writeHead(307,{location:new URL(req.url,legacyUrl).href,'cache-control':'no-store'})
        return res.end()
      }
      if(pathname==='/mediaflow/bootstrap') {
        let config
        try {
          config=await connection()
          const response=await fetch(config.url+'/global/health',{headers:{authorization:'Basic '+Buffer.from(`${config.username}:${config.password}`).toString('base64')},signal:AbortSignal.timeout(1500)})
          if(!response.ok || !(await response.json()).healthy)throw new Error('engine not healthy')
        } catch {
          // Startup is idempotent and does not start a task. No native API state is fabricated.
          await fetch(apiUrl+'/api/agent/start',{method:'POST',headers:{'content-type':'application/json'},body:'{}',signal:AbortSignal.timeout(3000)}).catch(()=>{})
          return error(res,503,'正在连接原生助手，请稍后重试；管理中心仍可使用')
        }
        res.writeHead(200,{'content-type':'application/json','cache-control':'no-store'})
        return res.end(JSON.stringify({workspace:config.workspace,server:'/opencode'}))
      }
      const nativeApi = baseline && /^\/(global|session|project|provider|auth|event|config|path|vcs|file|pty|command|skill|lsp|mcp|permission|question|experimental|location|worktree|agent|find|formatter|instance|log|sync|tui|api)(\/|$)/.test(pathname)
      if(nativeApi || pathname.startsWith('/opencode/') || pathname.startsWith('/api/') || management.test(pathname)
        || pathname.startsWith('/_') && !pathname.startsWith('/_native/') || !baseline && pathname.startsWith('/assets/')) {
        return forward(req,res,await destination(req))
      }
      if(!['GET','HEAD'].includes(req.method))return error(res,405,'不支持此操作')
      const raw=decodeURIComponent(req.url.split('?')[0])
      if(raw.includes('\\')||raw.split('/').includes('..')||raw.includes('\0'))return error(res,400,'无效资源路径')
      const relative=pathname.startsWith('/_native/')?decodeURIComponent(pathname.slice('/_native/'.length)):
        baseline && path.extname(pathname)?decodeURIComponent(pathname.slice(1)):'index.html'
      const root=path.resolve(nativeRoot), file=path.resolve(root,relative)
      if(!file.startsWith(root+path.sep))return error(res,400,'无效资源路径')
      let info
      try {info=await stat(file)}catch{return error(res,404,'页面资源不存在，请检查本机构建')}
      if(!info.isFile())return error(res,404,'页面资源不存在')
      res.writeHead(200,{'content-type':mime[path.extname(file)]||'application/octet-stream','content-length':info.size,
        'cache-control':relative==='index.html'?'no-store':'public, max-age=3600'})
      if(req.method==='HEAD')return res.end()
      createReadStream(file).pipe(res)
    }catch{if(!res.headersSent)error(res,503,'原生助手尚未连接，请刷新重试；管理中心和历史数据仍保留');else res.destroy()}
  })
  server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket))})
  server.on('upgrade',async(req,socket,head)=>{
    if(!localRequest(req)||!req.url.startsWith('/opencode/') && !(baseline && req.url.startsWith('/pty/')))return socket.end('HTTP/1.1 403 Forbidden\r\n\r\n')
    try {
      const target=await destination(req)
      const upstream=http.request({hostname:target.url.hostname,port:target.url.port,path:target.route,method:'GET',
        headers:{...req.headers,host:target.url.host,authorization:target.authorization}})
      upstream.on('upgrade',(response,peer,peerHead)=>{
        socket.write(`HTTP/1.1 ${response.statusCode} ${response.statusMessage}\r\n`+response.rawHeaders.reduce((all,v,i,items)=>i%2?all:all+`${v}: ${items[i+1]}\r\n`,'')+'\r\n')
        if(peerHead.length)socket.write(peerHead)
        if(head.length)peer.write(head)
        peer.pipe(socket);socket.pipe(peer)
        peer.on('error',()=>socket.destroy());socket.on('error',()=>peer.destroy())
        socket.on('close',()=>peer.destroy());peer.on('close',()=>socket.destroy())
      })
      upstream.on('response',response=>{socket.end(`HTTP/1.1 ${response.statusCode} ${response.statusMessage}\r\nConnection: close\r\n\r\n`);response.resume()})
      upstream.on('error',()=>socket.destroy())
      socket.on('close',()=>upstream.destroy())
      upstream.end()
    }catch{socket.end('HTTP/1.1 503 Service Unavailable\r\n\r\n')}
  })
  const closeAll=server.closeAllConnections.bind(server)
  server.closeAllConnections=()=>{closeAll();for(const socket of sockets)socket.destroy()}
  return server
}
