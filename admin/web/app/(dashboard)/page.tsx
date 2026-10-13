"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, MapPin, ShieldCheck, Users } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { auditApi, regionsApi, rolesApi, usersApi } from "@/lib/api";
import { useSession } from "@/lib/session";

type Stat = {
  label: string;
  value: number | null;
  hint: string;
  href: string;
  icon: typeof Users;
};

export default function OverviewPage() {
  const { user, ctx, can } = useSession();
  const [stats, setStats] = useState<Stat[]>([
    { label: "用户", value: null, hint: "账号总数", href: "/system/users", icon: Users },
    { label: "角色", value: null, hint: "含内置角色", href: "/system/roles", icon: ShieldCheck },
    { label: "行政区划", value: null, hint: "省/市/区县", href: "/business/regions", icon: MapPin },
    { label: "审计记录", value: null, hint: "操作留痕", href: "/system/audit", icon: Activity },
  ]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const results = await Promise.allSettled([
        can("user:read") ? usersApi.list({ page: 1, size: 1 }) : Promise.resolve(null),
        can("role:read") ? rolesApi.list() : Promise.resolve(null),
        regionsApi.children(),
        can("audit:read") ? auditApi.list({ page: 1, size: 1 }) : Promise.resolve(null),
      ]);
      if (cancelled) return;

      const [users, roles, regions, audit] = results;
      setStats((prev) =>
        prev.map((stat, index) => {
          const result = [users, roles, regions, audit][index];
          if (result.status !== "fulfilled" || result.value === null) return stat;
          const value = Array.isArray(result.value)
            ? result.value.length
            : "total" in result.value
              ? result.value.total
              : null;
          return { ...stat, value };
        })
      );
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [can]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">概览</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          欢迎回来，{user?.display_name}。当前身份：
          {user?.roles.map((r) => r.name).join("、") || "无角色"}
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <Link key={stat.label} href={stat.href} className="group">
              <Card className="group-hover:border-ring/60 gap-3 py-4 transition-colors">
                <CardHeader className="flex-row items-center justify-between">
                  <CardTitle className="text-muted-foreground text-sm font-medium">
                    {stat.label}
                  </CardTitle>
                  <Icon className="text-muted-foreground size-4" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-semibold tabular-nums">
                    {stat.value ?? "—"}
                  </div>
                  <div className="text-muted-foreground mt-0.5 text-xs">{stat.hint}</div>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">关于权限</CardTitle>
        </CardHeader>
        <CardContent className="text-muted-foreground space-y-2 text-sm">
          <p>
            你当前拥有 <span className="text-foreground font-medium">{ctx?.permissions.length ?? 0}</span>{" "}
            个权限码。左侧菜单按权限过滤——没有权限的页面不会出现在这里。
          </p>
          <p>接口侧同样会校验权限，所以即使手输地址也会被拒（403）。</p>
        </CardContent>
      </Card>
    </div>
  );
}
