"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { useSessionStore } from "@/lib/session-client";
import type { AdminSessionContext } from "@/lib/admin-api-contract";

type Stage = "login" | "pick-tenant";

export default function LoginPage() {
  const router = useRouter();
  const refreshSession = useSessionStore((s) => s.refreshSession);
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reloginHint, setReloginHint] = useState(false);
  const [stage, setStage] = useState<Stage>("login");
  const [pendingCtx, setPendingCtx] = useState<AdminSessionContext | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ account, password }),
      });
      const data = (await res.json()) as AdminSessionContext & { error?: string };
      if (!res.ok) {
        setError(data.error ?? "登录失败");
        return;
      }
      if (data.needs_tenant_selection) {
        setPendingCtx(data);
        setStage("pick-tenant");
        return;
      }
      await refreshSession();
      const next = new URLSearchParams(window.location.search).get("next");
      router.replace(next && next.startsWith("/") ? next : "/");
    } catch {
      setError("无法连接认证服务，请确认后台服务已启动");
    } finally {
      setSubmitting(false);
    }
  }

  async function pickTenant(tenantId: string) {
    if (!pendingCtx) return;
    setError(null);
    try {
      const res = await fetch("/api/auth/switch-tenant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tenant_id: tenantId, expected_ctx_version: pendingCtx.context_version }),
      });
      if (!res.ok) {
        const data = (await res.json()) as { error?: string };
        setError(data.error ?? "进入企业失败");
        return;
      }
      await refreshSession();
      router.replace("/");
    } catch {
      setError("进入企业失败，请重试");
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="bg-primary text-primary-foreground mx-auto mb-3 flex size-10 items-center justify-center rounded-lg text-sm font-bold">
            AA
          </div>
          {stage === "login" ? (
            <>
              <h1 className="text-foreground text-lg font-semibold">登录 Air Agent</h1>
              <p className="text-muted-foreground mt-1 text-sm">
                {reloginHint ? "登录方式已升级，请使用统一账号重新登录" : "使用平台管理员开通的统一账号"}
              </p>
            </>
          ) : (
            <>
              <h1 className="text-foreground text-lg font-semibold">选择企业</h1>
              <p className="text-muted-foreground mt-1 text-sm">你的账号属于多个企业，选择后进入对话。</p>
            </>
          )}
        </div>

        {stage === "login" ? (
          <form onSubmit={onSubmit} className="space-y-3 rounded-xl border border-border bg-card p-5">
            <div>
              <label htmlFor="username" className="text-muted-foreground mb-1 block text-xs">账号</label>
              <input
                id="username"
                autoComplete="username"
                required
                value={account}
                onChange={(e) => { setAccount(e.target.value); setReloginHint(false); }}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
              />
            </div>
            <div>
              <label htmlFor="password" className="text-muted-foreground mb-1 block text-xs">密码</label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
              />
            </div>
            {error && <p className="text-red-500 text-sm" role="alert">{error}</p>}
            <button
              type="submit"
              disabled={submitting}
              className="bg-primary text-primary-foreground w-full rounded-md py-2 text-sm font-medium disabled:opacity-60"
            >
              {submitting ? "登录中…" : "登录"}
            </button>
            <p className="text-muted-foreground text-center text-xs">
              公开注册已停止；如需账号请联系平台管理员开通。
            </p>
          </form>
        ) : (
          <div className="space-y-3 rounded-xl border border-border bg-card p-5">
            {pendingCtx?.tenants.map((item) => (
              <button
                key={item.tenant.id}
                type="button"
                onClick={() => void pickTenant(item.tenant.id)}
                className="hover:bg-accent w-full rounded-md border border-border px-3 py-2.5 text-left text-sm"
              >
                <div className="text-foreground font-medium">{item.tenant.name}</div>
                <div className="text-muted-foreground text-xs">
                  角色：{item.membership.role_codes.join("、") || "成员"}
                </div>
              </button>
            ))}
            {error && <p className="text-red-500 text-sm" role="alert">{error}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
