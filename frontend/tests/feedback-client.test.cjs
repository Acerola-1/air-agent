const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const swc = require(path.join(root, 'node_modules/next/dist/build/swc/index.js'));
const tick = () => new Promise(setImmediate);
const ok = body => ({ok:true,status:200,json:async()=>body});
const defer = () => { let resolve; const promise = new Promise(r=>resolve=r); return {promise,resolve}; };
function load() {
  const result=swc.transformSync(fs.readFileSync(path.join(root,'lib/feedback/client.ts'),'utf8'),{filename:'client.ts',jsc:{parser:{syntax:'typescript'},target:'es2022'},module:{type:'commonjs'}});
  const module={exports:{}};new Function('module','exports',result.code)(module,module.exports);return module.exports;
}
const mapping = (id='answer')=>({answer_id:id,message_id:'message',thread_id:'thread',attempt_id:'attempt',request_id:'request',content_version:1,rating:null,reason_code:null,revision:0,feedbackable:true});
function fetchFor(t,handler) {
  const original=global.fetch;t.after(()=>{global.fetch=original});
  global.fetch=(url,options)=>String(url).includes('/feedback/policy')?Promise.resolve(ok({enabled:true})):handler(url,options);
}
async function scope(c,tenant='A',version=1){c.setFeedbackScope(tenant,'user',version);await tick();}

test('采集开关默认关闭，服务端拒绝启用时不产生计时或事件',async t=>{
  const original=global.fetch;t.after(()=>{global.fetch=original});
  const policy=defer();let posts=0;global.fetch=(url)=>String(url).includes('/policy')?policy.promise:(posts++,Promise.resolve(ok({items:[]})));
  const c=load();c.setFeedbackScope('A','user',1);c.trackCopy('answer');assert.equal(c.startTiming(1),'');assert.equal(c.queueSize(),0);
  policy.resolve(ok({enabled:false}));await tick();c.trackCopy('answer');assert.equal(posts,0);assert.equal(c.feedbackEnabled(),false);
});

test('未确认映射不永久负缓存，确认后可再次回读',async t=>{
  let confirmed=false,calls=0;fetchFor(t,async()=>{calls++;return ok({items:confirmed?[mapping()]:[]})});
  const c=load();await scope(c);await c.loadAnswerMappings(['message'],false,'thread');confirmed=true;await c.loadAnswerMappings(['message'],false,'thread');
  assert.equal(calls,2);assert.equal(c.cachedAnswerByMessage('message','thread').answer_id,'answer');
});

test('A到B到A后旧代次回读不能覆盖当前映射',async t=>{
  const old=defer();let first=true;fetchFor(t,()=>{if(first){first=false;return old.promise}return Promise.resolve(ok({items:[mapping('new-A')]}))});
  const c=load();await scope(c);const pending=c.loadAnswerMappings(['message'],false,'thread');await scope(c,'B',2);await scope(c,'A',3);await c.loadAnswerMappings(['message'],false,'thread');
  old.resolve(ok({items:[mapping('old-A')]}));await pending;assert.equal(c.cachedAnswerByMessage('message','thread').answer_id,'new-A');
});

test('旧企业保存响应不会作为新企业的成功结果',async t=>{
  const old=defer();fetchFor(t,()=>old.promise);const c=load();await scope(c);const pending=c.submitFeedback({answerId:'answer-A',rating:'positive',expectedRevision:0,operationId:'operation-A'});
  await scope(c,'B',2);old.resolve(ok({rating:'positive',revision:1}));const result=await pending;assert.equal(result.ok,false);
});

test('旧批次完成不删除新企业队列且新请求绑定新企业',async t=>{
  const pending=[];fetchFor(t,(url,options)=>{const response=defer();pending.push({options,response});return response.promise});
  const c=load();await scope(c);c.trackCopy('answer-A');await scope(c,'B',2);c.trackCopy('answer-B');assert.equal(c.queueSize(),1);
  pending[0].response.resolve(ok({items:[{valid:true}]}));await tick();assert.equal(pending.length,2);assert.equal(c.queueSize(),1);
  assert.equal(pending[1].options.headers['x-feedback-scope'],'B:user');assert.equal(JSON.parse(pending[1].options.body).events[0].payload.answer_id,'answer-B');
  pending[1].response.resolve(ok({items:[{valid:true}]}));await tick();assert.equal(c.queueSize(),0);
});

test('队列100条、批次50条和256KiB均有界',async t=>{
  const first=defer();const sent=[];fetchFor(t,(url,options)=>{sent.push(options.body);return sent.length===1?first.promise:Promise.resolve(ok({items:[]}))});
  const c=load();await scope(c);for(let i=0;i<180;i++)c.trackCopy('answer-'+i);assert.equal(c.queueSize(),100);
  first.resolve(ok({items:[]}));await tick();await tick();assert.equal(c.queueSize(),0);
  assert.ok(sent.every(body=>Buffer.byteLength(body)<=256*1024 && JSON.parse(body).events.length<=50));
  const count=sent.length;c.enqueueBrowserEvent('browser.copy',{answer_id:'x'.repeat(270000)});await tick();assert.equal(sent.length,count);assert.equal(c.queueSize(),0);
});

test('进度不充当首正文；不完整、完成和离开分别计时',async t=>{
  const events=[],keepalives=[];fetchFor(t,async(url,options)=>{for(const event of JSON.parse(options.body).events)if(!events.some(e=>e.event_id===event.event_id))events.push(event);if(options.keepalive)keepalives.push(options.body);return ok({items:[]})});
  const c=load();await scope(c);let id=c.startTiming(1);c.timingAttachAttempt(id,'attempt');c.timingProgressSeen(id);c.finishTiming(id);await tick();
  assert.equal(events[0].payload.first_visible_answer_ms,null);assert.equal(events[0].payload.display_complete_ms,null);assert.equal(events[0].payload.completion_state,'incomplete');
  id=c.startTiming(2);c.timingAttachAttempt(id,'attempt');c.timingFirstVisibleAnswer(id);c.timingDisplayComplete(id);c.finishTiming(id);await tick();
  assert.ok(events[1].payload.display_complete_ms>=events[1].payload.first_visible_answer_ms);assert.equal(events[1].payload.completion_state,'complete');
  id=c.startTiming(3);c.timingAttachAttempt(id,'attempt');c.abandonTimingsOnUnload();await tick();assert.equal(events[2].payload.completion_state,'abandoned');assert.equal(keepalives.length,1);assert.ok(Buffer.byteLength(keepalives[0])<=60*1024);assert.notEqual(events[0].payload.measurement_id,events[2].payload.measurement_id);
});

test('旧企业迟到的启用策略不能开启当前企业采集',async t=>{
  const original=global.fetch;t.after(()=>{global.fetch=original});const old=defer();let first=true;
  global.fetch=()=>{if(first){first=false;return old.promise}return Promise.resolve(ok({enabled:false}))};
  const c=load();c.setFeedbackScope('A','user',1);await scope(c,'B',2);old.resolve(ok({enabled:true}));await tick();assert.equal(c.feedbackEnabled(),false);assert.equal(c.startTiming(1),'');
});

test('DOM完成早于可信attempt确认时仍保留计时，不误用平台run UUID',async t=>{
 const sent=[];fetchFor(t,async(url,options)=>{sent.push(JSON.parse(options.body));return ok({items:[]})});
 const c=load();await scope(c);const id=c.startTiming(7);
 c.timingFirstVisibleAnswer(id);c.timingDisplayComplete(id);c.finishTiming(id);await tick();
 assert.equal(sent.flatMap(x=>x.events??[]).filter(e=>e.event_type==='browser.timing').length,0);
 c.timingAttachAttempt(id,'trusted-ledger-attempt');await tick();
 const events=sent.flatMap(x=>x.events??[]).filter(e=>e.event_type==='browser.timing');
 assert.equal(events.length,1);assert.equal(events[0].payload.attempt_id,'trusted-ledger-attempt');assert.equal(events[0].payload.completion_state,'complete');
});
