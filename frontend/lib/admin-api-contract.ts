/** 前后端共享的统一会话契约类型（与 admin API SessionContextOut 对齐）。
 * 服务端专用实现见 admin-api.ts；本文件刻意无 server-only 依赖，可进客户端。
 */
export type AdminTenant = { id: string; code: string; name: string; status: string };
export type AdminMembership = {
  id: string | null;
  tenant_id: string;
  department_id: string | null;
  status: string;
  revision: number | null;
  role_codes: string[];
};
export type AdminTenantListItem = { tenant: AdminTenant; membership: AdminMembership };
export type AdminSessionContext = {
  user: {
    id: string;
    account: string;
    display_name: string;
    is_active: boolean;
    last_login_at: string | null;
    created_at: string;
    roles: { id: string; code: string; name: string }[];
    followed_region_codes: string[];
  };
  session: {
    session_id: string;
    active_tenant_id: string | null;
    ctx_version: number;
    created_at: string;
    expires_at: string;
  };
  current_tenant: AdminTenant | null;
  membership: AdminMembership | null;
  permissions: string[];
  platform_admin: boolean;
  context_version: number;
  tenants: AdminTenantListItem[];
  needs_tenant_selection: boolean;
};
