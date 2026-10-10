"use client";

/** 全局会话状态：当前用户、可见菜单、权限判定。
 *
 * 会话是 httpOnly cookie，JS 读不到，所以登录状态只能问后端 `/auth/me`。
 * 路由守卫只做 UX（未登录跳登录页）；真正的授权在 API 侧，前端 gate 只是
 * 少让人点到会 403 的按钮。
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, authApi } from "./api";
import type { Me, PermissionNode } from "./types";

type SessionValue = {
  user: Me | null;
  menus: PermissionNode[];
  loading: boolean;
  error: string | null;
  /** 是否拥有某个权限码（可传多个，要求全部拥有）。 */
  can: (...codes: string[]) => boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [menus, setMenus] = useState<PermissionNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [me, menuTree] = await Promise.all([authApi.me(), authApi.menus()]);
      setUser(me);
      setMenus(menuTree);
    } catch (err) {
      // 401/403 是"未登录"的正常状态，不是错误；其它才是真的出问题
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setUser(null);
        setMenus([]);
      } else {
        setError(err instanceof Error ? err.message : "加载会话失败");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
      setMenus([]);
    }
  }, []);

  const can = useCallback(
    (...codes: string[]) => {
      if (!user) return false;
      return codes.every((code) => user.permissions.includes(code));
    },
    [user]
  );

  const value = useMemo<SessionValue>(
    () => ({ user, menus, loading, error, can, refresh, logout }),
    [user, menus, loading, error, can, refresh, logout]
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession 必须在 SessionProvider 内使用");
  return ctx;
}
