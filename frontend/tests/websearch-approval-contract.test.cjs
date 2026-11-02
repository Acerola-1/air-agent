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

test('web citation markers become internal links and unknown numbers are dropped', () => {
  const { withWebCitationLinks, webCitationNumber } = load('lib/web-citations.ts', {});
  assert.equal(withWebCitationLinks('要求[web:1]执行'), '要求[1](https://webcitation.invalid/1)执行');
  assert.equal(withWebCitationLinks('未知[web:9]编号', new Set([1])), '未知编号');
  assert.equal(withWebCitationLinks('非法[web:0]编号'), '非法编号');
  assert.equal(webCitationNumber('https://webcitation.invalid/2'), 2);
  assert.equal(webCitationNumber('https://example.com/2'), null);
});

test('knowledge and web citations are converted independently', () => {
  const { renderCitationMarkdown } = load('lib/web-citations.ts', {});
  const content = '依据[[cite:3f3d3f3d-0000-0000-0000-000000000000]]，新规要求[web:2]。';
  const rendered = renderCitationMarkdown(content, new Set([2]));
  assert.match(rendered, /\[📄 查看来源\]\(https:\/\/citation\.invalid\/3f3d3f3d-0000-0000-0000-000000000000\)/);
  assert.match(rendered, /\[2\]\(https:\/\/webcitation\.invalid\/2\)/);
  assert.ok(!rendered.includes('[web:2]'));
});

test('approval card exposes only approve/deny and the required disclosure fields', () => {
  const source = fs.readFileSync(path.join(frontend, 'components/chat/WebSearchApprovalCard.tsx'), 'utf8');
  assert.match(source, /批准/);
  assert.match(source, /禁止/);
  assert.match(source, /approval\.provider/);
  assert.match(source, /approval\.query/);
  assert.match(source, /approval\.reason/);
  assert.match(source, /approval\.remaining/);
  assert.match(source, /停止本次运行/);
  assert.ok(!/<textarea/i.test(source));
  assert.ok(!/<input/i.test(source));
});

test('markdown renders web citations as sup nodes without raw HTML injection', () => {
  const source = fs.readFileSync(path.join(frontend, 'components/chat/Markdown.tsx'), 'utf8');
  assert.match(source, /data-web-citation-id/);
  assert.match(source, /<sup/);
  assert.ok(!source.includes('dangerouslySetInnerHTML'));
});

function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }

function fixture({ threadState, interruptedRun = { run_id: 'r1', status: 'interrupted' }, calibration = { confirmed: true, attempt_id: 'a1' } } = {}) {
  const streams = [];
  const stateValue = threadState ?? {
    messages: [],
    next: ['websearch_gate'],
    tasks: [{ interrupts: [{ value: { type: 'websearch_approval', provider: 'DeepSeek', query: '最新空气质量政策', reason: '政策公告', remaining: 3 } }] }],
  };
  let submissionContext = null;
  const api = {
    searchThreads: async () => [],
    createThread: async () => 't1',
    getThreadState: async () => stateValue,
    updateThreadMetadata: async () => {},
    deleteThread: async () => {},
    deriveTitle: (s) => s,
    cancelRun: async () => {},
    getRunStatus: async () => ({ found: true, status: 'interrupted' }),
    listRecentRuns: async () => (interruptedRun ? [interruptedRun] : []),
    notifyIfContextConflict: () => {},
    ThreadAccessError: class ThreadAccessError extends Error {},
    newSubmissionKey: () => 'sub-key',
    setSubmissionContext: (ctx) => { submissionContext = ctx; },
    confirmRunAdmission: async () => ({ confirmed: true }),
    confirmInterruptedRun: async () => calibration,
    recordStopIntent: async () => true,
    recordNavigationStopBeacon: () => true,
    streamRun: (opts) => { const wait = deferred(); streams.push({ ...opts, wait }); return wait.promise; },
  };
  const timing = { hasVisibleTiming: () => false, startTiming: () => 'm', finishTiming: () => {},
    timingFirstVisibleAnswer: () => {}, timingDisplayComplete: () => {}, timingProgressSeen: () => {}, timingAttachAttempt: () => {} };
  const outputStream = load('lib/agent-output-stream.ts', {});
  const module = load('lib/chat-store.ts', {
    zustand,
    '@/lib/uuidv7': { generateUuidV7: () => `msg${streams.length}-${Math.random()}` },
    '@/lib/user-identity': { getUserIdentity: () => 'user' },
    '@/lib/langgraph-api': api,
    '@/lib/feedback/client': timing,
    '@/lib/agent-output-stream': outputStream,
  });
  return { ...module, streams, getSubmissionContext: () => submissionContext };
}

test('open thread restores a pending approval with calibrated attempt', async () => {
  global.fetch = async () => ({ ok: false, json: async () => ({}) });
  const f = fixture();
  await f.useChatStore.getState().openThread('t1');
  const approval = f.useChatStore.getState().websearchApproval;
  assert.equal(approval.query, '最新空气质量政策');
  assert.equal(approval.reason, '政策公告');
  assert.equal(approval.remaining, 3);
  assert.equal(approval.runId, 'r1');
  assert.equal(approval.attemptId, 'a1');
  assert.equal(approval.threadId, 't1');
});

test('approval resumes with operation_kind=resume and source attempt', async () => {
  global.fetch = async () => ({ ok: false, json: async () => ({}) });
  const f = fixture();
  await f.useChatStore.getState().openThread('t1');
  const running = f.useChatStore.getState().resolveWebSearchApproval(true);
  await tick();
  assert.deepEqual(f.streams[0].command, { resume: { approved: true } });
  assert.equal(f.streams[0].messages, undefined);
  assert.deepEqual(f.getSubmissionContext(), { submissionKey: 'sub-key', operationKind: 'resume', sourceAttemptId: 'a1' });
  assert.equal(f.useChatStore.getState().websearchApproval, null);
  assert.equal(f.useChatStore.getState().isStreaming, true);
  f.streams[0].wait.resolve();
  await running;
  assert.equal(f.useChatStore.getState().isStreaming, false);
});

test('approval denial sends resume false and never adds a user message', async () => {
  global.fetch = async () => ({ ok: false, json: async () => ({}) });
  const f = fixture();
  await f.useChatStore.getState().openThread('t1');
  const running = f.useChatStore.getState().resolveWebSearchApproval(false);
  await tick();
  assert.deepEqual(f.streams[0].command, { resume: { approved: false } });
  assert.deepEqual(f.useChatStore.getState().messages, []);
  f.streams[0].wait.resolve();
  await running;
});

test('stop while waiting for approval denies instead of cancelling an active run', async () => {
  global.fetch = async () => ({ ok: false, json: async () => ({}) });
  const f = fixture();
  await f.useChatStore.getState().openThread('t1');
  const stopping = f.useChatStore.getState().stopStreaming();
  await tick();
  assert.deepEqual(f.streams[0].command, { resume: { approved: false } });
  f.streams[0].wait.resolve();
  const outcome = await stopping;
  assert.deepEqual(outcome, { confirmed: true });
});

test('approval without calibrated attempt refuses to resume', async () => {
  global.fetch = async () => ({ ok: false, json: async () => ({}) });
  const f = fixture({ calibration: { confirmed: false } });
  await f.useChatStore.getState().openThread('t1');
  assert.equal(f.useChatStore.getState().websearchApproval.attemptId, null);
  await f.useChatStore.getState().resolveWebSearchApproval(true);
  assert.equal(f.streams.length, 0);
  assert.match(f.useChatStore.getState().error, /审批状态尚未就绪/);
});

test('interrupt BFF only calibrates a platform interrupted run to waiting_input', () => {
  const source = fs.readFileSync(path.join(frontend, 'app/api/agent-runs/interrupt/route.ts'), 'utf8');
  assert.match(source, /hasPendingApproval/);
  assert.match(source, /websearch_gate/);
  assert.match(source, /websearch_approval/);
  assert.match(source, /state: "waiting_input"/);
  assert.match(source, /evidence_source: "platform_read"/);
  assert.match(source, /platform_status: "interrupted"/);
  assert.match(source, /INTERNAL_API_TOKEN/);
  assert.match(source, /ledger_attempt_id/);
  assert.ok(!source.includes('state: "running"'));
});

test('interrupt BFF calibrates an interrupted run and returns the source attempt', async () => {
  class NextResponse extends Response {
    static json(value, init) { return new NextResponse(JSON.stringify(value), { ...init, headers: { 'content-type': 'application/json' } }); }
  }
  const adminCalls = [];
  const ctx = {
    user: { id: 'u1', account: 'tester' },
    current_tenant: { id: 'tenant-a', status: 'active' },
    membership: { status: 'active' },
    context_version: 3,
  };
  const route = load('app/api/agent-runs/interrupt/route.ts', {
    'next/server': { NextResponse },
    '@/lib/auth': { getSessionContext: async () => ctx },
    '@/lib/admin-api': {
      runtimeTicketViaAdmin: async () => ({ ticket: 'ticket-1' }),
      adminFetch: async (path, init) => {
        adminCalls.push({ path, init });
        if (path.startsWith('/agent/feedback/attempts/resolve')) {
          return { status: 200, json: { attempt_id: '22222222-2222-2222-2222-222222222222' } };
        }
        return { status: 200, json: { state: 'waiting_input' } };
      },
    },
  });
  const previousFetch = global.fetch;
  global.fetch = async (url) => {
    if (String(url).endsWith('/state')) {
      return Response.json({
        next: ['websearch_gate'],
        tasks: [{ interrupts: [{ value: { type: 'websearch_approval' } }] }],
      });
    }
    return Response.json({
      run_id: 'run-1',
      status: 'success',
      metadata: { ledger_attempt_id: '11111111-1111-1111-1111-111111111111' },
    });
  };
  const previousInternal = process.env.INTERNAL_API_TOKEN;
  const previousBase = process.env.LANGGRAPH_API_URL;
  process.env.INTERNAL_API_TOKEN = 'internal-token';
  process.env.LANGGRAPH_API_URL = 'http://test-api';
  try {
    const req = {
      headers: new Headers({ 'content-type': 'application/json', cookie: 'air_agent_session=x' }),
      json: async () => ({ thread_id: 'thread-1', run_id: 'run-1' }),
    };
    const response = await route.POST(req);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      confirmed: true,
      attempt_id: '22222222-2222-2222-2222-222222222222',
    });
    const observed = adminCalls.find((call) => call.path === '/agent/runs/22222222-2222-2222-2222-222222222222/state');
    assert.ok(observed);
    assert.equal(observed.init.internalToken, 'internal-token');
    assert.equal(observed.init.body.state, 'waiting_input');
    assert.equal(observed.init.body.platform_status, 'interrupted');
  } finally {
    global.fetch = previousFetch;
    if (previousInternal === undefined) delete process.env.INTERNAL_API_TOKEN;
    else process.env.INTERNAL_API_TOKEN = previousInternal;
    if (previousBase === undefined) delete process.env.LANGGRAPH_API_URL;
    else process.env.LANGGRAPH_API_URL = previousBase;
  }
});

test('interrupt BFF refuses to calibrate a non-interrupted run', async () => {
  class NextResponse extends Response {
    static json(value, init) { return new NextResponse(JSON.stringify(value), { ...init, headers: { 'content-type': 'application/json' } }); }
  }
  const adminCalls = [];
  const route = load('app/api/agent-runs/interrupt/route.ts', {
    'next/server': { NextResponse },
    '@/lib/auth': { getSessionContext: async () => ({
      user: { id: 'u1', account: 'tester' },
      current_tenant: { id: 'tenant-a', status: 'active' },
      membership: { status: 'active' },
      context_version: 3,
    }) },
    '@/lib/admin-api': {
      runtimeTicketViaAdmin: async () => ({ ticket: 'ticket-1' }),
      adminFetch: async (path, init) => { adminCalls.push({ path, init }); return { status: 200, json: {} }; },
    },
  });
  const previousFetch = global.fetch;
  const previousBase = process.env.LANGGRAPH_API_URL;
  process.env.LANGGRAPH_API_URL = 'http://test-api';
  global.fetch = async () => Response.json({ run_id: 'run-1', status: 'success', metadata: {} });
  try {
    const req = {
      headers: new Headers({ 'content-type': 'application/json', cookie: 'air_agent_session=x' }),
      json: async () => ({ thread_id: 'thread-1', run_id: 'run-1' }),
    };
    const response = await route.POST(req);
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { confirmed: false, reason: 'no_pending_approval' });
    assert.equal(adminCalls.length, 0);
  } finally {
    global.fetch = previousFetch;
    if (previousBase === undefined) delete process.env.LANGGRAPH_API_URL;
    else process.env.LANGGRAPH_API_URL = previousBase;
  }
});
