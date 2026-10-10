/**
 * session token 兼容性校验的 JS 侧参考实现。
 *
 * 算法逐行对齐 `frontend/lib/session-token.ts`，用来和 Python 侧
 * `app/security.py` 做逐字节比对，防止两处实现漂移（同一个 token 格式现在
 * 有前端 edge middleware 和 Python 后台两份实现，必须锁死）。
 *
 * 用法:
 *   node session_compat.mjs sign   <userId> <account> <exp> <secret>
 *   node session_compat.mjs verify <token>  <secret>
 */
const encoder = new TextEncoder();

function toBase64Url(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(text) {
  const normalized = text.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(normalized), (c) => c.charCodeAt(0));
}

async function hmacKey(secret) {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function signSession(userId, account, secret, exp) {
  const body = `${userId}.${account}.${exp}`;
  const key = await hmacKey(secret);
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(body));
  const payload = btoa(body).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${payload}.${toBase64Url(new Uint8Array(sig))}`;
}

export async function verifySession(token, secret) {
  if (!token || !secret) return null;
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const [payloadB64, sigB64] = parts;
  let body;
  try {
    body = atob(payloadB64.replace(/-/g, "+").replace(/_/g, "/"));
  } catch {
    return null;
  }
  const key = await hmacKey(secret);
  const valid = await crypto.subtle.verify(
    "HMAC",
    key,
    fromBase64Url(sigB64),
    encoder.encode(body),
  );
  if (!valid) return null;
  const [userId, account, expStr] = body.split(".");
  const exp = Number(expStr);
  if (!userId || !account || !Number.isFinite(exp) || exp < Date.now() / 1000) return null;
  return { userId, account, exp };
}

const [, , command, ...rest] = process.argv;

if (command === "sign") {
  const [userId, account, exp, secret] = rest;
  process.stdout.write(await signSession(userId, account, secret, Number(exp)));
} else if (command === "verify") {
  const [token, secret] = rest;
  const payload = await verifySession(token, secret);
  process.stdout.write(payload ? JSON.stringify(payload) : "null");
}
