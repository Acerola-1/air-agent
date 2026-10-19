/** admin API 客户端。
 *
 * 全部请求走同源 `/api/*`（next.config.ts 反代到 :8100），因此 httpOnly 会话
 * cookie 由浏览器自动携带，不需要处理 CORS 或手动传 token。
 */

import type {
  AuditLog,
  Department,
  McpBinding,
  Member,
  Page,
  Permission,
  PermissionNode,
  Region,
  Role,
  RoleDetail,
  SessionContext,
  Tenant,
  TenantListItem,
  User,
} from "./types";

/** R02：页面感知的企业上下文声明（SessionProvider 写入）。
 *  所有请求携带 `x-tenant-id` / `x-tenant-context-version`，服务端与实时会话
 *  比对：缺失/不一致的写操作明确 403/409，旧标签页不再能悄悄写入新企业。 */
type TenantClaim = { tenantId: string; version: number };
let tenantClaim: TenantClaim | null = null;
export function setTenantRequestClaim(next: TenantClaim | null): void {
  tenantClaim = next;
}

let conflictListener: (() => void) | null = null;
export function onTenantContextConflict(fn: (() => void) | null): void {
  conflictListener = fn;
}

/** 管理功能已迁出本服务（用户/企业/部门/角色/权限/审计）。 */
export type ManagementMoved = { url: string; message: string };
let managementMovedListener: ((moved: ManagementMoved) => void) | null = null;
export function onManagementMoved(fn: ((moved: ManagementMoved) => void) | null): void {
  managementMovedListener = fn;
}

export class ApiError extends Error {
  status: number;
  code?: string;
  iamUrl?: string;

  constructor(
    status: number,
    message: string,
    options: { code?: string; iamUrl?: string } = {}
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = options.code;
    this.iamUrl = options.iamUrl;
  }
}

/** 把 FastAPI 的 `detail` 归一成一句可展示的话。
 *  `detail` 可能是字符串（业务错误），也可能是数组（Pydantic 校验错误）。
 */
function extractMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (item && typeof item === "object" && "msg" in item) {
        return String((item as { msg: unknown }).msg);
      }
      return String(item);
    });
    if (parts.length) return parts.join("；");
  }
  return `请求失败（HTTP ${status}）`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (tenantClaim && !["/auth/me", "/auth/tenants", "/auth/login", "/auth/logout"].includes(path) && !headers.has("x-tenant-id")) {
    headers.set("x-tenant-id", tenantClaim.tenantId || "none");
    headers.set("x-tenant-context-version", String(tenantClaim.version));
  }

  let res: Response;
  try {
    res = await fetch(`/api${path}`, { ...init, headers, credentials: "include" });
  } catch {
    throw new ApiError(0, "无法连接后台服务，请确认 admin API 已启动");
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!res.ok) {
    const message = extractMessage(body, res.status);
    const payload = body as { code?: unknown; iam_url?: unknown } | null;
    const code = typeof payload?.code === "string" ? payload.code : undefined;
    const iamUrl = typeof payload?.iam_url === "string" ? payload.iam_url : undefined;

    // 只有真正的会话企业上下文冲突才刷新会话；功能迁移（410/management_moved）
    // 与普通业务冲突（409 conflict）不得触发"其他标签页变更"提示。
    if (
      res.status === 409 &&
      !path.startsWith("/auth/") &&
      code !== "management_moved" &&
      !iamUrl
    ) {
      conflictListener?.();
    }
    if (iamUrl && code === "management_moved") {
      managementMovedListener?.({ url: iamUrl, message });
    }
    throw new ApiError(res.status, message, { code, iamUrl });
  }
  return body as T;
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, data?: unknown) =>
  request<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });
const patch = <T,>(path: string, data: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(data) });
const put = <T,>(path: string, data: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(data) });
const del = <T,>(path: string) => request<T>(path, { method: "DELETE" });

export const authApi = {
  login: (account: string, password: string) =>
    post<SessionContext>("/auth/login", { account, password }),
  logout: () => post<void>("/auth/logout"),
  me: () => get<SessionContext>("/auth/me"),
  menus: () => get<PermissionNode[]>("/auth/menus"),
  tenants: () => get<TenantListItem[]>("/auth/tenants"),
  switchTenant: (tenantId: string, expectedCtxVersion: number) =>
    post<SessionContext>("/auth/switch-tenant", {
      tenant_id: tenantId,
      expected_ctx_version: expectedCtxVersion,
    }),
  changePassword: (oldPassword: string, newPassword: string) =>
    post<void>("/auth/change-password", {
      old_password: oldPassword,
      new_password: newPassword,
    }),
  myFollowedRegions: () => get<Region[]>("/auth/me/followed-regions"),
  setMyFollowedRegions: (regionCodes: string[]) =>
    put<Region[]>("/auth/me/followed-regions", { region_codes: regionCodes }),
};

export const usersApi = {
  list: (params: {
    page?: number;
    size?: number;
    q?: string;
    is_active?: boolean;
    role_code?: string;
  }) => {
    const search = new URLSearchParams();
    if (params.page) search.set("page", String(params.page));
    if (params.size) search.set("size", String(params.size));
    if (params.q) search.set("q", params.q);
    if (params.is_active !== undefined) search.set("is_active", String(params.is_active));
    if (params.role_code) search.set("role_code", params.role_code);
    const qs = search.toString();
    return get<Page<User>>(`/users${qs ? `?${qs}` : ""}`);
  },
  create: (data: {
    account: string;
    display_name: string;
    password: string;
    is_active?: boolean;
  }) => post<User>("/users", data),
  get: (id: string) => get<User>(`/users/${id}`),
  update: (id: string, data: { display_name?: string; is_active?: boolean }) =>
    patch<User>(`/users/${id}`, data),
  remove: (id: string) => del<void>(`/users/${id}`),
  resetPassword: (id: string, password: string) =>
    put<void>(`/users/${id}/password`, { password }),
  setRoles: (id: string, roleCodes: string[]) =>
    put<User>(`/users/${id}/roles`, { role_codes: roleCodes }),
  followedRegions: (id: string) => get<Region[]>(`/users/${id}/followed-regions/detail`),
  setFollowedRegions: (id: string, regionCodes: string[]) =>
    put<Region[]>(`/users/${id}/followed-regions`, { region_codes: regionCodes }),
};

export const rolesApi = {
  list: () => get<Role[]>("/roles"),
  create: (data: {
    code: string;
    name: string;
    description?: string;
    sort?: number;
    scope?: "platform" | "tenant";
    tenant_id?: string | null;
  }) => post<RoleDetail>("/roles", data),
  get: (id: string) => get<RoleDetail>(`/roles/${id}`),
  update: (id: string, data: { name?: string; description?: string; sort?: number }) =>
    patch<RoleDetail>(`/roles/${id}`, data),
  remove: (id: string) => del<void>(`/roles/${id}`),
  setPermissions: (id: string, permissionCodes: string[]) =>
    put<RoleDetail>(`/roles/${id}/permissions`, { permission_codes: permissionCodes }),
};

export const permissionsApi = {
  list: () => get<Permission[]>("/permissions"),
  tree: () => get<PermissionNode[]>("/permissions/tree"),
  /** R11：企业管理员配置企业角色用的"本企业可授予"目录（role:assign_permission 即可读）。 */
  grantableTree: () => get<PermissionNode[]>("/permissions/grantable-tree"),
};

export const regionsApi = {
  children: (parentCode?: string) => {
    const qs = parentCode ? `?parent_code=${encodeURIComponent(parentCode)}` : "";
    return get<Region[]>(`/regions/children${qs}`);
  },
  search: (q: string) => get<Region[]>(`/regions/search?q=${encodeURIComponent(q)}`),
};

export const auditApi = {
  list: (params: { page?: number; size?: number; action?: string }) => {
    const search = new URLSearchParams();
    if (params.page) search.set("page", String(params.page));
    if (params.size) search.set("size", String(params.size));
    if (params.action) search.set("action", params.action);
    const qs = search.toString();
    return get<Page<AuditLog>>(`/audit-logs${qs ? `?${qs}` : ""}`);
  },
};


export const tenantsApi = {
  list: (q?: string) => get<Tenant[]>(`/tenants${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  create: (data: { code: string; name: string }) => post<Tenant>("/tenants", data),
  get: (id: string) => get<Tenant>(`/tenants/${id}`),
  update: (id: string, data: { name?: string; status?: "active" | "suspended" }) =>
    patch<Tenant>(`/tenants/${id}`, data),
};

export const membersApi = {
  list: (tenantId: string, params: { page?: number; size?: number; q?: string; status?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.page) search.set("page", String(params.page));
    if (params.size) search.set("size", String(params.size));
    if (params.q) search.set("q", params.q);
    if (params.status) search.set("status", params.status);
    const qs = search.toString();
    return get<Page<Member>>(`/tenants/${tenantId}/members${qs ? `?${qs}` : ""}`);
  },
  link: (tenantId: string, data: { user_id?: string; account?: string; role_codes?: string[] }) =>
    post<Member>(`/tenants/${tenantId}/members`, data),
  update: (
    tenantId: string,
    membershipId: string,
    data: { department_id?: string | null; status?: "active" | "disabled" }
  ) => patch<Member>(`/tenants/${tenantId}/members/${membershipId}`, data),
  setRoles: (tenantId: string, membershipId: string, roleCodes: string[]) =>
    put<Member>(`/tenants/${tenantId}/members/${membershipId}/roles`, {
      role_codes: roleCodes,
    }),
  remove: (tenantId: string, membershipId: string) =>
    del<void>(`/tenants/${tenantId}/members/${membershipId}`),
};

export const departmentsApi = {
  list: () => get<Department[]>("/departments"),
  create: (data: { dept_code: string; name: string; parent_id?: string | null }) =>
    post<Department>("/departments", data),
  update: (id: string, data: { name?: string; parent_id?: string | null }) =>
    patch<Department>(`/departments/${id}`, data),
  remove: (id: string) => del<void>(`/departments/${id}`),
};

export const bindingsApi = {
  list: () => get<McpBinding[]>("/mcp-bindings"),
  upsert: (data: {
    tenant_id: string;
    membership_id?: string | null;
    server_key: string;
    external_subject: string;
    credential_ref: string;
    protocol_version: string;
    status?: "active" | "disabled";
  }) => put<McpBinding>("/mcp-bindings", data),
  remove: (id: string) => del<void>(`/mcp-bindings/${id}`),
};
