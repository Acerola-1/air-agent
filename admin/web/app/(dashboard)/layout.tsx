"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { AppSidebar } from "@/components/app-sidebar";
import { TenantSwitcher } from "@/components/tenant-switcher";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useSession } from "@/lib/session";

/** 受保护的外壳：加载会话 → 未登录跳登录页 → 否则渲染侧边栏 + 内容。 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { ctx, user, loading, error, moved, dismissMoved, refresh } = useSession();
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

  // 用户/企业/角色/权限/审计已迁到 IAM 管理台：给出明确入口，
  // 不再显示"企业上下文已被其他标签页变更"的误导提示。
  if (moved) {
    return (
      <div className="flex min-h-dvh items-center justify-center px-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>功能已迁移</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-muted-foreground text-sm">{moved.message}</p>
            <p className="text-muted-foreground text-sm">
              请使用统一身份管理后台继续管理用户、企业、部门、角色与权限。
            </p>
            <div className="flex flex-wrap gap-2">
              <Button asChild>
                <a href={moved.url} target="_blank" rel="noreferrer">
                  打开统一身份管理后台
                </a>
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  dismissMoved();
                  router.replace("/operations");
                }}
              >
                返回运营总览
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

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
