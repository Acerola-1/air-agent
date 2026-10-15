const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const swc = require(path.join(root, 'node_modules/next/dist/build/swc/index.js'));
const zustand = require(path.join(root, 'node_modules/zustand'));
const tick = () => new Promise(setImmediate);
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const defer = () => { let resolve; const promise = new Promise((r) => (resolve = r)); return { promise, resolve }; };

function loadStore() {
  const result = swc.transformSync(
    fs.readFileSync(path.join(root, 'lib/capability-store.ts'), 'utf8'),
    {
      filename: 'capability-store.ts',
      jsc: { parser: { syntax: 'typescript' }, target: 'es2022' },
      module: { type: 'commonjs' },
    }
  );
  const module = { exports: {} };
  const localRequire = (name) => {
    if (name === 'zustand') return zustand;
    throw new Error(`Unmocked dependency ${name}`);
  };
  new Function('require', 'module', 'exports', result.code)(localRequire, module, module.exports);
  return module.exports.useCapabilityStore;
}

test('默认草稿为自动模式，toPayload 输出规范标准 agent.capability-selection.v1', () => {
  const useStore = loadStore();
  const state = useStore.getState();
  assert.equal(state.selection.skillMode, 'auto');
  assert.equal(state.selection.selectedSkillId, null);
  assert.equal(state.selection.mcpMode, 'auto');
  assert.deepEqual(state.selection.selectedToolIds, []);

  const payload = state.toPayload();
  assert.equal(payload.schema_version, 'agent.capability-selection.v1');
  assert.equal(payload.selection_source, 'automatic');
  assert.deepEqual(payload.skill, { mode: 'auto', skill_id: null });
  assert.deepEqual(payload.mcp, { mode: 'auto', tool_ids: [] });
  assert.equal(payload.catalog_version, null);
});

test('用户显式选择 Skill 与 MCP 工具列表，toPayload 标注 user_explicit 并按字母序去重', () => {
  const useStore = loadStore();
  useStore.setState({
    catalog: {
      catalog_version: 'cat-20261015-v1',
      tenant_id: 'tenant-1',
      graph_id: 'basic-qa',
      skills: [
        { id: 'air-quality-basic-query', name: 'air-quality-basic-query', title: '基础查询', description: '', material_version: 'm1' },
      ],
      mcp_servers: [],
      mcp_tools: [],
      generated_at: new Date().toISOString(),
    },
  });

  useStore.getState().setSkillMode('explicit', 'air-quality-basic-query');
  useStore.getState().setMcpMode('allowlist', ['tool_z', 'tool_a', 'tool_z']);

  const payload = useStore.getState().toPayload();
  assert.equal(payload.selection_source, 'user_explicit');
  assert.equal(payload.catalog_version, 'cat-20261015-v1');
  assert.deepEqual(payload.skill, { mode: 'explicit', skill_id: 'air-quality-basic-query' });
  assert.equal(payload.mcp.mode, 'allowlist');
  assert.deepEqual(payload.mcp.tool_ids, ['tool_a', 'tool_z']); // 规范排序与去重
});

test('选择 MCP allowlist 空集代表禁用 MCP，toggleTool 可添加与移除工具', () => {
  const useStore = loadStore();
  useStore.getState().setMcpMode('allowlist', []);
  let payload = useStore.getState().toPayload();
  assert.equal(payload.selection_source, 'user_explicit');
  assert.deepEqual(payload.mcp, { mode: 'allowlist', tool_ids: [] });

  useStore.getState().toggleTool('srv:tool1');
  assert.deepEqual(useStore.getState().selection.selectedToolIds, ['srv:tool1']);
  assert.equal(useStore.getState().selection.mcpMode, 'allowlist');

  useStore.getState().toggleTool('srv:tool1');
  assert.deepEqual(useStore.getState().selection.selectedToolIds, []);
  assert.equal(useStore.getState().selection.mcpMode, 'allowlist'); // 移除后仍保留为已选模式空集（禁用）
});

test('切换图或租户时，旧草稿被清空重置为默认，且目录异步加载有 loading/error 状态', async (t) => {
  const originalFetch = global.fetch;
  t.after(() => { global.fetch = originalFetch; });

  const useStore = loadStore();
  useStore.getState().setSkillMode('explicit', 'some-skill');
  assert.equal(useStore.getState().selection.skillMode, 'explicit');

  useStore.getState().clearForScopeSwitch('data-analysis', 'tenant-b');
  assert.equal(useStore.getState().selection.skillMode, 'auto');
  assert.equal(useStore.getState().selection.selectedSkillId, null);
  assert.equal(useStore.getState().catalog, null);

  // 模拟目录加载
  let requestedUrl = '';
  global.fetch = async (url) => {
    requestedUrl = String(url);
    return ok({
      catalog_version: 'cat-da-1',
      tenant_id: 'tenant-b',
      graph_id: 'data-analysis',
      skills: [],
      mcp_servers: [],
      mcp_tools: [],
      generated_at: new Date().toISOString(),
    });
  };

  await useStore.getState().fetchCatalog('data-analysis', 'tenant-b');
  assert.ok(requestedUrl.includes('graph_id=data-analysis'));
  assert.ok(requestedUrl.includes('tenant_id=tenant-b'));
  assert.equal(useStore.getState().catalog?.catalog_version, 'cat-da-1');
  assert.equal(useStore.getState().catalogLoading, false);
  assert.equal(useStore.getState().catalogError, null);
});

test('Epoch 隔离：迟到的旧请求（如 A→B→A 切换时旧请求晚到）被丢弃，不覆盖当前目录', async (t) => {
  const originalFetch = global.fetch;
  t.after(() => { global.fetch = originalFetch; });

  const oldReq = defer();
  let callCount = 0;
  global.fetch = async (url) => {
    callCount++;
    if (callCount === 1) {
      return oldReq.promise;
    }
    return ok({
      catalog_version: 'cat-new-2',
      tenant_id: 'tenant-current',
      graph_id: 'basic-qa',
      skills: [{ id: 'new-skill', name: 'new-skill', title: '最新技能', description: '', material_version: 'v2' }],
      mcp_servers: [],
      mcp_tools: [],
      generated_at: new Date().toISOString(),
    });
  };

  const useStore = loadStore();
  // 发起旧请求 1
  const p1 = useStore.getState().fetchCatalog('old-graph', 'tenant-old');
  // 切换并完成新请求 2
  const p2 = useStore.getState().fetchCatalog('basic-qa', 'tenant-current');
  await p2;
  assert.equal(useStore.getState().catalog?.catalog_version, 'cat-new-2');

  // 旧请求 1 现在才返回
  oldReq.resolve(ok({
    catalog_version: 'cat-old-delayed',
    tenant_id: 'tenant-old',
    graph_id: 'old-graph',
    skills: [{ id: 'old-skill', name: 'old-skill', title: '旧技能', description: '', material_version: 'v1' }],
    mcp_servers: [],
    mcp_tools: [],
    generated_at: new Date().toISOString(),
  }));
  await p1;

  // 验证当前状态未被旧迟到响应覆盖
  assert.equal(useStore.getState().catalog?.catalog_version, 'cat-new-2');
  assert.equal(useStore.getState().catalog?.skills[0].id, 'new-skill');
});

test('目录错误正文迟到时也不能覆盖换范围后的新状态', async (t) => {
  const previous = global.fetch; t.after(() => { global.fetch = previous; });
  const useStore = loadStore(); const body = defer();
  global.fetch = async () => ({ ok: false, status: 503, json: () => body.promise });
  const work = useStore.getState().fetchCatalog('basic-qa', 'tenant-a'); await tick();
  useStore.getState().clearForScopeSwitch('data-analysis', 'tenant-b');
  body.resolve({ error: '旧目录失败' }); await work;
  assert.equal(useStore.getState().catalogError, null);
});

test('目录分页完整合并，版本变化时拒绝拼接', async (t) => {
  const previous = global.fetch; t.after(() => { global.fetch = previous; });
  const useStore = loadStore(); let calls = 0;
  const first = () => ({ graph_id: 'basic-qa', catalog_version: 'cat-current', skills: [{ id: 's1' }], tools: [], next_cursor: 'page-2' });
  global.fetch = async () => ok(++calls === 1 ? first() : { graph_id: 'basic-qa', catalog_version: 'cat-current', skills: [], tools: [{ id: 't1' }], next_cursor: null });
  await useStore.getState().fetchCatalog('basic-qa', 'tenant-a');
  assert.deepEqual(useStore.getState().catalog.tools, [{ id: 't1' }]); assert.equal(calls, 2);
  calls = 0;
  global.fetch = async () => ok(++calls === 1 ? first() : { graph_id: 'basic-qa', catalog_version: 'cat-new', skills: [], tools: [], next_cursor: null });
  await useStore.getState().fetchCatalog('basic-qa', 'tenant-a');
  assert.match(useStore.getState().catalogError, /目录已变化/);
  assert.equal(useStore.getState().catalog.catalog_version, 'cat-current');
});
