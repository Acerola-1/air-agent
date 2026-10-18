const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const swc = require(path.join(root, 'node_modules/next/dist/build/swc/index.js'));
const zustand = require(path.join(root, 'node_modules/zustand'));
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const defer = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const ctx = (tenant, version = 1) => ({ user: {id: 'user', account: 'tester'}, current_tenant: {id: tenant}, context_version: version, tenants: [] });
function loadStore(fetcher) {
  const result = swc.transformSync(fs.readFileSync(path.join(root, 'lib/session-client.ts'), 'utf8'), {
    filename: 'session-client.ts', jsc: { parser: { syntax: 'typescript' }, target: 'es2022' }, module: {type: 'commonjs'},
  });
  const module = {exports: {}}, location = {href: ''};
  const chat = {getState: () => ({clearForTenant() {}, switchGraph() {}, graphId: 'basic-qa'})};
  const capability = {getState: () => ({clearForScopeSwitch() {}})};
  function req(name) {
    if (name === 'zustand') return zustand;
    if (name === '@/lib/chat-store') return {useChatStore: chat};
    if (name === '@/lib/capability-store') return {useCapabilityStore: capability};
    if (name === '@/lib/langgraph-api') return {onTenantContextConflict() {}, setTenantRequestContext() {}};
    if (name === '@/lib/feedback/client') return {setFeedbackScope() {}};
    throw new Error(name);
  }
  new Function('require', 'module', 'exports', 'fetch', 'window', 'localStorage', result.code)(req, module, module.exports, fetcher, {location}, {setItem() {}, removeItem() {}});
  return {store: module.exports.useSessionStore, location};
}
test('expired session clears scope and navigates to reauthentication', async () => {
  const {store, location} = loadStore(async () => ({ok:false,status:401}));
  store.setState({ctx: ctx('A'), username:'tester'});
  await store.getState().refreshSession();
  assert.equal(store.getState().ctx, null);
  assert.equal(location.href, '/login?relogin=1');
});
test('IAM outage is recoverable and is not presented as successful logout', async () => {
  const {store, location} = loadStore(async () => ({ok:false,status:503}));
  store.setState({ctx:ctx('A'), username:'tester'});
  await store.getState().logout();
  assert.equal(store.getState().ctx.current_tenant.id, 'A');
  assert.equal(location.href, '');
  assert.match(store.getState().error, /退出尚未完成/);
});
test('failed identity refresh fails closed with retry, without a false login redirect', async () => {
  const {store, location} = loadStore(async () => ({ok:false,status:503}));
  store.setState({ctx:ctx('A'), username:'tester'});
  await store.getState().refreshSession();
  assert.equal(store.getState().ctx, null);
  assert.match(store.getState().error, /暂不可用/);
  assert.equal(location.href, '');
});
test('late refresh cannot resurrect identity after confirmed logout', async () => {
  const pending = defer();
  const {store} = loadStore(url => url.endsWith('/me') ? pending.promise : Promise.resolve({ok:true,status:204}));
  store.setState({ctx:ctx('A'), username:'tester'});
  const refreshing = store.getState().refreshSession();
  await store.getState().logout();
  pending.resolve(ok(ctx('A'))); await refreshing;
  assert.equal(store.getState().ctx, null);
});
test('A to B to A responses cannot overwrite the newest context version', async () => {
  const old = defer(); let n=0;
  const {store} = loadStore(async () => ++n===1 ? old.promise : ok(ctx('A',3)));
  const pending = store.getState().refreshSession();
  await store.getState().refreshSession();
  old.resolve(ok(ctx('A',1))); await pending;
  assert.equal(store.getState().ctx.context_version, 3);
});


test('BFF admin logout client rejects upstream 503 instead of resolving success', async () => {
  const result = swc.transformSync(fs.readFileSync(path.join(root, 'lib/admin-api.ts'), 'utf8'), {
    filename: 'admin-api.ts', jsc: {parser: {syntax: 'typescript'}, target: 'es2022'}, module: {type: 'commonjs'},
  });
  const module = {exports: {}};
  const req = name => {
    if (name === 'server-only' || name === '@/lib/admin-api-contract') return {};
    throw new Error(name);
  };
  let status = 503;
  const fetcher = async () => ({status, headers: {getSetCookie: () => []}, text: async () => ''});
  new Function('require', 'module', 'exports', 'fetch', 'process', result.code)(req, module, module.exports, fetcher, {env: {ADMIN_API_URL:'http://owned-test'}});
  await assert.rejects(module.exports.logoutViaAdmin('synthetic', null), error => error.status === 503);
  status = 204;
  await module.exports.logoutViaAdmin('synthetic', null);
});
