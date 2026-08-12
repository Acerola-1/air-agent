"use client";

import { useEffect } from "react";
import { ThreadSidebar } from "@/components/chat/ThreadSidebar";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { ArtifactProvider } from "@/components/artifact/ArtifactProvider";
import { CanvasWindow } from "@/components/artifact/CanvasWindow";
import { useChatStore } from "@/lib/chat-store";

/** 首页：顶栏 + 左侧线程列表 + 右侧对话区 + 画布悬浮窗.
 *
 *  原生 LangGraph 实现（无 assistant-ui）：
 *   - 线程列表按 graph_id + user_id 过滤（localStorage 持久用户标识隔离）
 *   - 对话通过 runs.stream（SSE）流式渲染，持久化走后端 Postgres checkpointer
 *   - 画布为 fixed 悬浮抽屉，覆盖在页面右侧，不推挤对话区
 */
export default function Home() {
  const switchGraph = useChatStore((s) => s.switchGraph);
  // 画布按会话隔离：每个活动线程一个 Artifact store（persist key 带 threadId）
  const activeThreadId = useChatStore((s) => s.activeThreadId);

  // 首次挂载：加载默认 graph 的线程列表
  useEffect(() => {
    void switchGraph("basic-qa");
  }, [switchGraph]);

  return (
    <ArtifactProvider threadId={activeThreadId ?? undefined}>
      <div className="flex h-dvh flex-col overflow-hidden">
        {/* 顶栏 */}
        <header className="z-10 flex h-14 shrink-0 items-center justify-between gap-3 border-b bg-background/95 px-3 backdrop-blur sm:px-4">
          <div className="flex min-w-0 items-center gap-2">
            <div className="h-6 w-6 shrink-0 rounded-md bg-gradient-to-br from-emerald-500 to-cyan-500" aria-hidden />
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-semibold">Air Agent</span>
              <span className="text-muted-foreground truncate text-[11px]">
                空气质量智能助手 · Powered by LangGraph
              </span>
            </div>
          </div>
        </header>

        <div className="flex min-h-0 flex-1">
          <aside className="flex w-80 max-w-md min-w-0 min-h-0 flex-col border-r bg-muted/20">
            <ThreadSidebar />
          </aside>
          <main className="flex min-w-0 flex-1 flex-col">
            <ChatPanel />
          </main>
        </div>
      </div>

      {/* 画布：fixed 悬浮抽屉，覆盖在右侧（不挤对话文字） */}
      <CanvasWindow />
    </ArtifactProvider>
  );
}
