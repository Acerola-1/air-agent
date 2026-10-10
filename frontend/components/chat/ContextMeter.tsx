"use client";

import { useMemo } from "react";
import { CpuIcon, GaugeIcon, InfoIcon } from "lucide-react";
import { useChatStore } from "@/lib/chat-store";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const MAX_CONTEXT_TOKENS = 192_000;
const PRUNE_BUDGET_TOKENS = 120_000;

export function ContextMeter({ className }: { className?: string }) {
  const totalTokens = useChatStore((s) => s.totalContextTokens);
  const messages = useChatStore((s) => s.messages);

  // 获取最近一条有 usage 记录的 assistant 消息
  const lastUsage = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].usage) return messages[i].usage;
    }
    return null;
  }, [messages]);

  const percentage = Math.min(100, Math.max(0.1, (totalTokens / MAX_CONTEXT_TOKENS) * 100));
  const isNearBudget = totalTokens >= PRUNE_BUDGET_TOKENS;

  // 格式化千位数显示
  const formattedTokens =
    totalTokens >= 1000 ? `${(totalTokens / 1000).toFixed(1)}k` : `${totalTokens}`;
  const formattedMax = `${MAX_CONTEXT_TOKENS / 1000}k`;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            className={cn(
              "group inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-all select-none cursor-pointer",
              isNearBudget
                ? "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                : "border-border/60 bg-muted/40 text-muted-foreground hover:bg-muted/80 hover:text-foreground",
              className
            )}
            role="status"
            aria-label={`当前上下文使用 ${formattedTokens} / ${formattedMax} Tokens`}
          >
            <GaugeIcon
              className={cn(
                "size-3 shrink-0 transition-transform group-hover:rotate-12",
                isNearBudget ? "text-amber-500" : "text-emerald-500"
              )}
            />
            <span className="tabular-nums font-mono">
              {formattedTokens} / {formattedMax}
            </span>
            <span className="text-[10px] opacity-75">({percentage.toFixed(1)}%)</span>
          </div>
        </TooltipTrigger>
        <TooltipContent
          side="top"
          align="end"
          className="w-64 p-3 bg-popover text-popover-foreground border shadow-xl rounded-xl space-y-2 text-xs"
        >
          <div className="flex items-center justify-between border-b pb-1.5 font-semibold">
            <span className="flex items-center gap-1.5">
              <CpuIcon className="size-3.5 text-primary" /> 上下文窗口监控
            </span>
            <span className="text-[11px] text-muted-foreground font-mono">HY3 192K</span>
          </div>

          <div className="space-y-1.5 font-mono text-[11px]">
            <div className="flex justify-between text-muted-foreground">
              <span>当前会话估算 Token:</span>
              <span className="font-semibold text-foreground">{totalTokens.toLocaleString()}</span>
            </div>
            {lastUsage && (
              <>
                <div className="flex justify-between text-muted-foreground/80">
                  <span>· 最近输入 (Prompt):</span>
                  <span>{lastUsage.inputTokens.toLocaleString()}</span>
                </div>
                <div className="flex justify-between text-muted-foreground/80">
                  <span>· 最近生成 (Output):</span>
                  <span>{lastUsage.outputTokens.toLocaleString()}</span>
                </div>
              </>
            )}
            <div className="flex justify-between text-muted-foreground">
              <span>历史修剪安全线:</span>
              <span>{PRUNE_BUDGET_TOKENS.toLocaleString()} Tokens</span>
            </div>
            <div className="flex justify-between text-muted-foreground">
              <span>物理最大窗口:</span>
              <span>{MAX_CONTEXT_TOKENS.toLocaleString()} Tokens</span>
            </div>
          </div>

          {/* 进度条 */}
          <div className="space-y-1 pt-1">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  "h-full rounded-full transition-all duration-500",
                  isNearBudget ? "bg-amber-500" : "bg-emerald-500"
                )}
                style={{ width: `${percentage}%` }}
              />
            </div>
            <p className="text-[10px] text-muted-foreground/80 flex items-center gap-1">
              <InfoIcon className="size-3 inline shrink-0" />
              {isNearBudget
                ? "接近 120K 安全预算，将自动修剪历史旧工具数据"
                : "当前会话容量充裕，历史数据完好保留"}
            </p>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
