const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const swc = require(path.join(root, 'node_modules/next/dist/build/swc/index.js'));
const next = require(path.join(root, 'node_modules/next/server.js'));

function callback(frontend, fetcher) {
  const filename = path.join(root, frontend === 'chat' ? '' : '../admin/web', 'app/api/auth/iam/callback/route.ts');
  const result = swc.transformSync(fs.readFileSync(filename, 'utf8'), {
    filename, jsc: {parser: {syntax: 'typescript'}, target: 'es2022'}, module: {type: 'commonjs'},
  });
  const module = {exports: {}};
  new Function('require', 'module', 'exports', 'fetch', 'process', result.code)(() => next, module, module.exports, fetcher, {env: {AGENT_IDENTITY_BACKEND: 'remote', ADMIN_API_URL: 'http://admin.test'}});
  return module.exports.GET;
}

for (const frontend of ['chat', 'admin']) {
  test(`${frontend}: expired SSO returns to password form with state bound upstream and cookies cleared`, async () => {
    let upstream;
    const get = callback(frontend, async (url) => {
      upstream = new URL(url);
      return new Response(null, {status: 303, headers: {location: '/login?reason=session-expired', 'set-cookie': 'air_agent_iam_state_chat=; Path=/; Max-Age=0'}});
    });
    const out = await get(new next.NextRequest('http://agent.test/api/auth/iam/callback?error=login_required&state=synthetic-state'));
    assert.equal(upstream.searchParams.get('error'), 'login_required');
    assert.equal(upstream.searchParams.get('state'), 'synthetic-state');
    assert.equal(upstream.searchParams.get('frontend'), frontend);
    assert.equal(out.status, 303);
    assert.equal(out.headers.get('cache-control'), 'no-store');
    assert.equal(out.headers.get('location'), 'http://agent.test/login?reason=session-expired');
    assert.ok(out.headers.get('set-cookie').includes('Max-Age=0'));
    assert.ok(!out.headers.get('location').includes('state='));
  });
  for (const status of [400, 401, 403, 503]) {
    test(`${frontend}: callback ${status} has a clean recoverable login destination`, async () => {
      const get = callback(frontend, async () => new Response('internal failure', {status}));
      const out = await get(new next.NextRequest('http://agent.test/api/auth/iam/callback?ticket=synthetic-secret&state=nonce'));
      assert.equal(out.status, 303);
    assert.equal(out.headers.get('cache-control'), 'no-store');
      assert.equal(out.headers.get('location'), `http://agent.test/login?reason=${status >= 500 ? 'identity-unavailable' : 'sso-failed'}`);
      assert.ok(!out.headers.get('location').includes('synthetic-secret'));
    });
  }
  test(`${frontend}: IAM outage and external redirect cannot escape login recovery`, async () => {
    for (const fetcher of [async () => {throw Error('timeout')}, async () => new Response(null, {status: 303, headers: {location: 'https://evil.test'}})]) {
      const out = await callback(frontend, fetcher)(new next.NextRequest('http://agent.test/api/auth/iam/callback?state=nonce'));
      assert.equal(out.status, 303);
    assert.equal(out.headers.get('cache-control'), 'no-store');
      assert.equal(new URL(out.headers.get('location')).origin, 'http://agent.test');
    }
  });
  test(`${frontend}: authenticated positive control still preserves application session cookie`, async () => {
    const get = callback(frontend, async () => new Response(null, {status: 303, headers: {location: '/', 'set-cookie': 'opaque_application_handle=synthetic; HttpOnly; SameSite=Lax; Path=/'}}));
    const out = await get(new next.NextRequest('http://agent.test/api/auth/iam/callback?ticket=synthetic&state=nonce'));
    assert.equal(out.headers.get('location'), 'http://agent.test/');
    assert.ok(out.headers.get('set-cookie').includes('HttpOnly'));
  });
}

for (const frontend of ['chat', 'admin']) {
  test(`${frontend}: internal Next URL normalization cannot move host-only cookies to another host`, async () => {
    const get = callback(frontend, async () => new Response(null, {status:303,headers:{location:'/', 'set-cookie':'opaque_handle=synthetic; HttpOnly; Path=/'}}));
    const out = await get(new next.NextRequest('http://localhost:13000/api/auth/iam/callback?ticket=synthetic&state=nonce', {headers:{host:'127.0.0.1:13000'}}));
    assert.equal(out.headers.get('location'), 'http://127.0.0.1:13000/');
    assert.ok(out.headers.get('set-cookie').includes('HttpOnly'));
  });
}
