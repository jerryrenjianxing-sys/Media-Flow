import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createGateway } from './gateway.mjs'

const root=path.dirname(fileURLToPath(import.meta.url))
const server=createGateway({
  connectionFile:process.env.MEDIAFLOW_NATIVE_CONNECTION,
  nativeRoot:process.env.MEDIAFLOW_NATIVE_DIST||path.join(root,'dist'),
  apiUrl:process.env.MEDIAFLOW_API_URL||'http://127.0.0.1:48138',
  legacyUrl:process.env.MEDIAFLOW_LEGACY_UI_URL||'http://127.0.0.1:3001',
  baseline:process.env.MEDIAFLOW_NATIVE_BASELINE==='1',
})
const port=Number(process.env.PORT||3000)
server.listen(port,'127.0.0.1',()=>console.log(`MediaFlow UI http://127.0.0.1:${port}`))
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>{server.close();server.closeAllConnections()})
