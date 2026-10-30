/** 事件台账 v1 契约测试（TS 侧，Node 运行）。
 *
 * 与 `tests/unit_tests/test_event_ledger_contract.py` 共用同一份
 * `docs/contracts/agent-events-v1/samples/samples.json`——任一侧规则漂移都会失败。
 * 同时断言前端嵌入副本 `lib/agent-events/contract-data.ts` 与事实源一致。
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const frontend = path.resolve(__dirname, '..');
const repo = path.resolve(frontend, '..');
const contracts = path.join(repo, 'docs/contracts/agent-events-v1');
const swc = require(path.join(frontend, 'node_modules/next/dist/build/swc/index.js'));

function loadTs(file) {
  const transformed = swc.transformSync(fs.readFileSync(path.join(frontend, file), 'utf8'), {
    filename: file,
    jsc: { parser: { syntax: 'typescript' }, target: 'es2022' },
    module: { type: 'commonjs' },
  });
  const module = { exports: {} };
  new Function('require', 'module', 'exports', transformed.code)(() => {
    throw new Error('contract.ts must not have runtime imports');
  }, module, module.exports);
  return module.exports;
}

const canonicalRegistry = JSON.parse(
  fs.readFileSync(path.join(contracts, 'event-registry.json'), 'utf8'),
);
const canonicalStates = JSON.parse(fs.readFileSync(path.join(contracts, 'states.json'), 'utf8'));
const samples = JSON.parse(fs.readFileSync(path.join(contracts, 'samples/samples.json'), 'utf8'));

const { validateEvent, validateBatch, enabledEventTypes, reservedEventTypes } = loadTs(
  'lib/agent-events/contract.ts',
);
const embedded = loadTs('lib/agent-events/contract-data.ts');

test('embedded frontend copy matches canonical contract files', () => {
  assert.deepEqual(embedded.EVENT_REGISTRY, canonicalRegistry, 'EVENT_REGISTRY 漂移');
  assert.deepEqual(embedded.RUN_STATES, canonicalStates, 'RUN_STATES 漂移');
});

test('every sample case matches the expected verdict (same set as Python)', () => {
  const declared = new Set(Object.keys(samples.reason_codes));
  const failures = [];
  for (const testCase of samples.cases) {
    assert.ok(declared.has(testCase.expect.reason), `undeclared reason ${testCase.name}`);
    const got = validateEvent(testCase.event, canonicalRegistry, canonicalStates);
    const expected = testCase.expect;
    if (got.valid !== expected.valid || (!got.valid && got.reason !== expected.reason)) {
      failures.push(`${testCase.name}: expected=${JSON.stringify(expected)} got=${JSON.stringify(got)}`);
    }
  }
  assert.equal(failures.length, 0, failures.join('\n'));
});

test('browser cannot claim server terminal facts', () => {
  const testCase = samples.cases.find((c) => c.name === 'browser-terminal-claim-invalid');
  const got = validateEvent(testCase.event, canonicalRegistry, canonicalStates);
  assert.equal(got.reason, 'source_not_allowed');
});

test('reserved types are registered but rejected as not enabled', () => {
  // change-3 启用 browser.feedback/copy/timing 后，预留集合精确为 runtime 五类；
  // 非法来源（browser 声明 runtime 事实）仍被拒绝。
  const reserved = reservedEventTypes(canonicalRegistry);
  assert.deepEqual([...reserved].sort(), []);
});

test('enabled types equal core plus browser and runtime', () => {
  assert.deepEqual(enabledEventTypes(canonicalRegistry).sort(), [
    'answer.confirmed',
    'answer.partial',
    'browser.copy',
    'browser.feedback',
    'browser.timing',
    'capability.selection_accepted',
    'request.accepted',
    'resource.deleted',
    'run.admission_confirmed',
    'run.cancel_requested',
    'run.resumed',
    'run.state_observed',
    'runtime.capability',
    'runtime.cost',
    'runtime.model_usage',
    'runtime.span',
    'runtime.tool_call',
    'runtime.ttft',
    'trace.linked',
  ]);
});

test('batch validation returns per-item results and enforces browser limits', () => {
  const good = samples.cases.find((c) => c.expect.valid).event;
  const bad = samples.cases.find((c) => !c.expect.valid).event;
  const mixed = validateBatch([good, bad, good], 'runtime', canonicalRegistry, canonicalStates);
  assert.equal(mixed.accepted, false);
  assert.deepEqual(mixed.items.map((i) => i.valid), [true, false, true]);
  const many = new Array(canonicalRegistry.envelope.limits.browser_batch_events + 1).fill(good);
  const oversize = validateBatch(many, 'browser', canonicalRegistry, canonicalStates);
  assert.equal(oversize.accepted, false);
  assert.match(oversize.batch_reason, /^batch_too_many/);
});

test('forbidden payload key wins over whitelist', () => {
  const base = samples.cases.find((c) => c.name === 'request-accepted-valid').event;
  const withTicket = {
    ...base,
    payload: { ...base.payload, runtime_ticket: 'x' },
  };
  assert.equal(validateEvent(withTicket, canonicalRegistry, canonicalStates).reason, 'forbidden_payload_key');
});
