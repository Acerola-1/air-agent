"use client";

import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { GraphSwitcher } from "@/components/assistant-ui/graph-switcher";
import { ModeSwitcher } from "@/components/assistant-ui/mode-switcher";
import { useEffect, useState } from "react";

/** useIsMounted Hook: 客户端挂载后返回 true, 否则 false
 *
 *  目的: 解决 Next.js SSR / Client Component Hydration Mismatch 问题.
 *    - assistant-ui 的 ThreadList / Thread 组件依赖客户端状态
 *      (useAuiState 从 useStreamRuntime 的 LangGraph SDK 取数据),
 *      SSR 阶段这些状态是空的, 客户端挂载后再 fetch 会导致首屏 HTML 结构不一致.
 *    - 在 isMounted=false 时统一渲染 SSR/CSR 都一致的骨架占位,
 *      isMounted=true 后才渲染真实 ThreadList/Thread, 从根上消除 mismatch.
 *
 *  参见: https://react.dev/link/hydration-mismatch
 */
function useIsMounted() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}

/** 首屏占位骨架: SSR 和首次 CSR 渲染内容完全一致 → 不会 Hydration Mismatch
 *  - 顶栏: 带右侧 Graph 切换器占位
 *  - 左: 会话列表骨架 (新建按钮 + 搜索框 + 3 条会话占位)
 *  - 右: 主对话区骨架 (顶栏 + 空消息区)
 */
function Skeleton() {
  return (
    <div className="flex h-dvh flex-col">
      {/* 顶栏占位 — 与真实顶栏高度一致 */}
      <header className="h-14 shrink-0 border-b bg-background/95 flex items-center px-4 justify-between backdrop-blur">
        <div className="flex items-center gap-2">
          <div className="h-6 w-6 rounded-md bg-muted animate-pulse" />
          <div className="h-4 w-32 rounded bg-muted animate-pulse" />
        </div>
        <div className="h-9 w-48 rounded-md bg-muted animate-pulse" />
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-80 max-w-md flex-col border-r bg-muted/30 p-3 gap-3">
          <div className="h-9 rounded-md bg-muted animate-pulse" />
          <div className="h-9 rounded-md bg-muted animate-pulse" />
          <div className="flex flex-col gap-2 mt-2">
            <div className="h-11 rounded-md bg-muted animate-pulse" />
            <div className="h-11 rounded-md bg-muted animate-pulse" />
            <div className="h-11 rounded-md bg-muted animate-pulse" />
          </div>
        </aside>
        <main className="flex flex-1 flex-col">
          <div className="flex-1 flex items-center justify-center text-sm text-muted-foreground">
            加载中…
          </div>
          <div className="h-20 border-t bg-muted/10" />
        </main>
      </div>
    </div>
  );
}

/** 首页: 顶栏(Graph 切换器) + ThreadList（左侧会话列表） + Thread（主对话区）
 *  - 用 useIsMounted 门闩, 解决 Hydration Mismatch
 *  - 布局: 三部分 Header / Sidebar / Main, 响应式安全
 */
export default function Home() {
  const mounted = useIsMounted();
  if (!mounted) return <Skeleton />;

  return (
    <div className="flex h-dvh flex-col">
      {/* 顶栏: 左 Logo 区 / 右 Graph 切换器 */}
      <header className="h-14 shrink-0 border-b bg-background/95 backdrop-blur flex items-center px-3 sm:px-4 justify-between gap-3 z-10">
        <div className="flex items-center gap-2 min-w-0">
          <div className="h-6 w-6 rounded-md bg-gradient-to-br from-emerald-500 to-cyan-500 shrink-0" aria-hidden />
          <div className="flex flex-col min-w-0 leading-tight">
            <span className="font-semibold text-sm truncate">Air Agent</span>
            <span className="text-[11px] text-muted-foreground truncate">
              空气质量智能助手 · Powered by LangGraph
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <ModeSwitcher />
          <GraphSwitcher />
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="w-80 max-w-md min-w-0 border-r flex flex-col bg-muted/20">
          <ThreadList />
        </aside>
        <main className="flex-1 min-w-0 flex flex-col">
          <Thread />
        </main>
      </div>
    </div>
  );
}
