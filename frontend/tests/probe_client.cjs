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

  // 1. R02: toPayload() 不在 JSON.stringify 时序列化 selection_source (避免 extra="forbid" 拒绝)
  store.setSkillMode('explicit', 'skill:basic-qa:air-quality-basic-query');
  store.setMcpMode('allowlist', ['mcp:business:query']);
  useCapabilityStore.setState({ catalog: { catalog_version: 'v1' } });

  const payload = store.toPayload();
  const serialized = JSON.stringify(payload);
  const parsed = JSON.parse(serialized);

  const r02_results = {
    payload_selection_source_property: payload.selection_source,
    serialized_has_selection_source: 'selection_source' in parsed,
    serialized_keys: Object.keys(parsed),
    skill_mode: parsed.skill.mode,
    skill_id: parsed.skill.skill_id,
    mcp_mode: parsed.mcp.mode,
    tool_ids: parsed.mcp.tool_ids,
  };

  // 2. R08: 租户切换重置草稿与快照
  store.setAcceptedSelection({
    id: 'sel_123',
    skill_mode: 'explicit',
    skill_id: 'skill:basic-qa:air-quality-basic-query',
    mcp_mode: 'allowlist',
    tool_ids: ['mcp:business:query'],
    selection_digest: 'digest_123',
    selection_source: 'user_explicit',
  });

  store.clearForScopeSwitch('basic-qa', 'new-tenant-id');
  const clearedState = useCapabilityStore.getState();

  const r08_results = {
    draft_skill_mode: clearedState.selection.skillMode,
    draft_skill_id: clearedState.selection.selectedSkillId,
    draft_mcp_mode: clearedState.selection.mcpMode,
    draft_tool_ids: clearedState.selection.selectedToolIds,
    accepted_selection: clearedState.acceptedSelection,
  };

  const allPassed =
    r02_results.serialized_has_selection_source === false &&
    r02_results.payload_selection_source_property === 'user_explicit' &&
    r08_results.draft_skill_mode === 'auto' &&
    r08_results.draft_mcp_mode === 'auto' &&
    r08_results.draft_tool_ids.length === 0 &&
    r08_results.accepted_selection === null;

  const output = {
    r02_payload_strictness: r02_results,
    r08_scope_switch_isolation: r08_results,
    all_passed: allPassed,
  };

  console.log(JSON.stringify(output, null, 2));
}

runClientProbe();
