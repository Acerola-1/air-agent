"use client";

/** 企业选择页：多企业首次登录/未选择时进入；不自动落到列表第一个。 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Building2, LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, authApi } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function SelectTenantPage() {
  const { ctx, refresh } = useSession();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  if (!ctx) return null;
  const tenants = ctx.tenants;

  async function pick(tenantId: string) {
    setBusy(tenantId);
    setMessage(null);
    try {
      await authApi.switchTenant(tenantId, ctx!.context_version);
      await refresh();
      router.replace("/");
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "进入企业失败");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-lg py-10">
      <h1 className="text-lg font-semibold">选择企业</h1>
      <p className="text-muted-foreground mt-1 text-sm">
        你的账号是以下企业的成员。选择后将进入对应企业的管理上下文。
      </p>
      {tenants.length === 0 && (
        <p className="text-muted-foreground mt-6 text-sm">
          你暂无有效企业。请联系平台管理员为你开通企业成员身份。
        </p>
      )}
      <div className="mt-6 space-y-3">
        {tenants.map((item) => (
          <Card key={item.tenant.id}>
            <CardContent className="flex items-center gap-3 py-4">
              <Building2 className="text-muted-foreground size-5" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{item.tenant.name}</div>
                <div className="text-muted-foreground text-xs">
                  角色：{item.membership.role_codes.length ? item.membership.role_codes.join("、") : "成员"}
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={busy === item.tenant.id}
                onClick={() => void pick(item.tenant.id)}
              >
                {busy === item.tenant.id && <LoaderCircle className="size-4 animate-spin" />}
                进入
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>
      {message && <p className="text-destructive mt-4 text-sm" role="alert">{message}</p>}
    </div>
  );
}
