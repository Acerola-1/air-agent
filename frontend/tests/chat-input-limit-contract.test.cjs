const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const frontend = path.resolve(__dirname, '..');
const swc = require(path.join(frontend, 'node_modules/next/dist/build/swc/index.js'));

function load(file, imports, extraSource = '') {
  const transformed = swc.transformSync(fs.readFileSync(path.join(frontend, file), 'utf8') + extraSource, {
    filename: file,
    jsc: { parser: { syntax: 'typescript', tsx: file.endsWith('.tsx') }, target: 'es2022', transform: { react: { runtime: 'automatic' } } },
    module: { type: 'commonjs' },
  });
  const module = { exports: {} };
  const localRequire = (name) => {
    if (name in imports) return imports[name];
    throw new Error(`Unmocked dependency ${name}`);
  };
  new Function('require', 'module', 'exports', transformed.code)(localRequire, module, module.exports);
  return module.exports;
}

test('chat config client uses tenant context and server limit', async () => {
  const originalFetch = global.fetch;
  let captured = null;
  global.fetch = async (url, init) => {
    captured = { url: String(url), init };
    return {
      ok: true,
      status: 200,
      json: async () => ({ graph_id: 'basic-qa', max_input_chars: 8000, character_count_mode: 'unicode_code_point' }),
    };
  };
  try {
    const mod = load('lib/chat-config.ts', {
      '@/lib/langgraph-api': { getTenantRequestContext: () => ({ tenantId: 'tenant-a', contextVersion: 3 }) },
    });
    const config = await mod.fetchChatConfig('basic-qa');
    assert.equal(config.max_input_chars, 8000);
    assert.equal(config.character_count_mode, 'unicode_code_point');
    assert.match(captured.url, /\/api\/chat\/config\?graph_id=basic-qa/);
    assert.equal(captured.init.cache, 'no-store');
    assert.equal(captured.init.headers['x-tenant-id'], 'tenant-a');
    assert.equal(captured.init.headers['x-tenant-context-version'], '3');
    assert.equal(mod.countUnicodeCodePoints('👨‍👩‍👧‍👦'), 7);
    assert.equal(mod.countUnicodeCodePoints('中文abc'), 5);
  } finally {
    global.fetch = originalFetch;
  }
});

function composerHarness() {
  let cursor = 0;
  const values = [], dependencies = [], cleanups = [], effects = [], requests = [], sent = [];
  const session = {
    username: 'account', sessionEpoch: 1,
    ctx: { user: { id: 'u' }, current_tenant: { id: 'A' }, context_version: 1 },
  };
  const hooks = {
    useState(initial) {
      const i = cursor++;
      if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
      return [values[i], next => { values[i] = typeof next === 'function' ? next(values[i]) : next; }];
    },
    useRef(initial) {
      const i = cursor++;
      if (!(i in values)) values[i] = { current: initial };
      return values[i];
    },
    useCallback(fn) { return fn; },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!dependencies[i] || deps.some((value, j) => !Object.is(value, dependencies[i][j]))) {
        dependencies[i] = [...deps];
        effects.push(() => { cleanups[i]?.(); cleanups[i] = fn(); });
      }
    },
  };
  const jsx = (type, props) => ({ type, props });
  const noop = () => null;
  const imports = new Proxy({
    react: hooks,
    'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
    '@/lib/chat-store': { useChatStore: selector => selector({ graphId: 'basic-qa' }) },
    '@/lib/session-client': { useSessionStore: selector => selector(session) },
    '@/lib/utils': { cn: () => '' },
    '@/lib/chat-config': {
      countUnicodeCodePoints: text => Array.from(text).length,
      fetchChatConfig: (graphId, signal) => new Promise((resolve, reject) => {
        requests.push({ graphId, tenant: session.ctx.current_tenant.id, signal, resolve, reject });
      }),
    },
  }, { has: () => true, get: (target, key) => target[key] ?? new Proxy({}, { get: () => noop }) });
  const { TestComposer } = load('components/chat/ChatPanel.tsx', imports, '\nexport { Composer as TestComposer };');
  const render = () => {
    cursor = 0;
    const tree = TestComposer({ disabled: false, onSend: text => sent.push(text), onStop() {} });
    while (effects.length) effects.shift()();
    return tree;
  };
  const find = (node, type) => {
    if (!node || typeof node !== 'object') return null;
    if (node.type === type) return node;
    const children = Array.isArray(node) ? node : [node.props?.children];
    for (const child of children) {
      const result = find(child, type);
      if (result) return result;
    }
    return null;
  };
  const enter = text => {
    find(render(), 'textarea').props.onChange({ target: { value: text, style: {}, scrollHeight: 20 } });
  };
  const submit = () => render().props.onSubmit({ preventDefault() {} });
  const resolve = async (index, maxChars = 10) => {
    requests[index].resolve({ graph_id: 'basic-qa', max_input_chars: maxChars, character_count_mode: 'unicode_code_point' });
    await Promise.resolve();
  };
  return { session, requests, sent, render, enter, submit, resolve, find };
}

test('composer preserves unknown/oversized drafts and accepts an input at the code point limit', async () => {
  const h = composerHarness();
  h.enter('👋中');
  h.submit();
  assert.equal(h.sent.length, 0);
  await h.resolve(0, 2);
  h.submit();
  assert.deepEqual(h.sent, ['👋中']);
  h.enter('👋中文');
  h.submit();
  assert.equal(h.sent.length, 1);
  assert.equal(h.find(h.render(), 'textarea').props.value, '👋中文');
});

test('composer discards pending config across A to B to A and session epoch changes', async () => {
  const h = composerHarness();
  h.enter('draft');
  const switchScope = tenant => {
    h.session.ctx = { ...h.session.ctx, current_tenant: { id: tenant }, context_version: h.session.ctx.context_version + 1 };
    h.session.sessionEpoch++;
    h.render();
  };
  switchScope('B');
  switchScope('A');
  await h.resolve(0, 100);
  await h.resolve(1, 100);
  h.submit();
  assert.equal(h.sent.length, 0);
  assert.deepEqual(h.requests.map(request => request.tenant), ['A', 'B', 'A']);
  assert.ok(h.requests[0].signal.aborted && h.requests[1].signal.aborted);
  await h.resolve(2, 10);
  h.submit();
  assert.deepEqual(h.sent, ['draft']);
  h.enter('new');
  h.session.sessionEpoch++;
  h.submit();
  assert.equal(h.sent.length, 1, 'same identity with a new session epoch must reload config');
});

test('composer input method confirmation does not submit', async () => {
  const h = composerHarness();
  h.enter('中文');
  await h.resolve(0);
  const input = h.find(h.render(), 'textarea');
  input.props.onCompositionStart();
  input.props.onKeyDown({ key: 'Enter', nativeEvent: {}, preventDefault() {} });
  assert.equal(h.sent.length, 0);
  input.props.onCompositionEnd();
  input.props.onKeyDown({ key: 'Enter', nativeEvent: {}, preventDefault() {} });
  assert.deepEqual(h.sent, ['中文']);
});

test('bff config route is no-store and forwards graph id', () => {
  const route = fs.readFileSync(path.join(frontend, 'app/api/chat/config/route.ts'), 'utf8');
  assert.match(route, /\/chat\/config/);
  assert.match(route, /Cache-Control.*no-store/);
  assert.match(route, /x-tenant-id/);
  assert.match(route, /x-tenant-context-version/);
});

test('context meter distinguishes window, trigger and effective input', () => {
  const source = fs.readFileSync(path.join(frontend, 'components/chat/ContextMeter.tsx'), 'utf8');
  assert.match(source, /summary_trigger_tokens/);
  assert.match(source, /模型窗口/);
  assert.match(source, /可用输入容量/);
  assert.match(source, /历史整理水位/);
});
