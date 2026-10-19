/** admin API 的响应类型，与后端 `app/schemas.py` 一一对应。 */

export type RoleBrief = {
  id: string;
  code: string;
  name: string;
};

export type User = {
  id: string;
  account: string;
  display_name: string;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  roles: RoleBrief[];
  followed_region_codes: string[];
};

/** 当前登录用户：额外带权限码，用于 gate 菜单与按钮。 */
export type Me = User & {
  permissions: string[];
};

export type Role = {
  id: string;
  code: string;
  name: string;
  description: string | null;
  is_system: boolean;
  sort: number;
  created_at: string;
  permission_count: number;
  user_count: number;
  /** 平台角色 or 企业角色（统一身份协议）。 */
  scope: "platform" | "tenant";
  tenant_id: string | null;
};

export type RoleDetail = Role & {
  permission_codes: string[];
};

/** 权限类型：1=目录 2=菜单 3=按钮 */
export const PERMISSION_DIRECTORY = 1;
export const PERMISSION_MENU = 2;
export const PERMISSION_BUTTON = 3;

export type Permission = {
  id: string;
  code: string;
  name: string;
  type: number;
  path: string | null;
  icon: string | null;
  sort: number;
  is_active: boolean;
};

export type PermissionNode = Permission & {
  children: PermissionNode[];
};

export type Region = {
  code: string;
  name: string;
  level: number;
  parent_code: string | null;
  has_children: boolean;
};

export type AuditLog = {
  id: number;
  actor_id: string | null;
  actor_account: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  detail: Record<string, unknown>;
  ip: string | null;
  created_at: string;
};

export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  size: number;
};

export type AuditAction =
  | string
  | undefined;


// ---------------------------------------------------------- 企业身份（统一会话协议）

export type TenantBrief = {
  id: string;
  code: string;
  name: string;
  status: string;
};

export type MembershipBrief = {
  id: string | null;
  tenant_id: string;
  department_id: string | null;
  status: string;
  revision: number | null;
  role_codes: string[];
};

export type TenantListItem = {
  tenant: TenantBrief;
  membership: MembershipBrief;
};

export type SessionContext = {
  user: User;
  session: {
    session_id: string;
    active_tenant_id: string | null;
    ctx_version: number;
    created_at: string;
    expires_at: string;
  };
  current_tenant: TenantBrief | null;
  membership: MembershipBrief | null;
  permissions: string[];
  platform_admin: boolean;
  context_version: number;
  tenants: TenantListItem[];
  needs_tenant_selection: boolean;
};

export type Tenant = {
  id: string;
  code: string;
  name: string;
  status: string;
  auth_revision: number;
  created_at: string;
};

export type Member = {
  id: string;
  tenant_id: string;
  user_id: string;
  account: string;
  display_name: string;
  user_is_active: boolean;
  department_id: string | null;
  status: string;
  revision: number;
  role_codes: string[];
  created_at: string;
};

export type Department = {
  id: string;
  tenant_id: string;
  dept_code: string;
  name: string;
  parent_id: string | null;
  created_at: string;
};
