"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { AppSidebar } from "@/components/app-sidebar";
import { TenantSwitcher } from "@/components/tenant-switcher";
import { Button } from "@/components/ui/button";
import { useSession } from "@/lib/session";

/** 受保护的外壳：加载会话 → 未登录跳登录页 → 否则渲染侧边栏 + 内容。 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { ctx, user, loading, error, refresh } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (loading || error || !user) {
      if (!loading && !user && !error) router.replace("/login");
      return;
    }
    // 已登录但多企业未选择：管理界面同样先进选择页（平台管理员无企业可用平台域）
    if (ctx?.needs_tenant_selection) router.replace("/select-tenant");
  }, [loading, user, error, ctx, router]);

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <div className="text-muted-foreground text-sm">正在加载…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3">
        <div className="text-destructive text-sm">{error}</div>
        <Button variant="outline" size="sm" onClick={() => void refresh()}>
          重试
        </Button>
      </div>
    );
  }

  if (!user) return null;

  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      <AppSidebar />
      <main className="min-w-0 flex-1 overflow-x-hidden">
        <div className="border-border flex justify-end border-b px-4 py-2 md:px-6">
          <TenantSwitcher />
        </div>
        <div className="mx-auto max-w-6xl px-4 py-6 md:px-6">{children}</div>
      </main>
    </div>
  );
}
