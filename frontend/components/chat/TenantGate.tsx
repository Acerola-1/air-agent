"use client";

import { useState } from "react";
import { Building2, LoaderCircle } from "lucide-react";

import { useSessionStore } from "@/lib/session-client";

/** 企业门禁：已登录但尚未选择当前企业时，先完成选择再渲染对话。
 *
 *  IAM 登录（含 SSO）不会自动落到某个企业，即使账号只有一个企业；
 *  聊天 BFF 在无当前企业时统一拒绝，所以这里必须给单企业也提供选择入口。
 */
export function TenantGate() {
  const ctx = useSessionStore((s) => s.ctx);
  const switchTenant = useSessionStore((s) => s.switchTenant);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!ctx) return null;

  async function pick(tenantId: string) {
    setBusy(tenantId);
    setError(null);
    const result = await switchTenant(tenantId);
    if (!result.ok) {
      setBusy(null);
      if (!result.conflict) setError(result.error ?? "进入企业失败");
      return;
    }
    // 切企业后重建会话上下文与线程/归档缓存；整页重载最不易留下旧企业视图。
    window.location.reload();
  }

  return (
    <div className="flex h-full min-h-0 items-center justify-center overflow-y-auto px-4 py-8">
      <div className="w-full max-w-lg rounded-xl border bg-card p-5">
        <div className="flex items-center gap-2">
          <Building2 className="text-muted-foreground size-5" />
          <h1 className="font-semibold">选择企业</h1>
        </div>
        <p className="text-muted-foreground mt-1 text-sm">
          {ctx.tenants.length
            ? "选择后将进入对应企业的对话上下文。"
            : "当前账号未加入任何有效企业，请联系平台管理员开通成员身份。"}
        </p>
        <div className="mt-5 space-y-2">
          {ctx.tenants.map((item) => (
            <button
              key={item.tenant.id}
              type="button"
              disabled={busy !== null}
              onClick={() => void pick(item.tenant.id)}
              className="hover:bg-accent flex w-full items-center gap-3 rounded-lg border px-3 py-3 text-left text-sm disabled:opacity-60"
            >
              <Building2 className="text-muted-foreground size-4 shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{item.tenant.name}</span>
                <span className="text-muted-foreground block text-xs">
                  角色：{item.membership.role_codes.length ? item.membership.role_codes.join("、") : "成员"}
                </span>
              </span>
              {busy === item.tenant.id && <LoaderCircle className="size-4 animate-spin" />}
            </button>
          ))}
        </div>
        {error && <p role="alert" className="text-destructive mt-3 text-sm">{error}</p>}
      </div>
    </div>
  );
}
