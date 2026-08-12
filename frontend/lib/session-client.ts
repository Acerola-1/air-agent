"use client";

import { create } from "zustand";

/** 当前登录会话（客户端）。
 *
 *   - refreshSession(): 调 /api/auth/me，成功则把账号 id 写入 localStorage
 *     `air_agent_user_id`（与匿名 UUID 同 key，线程 metadata.user_id 按账号隔离），
 *     并记录用户名；401 则标记未登录。
 *   - 首页挂载时调用；登录页登录成功后也会跳转并触发。
 */
type SessionState = {
  username: string | null;
  userId: string | null;
  loaded: boolean;
  refreshSession: () => Promise<void>;
  logout: () => Promise<void>;
};

export const useSessionStore = create<SessionState>((set) => ({
  username: null,
  userId: null,
  loaded: false,

  refreshSession: async () => {
    try {
      const res = await fetch("/api/auth/me", { cache: "no-store" });
      if (!res.ok) {
        set({ username: null, userId: null, loaded: true });
        return;
      }
      const data = (await res.json()) as { id: string; username: string };
      // 账号 id 覆盖匿名 UUID：之后创建的线程 metadata.user_id 为账号 id
      try {
        localStorage.setItem("air_agent_user_id", data.id);
      } catch {
        /* ignore */
      }
      set({ username: data.username, userId: data.id, loaded: true });
    } catch {
      set({ username: null, userId: null, loaded: true });
    }
  },

  logout: async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch {
      /* ignore */
    }
    set({ username: null, userId: null, loaded: true });
    window.location.href = "/login";
  },
}));

export const useUsername = () => useSessionStore((s) => s.username);
export const useSessionLoaded = () => useSessionStore((s) => s.loaded);
