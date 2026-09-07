import {test} from 'node:test';
import assert from 'node:assert/strict';
import plugin from '../fixed_runner/assets/pi/plugins/mediaflow/index.mjs';

test('management plugin registers read-only download and budget routes, no agent tools',()=>{
  const routes=[];
  plugin.activate({route:(method,path,handler)=>{routes.push({method,path,handler});return ()=>{};}});
  assert.deepEqual(routes.map(({method,path})=>[method,path]),[['GET','/skill'],['GET','/trial']]);
});
