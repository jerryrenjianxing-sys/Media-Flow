import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import plugin from '../fixed_runner/assets/pi/plugins/mediaflow/index.mjs';
import {formatTrialBudget} from '../fixed_runner/assets/pi/plugins/mediaflow/client/entry.mjs';

test('management plugin registers read-only download and budget routes, no agent tools',()=>{
  const routes=[];
  plugin.activate({route:(method,path,handler)=>{routes.push({method,path,handler});return ()=>{};}});
  assert.deepEqual(routes.map(({method,path})=>[method,path]),[['GET','/skill'],['GET','/trial']]);
});

test('trial route reports unlimited usage above ten and client avoids a null quota',()=>{
  const directory=mkdtempSync(join(tmpdir(),'mediaflow-plugin-budget-'));
  for(let slot=1;slot<=12;slot++) writeFileSync(join(directory,`${slot}.claim`),'claim');
  writeFileSync(join(directory,'unrelated.txt'),'ignored');
  const routes=[];
  plugin.activate({route:(method,path,handler)=>{routes.push({method,path,handler});return ()=>{};}});
  const handler=routes.find(route=>route.path==='/trial').handler;
  const originalDirectory=process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR;
  const originalLimit=process.env.MEDIAFLOW_PI_REQUEST_LIMIT;
  let payload;
  process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR=directory;
  process.env.MEDIAFLOW_PI_REQUEST_LIMIT='unlimited';
  try {
    handler({}, {json:value=>{payload=value;}});
  } finally {
    if(originalDirectory===undefined) delete process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR;
    else process.env.MEDIAFLOW_PI_TRIAL_BUDGET_DIR=originalDirectory;
    if(originalLimit===undefined) delete process.env.MEDIAFLOW_PI_REQUEST_LIMIT;
    else process.env.MEDIAFLOW_PI_REQUEST_LIMIT=originalLimit;
  }
  assert.deepEqual(payload,{limit:'unlimited',used:12,remaining:null,
    message:'已记录12次真实模型请求；当前不设固定总次数，失败和工具续接仍计入。'});
  const display=formatTrialBudget(payload);
  assert.match(display,/已用 12 次（不限次数）/);
  assert.doesNotMatch(display,/\/null|剩余 10|\/10/);
});

test('trial route ignores old numeric or invalid quota settings',()=>{
  const routes=[];
  plugin.activate({route:(method,path,handler)=>{routes.push({method,path,handler});return ()=>{};}});
  const handler=routes.find(route=>route.path==='/trial').handler;
  const originalLimit=process.env.MEDIAFLOW_PI_REQUEST_LIMIT;
  try {
    for(const limit of ['10','invalid']) {
      process.env.MEDIAFLOW_PI_REQUEST_LIMIT=limit;
      let payload;
      handler({}, {json:value=>{payload=value;}});
      assert.equal(payload.limit,'unlimited');
      assert.equal(payload.remaining,null);
    }
  } finally {
    if(originalLimit===undefined) delete process.env.MEDIAFLOW_PI_REQUEST_LIMIT;
    else process.env.MEDIAFLOW_PI_REQUEST_LIMIT=originalLimit;
  }
});
