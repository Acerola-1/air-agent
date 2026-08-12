/** 认证服务：登录/注册/登出/会话查询（node server 端使用）.
 *
 *  - 密码：bcrypt 哈希（user-store.ts，存 data/users.json）
 *  - 会话：HMAC 签名 cookie（session-token.ts），httpOnly + sameSite=lax
 *  - 登录后前端把 user.id 写入 localStorage `air_agent_user_id`，
 *    线程 metadata.user_id 按账号隔离历史（机制与匿名 UUID 一致）
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { signSession, verifySession } from "@/lib/session-token";
import {
  createUser,
  findUserById,
  findUserByUsername,
  validateCredentials,
  verifyPassword,
  type StoredUser,
} from "@/lib/user-store";

export const SESSION_COOKIE = "air_agent_session";

function getSecret(): string {
  const secret = process.env.AUTH_SECRET;
  if (!secret) throw new Error("AUTH_SECRET 未设置（.env / .env.example）");
  return secret;
}

const COOKIE_OPTS = {
  httpOnly: true,
  sameSite: "lax" as const,
  path: "/",
  maxAge: 60 * 60 * 24 * 30, // 30 天
  // 生产环境（https）启用 secure；本地 http 不开
  secure: process.env.NODE_ENV === "production",
};

function setSessionCookie(res: NextResponse, token: string): NextResponse {
  res.cookies.set(SESSION_COOKIE, token, COOKIE_OPTS);
  return res;
}

function clearSessionCookie(res: NextResponse): NextResponse {
  res.cookies.set(SESSION_COOKIE, "", { ...COOKIE_OPTS, maxAge: 0 });
  return res;
}

export async function register(username: string, password: string): Promise<
  | { ok: true; user: StoredUser }
  | { ok: false; error: string }
> {
  const invalid = validateCredentials(username, password);
  if (invalid) return { ok: false, error: invalid };
  const user = createUser(username, password);
  if (!user) return { ok: false, error: "用户名已存在" };
  return { ok: true, user };
}

export async function login(username: string, password: string): Promise<
  | { ok: true; user: StoredUser }
  | { ok: false; error: string }
> {
  const user = findUserByUsername(username);
  if (!user || !verifyPassword(user, password)) {
    return { ok: false, error: "用户名或密码错误" };
  }
  return { ok: true, user };
}

/** 从请求 cookie 恢复当前会话用户；未登录返回 null */
export async function getSessionUser(): Promise<StoredUser | null> {
  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;
  const payload = token ? await verifySession(token, getSecret()) : null;
  if (!payload) return null;
  return findUserById(payload.userId) ?? null;
}

export { getSecret, setSessionCookie, clearSessionCookie };

/** 生成会话 cookie 的响应（供 login/register 共用） */
export async function buildSessionResponse(
  user: StoredUser,
): Promise<NextResponse> {
  const token = await signSession(user.id, user.username, getSecret());
  const res = NextResponse.json({ id: user.id, username: user.username });
  return setSessionCookie(res, token);
}
