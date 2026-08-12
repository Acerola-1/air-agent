/** Session Token（HMAC-SHA256 签名）—— edge 兼容实现（middleware 与 server 共用）.
 *
 *  token 格式: base64url(payload) + "." + base64url(hmac)
 *  payload = `${userId}.${username}.${exp}`，由 WebCrypto HMAC-SHA256 签名。
 *  middleware（edge runtime，无 fs）只验签名 + 过期；users.json 由 server 端负责。
 */
export type SessionPayload = { userId: string; username: string; exp: number };

const encoder = new TextEncoder();
const SESSION_TTL_SECONDS = 60 * 60 * 24 * 30; // 30 天

function toBase64Url(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

/** 签发 session token（server 端调用） */
export async function signSession(
  userId: string,
  username: string,
  secret: string,
): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const body = `${userId}.${username}.${exp}`;
  const key = await hmacKey(secret);
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(body));
  const sigB64 = toBase64Url(new Uint8Array(sig));
  return `${btoa(body).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}.${sigB64}`;
}

/** 校验 session token，返回 payload；非法/过期返回 null（middleware 与 server 通用） */
export async function verifySession(
  token: string | undefined,
  secret: string,
): Promise<SessionPayload | null> {
  if (!token || !secret) return null;
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const [bodyB64, sigB64] = parts;
  let body: string;
  try {
    body = atob(bodyB64.replace(/-/g, "+").replace(/_/g, "/"));
  } catch {
    return null;
  }
  const key = await hmacKey(secret);
  const sig = Uint8Array.from(atob(sigB64.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
  const valid = await crypto.subtle.verify("HMAC", key, sig, encoder.encode(body));
  if (!valid) return null;
  const [userId, username, expStr] = body.split(".");
  const exp = Number(expStr);
  if (!userId || !username || !Number.isFinite(exp) || exp < Date.now() / 1000) return null;
  return { userId, username, exp };
}

export { SESSION_TTL_SECONDS };
