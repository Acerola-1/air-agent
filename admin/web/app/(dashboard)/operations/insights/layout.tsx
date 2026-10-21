"use client";

import { usePathname } from "next/navigation";
import { useScopeKey } from "@/lib/operations/hooks";

/** 企业/上下文或详情目标改变时，连同操作草稿一起卸载旧页面。 */
export default function InsightsLayout({ children }: { children: React.ReactNode }) {
  const scope = useScopeKey();
  const pathname = usePathname();
  return <div key={`${scope}|${pathname}`}>{children}</div>;
}
