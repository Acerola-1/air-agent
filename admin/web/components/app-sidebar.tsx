"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Database,
  ExternalLink,
  History,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Map as MapIcon,
  Settings,
  Shield,
  Star,
  Users,
  type LucideIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { getBackendConfig, type BackendConfig } from "@/lib/config";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";
import { PERMISSION_MENU, type PermissionNode } from "@/lib/types";

/** remote 模式下这些路径的管理功能已迁到 IAM 管理台。 */
const RETIRED_PREFIXES = [
  "/system/users",
  "/system/roles",
  "/system/permissions",
  "/system/audit",
  "/platform/tenants",
  "/enterprise/",
];

function isRetired(path: string): boolean {
  return RETIRED_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(prefix)
  );
}

/** 权限表里的 `icon` 字段是字符串，这里映射到具体图标。 */
const ICONS: Record<string, LucideIcon> = {
  settings: Settings,
  users: Users,
  shield: Shield,
  key: KeyRound,
  history: History,
  database: Database,
  map: MapIcon,
  star: Star,
};

function iconFor(name: string | null): LucideIcon {
  return (name && ICONS[name]) || LayoutDashboard;
}

export function AppSidebar() {
  const { menus, user, logout, can } = useSession();
  const pathname = usePathname();
  const [config, setConfig] = useState<BackendConfig | null>(null);

  useEffect(() => {
    void getBackendConfig().then(setConfig);
  }, []);

  const remote = config?.identityBackend === "remote";
  const iamAdminUrl = config?.iamAdminUrl ?? null;
  // 运营页面由本地静态路由清单 + 实时权限显示；IAM 菜单表不要求包含 Agent 页面。
  const operations = [
    { href: "/operations", label: "运营总览", permissions: ["agent:metrics:read"] },
    {
      href: "/operations/users",
      label: "用户用量",
      permissions: ["agent:metrics:read", "agent:operations:read"],
    },
    {
      href: "/operations/runs",
      label: "对话运行",
      permissions: ["agent:operations:read"],
    },
    {
      href: "/operations/feedback",
      label: "反馈处理",
      permissions: ["agent:operations:read"],
    },
    {
      href: "/operations/insights",
      label: "需求洞察",
      permissions: ["agent:metrics:read"],
    },
    {
      href: "/operations/capabilities",
      label: "业务能力",
      permissions: ["agent:metrics:read"],
    },
    {
      href: "/operations/performance",
      label: "性能",
      permissions: ["agent:metrics:read"],
    },
    { href: "/operations/costs", label: "成本", permissions: ["agent:metrics:read"] },
  ].filter((item) => can(...item.permissions));

  return (
    <aside className="bg-sidebar text-sidebar-foreground flex w-full shrink-0 flex-col border-b md:w-60 md:border-r md:border-b-0">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <div className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-md text-xs font-bold">
          AA
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">air_agent 后台</div>
        </div>
      </div>

      <nav aria-label="后台导航" className="flex max-h-[60vh] flex-wrap items-start gap-2 overflow-y-auto p-3 md:block md:max-h-none md:flex-1 md:space-y-4">
        <Link
          href="/"
          className={cn(
            "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
            pathname === "/" && "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
          )}
        >
          <LayoutDashboard className="size-4" />
          概览
        </Link>

        {remote && iamAdminUrl && (
          <a
            href={iamAdminUrl}
            target="_blank"
            rel="noreferrer"
            className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors"
          >
            <ExternalLink className="size-4" />
            统一身份管理
          </a>
        )}

        {operations.length > 0 && (
          <div className="space-y-1">
            <div className="text-muted-foreground px-3 py-1 text-xs font-medium tracking-wide">
              Agent 运营
            </div>
            {operations.map((item) => {
              const active =
                pathname === item.href ||
                (item.href !== "/operations" && pathname.startsWith(`${item.href}/`));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                    active && "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                  )}
                >
                  <Activity className="size-4" />
                  <span className="truncate">{item.label}</span>
                </Link>
              );
            })}
          </div>
        )}

        {menus.map((group) => {
          const items = (group.children ?? []).filter(
            (child) =>
              child.type === PERMISSION_MENU &&
              child.path &&
              !(remote && isRetired(child.path))
          );
          if (!items.length) return null;
          return (
            <div key={group.id} className="space-y-1">
              <div className="text-muted-foreground px-3 py-1 text-xs font-medium tracking-wide">
                {group.name}
              </div>
              {items.map((item) => {
                const Icon = iconFor(item.icon);
                const active = pathname === item.path || pathname.startsWith(`${item.path}/`);
                return (
                  <Link
                    key={item.id}
                    href={item.path as string}
                    className={cn(
                      "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                      active && "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    )}
                  >
                    <Icon className="size-4" />
                    <span className="truncate">{item.name}</span>
                  </Link>
                );
              })}
            </div>
          );
        })}
      </nav>

      <div className="border-t p-3">
        <div className="mb-2 px-1">
          <div className="truncate text-sm font-medium">{user?.display_name}</div>
          <div className="text-muted-foreground truncate text-xs">
            {user?.account}
            {user?.roles.length ? ` · ${user.roles.map((r) => r.name).join("/")}` : ""}
          </div>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline" size="sm" className="flex-1">
            <Link href="/profile">我的资料</Link>
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            title="退出登录"
            onClick={async () => {
              await logout();
              window.location.href = "/login";
            }}
          >
            <LogOut className="size-4" />
          </Button>
        </div>
      </div>
    </aside>
  );
}
