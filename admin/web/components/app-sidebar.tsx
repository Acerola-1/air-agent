"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Database,
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
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";
import { PERMISSION_MENU, type PermissionNode } from "@/lib/types";

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
  const { menus, user, logout } = useSession();
  const pathname = usePathname();

  return (
    <aside className="bg-sidebar text-sidebar-foreground flex w-60 shrink-0 flex-col border-r">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <div className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-md text-xs font-bold">
          AA
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">air_agent 后台</div>
        </div>
      </div>

      <nav className="flex-1 space-y-4 overflow-y-auto p-3">
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

        {menus.map((group) => {
          const items = (group.children ?? []).filter(
            (child) => child.type === PERMISSION_MENU && child.path
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
