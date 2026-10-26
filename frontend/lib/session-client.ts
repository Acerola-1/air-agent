"use client";

import { create } from "zustand";
import type { AdminSessionContext } from "@/lib/admin-api-contract";
import { useChatStore } from "@/lib/chat-store";
import { onTenantContextConflict, setTenantRequestContext } from "@/lib/langgraph-api";
import { setFeedbackScope } from "@/lib/feedback/client";

/** 当前登录会话（客户端）。
 *
 * 统一身份协议：`/api/auth/me` 返回固定外壳（user/session/current_tenant/
 * membership/permissions/context_version/tenants/needs_tenant_selection）。
 * 账号 id 写入 localStorage `air_agent_user_id`（线程按用户隔离的旧机制不变，
 * 但企业维度由服务端可信过滤保证）。
 */
type SessionState = {
  sessionEpoch: number;
  ctx: AdminSessionContext | null;
  username: string | null;
  userId: string | null;
  loaded: boolean;
  conflict: string | null;
  error: string | null;
  logoutPending: boolean;
  refreshSession: () => Promise<void>;
  logout: () => Promise<void>;
  /** 切换企业：先由调用方停止活动 run；成功后清空旧企业的线程/归档缓存。 */
  switchTenant: (tenantId: string) => Promise<{ ok: boolean; conflict?: boolean; error?: string }>;
};

/** 每次异步会话操作递增，连 A→B→A 也不接受旧响应。 */
let sessionEpoch = 0;

async function fetchMe(): Promise<AdminSessionContext | null> {
  const res = await fetch("/api/auth/me", { cache: "no-store" });
  if (res.status === 401) return null;
  if (!res.ok) throw new Error("身份服务暂不可用，请重试");
  return (await res.json()) as AdminSessionContext;
}

function applyCtx(ctx: AdminSessionContext | null) {
  const previous = useSessionStore.getState().ctx;
  if (previous?.user.id !== ctx?.user.id || previous?.current_tenant?.id !== ctx?.current_tenant?.id || previous?.context_version !== ctx?.context_version) {
    useChatStore.getState().clearForTenant();
  }
  if (ctx) {
    try {
      localStorage.setItem("air_agent_user_id", ctx.user.id);
    } catch {
      /* ignore */
    }
  } else {
    try { localStorage.removeItem("air_agent_user_id"); } catch { /* ignore */ }
  }
  // R02：页面感知的企业上下文同步给 API 层，之后每个业务请求携带，
  // 由服务端与实时会话比对；刷新后重新登记（版本前进自动覆盖旧值）。
  setTenantRequestContext(
    ctx
      ? { tenantId: ctx.current_tenant?.id ?? "", contextVersion: ctx.context_version }
      : null
  );
  // 反馈域作用域：企业/用户变化即清空映射/队列/迟到回调（spec：不串评价）
  setFeedbackScope(ctx?.current_tenant?.id ?? "", ctx?.user.id ?? "", ctx?.context_version ?? 0);
  useSessionStore.setState({
    ctx,
    username: ctx ? ctx.user.account : null,
    userId: ctx ? ctx.user.id : null,
  });
}

// R02：业务请求收到服务端 409 context_changed → 刷新实时会话并清空旧企业缓存，
// 旧标签页不得继续展示/写入旧企业数据。
onTenantContextConflict(() => {
  void (async () => {
    await useSessionStore.getState().refreshSession();
    useChatStore.getState().clearForTenant();
    useChatStore.getState().switchGraph(useChatStore.getState().graphId);
    useSessionStore.setState({ conflict: "该企业上下文已被其他标签页变更，页面已刷新" });
  })();
});

export const useSessionStore = create<SessionState>((set, get) => ({
  sessionEpoch: 0,
  ctx: null,
  username: null,
  userId: null,
  loaded: false,
  conflict: null,
  error: null,
  logoutPending: false,

  refreshSession: async () => {
    if (get().logoutPending) return;
    const epoch = ++sessionEpoch;
    set({ sessionEpoch: epoch });
    try {
      const ctx = await fetchMe();
      if (epoch !== sessionEpoch) return;
      applyCtx(ctx);
      set({ loaded: true, conflict: null, error: null });
      if (!ctx) window.location.href = "/login?relogin=1";
    } catch {
      if (epoch !== sessionEpoch) return;
      applyCtx(null);
      set({ loaded: true, error: "身份服务暂不可用，请重试" });
    }
  },

  logout: async () => {
    if (get().logoutPending) return;
    const epoch = ++sessionEpoch;
    set({ sessionEpoch: epoch, logoutPending: true, error: null });
    try {
      const res = await fetch("/api/auth/logout", { method: "POST" });
      if (!res.ok && res.status !== 401) throw new Error("logout unconfirmed");
      if (epoch !== sessionEpoch) return;
      applyCtx(null);
      set({ loaded: true, logoutPending: false, error: null });
      window.location.href = "/login";
    } catch {
      if (epoch !== sessionEpoch) return;
      set({ logoutPending: false, error: "退出尚未完成，请重试" });
    }
  },

  switchTenant: async (tenantId) => {
    const current = get().ctx;
    if (get().logoutPending) return { ok: false, error: "正在退出，请稍后重试" };
    if (!current) return { ok: false, error: "未登录" };
    if (current.current_tenant?.id === tenantId) return { ok: true };
    const epoch = ++sessionEpoch;
    set({ sessionEpoch: epoch });
    set({ error: null });
    try {
      const res = await fetch("/api/auth/switch-tenant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          tenant_id: tenantId,
          expected_ctx_version: current.context_version,
        }),
        cache: "no-store",
      });
      if (epoch !== sessionEpoch) return { ok: false, conflict: true };
      if (res.status === 409) {
        const fresh = await fetchMe();
        if (epoch !== sessionEpoch) return { ok: false, conflict: true };
        applyCtx(fresh);
        // 冲突刷新也必须丢弃旧企业的线程/归档缓存并重载当前企业列表
        useChatStore.getState().clearForTenant();
        useChatStore.getState().switchGraph(useChatStore.getState().graphId);
        set({ conflict: "该企业上下文已被其他标签页变更，页面已刷新" });
        return { ok: false, conflict: true };
      }
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        return { ok: false, error: data.error ?? "企业切换失败" };
      }
      const ctx = (await res.json()) as AdminSessionContext;
      if (epoch !== sessionEpoch) return { ok: false, conflict: true };
      applyCtx(ctx);
      // 切换成功：清空旧企业的线程/归档/偏好缓存（spec：不得展示旧企业数据）
      useChatStore.getState().clearForTenant();
      return { ok: true };
    } catch {
      return { ok: false, error: "企业切换失败，请重试" };
    }
  },
}));

export const useUsername = () => useSessionStore((s) => s.username);
export const useSessionLoaded = () => useSessionStore((s) => s.loaded);
export const useSessionCtx = () => useSessionStore((s) => s.ctx);
