const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const frontend = path.resolve(__dirname, '..');
const swc = require(path.join(frontend, 'node_modules/next/dist/build/swc/index.js'));
const zustand = require(path.join(frontend, 'node_modules/zustand'));

function load(file, imports) {
  const transformed = swc.transformSync(fs.readFileSync(path.join(frontend, file), 'utf8'), {
    filename: file, jsc: { parser: { syntax: 'typescript', tsx: file.endsWith('.tsx') }, target: 'es2022', transform: { react: { runtime: 'automatic' } } }, module: { type: 'commonjs' },
  });
  const module = { exports: {} };
  const localRequire = (name) => {
    if (name in imports) return imports[name];
    throw new Error(`Unmocked dependency ${name}`);
  };
  new Function('require', 'module', 'exports', transformed.code)(localRequire, module, module.exports);
  return module.exports;
}
const tick = () => new Promise((resolve) => setImmediate(resolve));

test('context meter uses provider input after completion and labels preparation estimates',()=>{
  const React=require(path.join(frontend,'node_modules/react'));
  const {renderToStaticMarkup}=require(path.join(frontend,'node_modules/react-dom/server'));
  const metrics={model:'unknown',input_tokens:82060,input_budget_tokens:120000,window_tokens:192000,output_reserved_tokens:8192,safety_tokens:4096,measurement_source:'estimate:multilingual:1.25x',tool_count:115,input_breakdown_tokens:{system:26805,conversation:7000,tools:44319,framing:3936},latest_usage:{input_tokens:24785,output_tokens:1328}};
  const pass=({children})=>React.createElement(React.Fragment,null,children);
  const {ContextMeter}=load('components/chat/ContextMeter.tsx',{
    'react/jsx-runtime':require(path.join(frontend,'node_modules/react/jsx-runtime')),
    'lucide-react':{GaugeIcon:()=>null},'@/lib/chat-store':{useChatStore:selector=>selector({contextMetrics:metrics})},
    '@/lib/utils':{cn:(...parts)=>parts.filter(Boolean).join(' ')},
    '@/components/ui/tooltip':{Tooltip:pass,TooltipContent:pass,TooltipProvider:pass,TooltipTrigger:pass},
  });
  const actual=renderToStaticMarkup(React.createElement(ContextMeter));
  assert.match(actual,/24\.8k \/ 120\.0k/);
  assert.match(actual,/最近主模型实际输入 24785/);
  assert.match(actual,/工具定义（115 个）/);
  assert.match(actual,/82,060/);
  assert.ok(!actual.includes('≈ '));
  delete metrics.latest_usage;
  const preparing=renderToStaticMarkup(React.createElement(ContextMeter));
  assert.match(preparing,/≈ 82\.1k \/ 120\.0k/);
  assert.match(preparing,/最近准备的模型输入估算/);
  metrics.latest_usage={input_tokens:0};
  assert.match(renderToStaticMarkup(React.createElement(ContextMeter)),/>0 \/ 120\.0k</);
});
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
function fixture() {
  const streams = [], canceled = [], state = { messages: [] };
  let ids = 0;
  const api = { searchThreads: async()=>[], createThread: async()=>`t${++ids}`, getThreadState: async()=>state,
    updateThreadMetadata: async()=>{}, deleteThread: async()=>{}, deriveTitle:(s)=>s,
    cancelRun:async(thread,run)=>{ canceled.push([thread,run]); },
    streamRun: (opts)=>{ const wait = deferred(); streams.push({...opts,wait}); return wait.promise; } };
  const module = load('lib/chat-store.ts', { zustand, '@/lib/uuidv7':{ generateUuidV7:()=>`msg${++ids}` },
    '@/lib/user-identity':{getUserIdentity:()=> 'user'}, '@/lib/langgraph-api':api });
  return { ...module, streams, canceled, state };
}

test('every intermediate frame excludes tool JSON and internal summaries from body', async()=>{
  const f=fixture(), store=f.useChatStore;
  const snapshots=[]; store.subscribe(s=>snapshots.push(s.messages.map(m=>m.content).join('\n')+s.streamingText));
  const running=store.getState().sendMessage('问题','fast'); await tick();
  const handlers=f.streams[0].handlers;
  handlers.onRun('r1');
  for(const artifact_id of ['a','b']) handlers.onCustom({type:'context_status',stage:'data',status:'started',processing_id:'p',artifact_id});
  handlers.onCustom({type:'context_status',stage:'data',status:'completed',processing_id:'p',artifact_id:'a'});
  assert.equal(store.getState().dataStatus.status,'started');
  handlers.onCustom({type:'context_status',stage:'data',status:'completed',processing_id:'p',artifact_id:'b'});
  assert.equal(store.getState().dataStatus.status,'completed');
  handlers.onMessagesDelta([[{type:'tool', content:'{"code":400}'},{}], [{type:'AIMessageChunk',content:'内部摘要'},{tags:['nostream']}]]);
  assert.equal(store.getState().streamingText,'');
  const raw=[{type:'human',id:'h',content:'问题'}, {type:'ai',id:'draft',content:'内部计划',tool_calls:[{id:'c',name:'query',args:{}}]},
    {type:'tool',tool_call_id:'c',content:'{"success":false,"code":400}',status:'error'},
    {type:'ai',id:'internal',content:'内部摘要',additional_kwargs:{ui_visible:false}}];
  handlers.onValues({messages:raw,data_status:{}});
  assert.equal(store.getState().dataStatus,null);
  handlers.onCustom({type:'final_output_done', message:'可读终答',message_id:'final'});
  f.state.messages=[...raw,{type:'ai',id:'final',content:'可读终答',additional_kwargs:{ui_visible:true}}];
  handlers.onValues(f.state); f.streams[0].wait.resolve(); await running;
  assert.ok(snapshots.every(body=>!body.includes('code')&&!body.includes('内部摘要')&&!body.includes('内部计划')));
  assert.equal(store.getState().messages.at(-1).content,'可读终答');
  assert.equal(store.getState().messages.at(-1).tools[0].status,'error');
  assert.ok(store.getState().messages.at(-1).tools[0].result.includes('400'));
});

test('old run callbacks and completion cannot clear a newer run', async()=>{
  const f=fixture(), store=f.useChatStore;
  const first=store.getState().sendMessage('旧问题','fast'); await tick();
  const old=f.streams[0]; old.handlers.onRun('old-run');
  store.getState().newThread();
  const second=store.getState().sendMessage('新问题','fast'); await tick();
  const current=f.streams[1]; current.handlers.onRun('new-run');
  old.handlers.onValues({messages:[{type:'ai',content:'旧答案'}]});
  old.handlers.onCustom({type:'final_output_done',message:'旧答案'});
  old.handlers.onDone(new Error('旧错误')); old.wait.resolve(); await first;
  assert.equal(store.getState().isStreaming,true);
  assert.equal(store.getState().error,null);
  assert.ok(store.getState().messages.every(m=>!m.content.includes('旧')));
  assert.deepEqual(f.canceled[0],[old.threadId,'old-run']);
  current.wait.resolve(); await second;
});

test('stop sends server cancellation and ignores late metadata safely', async()=>{
  const f=fixture(),store=f.useChatStore;
  const running=store.getState().sendMessage('问题','fast'); await tick();
  const stream=f.streams[0];
  store.getState().stopStreaming();
  stream.handlers.onRun('late-run');
  stream.handlers.onCustom({type:'final_output_done',message:'取消后旧答案'});
  stream.wait.resolve(); await running; await tick();
  assert.equal(store.getState().isStreaming,false);
  assert.ok(f.canceled.some(([thread,run])=>thread===stream.threadId&&run==='late-run'));
  assert.ok(!store.getState().messages.some(m=>m.content==='取消后旧答案'));
});

test('legacy double answer merges, marked draft hidden, archives require actual stored status',()=>{
  const f=fixture();
  const parsed=f.parseMessages([{type:'human',content:'问'}, {type:'ai',content:'原稿',additional_kwargs:{ui_visible:false}},
    {type:'ai',content:'终稿',id:'final'}, {type:'tool',tool_call_id:'failed',status:'error',content:'failed',artifact:{artifact_id:'storage_failed'} }]);
  assert.equal(parsed.length,2); assert.equal(parsed[1].content,'终稿'); assert.equal(parsed[1].tools[0].artifactId,undefined);
  const legacy=f.parseMessages([{type:'human',content:'问'},{type:'ai',content:'原稿'},{type:'ai',content:'终稿'}]);
  assert.equal(legacy.length,2); assert.equal(legacy[1].content,'终稿');
  const legacyError=f.parseMessages([{type:'ai',content:'{"code":400,"success":false,"msg":"raw error"}'}]);
  assert.equal(legacyError[0].content,'查询未完成，请调整条件或稍后重试。');
});

test('SDK error event becomes failed completion, metadata and custom consumed',async()=>{
  const calls=[];
  const client={runs:{stream:async function*(){yield {event:'metadata',data:{run_id:'r'}};yield {event:'custom',data:{type:'context_status'}};yield {event:'error',data:{message:'raw diagnostic'}};},cancel:async()=>{}}};
  const module=load('lib/langgraph-api.ts',{'@langchain/langgraph-sdk':{Client:class{constructor(){return client;}}}, '@/lib/uuidv7':{generateUuidV7:()=> 'id'}});
  await module.streamRun({threadId:'t',graphId:'basic-qa',messages:[], handlers:{onValues:()=>{},onRun:id=>calls.push(id),onCustom:event=>calls.push(event.type),onDone:error=>calls.push(error?.message)}});
  assert.deepEqual(calls.slice(0,2),['r','context_status']);
  assert.match(calls[2],/处理失败/); assert.ok(!calls[2].includes('raw diagnostic'));
});

function proxyFixture(user, upstream) {
  const { randomUUID }=require('node:crypto');
  class NextResponse extends Response { static json(value,init){return new NextResponse(JSON.stringify(value),{...init,headers:{'content-type':'application/json'}});} }
  const scope=load('lib/artifact-scope.ts',{'server-only':{},'node:crypto':require('node:crypto')});
  const routes=load('app/api/[..._path]/route.ts',{'node:crypto':{randomUUID},'next/server':{NextResponse},'@/lib/auth':{getSessionUser:async()=>user},'@/lib/artifact-scope':scope});
  return routes;
}
function request(path,body={}) {return {nextUrl:{pathname:`/api/${path}`,search:''},headers:new Headers({'content-type':'application/json','x-artifact-scope':'forged'}),signal:new AbortController().signal,text:async()=>JSON.stringify(body)};}

test('proxy rejects foreign threads and overrides user, metadata, and scope for own requests',async()=>{
  const previous=global.fetch, oldBase=process.env.LANGGRAPH_API_URL, oldSecret=process.env.AUTH_SECRET;
  process.env.LANGGRAPH_API_URL='http://test-api';process.env.AUTH_SECRET='test-only-secret';
  const calls=[];
  try {
    const routes=proxyFixture({id:'current'},null);
    global.fetch=async(url,options)=>{calls.push({url,options});return Response.json({metadata:{user_id:'other'}});};
    const denied=await routes.GET(request('threads/foreign/state'));
    assert.equal(denied.status,403);assert.equal(calls.length,1);
    calls.length=0;
    global.fetch=async(url,options)=>{calls.push({url,options});return Response.json(url.endsWith('/threads/own')?{metadata:{user_id:'current'}}:{ok:true});};
    const own=await routes.POST(request('threads/own/runs/stream',{config:{configurable:{user_id:'other',thread_id:'foreign',artifact_scope:'forged'}}}));
    assert.equal(own.status,200);
    const body=JSON.parse(calls.at(-1).options.body);
    assert.equal(body.config.configurable.user_id,'current');assert.equal(body.config.configurable.thread_id,'own');
    assert.notEqual(body.config.configurable.__artifact_scope,'forged');
    assert.equal(calls.at(-1).options.headers['content-type'],'application/json');
    assert.equal(calls.at(-1).options.signal.aborted,false);
    calls.length=0;
    await routes.POST(request('threads/search',{metadata:{user_id:'other',graph_id:'basic-qa'}}));
    assert.equal(JSON.parse(calls[0].options.body).metadata.user_id,'current');
    const noAuth=proxyFixture(null,null);
    assert.equal((await noAuth.GET(request('threads/own/state'))).status,401);
  } finally {
    global.fetch=previous;
    if(oldBase===undefined)delete process.env.LANGGRAPH_API_URL;else process.env.LANGGRAPH_API_URL=oldBase;
    if(oldSecret===undefined)delete process.env.AUTH_SECRET;else process.env.AUTH_SECRET=oldSecret;
  }
});
