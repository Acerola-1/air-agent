"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Activity, ExternalLink, MapPin, ShieldCheck, Users } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { auditApi, regionsApi, rolesApi, usersApi } from "@/lib/api";
import { getBackendConfig, type BackendConfig } from "@/lib/config";
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
  const [config, setConfig] = useState<BackendConfig | null>(null);
  const [stats, setStats] = useState<Stat[]>([
    { label: "用户", value: null, hint: "账号总数", href: "/system/users", icon: Users },
    { label: "角色", value: null, hint: "含内置角色", href: "/system/roles", icon: ShieldCheck },
    { label: "行政区划", value: null, hint: "省/市/区县", href: "/business/regions", icon: MapPin },
    { label: "审计记录", value: null, hint: "操作留痕", href: "/system/audit", icon: Activity },
  ]);

  useEffect(() => {
    void getBackendConfig().then(setConfig);
  }, []);

  const remote = config?.identityBackend === "remote";
  const iamAdminUrl = config?.iamAdminUrl ?? null;

  useEffect(() => {
    // 配置未就绪时不发请求，避免 remote 模式下先打已退役接口再弹迁移提示。
    if (!config) return;
    let cancelled = false;

    async function load() {
      const results = await Promise.allSettled([
        !remote && can("user:read")
          ? usersApi.list({ page: 1, size: 1 })
          : Promise.resolve(null),
        !remote && can("role:read") ? rolesApi.list() : Promise.resolve(null),
        regionsApi.children(),
        !remote && can("audit:read")
          ? auditApi.list({ page: 1, size: 1 })
          : Promise.resolve(null),
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
  }, [can, config, remote]);

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
          // remote 模式下用户/角色/审计由 IAM 管理台负责；行政区划仍在本服务。
          const migrated = remote && !!iamAdminUrl && stat.label !== "行政区划";
          const card = (
            <Card className="group-hover:border-ring/60 gap-3 py-4 transition-colors">
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-muted-foreground text-sm font-medium">
                  {stat.label}
                </CardTitle>
                {migrated ? (
                  <ExternalLink className="text-muted-foreground size-4" />
                ) : (
                  <Icon className="text-muted-foreground size-4" />
                )}
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-semibold tabular-nums">
                  {stat.value ?? "—"}
                </div>
                <div className="text-muted-foreground mt-0.5 text-xs">
                  {migrated ? "统一身份管理后台" : stat.hint}
                </div>
              </CardContent>
            </Card>
          );
          return migrated ? (
            <a
              key={stat.label}
              href={iamAdminUrl}
              target="_blank"
              rel="noreferrer"
              className="group"
            >
              {card}
            </a>
          ) : (
            <Link key={stat.label} href={stat.href} className="group">
              {card}
            </Link>
          );
        })}
      </div>

      {remote && iamAdminUrl && (
        <p className="text-muted-foreground text-xs">
          用户、角色、审计已迁移到统一身份管理后台。
          <a
            className="text-primary ml-1 underline"
            href={iamAdminUrl}
            target="_blank"
            rel="noreferrer"
          >
            打开管理后台
          </a>
        </p>
      )}

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
