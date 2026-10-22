"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/operations", label: "总览", permissions: ["agent:metrics:read"] },
  {
    href: "/operations/users",
    label: "用户用量",
    permissions: ["agent:metrics:read", "agent:operations:read"],
  },
  { href: "/operations/runs", label: "对话运行", permissions: ["agent:operations:read"] },
  {
    href: "/operations/feedback",
    label: "反馈处理",
    permissions: ["agent:operations:read"],
  },
  { href: "/operations/capabilities", label: "业务能力", permissions: ["agent:metrics:read"] },
  { href: "/operations/performance", label: "性能", permissions: ["agent:metrics:read"] },
  { href: "/operations/costs", label: "成本", permissions: ["agent:metrics:read"] },
];

export default function OperationsLayout({ children }: { children: React.ReactNode }) {
  const { can } = useSession();
  const pathname = usePathname();
  const visible = TABS.filter((tab) => can(...tab.permissions));
  const allowed = can("agent:metrics:read") || can("agent:operations:read");

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">Agent 运营后台</h1>
        <p className="text-muted-foreground text-sm">
          当前企业范围内的事实查询与反馈处理；账号、企业、部门与角色仍在统一身份管理后台维护。
        </p>
      </header>
      {!allowed ? (
        <div className="rounded-lg border border-dashed p-8 text-center">
          <p className="font-medium">当前账号没有运营查看权限</p>
          <p className="text-muted-foreground mt-1 text-sm">
            需要统计读取或企业运营明细权限；直接访问 API 同样会被拒绝。
          </p>
        </div>
      ) : (
        <>
          <nav aria-label="运营页面" className="flex flex-wrap gap-1 border-b pb-2">
            {visible.map((tab) => {
              const active =
                pathname === tab.href ||
                (tab.href !== "/operations" && pathname.startsWith(`${tab.href}/`));
              return (
                <Link
                  key={tab.href}
                  href={tab.href}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm transition-colors",
                    active
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  )}
                  aria-current={active ? "page" : undefined}
                >
                  {tab.label}
                </Link>
              );
            })}
          </nav>
          {children}
        </>
      )}
    </div>
  );
}
