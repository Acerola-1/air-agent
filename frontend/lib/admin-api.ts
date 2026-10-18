import "server-only";
export * from "@/lib/admin-api-contract";
import type { AdminSessionContext } from "@/lib/admin-api-contract";


/** admin API（统一身份域）的服务端客户端。
 *
 * 聊天前端不再持有本地账号库：登录/会话/企业切换全部由 admin API 判定，
 * Next 服务端转发浏览器 cookie 并透传 Set-Cookie（HttpOnly 会话对 JS 不可见）。
 * 任何"拿不到身份"的路径都失败关闭（401/403/503），绝不回退 JSON/匿名身份。
 */

export class AdminApiError extends Error {
  status: number;
  json?: unknown;
  constructor(status: number, message: string, json?: unknown) {
    super(message);
    this.status = status;
    this.json = json;
  }
}

const BASE = () => process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100";

function extractDetail(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  return `身份服务返回 HTTP ${status}`;
}

/** 转发一次 admin 调用。返回 [status, parsedBody, setCookieHeaders]。 */
export async function adminFetch(
  path: string,
  init: {
    method?: string;
    body?: unknown;
    cookie?: string;
    origin?: string | null;
    internalToken?: string;
    /** 台账写入口需要页面感知的企业上下文声明头（R02 契约）。 */
    tenantContext?: { tenantId: string; contextVersion: number } | null;
  } = {}
): Promise<{ status: number; json: unknown; setCookies: string[] }> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (init.cookie) headers.cookie = process.env.AGENT_IDENTITY_BACKEND === "remote"
    ? init.cookie.split(";").map((part) => part.trim()).filter((part) => part.startsWith("air_agent_iam_handle=")).join("; ")
    : init.cookie;
  if (init.origin) headers.origin = init.origin;
  if (init.internalToken) headers["x-internal-token"] = init.internalToken;
  if (init.tenantContext) {
    headers["x-tenant-id"] = init.tenantContext.tenantId;
    headers["x-tenant-context-version"] = String(init.tenantContext.contextVersion);
  }
  let res: Response;
  try {
    res = await fetch(`${BASE()}${path}`, {
      method: init.method ?? "GET",
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
    });
  } catch {
    throw new AdminApiError(503, "身份服务暂不可用，请稍后重试");
  }
  const setCookies = res.headers.getSetCookie ? res.headers.getSetCookie() : [];
  const text = await res.text();
  let json: unknown = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
  }
  return { status: res.status, json, setCookies };
}

function unwrap<T>(result: { status: number; json: unknown }, okStatuses: number[]): T {
  if (!okStatuses.includes(result.status)) {
    throw new AdminApiError(result.status, extractDetail(result.json, result.status), result.json);
  }
  return result.json as T;
}

export async function loginViaAdmin(
  account: string,
  password: string,
  origin: string | null
): Promise<{ ctx: AdminSessionContext; setCookies: string[] }> {
  const result = await adminFetch("/auth/login", {
    method: "POST",
    body: { account, password },
    origin,
  });
  return {
    ctx: unwrap<AdminSessionContext>(result, [200]),
    setCookies: result.setCookies,
  };
}

export async function meViaAdmin(cookie: string): Promise<AdminSessionContext | null> {
  const result = await adminFetch("/auth/me", { cookie });
  if (result.status === 401 || result.status === 403) return null;
  if (result.status !== 200) throw new AdminApiError(result.status, extractDetail(result.json, result.status));
  return result.json as AdminSessionContext;
}

export async function logoutViaAdmin(cookie: string, origin: string | null) {
  const result = await adminFetch("/auth/logout", { method: "POST", cookie, origin });
  unwrap<null>(result, [204, 401]);
}

export async function switchTenantViaAdmin(
  cookie: string,
  origin: string | null,
  tenantId: string,
  expectedCtxVersion: number
) {
  const result = await adminFetch("/auth/switch-tenant", {
    method: "POST",
    body: { tenant_id: tenantId, expected_ctx_version: expectedCtxVersion },
    cookie,
    origin,
  });
  return result;
}

export async function runtimeTicketViaAdmin(
  cookie: string,
  targetTenantId: string | null,
  expectedCtxVersion?: number
): Promise<{ ticket: string; expires_at: string; status: number }> {
  const internal = process.env.INTERNAL_API_TOKEN ?? "";
  if (!internal) throw new AdminApiError(503, "对话服务凭据配置不可用");
  const body: Record<string, unknown> = {};
  if (targetTenantId) body.target_tenant_id = targetTenantId;
  // F05/R02：签发窗口携带页面声明的上下文版本，服务端与实时会话比对
  if (expectedCtxVersion !== undefined) body.expected_ctx_version = expectedCtxVersion;
  const result = await adminFetch("/auth/runtime-ticket", {
    method: "POST",
    body,
    cookie,
    internalToken: internal,
  });
  if (result.status === 401) {
    // 内部凭据错误是配置事故，不是用户错误：失败关闭并明确报错
    throw new AdminApiError(503, "对话服务凭据配置不可用");
  }
  // R02：403/409 是业务语义（上下文冲突/企业停用），交给代理映射为明确结果，
  // 不能伪装成 503 配置故障。
  if (result.status !== 200 && result.status !== 403 && result.status !== 409) {
    throw new AdminApiError(result.status, extractDetail(result.json, result.status));
  }
  const json = (result.json ?? {}) as { ticket?: string; expires_at?: string };
  return {
    ticket: json.ticket ?? "",
    expires_at: json.expires_at ?? "",
    status: result.status,
  };
}
