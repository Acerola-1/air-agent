/**
 * 独立复测探针：前端 Store、载荷严格性与作用域切换隔离（R02 / R08）
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const swc = require(path.join(root, 'node_modules/next/dist/build/swc/index.js'));
const zustand = require(path.join(root, 'node_modules/zustand'));

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
  Function('module', 'exports', 'require', result.code)(module, module.exports, localRequire);
  return module.exports.useCapabilityStore;
}

function runClientProbe() {
  const useCapabilityStore = loadStore();
  const store = useCapabilityStore.getState();

  // 1. 当前协议只保留 MCP 选择；载荷不附带 Skill 或来源推断字段。
  store.setMcpMode('allowlist', ['mcp:business:query']);
  useCapabilityStore.setState({ catalog: { catalog_version: 'v1' } });

  const payload = store.toPayload();
  const serialized = JSON.stringify(payload);
  const parsed = JSON.parse(serialized);

  const protocol_results = {
    schema_version: parsed.schema_version,
    serialized_keys: Object.keys(parsed),
    mcp_mode: parsed.mcp.mode,
    tool_ids: parsed.mcp.tool_ids,
  };

  // 2. R08: 租户切换重置草稿与快照
  store.setAcceptedSelection({
    selection_id: 'sel_123',
    selection_digest: 'a'.repeat(64),
    digest_version: 3,
    selection_status: 'collected',
    schema_version: 'agent.capability-selection.v2',
    mcp: { mode: 'allowlist', tool_ids: ['mcp:business:query'] },
    catalog_version: 'v1',
    selection_source: 'user',
    validation_time: '2026-10-30T00:00:00Z',
  });

  store.clearForScopeSwitch('basic-qa', 'new-tenant-id');
  const clearedState = useCapabilityStore.getState();

  const r08_results = {
    draft_mcp_mode: clearedState.selection.mcpMode,
    draft_tool_ids: clearedState.selection.selectedToolIds,
    accepted_selection: clearedState.acceptedSelection,
  };

  const allPassed =
    protocol_results.schema_version === 'agent.capability-selection.v2' &&
    protocol_results.serialized_keys.sort().join(',') === 'catalog_version,mcp,schema_version' &&
    protocol_results.mcp_mode === 'allowlist' &&
    protocol_results.tool_ids.length === 1 &&
    r08_results.draft_mcp_mode === 'auto' &&
    r08_results.draft_tool_ids.length === 0 &&
    r08_results.accepted_selection === null;

  const output = {
    capability_selection_v2: protocol_results,
    r08_scope_switch_isolation: r08_results,
    all_passed: allPassed,
  };

  console.log(JSON.stringify(output, null, 2));
}

runClientProbe();
