import test from 'node:test';
import assert from 'node:assert/strict';
import { loadSessionUi, saveSessionUi, chooseSession, rememberSession } from '../app/lib/agent-workspace-state.mjs';
const storage = () => { const map = new Map(); return { getItem: k => map.get(k) ?? null, setItem: (k,v) => map.set(k,v) }; };
test('URL and recent selection survive full-page navigation without replacing authoritative history', () => {
  const s=storage(); rememberSession(s,'ses_two');
  assert.equal(chooseSession('?session=ses_one',s,['ses_one','ses_two']), 'ses_one');
  assert.equal(chooseSession('',s,['ses_one','ses_two']), 'ses_two');
  assert.equal(chooseSession('?session=ses_missing',s,['ses_two']), 'ses_missing');
  assert.equal(chooseSession('',storage(),['ses_one']), 'ses_one');
});
test('draft, pending request, question answers and failure survive remount per session', () => {
  const s=storage(); saveSessionUi(s,'ses_one',{text:'继续',requestId:'req_1',notice:'等待确认',answers:{q0:'两条'}});
  saveSessionUi(s,'ses_two',{text:'另一个任务'});
  assert.equal(loadSessionUi(s,'ses_one').requestId,'req_1');
  assert.equal(loadSessionUi(s,'ses_one').answers.q0,'两条');
  assert.equal(loadSessionUi(s,'ses_two').text,'另一个任务');
  assert.equal(loadSessionUi(s,'ses_two').requestId,null);
});
test('unavailable or corrupt preference storage cannot break chat', () => {
  const s={getItem(){throw Error('disabled');},setItem(){throw Error('full');}};
  assert.equal(chooseSession('',s,['ses_one']),'ses_one');
  assert.equal(saveSessionUi(s,'ses_one',{text:'keep in memory'}),false);
  assert.equal(loadSessionUi(s,'ses_one').text,'');
});
