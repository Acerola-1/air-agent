/** 认证服务：全部委托统一身份域（admin API）。
 *
 *  - 会话：admin 签发的不透明 HttpOnly cookie（`air_agent_session`），Next 仅转发；
 *  - JSON 账号库与公开注册已退出线上（迁移工具单独读取旧文件）；
 *  - Node 侧 `getSessionContext()` 返回已验证上下文；拿不到即失败关闭。
 */
import { cookies } from "next/headers";
import {
  AdminApiError,
  type AdminSessionContext,
  loginViaAdmin,
  logoutViaAdmin,
  meViaAdmin,
} from "@/lib/admin-api";

export const SESSION_COOKIE = "air_agent_session";

export type LoginResult =
  | { ok: true; ctx: AdminSessionContext; setCookies: string[] }
  | { ok: false; error: string; status: number };

export async function loginViaUnified(
  account: string,
  password: string,
  origin: string | null
): Promise<LoginResult> {
  try {
    const { ctx, setCookies } = await loginViaAdmin(account, password, origin);
    return { ok: true, ctx, setCookies };
  } catch (err) {
    if (err instanceof AdminApiError) {
      return {
        ok: false,
        error: err.status === 503 ? err.message : "账号或密码错误",
        status: err.status,
      };
    }
    return { ok: false, error: "登录失败，请稍后重试", status: 503 };
  }
}

export async function logoutViaUnified(cookieHeader: string, origin: string | null) {
  try {
    await logoutViaAdmin(cookieHeader, origin);
  } catch {
    // 服务端撤销失败时仍清本地 cookie：会话过期后自然失效
  }
}

/** 从请求 cookie 解析统一会话上下文；未登录/撤销返回 null。 */
export async function getSessionContext(): Promise<AdminSessionContext | null> {
  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  try {
    return await meViaAdmin(`${SESSION_COOKIE}=${token}`);
  } catch {
    return null;
  }
}
