"use client";

import { AccountMenu } from "@/components/chat/AccountMenu";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { cn } from "@/lib/utils";

export function SidebarAccountBar({ className }: { className?: string }) {
  return (
    <div className={cn("relative flex items-center gap-0.5", className)}>
      <AccountMenu />
      <ThemeToggle />
    </div>
  );
}
