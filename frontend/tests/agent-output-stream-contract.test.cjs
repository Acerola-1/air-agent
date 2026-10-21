/**
 * agent-output-stream-v1 client contract tests.
 *
 * Verifies identity scoping, monotonic sequence handling, duplicate/replay
 * filtering, gap/conflict resync, DeepAgents section replay dedupe and legacy
 * done-only compatibility without loading a browser.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const swc = require(path.join(root, "node_modules/next/dist/build/swc/index.js"));

function loadModule() {
  const result = swc.transformSync(
    fs.readFileSync(path.join(root, "lib/agent-output-stream.ts"), "utf8"),
    {
      filename: "agent-output-stream.ts",
      jsc: { parser: { syntax: "typescript" }, target: "es2022" },
      module: { type: "commonjs" },
    }
  );
  const module = { exports: {} };
  Function("module", "exports", "require", result.code)(module, module.exports, () => {
    throw new Error("unexpected dependency");
  });
  return module.exports;
}

const { AgentOutputStream, isFinalOutputEvent } = loadModule();

function start(seq = 0, extra = {}) {
  return {
    type: "final_output_start",
    output_protocol_version: 1,
    thread_id: "t1",
    run_id: "r1",
    graph_id: "basic-qa",
    tenant_id: "tenant-1",
    output_id: "out_1",
    message_id: "msg_1",
    seq,
    delivery_mode: "token",
    ...extra,
  };
}
function delta(seq, message, extra = {}) {
  return {
    type: "final_output_delta",
    output_protocol_version: 1,
    thread_id: "t1",
    run_id: "r1",
    graph_id: "basic-qa",
    tenant_id: "tenant-1",
    output_id: "out_1",
    message_id: "msg_1",
    seq,
    message,
    ...extra,
  };
}
function done(seq, message, extra = {}) {
  return {
    type: "final_output_done",
    output_protocol_version: 1,
    thread_id: "t1",
    run_id: "r1",
    graph_id: "basic-qa",
    tenant_id: "tenant-1",
    output_id: "out_1",
    message_id: "msg_1",
    seq,
    message,
    ...extra,
  };
}

const tests = [];
function test(name, fn) {
  tests.push([name, fn]);
}

test("lifecycle appends contiguous deltas and merges done into one message", () => {
  const stream = new AgentOutputStream({ threadId: "t1", runId: "r1", graphId: "basic-qa" });
  assert.equal(stream.apply(start()).action, "start");
  assert.equal(stream.apply(delta(1, "第一段。")).action, "append");
  assert.equal(stream.apply(delta(2, "第二段。")).action, "append");
  const result = stream.apply(done(3, "第一段。第二段。"));
  assert.equal(result.action, "done");
  const snapshot = stream.snapshot();
  assert.equal(snapshot.text, "第一段。第二段。");
  assert.equal(snapshot.messageId, "msg_1");
  assert.equal(snapshot.status, "done");
  assert.equal(snapshot.firstDeltaSource, "new_delta");
});

test("duplicate frame is ignored, conflicting replay and gaps require resync", () => {
  const stream = new AgentOutputStream({ threadId: "t1", runId: "r1", graphId: "basic-qa" });
  stream.apply(start());
  assert.equal(stream.apply(delta(1, "A")).action, "append");
  assert.equal(stream.apply(delta(1, "A")).action, "ignore");
  assert.equal(stream.apply(delta(1, "B")).action, "resync");
  assert.equal(stream.apply(delta(3, "C")).action, "resync");
});

test("other thread/run/graph frames never pollute the active answer", () => {
  const stream = new AgentOutputStream({ threadId: "t1", runId: "r1", graphId: "basic-qa" });
  stream.apply(start());
  assert.equal(stream.apply(delta(1, "X", { thread_id: "t2" })).action, "ignore");
  assert.equal(stream.apply(delta(1, "X", { run_id: "r2" })).action, "ignore");
  assert.equal(stream.snapshot().text, "");
});

test("A to B to A does not revive the old output", () => {
  const stream = new AgentOutputStream({ threadId: "t1", runId: "r1", graphId: "basic-qa" });
  stream.apply(start());
  stream.apply(delta(1, "旧正文。"));
  stream.apply(done(2, "旧正文。"));
  assert.equal(stream.apply(delta(3, "迟到片段")).action, "ignore");
  const other = new AgentOutputStream({ threadId: "t2", runId: "r2", graphId: "basic-qa" });
  assert.equal(other.apply(delta(1, "另一运行", { output_id: "out_2" })).action, "ignore");
});

test("section replay is deduped but a conflicting section is a resync", () => {
  const stream = new AgentOutputStream({ threadId: "t1", runId: "r1", graphId: "intelligent-tracing" });
  stream.apply(start(0, { delivery_mode: "validated_section", graph_id: "intelligent-tracing" }));
  assert.equal(stream.apply(delta(1, "第一节。", { graph_id: "intelligent-tracing", section_id: "header", plan_digest: "p1" })).action, "append");
  assert.equal(stream.apply(delta(2, "第一节。", { graph_id: "intelligent-tracing", section_id: "header", plan_digest: "p1", replay: true })).action, "ignore");
  assert.equal(stream.apply(delta(3, "篡改。", { graph_id: "intelligent-tracing", section_id: "header", plan_digest: "p1" })).action, "resync");
});

test("aborted keeps partial text and never becomes done", () => {
  const stream = new AgentOutputStream({ threadId: "t1", runId: "r1", graphId: "basic-qa" });
  stream.apply(start());
  stream.apply(delta(1, "部分。"));
  const result = stream.apply({
    type: "final_output_aborted",
    output_protocol_version: 1,
    thread_id: "t1",
    run_id: "r1",
    graph_id: "basic-qa",
    tenant_id: "tenant-1",
    output_id: "out_1",
    message_id: "msg_1",
    seq: 2,
    reason_code: "cancelled",
    partial_chars: 3,
  });
  assert.equal(result.action, "abort");
  assert.equal(stream.snapshot().text, "部分。");
  assert.equal(stream.snapshot().status, "aborted");
  assert.equal(stream.apply(delta(3, "晚到")).action, "ignore");
});

test("legacy done-only events remain readable and are labelled legacy", () => {
  const stream = new AgentOutputStream({ threadId: "t1" });
  const result = stream.apply({
    type: "final_output_done",
    message: "旧版完整答案",
    message_id: "legacy-msg",
  });
  assert.equal(result.action, "legacy");
  assert.equal(stream.snapshot().firstDeltaSource, "legacy_done");
});

test("event type guard rejects unrelated custom events", () => {
  assert.equal(isFinalOutputEvent({ type: "context_metrics" }), false);
  assert.equal(isFinalOutputEvent({ type: "final_output_delta" }), true);
});

test("empty aborted output may become a fixed failure but partial output cannot", () => {
  const empty = new AgentOutputStream({ threadId: "t1", runId: "r1" });
  empty.apply(start());
  empty.apply({ ...delta(1, ""), type: "final_output_aborted", reason_code: "generation_failed" });
  assert.equal(empty.apply(start(0, { output_id: "out_2", message_id: "msg_2", delivery_mode: "single_shot" })).action, "start");
  assert.equal(empty.apply(delta(1, "服务暂不可用。", { output_id: "out_2", message_id: "msg_2" })).action, "append");
  assert.equal(empty.apply(done(2, "服务暂不可用。", { output_id: "out_2", message_id: "msg_2" })).action, "done");
  const partial = new AgentOutputStream({ threadId: "t1", runId: "r1" });
  partial.apply(start()); partial.apply(delta(1, "已经公开。"));
  partial.apply({ ...delta(2, ""), type: "final_output_aborted", reason_code: "generation_failed" });
  assert.equal(partial.apply(start(0, { output_id: "out_2", message_id: "msg_2", delivery_mode: "single_shot" })).action, "resync");
  assert.equal(partial.snapshot().text, "已经公开。");
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`not ok - ${name}: ${error.message}`);
  }
}
if (failed) {
  console.error(`${failed} contract test(s) failed`);
  process.exit(1);
}
console.log(`${tests.length} agent-output-stream contract tests passed`);
