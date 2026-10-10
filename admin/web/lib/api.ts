/** admin API 客户端。
 *
 * 全部请求走同源 `/api/*`（next.config.ts 反代到 :8100），因此 httpOnly 会话
 * cookie 由浏览器自动携带，不需要处理 CORS 或手动传 token。
 */

import type {
  AuditLog,
  Me,
  Page,
  Permission,
  PermissionNode,
  Region,
  Role,
  RoleDetail,
  User,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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

  if (!res.ok) throw new ApiError(res.status, extractMessage(body, res.status));
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
  login: (account: string, password: string) => post<Me>("/auth/login", { account, password }),
  logout: () => post<void>("/auth/logout"),
  me: () => get<Me>("/auth/me"),
  menus: () => get<PermissionNode[]>("/auth/menus"),
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
  create: (data: { code: string; name: string; description?: string; sort?: number }) =>
    post<RoleDetail>("/roles", data),
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
