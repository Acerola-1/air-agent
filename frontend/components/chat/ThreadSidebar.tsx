"use client";

import { useEffect, useMemo, useState } from "react";
import { PlusIcon, SearchIcon, Trash2Icon } from "lucide-react";
import { useChatStore } from "@/lib/chat-store";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/** 左侧线程列表：按 graph_id + user_id 过滤（由 chat-store.loadThreads 处理），
 *  支持新建 / 切换 / 删除。搜索为客户端过滤（MVP 够用）。 */
export function ThreadSidebar() {
  const threads = useChatStore((s) => s.threads);
  const activeThreadId = useChatStore((s) => s.activeThreadId);
  const graphId = useChatStore((s) => s.graphId);
  const loadingThreads = useChatStore((s) => s.loadingThreads);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const openThread = useChatStore((s) => s.openThread);
  const newThread = useChatStore((s) => s.newThread);
  const removeThread = useChatStore((s) => s.removeThread);
  const loadThreads = useChatStore((s) => s.loadThreads);

  const [search, setSearch] = useState("");

  // 切换 graph 后自动重新加载（store.switchGraph 已触发；此处兜底首次挂载）
  useEffect(() => {
    void loadThreads();
  }, [graphId, loadThreads]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return threads;
    return threads.filter((t) => (t.metadata?.title ?? "").toLowerCase().includes(q));
  }, [threads, search]);

  const hasThreads = threads.length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3">
      <Button
        type="button"
        variant="default"
        className="w-full shrink-0 gap-2"
        onClick={newThread}
        disabled={isStreaming}
      >
        <PlusIcon className="size-4" />
        New Thread
      </Button>

      {hasThreads && (
        <div className="relative shrink-0">
          <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索会话"
            aria-label="搜索会话"
            className="border-border/60 focus:border-ring bg-muted/40 h-9 w-full rounded-lg border pl-8 pr-3 text-sm outline-none"
          />
        </div>
      )}

      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
        {loadingThreads && threads.length === 0 ? (
          <p className="text-muted-foreground px-2 py-3 text-center text-xs">加载中…</p>
        ) : filtered.length === 0 ? (
          <p className="text-muted-foreground px-2 py-3 text-center text-xs">
            {hasThreads ? "没有匹配的会话" : "暂无会话，点击 New Thread 开始"}
          </p>
        ) : (
          filtered.map((t) => {
            const threadId = t.thread_id;
            const active = threadId === activeThreadId;
            const title = (t.metadata?.title as string | undefined) || "新会话";
            return (
              <div
                key={threadId}
                className={cn(
                  "group flex items-center gap-1 rounded-lg transition-colors",
                  active ? "bg-accent" : "hover:bg-muted",
                )}
              >
                <button
                  type="button"
                  onClick={() => void openThread(threadId)}
                  className={cn(
                    "flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2.5 py-2 text-left",
                    active ? "text-accent-foreground" : "text-foreground",
                  )}
                  title={title}
                >
                  <span
                    className={cn(
                      "size-1.5 shrink-0 rounded-full",
                      active ? "bg-primary" : "bg-muted-foreground/40",
                    )}
                    aria-hidden
                  />
                  <span className="truncate text-sm">{title}</span>
                </button>
                <button
                  type="button"
                  aria-label="删除会话"
                  title="删除会话"
                  onClick={() => void removeThread(threadId)}
                  className="text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 mr-1 shrink-0 rounded p-1 transition-opacity"
                >
                  <Trash2Icon className="size-3.5" />
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
