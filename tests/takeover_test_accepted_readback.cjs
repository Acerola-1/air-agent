const fs = require('fs');
const path = require('path');
const root = '/Users/acerola/Dev/Python/air_agent/frontend';
const swc = require(path.join(root, 'node_modules/next/dist/build/swc/index.js'));
const zustand = require(path.join(root, 'node_modules/zustand'));

function load(rel, mocks) {
  const code = swc.transformSync(fs.readFileSync(path.join(root, rel), 'utf8'), {
    filename: rel,
    jsc: { parser: { syntax: 'typescript' }, target: 'es2022' },
    module: { type: 'commonjs' }
  }).code;
  const mod = { exports: {} };
  new Function('require', 'module', 'exports', code)(n => {
    if (n in mocks) return mocks[n];
    throw Error(n);
  }, mod, mod.exports);
  return mod.exports;
}

(async () => {
  // Read authoritative selection from server probe
  const server = JSON.parse(
    fs.readFileSync('/tmp/flash-05-repair-02-independent-backup/live-probes.json', 'utf8')
  ).forged_catalog_with_valid_skill_and_no_mcp.body.capability_selection;

  const cap = load('lib/capability-store.ts', { zustand }).useCapabilityStore;
  cap.setState({ catalog: { catalog_version: server.catalog_version } });
  cap.getState().setSkillMode('explicit', 'skill:basic-qa:air-quality-basic-query');
  cap.getState().setMcpMode('allowlist', []);

  let sent, handlers, releaseConfirm, releaseStream;
  const confirm = new Promise(r => releaseConfirm = r);
  const stream = new Promise(r => releaseStream = r);

  // Authoritative server readback: confirmRunAdmission returns the server-admitted capability_selection
  const api = new Proxy({
    newSubmissionKey: () => 'key',
    setSubmissionContext: c => sent = JSON.parse(JSON.stringify(c)),
    createThread: async () => 'thread',
    deriveTitle: () => 'test',
    streamRun: async opts => { handlers = opts.handlers; return stream; },
    confirmRunAdmission: async (t, r, onConfirmed) => {
      const res = await confirm;
      if (res && res.confirmed && onConfirmed) {
        onConfirmed(res.attempt_id, res.capability_selection);
      }
      return res;
    },
    getThreadState: async () => ({ messages: [] }),
    notifyIfContextConflict: () => false,
    ThreadAccessError: class extends Error {}
  }, { get: (o, k) => k in o ? o[k] : async () => {} });

  const timing = new Proxy({
    startTiming: () => 'time',
    hasVisibleTiming: () => false
  }, { get: (o, k) => k in o ? o[k] : () => {} });

  const chat = load('lib/chat-store.ts', {
    zustand,
    '@/lib/uuidv7': { generateUuidV7: () => 'uuid' },
    '@/lib/user-identity': { getUserIdentity: () => 'u' },
    '@/lib/langgraph-api': api,
    '@/lib/feedback/client': timing,
    '@/lib/capability-store': { useCapabilityStore: cap }
  }).useChatStore;

  const work = chat.getState().sendMessage('test', 'fast');
  await new Promise(r => setImmediate(r));
  handlers.onRun('platform-run-id');

  // Change draft to S2 before confirmation arrives
  cap.getState().setSkillMode('explicit', 'skill:basic-qa:S2');

  // Server confirms with authoritative server selection for S1
  releaseConfirm({
    confirmed: true,
    attempt_id: 'att-authoritative-001',
    capability_selection: server
  });

  await new Promise(r => setImmediate(r));

  const out = {
    kind: 'compiled_actual_chat_store_with_server_authoritative_confirmation',
    sent_selection: sent.capabilitySelection,
    accepted_after_draft_changed: cap.getState().acceptedSelection,
    actual_server_selection: server,
    selection_id_matches: cap.getState().acceptedSelection?.id === server.id,
    selection_digest_matches: cap.getState().acceptedSelection?.selection_digest === server.selection_digest,
    draft_skill_retained_as_s2: cap.getState().selection.selectedSkillId === 'skill:basic-qa:S2',
    accepted_skill_retained_as_s1: cap.getState().acceptedSelection?.skill_id === 'skill:basic-qa:air-quality-basic-query'
  };

  releaseStream();
  await work;

  fs.writeFileSync(
    '/Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/takeover-05/accepted-vs-server.json',
    JSON.stringify(out, null, 2)
  );
  console.log(JSON.stringify(out, null, 2));
})();
