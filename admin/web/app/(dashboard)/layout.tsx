"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { AppSidebar } from "@/components/app-sidebar";
import { Button } from "@/components/ui/button";
import { useSession } from "@/lib/session";

/** 受保护的外壳：加载会话 → 未登录跳登录页 → 否则渲染侧边栏 + 内容。 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, error, refresh } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user && !error) router.replace("/login");
  }, [loading, user, error, router]);

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
    <div className="flex min-h-dvh">
      <AppSidebar />
      <main className="min-w-0 flex-1 overflow-x-hidden">
        <div className="mx-auto max-w-6xl px-6 py-6">{children}</div>
      </main>
    </div>
  );
}
