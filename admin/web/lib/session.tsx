"use client";

/** 全局会话状态：统一身份（用户 + 当前企业 + 权限）与可见菜单。
 *
 * 会话是 httpOnly cookie，JS 读不到，登录状态只能问 `/auth/me`（服务端实时解析）。
 * 路由守卫只做 UX；真正授权在 API 侧。企业切换带 context_version：并发切换
 * 冲突时服务端返回 409，本上下文随即刷新（旧页面的操作被拒绝而非静默改企业）。
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  ApiError,
  authApi,
  onManagementMoved,
  onTenantContextConflict,
  setTenantRequestClaim,
  type ManagementMoved,
} from "./api";
import type { PermissionNode, SessionContext, User } from "./types";

type SessionValue = {
  /** 完整统一会话上下文（null=未登录）。 */
  ctx: SessionContext | null;
  user: User | null;
  menus: PermissionNode[];
  loading: boolean;
  error: string | null;
  /** 当前页面访问了已迁移到 IAM 管理台的功能。 */
  moved: ManagementMoved | null;
  dismissMoved: () => void;
  can: (...codes: string[]) => boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
  switchTenant: (
    tenantId: string
  ) => Promise<{ ok: boolean; conflict?: boolean; message?: string }>;
};

const SessionStateContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const generation = useRef(0);
  const [ctx, setCtx] = useState<SessionContext | null>(null);
  const [menus, setMenus] = useState<PermissionNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [moved, setMoved] = useState<ManagementMoved | null>(null);

  const dismissMoved = useCallback(() => setMoved(null), []);

  const refresh = useCallback(async () => {
    const epoch = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const meData = await authApi.me();
      if (epoch !== generation.current) return;
      setTenantRequestClaim({tenantId: meData.current_tenant?.id ?? "none", version: meData.context_version});
      const menuTree = await authApi.menus();
      if (epoch !== generation.current) return;
      setCtx(meData);
      setMenus(menuTree);
      // R02：登记页面感知企业上下文，后续业务请求携带声明
      setTenantRequestClaim(
        meData
          ? { tenantId: meData.current_tenant?.id ?? "none", version: meData.context_version }
          : null
      );
    } catch (err) {
      if (epoch !== generation.current) return;
      // 401/403 是"未登录/会话撤销"的正常状态，不是错误；其它才是真的出问题
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setCtx(null);
        setMenus([]);
      } else {
        setError(err instanceof Error ? err.message : "加载会话失败");
      }
    } finally {
      if (epoch === generation.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // R02：业务请求收到 409 上下文冲突 → 刷新实时会话并给出可读提示（旧标签页
  // 不继续展示/写入旧企业数据）。
  useEffect(() => {
    onTenantContextConflict(() => {
      // 在刷新网络完成前撤下旧企业视图；IAM不可用时也不能继续展示旧上下文。
      setCtx(null);
      setMenus([]);
      setTenantRequestClaim(null);
      void refresh().then(() =>
        setError("企业上下文已被其他标签页变更，页面已重新加载；请以当前企业为准继续操作。")
      );
    });
    return () => onTenantContextConflict(null);
  }, [refresh]);

  // 用户/企业/角色/权限/审计已迁到 IAM 管理台：收到 410 后展示明确入口，
  // 不再复用"企业上下文已被其他标签页变更"的提示。
  useEffect(() => {
    onManagementMoved(setMoved);
    return () => onManagementMoved(null);
  }, []);

  const logout = useCallback(async () => {
    await authApi.logout();
    ++generation.current;
    setTenantRequestClaim(null);
    setCtx(null);
    setMenus([]);
    setMoved(null);
  }, []);

  const switchTenant = useCallback(
    async (tenantId: string) => {
      if (!ctx) return { ok: false, message: "未登录" };
      const epoch = ++generation.current;
      try {
        const next = await authApi.switchTenant(tenantId, ctx.context_version);
        if (epoch !== generation.current) return { ok: false, message: "企业上下文已变更" };
        setTenantRequestClaim({
          tenantId: next.current_tenant?.id ?? "none",
          version: next.context_version,
        });
        const menuTree = await authApi.menus();
        if (epoch !== generation.current) return { ok: false, message: "企业上下文已变更" };
        setCtx(next);
        setMenus(menuTree);
        return { ok: true };
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          await refresh();
          return { ok: false, conflict: true, message: err.message };
        }
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          await refresh();
          return { ok: false, message: err.message };
        }
        return { ok: false, message: err instanceof Error ? err.message : "切换失败" };
      }
    },
    [ctx, refresh]
  );

  const can = useCallback(
    (...codes: string[]) => {
      if (!ctx) return false;
      return codes.every((code) => ctx.permissions.includes(code));
    },
    [ctx]
  );

  const value = useMemo<SessionValue>(
    () => ({
      ctx,
      user: ctx ? ctx.user : null,
      menus,
      loading,
      error,
      moved,
      dismissMoved,
      can,
      refresh,
      logout,
      switchTenant,
    }),
    [ctx, menus, loading, error, moved, dismissMoved, can, refresh, logout, switchTenant]
  );

  return (
    <SessionStateContext.Provider value={value}>{children}</SessionStateContext.Provider>
  );
}

export function useSession(): SessionValue {
  const current = useContext(SessionStateContext);
  if (!current) throw new Error("useSession 必须在 SessionProvider 内使用");
  return current;
}
