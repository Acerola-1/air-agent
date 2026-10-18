"use client";

/** 当前企业徽章 + 切换器：单/多企业共用；切换成功后强制刷新页面数据缓存。 */

import { useState } from "react";
import { Building2 } from "lucide-react";

import { useSession } from "@/lib/session";

export function TenantSwitcher() {
  const { ctx, switchTenant } = useSession();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  if (!ctx) return null;
  const current = ctx.current_tenant;
  const tenants = ctx.tenants;

  async function pick(tenantId: string) {
    if (!current || tenantId === current.id) return;
    setBusy(true);
    setMessage(null);
    const result = await switchTenant(tenantId);
    setBusy(false);
    if (!result.ok) {
      setMessage(result.conflict ? "该企业上下文已被其他标签页变更，已刷新" : result.message ?? "切换失败");
      return;
    }
    // 切换成功：整页刷新，清空旧企业的列表/详情缓存（spec 要求）
    window.location.reload();
  }

  if (ctx.tenants.length === 0 && ctx.platform_admin) {
    return (
      <div className="text-muted-foreground flex min-w-0 max-w-full items-center gap-1.5 text-xs">
        <Building2 className="size-3.5" /> 平台域（无企业上下文）
      </div>
    );
  }

  return (
    <label className="text-muted-foreground flex min-w-0 max-w-full items-center gap-1.5 text-xs">
      <Building2 className="size-3.5" />
      <span className="sr-only">切换企业</span>
      <select
        className="bg-background text-foreground min-w-0 max-w-full rounded border px-2 py-1 text-xs disabled:opacity-60"
        value={current?.id ?? ""}
        disabled={busy || tenants.length === 0}
        onChange={(event) => {
          const nextTenant = event.target.value;
          if (nextTenant) void pick(nextTenant);
        }}
      >
        {tenants.length === 0 && <option value="">无可用企业</option>}
        {tenants.map((item) => (
          <option key={item.tenant.id} value={item.tenant.id}>
            {item.tenant.name}
            {item.membership.role_codes.includes("tenant_admin") ? "（管理员）" : ""}
          </option>
        ))}
      </select>
      {message && <span className="text-destructive">{message}</span>}
    </label>
  );
}
