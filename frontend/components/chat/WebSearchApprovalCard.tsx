"use client";

import { useEffect, useRef } from "react";
import { Globe2Icon, SearchIcon, SquareIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { WebSearchApproval } from "@/lib/chat-store";

/** 输入区上方的联网审批卡片。
 *
 * 不做全屏模态：只占输入框上方一条，保留对话上下文可见；页脚只有批准/禁止，
 * 右上角小图标用于结束本次运行（按拒绝收尾）。Escape 等同禁止。
 */
export function WebSearchApprovalCard({
  approval,
  busy,
  onResolve,
  onStop,
}: {
  approval: WebSearchApproval;
  busy: boolean;
  onResolve: (approved: boolean) => void;
  onStop: () => void;
}) {
  const denyRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!busy) denyRef.current?.focus();
  }, [busy]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onResolve(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onResolve]);

  return (
    <section
      role="group"
      aria-label="联网搜索审批"
      aria-live="polite"
      className="animate-in fade-in-0 slide-in-from-bottom-2 duration-200 rounded-xl border border-emerald-500/25 bg-background/95 p-3 shadow-lg shadow-emerald-950/5 ring-1 ring-emerald-500/10"
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
          <Globe2Icon className="size-4" />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h2 className="text-sm font-semibold tracking-tight text-foreground">
              请求联网搜索
            </h2>
            <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              {approval.provider}
            </span>
            <span className="rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
              本轮剩余 {Math.max(0, approval.remaining)} 次
            </span>
            <button
              type="button"
              aria-label="停止本次运行"
              title="停止本次运行（不联网，按拒绝处理）"
              disabled={busy}
              onClick={onStop}
              className="ml-auto inline-flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
            >
              <SquareIcon className="size-3 fill-current" />
            </button>
          </div>

          <div className="mt-2 flex items-start gap-1.5 rounded-md bg-muted/40 px-2 py-1.5">
            <SearchIcon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <p className="line-clamp-2 break-words font-mono text-[12px] leading-5 text-foreground">
              {approval.query || "（未提供查询词）"}
            </p>
          </div>

          {approval.reason ? (
            <p className="mt-1.5 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
              {approval.reason}
            </p>
          ) : null}

          <div className="mt-2 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-[10px] leading-relaxed text-muted-foreground">
              批准后查询词会发送给 {approval.provider}；不批准则基于现有数据回答。
            </p>
            <div className="flex shrink-0 justify-end gap-2">
              <Button
                ref={denyRef}
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => onResolve(false)}
              >
                禁止
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={busy}
                onClick={() => onResolve(true)}
              >
                批准
              </Button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
