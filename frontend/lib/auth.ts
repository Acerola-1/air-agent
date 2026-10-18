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
/** remote 部署的应用会话 handle cookie（与 admin/api remote 门面一致；名称隔离）. */
export const IAM_SESSION_COOKIE = "air_agent_iam_handle";

export function sessionCookieName(): string {
  return process.env.AGENT_IDENTITY_BACKEND === "remote" ? IAM_SESSION_COOKIE : SESSION_COOKIE;
}

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
  await logoutViaAdmin(cookieHeader, origin);
}

/** 从请求 cookie 解析统一会话上下文；未登录/撤销返回 null。 */
export async function getSessionContext(): Promise<AdminSessionContext | null> {
  const name = sessionCookieName();
  const store = await cookies();
  const token = store.get(name)?.value;
  if (!token) return null;
  try {
    return await meViaAdmin(`${name}=${token}`);
  } catch (error) {
    if (error instanceof AdminApiError && error.status === 401) return null;
    throw error;
  }
}
