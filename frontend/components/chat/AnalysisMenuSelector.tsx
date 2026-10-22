"use client";

import menus from "@/lib/data-analysis-menus.json";
import { useChatStore } from "@/lib/chat-store";

/** 原生 select 与企业切换控件一致，由系统负责弹出列表和键盘选择。 */
export function AnalysisMenuSelector() {
  const graphId = useChatStore((s) => s.graphId);
  const menuName = useChatStore((s) => s.analysisMenuName);
  const setMenu = useChatStore((s) => s.setAnalysisMenu);
  const locked = useChatStore((s) => s.isStreaming || s.stopPending);
  if (graphId !== "data-analysis") return null;
  return (
    <label className="inline-flex min-w-0 max-w-full items-center gap-2 text-xs text-muted-foreground">
      分析项目
      <select aria-label="分析项目" value={menuName} disabled={locked}
        className="h-8 min-w-0 max-w-[14rem] rounded-md border border-border bg-background px-2 text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        onChange={(event) => setMenu(event.target.value)}>
        {menus.map((menu) => <option key={menu.value} value={menu.value}>{menu.label}</option>)}
      </select>
    </label>
  );
}
