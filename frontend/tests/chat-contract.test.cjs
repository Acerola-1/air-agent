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
function fixture(options = {}) {
  const streams = [], canceled = [], stopIntents = [], navigationStops = [], state = { messages: [] };
  let ids = 0, submissionContext = null;
  const api = { searchThreads: async()=>[], createThread: async()=>`t${++ids}`, getThreadState: async()=>state,
    updateThreadMetadata: async()=>{}, deleteThread: async()=>{}, deriveTitle:(s)=>s,
    cancelRun:async(thread,run)=>{ canceled.push([thread,run]); if (options.cancelThrows) { const e = new Error('cancel'); e.status = options.cancelThrows; throw e; } },
    getRunStatus: options.getRunStatus ?? (async(thread, run) => {
      const seq = options.runStatuses ?? [{ found: true, status: 'cancelled' }];
      const nextIdx = Math.min(options._statusCursor ?? 0, seq.length - 1);
      if (options._setStatusCursor) options._setStatusCursor(nextIdx + 1);
      const value = seq[nextIdx];
      return typeof value === 'function' ? value() : value;
    }),
    listActiveRunIds: async()=> options.activeRuns ?? [],
    listRecentRuns: options.listRecentRuns ?? (async() =>
      (options.activeRuns ?? []).map((id) => ({ run_id: id, status: 'running' }))),
    notifyIfContextConflict: ()=>{},
    ThreadAccessError: class ThreadAccessError extends Error {},
    // 台账接线（change add-agent-event-ledger）：提交键/关联确认/停止意图
    newSubmissionKey: ()=> 'sub-key',
    setSubmissionContext: (ctx)=>{ submissionContext = ctx; },
    confirmRunAdmission: async()=> true,
    recordStopIntent: async(input)=>{ stopIntents.push(input); return true; },
    recordNavigationStopBeacon: (input)=>{ navigationStops.push(input); return true; },
    streamRun: (opts)=>{ const wait = deferred(); streams.push({...opts,wait}); return wait.promise; } };
  const timingCalls = [];
  const timingStubs = { hasVisibleTiming:()=>false, startTiming:()=>{timingCalls.push('start');return `m-${timingCalls.length}`;},
    finishTiming:(id)=>timingCalls.push(['finish',id]),
    timingFirstVisibleAnswer:(id)=>timingCalls.push(['first',id]),
    timingDisplayComplete:(id)=>timingCalls.push(['complete',id]),
    timingProgressSeen:(id)=>timingCalls.push(['progress',id]),
    timingAttachAttempt:(id,a)=>timingCalls.push(['attach',id,a]) };
  const module = load('lib/chat-store.ts', { zustand, '@/lib/uuidv7':{ generateUuidV7:()=>`msg${++ids}` },
    '@/lib/user-identity':{getUserIdentity:()=> 'user'}, '@/lib/langgraph-api':api, '@/lib/feedback/client':timingStubs });
  return { ...module, streams, canceled, stopIntents, navigationStops, state, api, timingCalls, getSubmissionContext: ()=> submissionContext };
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

class AdminApiErrorMock extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
function makeCtx(overrides = {}) {
  return {
    user: { id: 'current', account: 'tester', display_name: 'T', is_active: true, last_login_at: null, created_at: '', roles: [], followed_region_codes: [] },
    session: { session_id: 's1', active_tenant_id: 'tenant-a', ctx_version: 1, created_at: '', expires_at: '' },
    current_tenant: { id: 'tenant-a', code: 'a', name: '企业A', status: 'active' },
    membership: { id: 'm1', tenant_id: 'tenant-a', department_id: null, status: 'active', revision: 1, role_codes: [] },
    permissions: [], platform_admin: false, context_version: 1, tenants: [], needs_tenant_selection: false,
    ...overrides,
  };
}
function proxyFixture(ctx, hooks = {}) {
  class NextResponse extends Response {
    static json(value, init) { return new NextResponse(JSON.stringify(value), { ...init, headers: { 'content-type': 'application/json' } }); }
  }
  const routes = load('app/api/[..._path]/route.ts', {
    'next/server': { NextResponse },
    '@/lib/admin-api': {
      AdminApiError: AdminApiErrorMock,
      runtimeTicketViaAdmin: hooks.ticket ?? (async () => ({ ticket: 'issued-ticket', expires_at: '', status: 200 })),
    },
    '@/lib/auth': {
      getSessionContext: async () => (typeof ctx === 'function' ? ctx() : ctx),
      SESSION_COOKIE: 'air_agent_session',
    },
    '@/lib/artifact-scope': { signArtifactScope: (tenant, user, thread) => `scope:${tenant}:${user}:${thread}` },
    '@/lib/run-admission': {
      confirmRunWithMarker: hooks.confirmRunWithMarker ?? (async () => true),
      markAdmissionUnknown: hooks.markAdmissionUnknown ?? (async () => {}),
    },
    '@/lib/agent-ledger': {
      admitRequest: hooks.admit ?? (async (input) => ({
        request_id: 'req-1', attempt_id: 'att-1', submission_id: 'sub-1',
        admission_status: 'admitting',
        operation_kind: input.operationKind, reused: false, thread_id: input.threadId,
        graph_id: input.graphId, ledger_marker: 'lr:req-1:att-1',
      })),
      // R01：创建平台运行前先取启动权；默认由第一个调用方胜出
      claimStart: hooks.claimStart ?? (async () => ({ status: 'claimed', start_claim_token: 'tok-1' })),
      confirmAdmission: hooks.confirmAdmission ?? (async () => {}),
      ledgerMarker: (requestId, attemptId) => `lr:${requestId}:${attemptId}`,
      requestThreadDeletion: hooks.requestThreadDeletion ?? (async () => {}),
      parseLedgerMarker: (marker) => {
        const parts = typeof marker === 'string' ? marker.split(':') : [];
        return parts.length === 3 && parts[0] === 'lr' && parts[1] && parts[2]
          ? { requestId: parts[1], attemptId: parts[2] } : null;
      },
    },
  });
  return routes;
}
function request(path, body = {}, method = 'GET', claimHeaders = { 'x-tenant-id': 'tenant-a', 'x-tenant-context-version': '1' }) {
  return {
    nextUrl: { pathname: `/api/${path}`, search: '' },
    headers: new Headers({ 'content-type': 'application/json', cookie: 'air_agent_session=x', ...claimHeaders }),
    signal: new AbortController().signal,
    method,
    text: async () => (method === 'GET' ? '' : JSON.stringify(body)),
  };
}

test('proxy: unified identity gates, ticket upstream, v2 scope, tenant checks', async () => {
  const previous = global.fetch, oldBase = process.env.LANGGRAPH_API_URL;
  process.env.LANGGRAPH_API_URL = 'http://test-api';
  const calls = [];
  try {
    // 未登录 → 401，且不触上游
    const anon = proxyFixture(null);
    assert.equal((await anon.GET(request('threads/own/state'))).status, 401);
    assert.equal(calls.length, 0);

    // 多企业未选择 → 403
    const pending = proxyFixture(makeCtx({ needs_tenant_selection: true, current_tenant: null, membership: null }));
    assert.equal((await pending.GET(request('threads/own/state'))).status, 403);

    // 成员停用 → 403
    const disabled = proxyFixture(makeCtx({ membership: { id: 'm1', tenant_id: 'tenant-a', department_id: null, status: 'disabled', revision: 1, role_codes: [] } }));
    assert.equal((await disabled.GET(request('threads/own/state'))).status, 403);

    // 票据端点故障 → 失败关闭（不回退匿名）
    const noTicket = proxyFixture(makeCtx(), { ticket: async () => { throw new AdminApiErrorMock(503, '身份服务暂不可用'); } });
    assert.equal((await noTicket.GET(request('threads/own/state'))).status, 503);

    // 正常路径：Authorization=票据、v2 scope、可信心跳注入
    const routes = proxyFixture(makeCtx());
    global.fetch = async (url, options) => {
      calls.push({ url, options });
      return Response.json({ ok: true });
    };
    const own = await routes.POST(request('threads/own/runs/stream', { config: { configurable: { user_id: 'other', thread_id: 'foreign', __artifact_scope: 'forged' } } }, 'POST'));
    assert.equal(own.status, 200);
    // 按 URL 精确定位"被转发的运行创建"：后台的归属确认调用可能在其后到达，
    // 用 calls.at(-1) 会读到确认调用（既有脆弱断言，见本轮复验记录）。
    const upstream = calls.filter((c) => String(c.url).endsWith('/threads/own/runs/stream')).at(-1);
    assert.ok(upstream, '必须转发运行创建请求');
    assert.equal(upstream.options.headers.authorization, 'Bearer issued-ticket');
    const body = JSON.parse(upstream.options.body);
    assert.equal(body.config.configurable.user_id, 'current');
    assert.equal(body.config.configurable.tenant_id, 'tenant-a');
    assert.equal(body.config.configurable.thread_id, 'own');
    assert.equal(body.config.configurable.__artifact_scope, 'scope:tenant-a:current:own');
    assert.notEqual(body.config.configurable.__artifact_scope, 'forged');
    assert.equal(upstream.options.signal.aborted, false);

    // 创建线程：metadata 覆盖为可信 user/tenant
    calls.length = 0;
    await routes.POST(request('threads', { metadata: { user_id: 'other', graph_id: 'basic-qa' } }, 'POST'));
    const createdCall = calls.filter((c) => String(c.url).endsWith('/threads')).at(-1);
    assert.ok(createdCall, '必须转发线程创建请求');
    const created = JSON.parse(createdCall.options.body);
    assert.equal(created.metadata.user_id, 'current');
    assert.equal(created.metadata.tenant_id, 'tenant-a');

    // 删除线程先清归档（带票据），失败则不删线程
    calls.length = 0;
    global.fetch = async (url, options) => {
      calls.push({ url, options });
      return url.includes('/archives/') ? Response.json({ detail: 'gone' }, { status: 503 }) : Response.json({ ok: true });
    };
    const del = await routes.DELETE(request('threads/own', {}, 'DELETE'));
    assert.equal(del.status, 503);
    assert.ok(calls[0].url.includes('/archives/own'));
    assert.equal(calls.length, 1);

    // 上游 401（票据过期等）原样透传，客户端得到明确未认证
    calls.length = 0;
    global.fetch = async () => Response.json({ detail: '运行凭据无效' }, { status: 401 });
    const denied = await routes.GET(request('threads/own/state'));
    assert.equal(denied.status, 401);
  } finally {
    global.fetch = previous;
    if (oldBase === undefined) delete process.env.LANGGRAPH_API_URL; else process.env.LANGGRAPH_API_URL = oldBase;
  }
});

test('archive scope v2 signing requires configured keys', () => {
  const old = process.env.ARCHIVE_SCOPE_KEYS;
  try {
    delete process.env.ARCHIVE_SCOPE_KEYS;
    const scope = load('lib/artifact-scope.ts', { 'server-only': {}, 'node:crypto': require('node:crypto') });
    assert.throws(() => scope.signArtifactScope('t', 'u', 'th'), /ARCHIVE_SCOPE_KEYS/);
    process.env.ARCHIVE_SCOPE_KEYS = JSON.stringify(['unit-test-key']);
    const token = scope.signArtifactScope('tenant-a', 'user-1', 'thread-1');
    const claims = JSON.parse(Buffer.from(token.split('.')[0], 'base64url').toString());
    assert.equal(claims.v, 2);
    assert.equal(claims.tenantId, 'tenant-a');
    assert.equal(claims.userId, 'user-1');
    assert.equal(claims.threadId, 'thread-1');
    assert.equal(claims.aud, 'air-agent-archive');
  } finally {
    if (old === undefined) delete process.env.ARCHIVE_SCOPE_KEYS; else process.env.ARCHIVE_SCOPE_KEYS = old;
  }
});

// ------------------------------------------------------------- R10 停止确认（轮次01 修复）

test('stopStreaming only confirms after server reaches terminal run status', async()=>{
  const seq = [{ found: true, status: 'running' }, { found: true, status: 'running' }, { found: true, status: 'cancelled' }];
  const opts = { runStatuses: seq };
  opts._setStatusCursor = (v) => { opts._statusCursor = v; };
  const f=fixture(opts), store=f.useChatStore;
  const running=store.getState().sendMessage('问题','fast'); await tick();
  const stream=f.streams[0];
  stream.handlers.onRun('run-1'); await tick();
  const outcome = store.getState().stopStreaming();
  // 前两轮轮询仍在运行时不得提前宣告确认
  await tick(); await tick();
  const result = await outcome;
  assert.equal(opts._statusCursor, 3, '应在观察到终态后才停止轮询');
  assert.equal(result.confirmed, true);
  assert.ok(f.canceled.some(([thread, run])=>run==='run-1'));
  stream.wait.resolve(); await running;
});

test('stopStreaming reports unconfirmed when cancellation and status both fail', async()=>{
  const f=fixture({
    cancelThrows: 500,
    getRunStatus: async()=>{ const e=new Error('backend'); e.status=500; throw e; },
  }), store=f.useChatStore;
  const running=store.getState().sendMessage('问题','fast'); await tick();
  const stream=f.streams[0];
  stream.handlers.onRun('run-fail'); await tick();
  const result = await store.getState().stopStreaming();
  assert.equal(result.confirmed, false);
  assert.equal(result.reason, 'backend');
  stream.wait.resolve(); await running;
});

// F05：本栈 409 的现实来源是代理 context_changed——绝不能当作"已终态"确认
test('stop cancel 409 is context_changed, never a false confirmation', async()=>{
  const f=fixture({ cancelThrows: 409 }), store=f.useChatStore;
  const running=store.getState().sendMessage('问题','fast'); await tick();
  const stream=f.streams[0];
  stream.handlers.onRun('run-conflict'); await tick();
  const result = await store.getState().stopStreaming();
  assert.equal(result.confirmed, false);
  assert.equal(result.reason, 'context_changed');
  stream.wait.resolve(); await running;
});

// F05：404 取消（多半已终态）只有在 runs.get 见证终态后才确认
test('stop after 404 cancel confirms only on observed terminal status', async()=>{
  const f=fixture({ cancelThrows: 404 }), store=f.useChatStore;
  const running=store.getState().sendMessage('问题','fast'); await tick();
  const stream=f.streams[0];
  stream.handlers.onRun('run-gone'); await tick();
  const result = await store.getState().stopStreaming();
  assert.equal(result.confirmed, true); // 默认 getRunStatus → cancelled
  stream.wait.resolve(); await running;
});

test('stop treats missing/unknown run status as unconfirmed', async()=>{
  for (const obs of [{ found: false, status: null }, { found: true, status: null }]) {
    const f=fixture({ getRunStatus: async()=>obs }), store=f.useChatStore;
    const running=store.getState().sendMessage('问题','fast'); await tick();
    const stream=f.streams[0];
    stream.handlers.onRun('run-x'); await tick();
    const result = await store.getState().stopStreaming();
    assert.equal(result.confirmed, false);
    assert.equal(result.reason, 'unknown');
    stream.wait.resolve(); await running;
  }
});

test('stop with empty run list within grace window is unconfirmed unknown', async()=>{
  const f=fixture({ listRecentRuns: async()=>[] }), store=f.useChatStore;
  const running=store.getState().sendMessage('问题','fast'); await tick();
  const t0=Date.now();
  const result = await store.getState().stopStreaming();
  assert.equal(result.confirmed, false);
  assert.equal(result.reason, 'unknown');
  assert.ok(Date.now()-t0 >= 2500, 'resolve 窗口内持续找回而非立即下结论');
  if (f.streams[0]) f.streams[0].wait.resolve();
  await running;
});

test('stop recovers late run id from recent runs and waits terminal', async()=>{
  const opts = {
    listRecentRuns: async()=>[{ run_id: 'late-77', status: 'running' }],
    getRunStatus: async()=>({ found: true, status: 'cancelled' }),
  };
  const f=fixture(opts), store=f.useChatStore;
  const running=store.getState().sendMessage('问题','fast'); await tick();
  const stream=f.streams[0];
  const result = await store.getState().stopStreaming();
  assert.equal(result.confirmed, true);
  assert.ok(f.canceled.some(([, run])=>run==='late-77'));
  stream.wait.resolve(); await running;
});

test('stop before run id arrives recovers the active run from the run list', async()=>{
  const f=fixture({ activeRuns: ['late-run-9'] }), store=f.useChatStore;
  const running=store.getState().sendMessage('问题','fast'); await tick();
  const stream=f.streams[0];
  const result = await store.getState().stopStreaming();
  assert.equal(result.confirmed, true);
  assert.ok(f.canceled.some(([, run])=>run==='late-run-9'));
  stream.wait.resolve(); await running;
});

// ------------------------------------------------- R02 代理上下文声明（轮次01 修复）

test('proxy: stale-tab claim rejected, missing headers rejected, ticket 409 surfaces 409', async () => {
  const previous = global.fetch;
  process.env.LANGGRAPH_API_URL = 'http://test-api';
  const calls = [];
  try {
    const routes = proxyFixture(makeCtx());
    global.fetch = async (url, options) => { calls.push({ url, options }); return Response.json({ ok: true }); };

    // 旧标签页：会话在 tenant-a，页面仍声称 tenant-b → 409 context_changed
    const stale = await routes.POST(request('threads', { thread_id: 't-stale', metadata: {} }, 'POST',
      { 'x-tenant-id': 'tenant-b', 'x-tenant-context-version': '1' }));
    assert.equal(stale.status, 409);
    assert.equal((await stale.json()).code, 'context_changed');

    // 旧版本：企业一致但 ctx_version 落后 → 409
    const oldVersion = await routes.GET(request('threads/own/state', {}, 'GET',
      { 'x-tenant-id': 'tenant-a', 'x-tenant-context-version': '0' }));
    assert.equal(oldVersion.status, 409);

    // 缺失声明头 → 403 context_missing
    const missing = await routes.POST(request('threads', { thread_id: 'x' }, 'POST', {}));
    assert.equal(missing.status, 403);
    assert.equal((await missing.json()).code, 'context_missing');

    // 票据端点返回 409（会话已切换）→ 代理透传 409
    const ticketRoutes = proxyFixture(makeCtx(), { ticket: async () => ({ ticket: '', expires_at: '', status: 409 }) });
    const t409 = await ticketRoutes.POST(request('threads', { thread_id: 'y', metadata: {} }, 'POST'));
    assert.equal(t409.status, 409);

    // F01：代理铸造线程 ID——客户端自选 ID 一律不采信；确认端点只带 thread_id。
    // 合法路径触发线程创建后的归属确认（内部端点 + X-Internal-Token）
    process.env.INTERNAL_API_TOKEN = 'unit-internal';
    const confirmedCalls = [];
    global.fetch = async (url, options) => {
      confirmedCalls.push({ url, headers: options.headers, body: options.body ? JSON.parse(options.body) : undefined });
      return Response.json(url.includes('claim-confirmation') ? { confirmed: true } : { ok: true });
    };
    const created = await routes.POST(request('threads', { thread_id: 'new-1', if_exists: 'do_nothing', metadata: { user_id: 'current', tenant_id: 'tenant-a' } }, 'POST'));
    assert.equal(created.status, 200);
    const upstreamCreate = confirmedCalls.find((c) => /\/threads$/.test(c.url));
    assert.match(upstreamCreate.body.thread_id, /^[0-9a-f-]{36}$/, '代理必须铸造 uuid');
    assert.notEqual(upstreamCreate.body.thread_id, 'new-1');
    assert.equal(upstreamCreate.body.if_exists, undefined, 'if_exists 不转发（只允许严格创建）');
    const confirm = confirmedCalls.find((c) => c.url.includes('/internal/threads/claim-confirmation'));
    assert.ok(confirm, '创建成功后必须确认归属');
    assert.equal(confirm.headers['x-internal-token'], 'unit-internal');
    assert.deepEqual(Object.keys(confirm.body), ['thread_id']);
    assert.equal(confirm.body.thread_id, upstreamCreate.body.thread_id);

    // F01：平台 409 一律不触发确认（不再有"409=上次成功"的补偿分支）
    confirmedCalls.length = 0;
    global.fetch = async (url, options) => {
      confirmedCalls.push({ url });
      if (url.includes('claim-confirmation')) return Response.json({ confirmed: false });
      return Response.json({ detail: 'Thread already exists' }, { status: 409 });
    };
    const conflicted = await routes.POST(request('threads', { metadata: {} }, 'POST'));
    assert.equal(conflicted.status, 409);
    assert.ok(!confirmedCalls.some((c) => c.url.includes('claim-confirmation')), '409 路径不得调用确认端点');
  } finally {
    global.fetch = previous;
    delete process.env.INTERNAL_API_TOKEN;
  }
});

test('stop cannot confirm the previous answer while current run metadata is delayed', async()=>{
  const f=fixture({ listRecentRuns:async()=>[{run_id:'previous-run',status:'success'}] });
  const running=f.useChatStore.getState().sendMessage('current','fast'); await tick();
  const stopped=await f.useChatStore.getState().stopStreaming();
  f.streams[0].wait.resolve(); await running;
  assert.equal(stopped.confirmed,false,'上一轮完成不能证明本轮提交已经停止');
});

test('unconfirmed stop keeps the run for late metadata and blocks new submissions', async()=>{
  const f=fixture({listRecentRuns:async()=>[{run_id:'previous-run',status:'success'}]});
  const store=f.useChatStore;
  const running=store.getState().sendMessage('current','fast'); await tick();
  assert.equal((await store.getState().stopStreaming()).confirmed,false);
  assert.equal(store.getState().stopPending,true);
  await store.getState().sendMessage('must not submit','fast');
  assert.equal(f.streams.length,1);
  f.streams[0].handlers.onRun('current-late-run');
  assert.equal((await store.getState().stopStreaming()).confirmed,true);
  assert.equal(store.getState().stopPending,false);
  assert.ok(f.canceled.some(([,id])=>id==='current-late-run'));
  f.streams[0].wait.resolve();await running;
});


test('页面导航离开记录 origin=navigation 的独立停止意图', async()=>{
  const f=fixture(), store=f.useChatStore;
  const running=store.getState().sendMessage('长任务','expert'); await tick();
  const stream=f.streams.at(-1);
  stream.handlers.onRun('run-nav-1');
  // 运行中卸载 → 记录 navigation（不冒充已取消）
  store.getState().recordNavigationStop();
  assert.deepEqual(f.navigationStops.at(-1), { threadId: stream.threadId, runId: 'run-nav-1', origin: 'navigation' });
  stream.wait.resolve(); await running;
});

test('无活动运行时的导航不产生停止事实', async()=>{
  const f=fixture(), store=f.useChatStore;
  store.getState().recordNavigationStop();
  assert.equal(f.navigationStops.length, 0);
});


test('重新生成声明 regenerate 并复用上一条用户问题（不新增提问）', async()=>{
  const f=fixture(), store=f.useChatStore;
  const first=store.getState().sendMessage('原始问题','fast'); await tick();
  const s1=f.streams.at(-1);
  assert.equal(f.getSubmissionContext().operationKind, 'initial');
  s1.handlers.onValues({messages:[{type:'ai',content:'答案'}]});
  s1.handlers.onDone(); s1.wait.resolve(); await first;

  // 重新打开线程后本地可能无消息：回退到后端会话内容
  f.state.messages = [
    { type: 'human', content: '原始问题', id: 'u1' },
    { type: 'ai', content: '答案', id: 'a1' },
  ];
  const regen=store.getState().regenerateLast('fast'); await tick();
  const s2=f.streams.at(-1);
  assert.notEqual(s2, s1, '重新生成必须新建一次运行');
  assert.equal(f.getSubmissionContext().operationKind, 'regenerate');
  assert.equal(s2.messages.at(-1).content, '原始问题', '重新生成复用上一条用户问题');
  s2.wait.resolve(); await regen;
});

test('重新发送声明 retry', async()=>{
  const f=fixture(), store=f.useChatStore;
  const run=store.getState().sendMessage('失败问题','fast'); await tick();
  const stream=f.streams.at(-1);
  stream.handlers.onDone(new Error('boom')); stream.wait.resolve(); await run;
  assert.equal(store.getState().failedInput, '失败问题');
  const retry=store.getState().sendMessage(store.getState().failedInput,'fast','retry'); await tick();
  assert.equal(f.getSubmissionContext().operationKind, 'retry');
  f.streams.at(-1).wait.resolve(); await retry;
});

test('R01 代理：原生入口单点裁决——首笔成功、重放复用/待确认、绝不重复创建平台运行', async () => {
  const previous = global.fetch, oldBase = process.env.LANGGRAPH_API_URL;
  process.env.LANGGRAPH_API_URL = 'http://test-api';
  const calls = [];
  let platformReply = null;
  const createCalls = () => calls.filter((c) => String(c.url).includes('/threads/own/runs')).length;
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    const target = String(url);
    if (target.includes('/agent/requests')) {
      return Response.json({
        request_id: 'req-1', attempt_id: 'att-1', submission_id: 'sub-1',
        admission_status: 'admitting', operation_kind: 'initial', reused: false,
        thread_id: 'own', graph_id: 'basic-qa', ledger_marker: 'lr:req-1:att-1',
      });
    }
    if (target.includes('/agent/')) return Response.json({ ok: true });
    return platformReply ? platformReply() : Response.json({ run_id: 'run-new', thread_id: 'own', status: 'pending' });
  };
  const body = { assistant_id: 'basic-qa', input: { messages: [{ role: 'user', content: 'x' }] } };
  try {
    // 情形一（F01 正向回归）：**首笔提交必须真正启动运行**。
    // 修复前：BFF 先 claim、原生再 claim → 首笔 409「启动权已被消费」、重试 202，
    // 正常业务不可用；这里锁定"第一笔能通过整个链路"。
    const first = await proxyFixture(makeCtx()).POST(request('threads/own/runs', body, 'POST'));
    assert.equal(first.status, 200, '首笔提交必须成功启动');
    assert.equal((await first.json()).run_id, 'run-new');
    assert.equal(createCalls(), 1, '首笔只允许创建一次平台运行');
    const upstream = JSON.parse(calls.find((c) => String(c.url).includes('/threads/own/runs')).options.body);
    assert.equal(upstream.metadata.ledger_submission_id, 'sub-1');
    assert.equal(upstream.config.configurable.ledger_attempt_id, 'att-1');

    // 情形二：原生裁决「已有运行」→ 复用既有 run_id，不产生第二次创建
    calls.length = 0;
    platformReply = () => Response.json(
      { detail: { code: 'run_already_confirmed', platform_run_id: 'run-existing' } },
      { status: 409 },
    );
    const second = await proxyFixture(makeCtx()).POST(request('threads/own/runs', body, 'POST'));
    assert.equal(second.status, 200);
    assert.equal((await second.json()).run_id, 'run-existing', '必须复用既有运行');
    assert.equal(createCalls(), 1, '复用不得触发第二次平台创建');

    // 情形三：启动权已消费但无运行映射 → 202 待确认，绝不盲重发
    calls.length = 0;
    platformReply = () => Response.json(
      { detail: { code: 'run_already_started' } }, { status: 409 },
    );
    const unknown = await proxyFixture(makeCtx()).POST(request('threads/own/runs', body, 'POST'));
    assert.equal(unknown.status, 202);
    assert.equal((await unknown.json()).code, 'run_admission_unknown');
    assert.equal(createCalls(), 1, '待确认的提交不得盲重发');

    // 情形四：图/正文不一致 → 409 原样拒绝
    calls.length = 0;
    platformReply = () => Response.json(
      { detail: { code: 'run_request_mismatch', field: 'graph' } }, { status: 409 },
    );
    const mismatch = await proxyFixture(makeCtx()).POST(request('threads/own/runs', body, 'POST'));
    assert.equal(mismatch.status, 409);
    assert.equal((await mismatch.json()).code, 'run_request_mismatch');
  } finally {
    global.fetch = previous;
    if (oldBase === undefined) delete process.env.LANGGRAPH_API_URL; else process.env.LANGGRAPH_API_URL = oldBase;
  }
});

test('官方 interrupt 的权威状态确认本次已停止执行，不冒充业务已取消',async()=>{
  const f=fixture({runStatuses:[{found:true,status:'interrupted'}]}),store=f.useChatStore;
  const running=store.getState().sendMessage('问题','fast');await tick();
  const stream=f.streams[0];stream.handlers.onRun('run-interrupted');await tick();
  const result=await store.getState().stopStreaming();assert.equal(result.confirmed,true);
  assert.equal(store.getState().stopPending,false);
  stream.wait.resolve();await running;
});
