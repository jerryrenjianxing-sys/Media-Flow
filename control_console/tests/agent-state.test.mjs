import test from 'node:test';
import assert from 'node:assert/strict';
import { loadSessionUi, saveSessionUi, chooseSession, rememberSession, resizeComposer, shouldSendKey } from '../app/lib/agent-workspace-state.mjs';
const storage = () => { const map = new Map(); return { getItem: k => map.get(k) ?? null, setItem: (k,v) => map.set(k,v) }; };
test('Enter sends; Shift, IME selection and held Enter never send', () => {
  assert.equal(shouldSendKey({key:'Enter'}),true);
  for(const extra of [{shiftKey:true},{isComposing:true},{keyCode:229},{repeat:true}]) assert.equal(shouldSendKey({key:'Enter',...extra}),false);
  assert.equal(shouldSendKey({key:'a'}),false);
});
test('multi-choice answers and next draft are restored separately from the pending sent text', () => {
  const s=storage(); saveSessionUi(s,'ses_one',{text:'下一段草稿',sentText:'原请求',requestId:'req_1',answers:{q0:['首页','搜索']}});
  const saved=loadSessionUi(s,'ses_one'); assert.deepEqual(saved.answers.q0,['首页','搜索']);
  assert.equal(saved.sentText,'原请求'); assert.equal(saved.text,'下一段草稿');
});
test('composer starts at 36px, grows to 200px, and shrinks again after clearing', () => {
  const input={style:{},scrollHeight:24}; resizeComposer(input); assert.equal(input.style.height,'36px');
  input.scrollHeight=500; resizeComposer(input); assert.equal(input.style.height,'200px');
  input.scrollHeight=72; resizeComposer(input); assert.equal(input.style.height,'72px');
  input.scrollHeight=24; resizeComposer(input); assert.equal(input.style.height,'36px');
  resizeComposer(null);
});
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
