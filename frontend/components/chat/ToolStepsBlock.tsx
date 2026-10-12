"use client";

import { useState } from "react";
import {
  CheckCircle2Icon,
  ChevronDownIcon,
  CopyIcon,
  DatabaseIcon,
  Loader2Icon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { type ToolExecution } from "@/lib/chat-store";
import { ArchiveReader } from "@/components/chat/ArchiveReader";
import { cn } from "@/lib/utils";

interface ToolStepsBlockProps {
  tools: ToolExecution[];
  className?: string;
}

export function ToolStepsBlock({ tools, className }: { tools: ToolExecution[]; className?: string }) {
  // 顶层工具卡片默认始终折叠，保持页面极简干净
  const [isMainOpen, setIsMainOpen] = useState(false);
  // 单个子工具的展开状态
  const [expandedSubId, setExpandedSubId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  if (!tools || tools.length === 0) return null;

  const runningCount = tools.filter((t) => t.status === "running" || t.status === "pending").length;
  const errorCount = tools.filter((t) => t.status === "error").length;
  const isAllCompleted = runningCount === 0 && errorCount === 0;

  const toolNames = Array.from(new Set(tools.map((t) => t.name))).join(", ");

  const copyResult = (id: string, text: string) => {
    void navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <div
      className={cn(
        "my-2 overflow-hidden rounded-xl border border-border/60 bg-muted/20 text-xs transition-all",
        className
      )}
    >
      {/* 聚合头部：仅占一行高度 */}
      <button
        type="button"
        onClick={() => setIsMainOpen((prev) => !prev)}
        className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-muted-foreground hover:bg-muted/40 hover:text-foreground transition-colors focus-visible:outline-2 focus-visible:outline-ring"
        aria-expanded={isMainOpen}
      >
        <span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
          <WrenchIcon className="size-3" />
        </span>

        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span className="font-medium text-foreground truncate">
            已执行 {tools.length} 个工具操作
          </span>
          <span className="text-[11px] text-muted-foreground/80 truncate hidden sm:inline">
            ({toolNames})
          </span>
        </div>

        {/* 状态徽标 */}
        <div className="flex items-center gap-2 shrink-0">
          {runningCount > 0 ? (
            <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium text-[11px]">
              <Loader2Icon className="size-3 animate-spin" />
              执行中
            </span>
          ) : isAllCompleted ? (
            <span className="flex items-center gap-1 text-muted-foreground font-medium text-[11px]">
              <CheckCircle2Icon className="size-3 text-emerald-500" />
              已完成
            </span>
          ) : (
            <span className="flex items-center gap-1 text-destructive font-medium text-[11px]">
              <XCircleIcon className="size-3" />
              含异常
            </span>
          )}

          <ChevronDownIcon
            className={cn("size-3.5 transition-transform duration-200 text-muted-foreground", isMainOpen && "rotate-180")}
          />
        </div>
      </button>

      {errorCount > 0 && !isMainOpen && <p role="status" className="px-3 pb-2 text-destructive">{tools.find((tool) => tool.status === "error")?.errorMessage || "查询未完成，请调整条件或稍后重试。"}</p>}
      {/* 展开的工具列表 */}
      {isMainOpen && (
        <div className="divide-y divide-border/40 border-t border-border/50 bg-background/50">
          {tools.map((tool, idx) => {
            const isSubOpen = expandedSubId === tool.id;
            const hasArtifact = Boolean(tool.artifactId);

            return (
              <div key={`${tool.id}-${idx}`} className="p-2.5 space-y-2">
                <button type="button" aria-expanded={isSubOpen}
                  className="flex w-full items-center justify-between cursor-pointer select-none text-left hover:bg-muted/40 focus-visible:outline-2 focus-visible:outline-ring"
                  onClick={() => setExpandedSubId(isSubOpen ? null : tool.id)}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-mono font-semibold text-foreground truncate">
                      {tool.name}
                    </span>
                    {hasArtifact && (
                      <span className="inline-flex items-center gap-1 rounded bg-indigo-500/10 px-1.5 py-0.5 text-[10px] font-medium text-indigo-500 dark:text-indigo-400">
                        <DatabaseIcon className="size-2.5" /> 已归档
                      </span>
                    )}
                  </div>

                  <span className="text-[11px] text-muted-foreground/70 flex items-center gap-1">
                    {isSubOpen ? "收起详情" : "查看详情"}
                    <ChevronDownIcon
                      className={cn("size-3 transition-transform", isSubOpen && "rotate-180")}
                    />
                  </span>
                </button>
                {tool.status === "error" && <p role="status" className="text-destructive">{tool.errorMessage || "查询未完成，请调整条件或稍后重试。"}</p>}

                {isSubOpen && (
                  <div className="space-y-2 pt-1 font-mono text-[11px]">
                    {tool.artifactId && <ArchiveReader artifactId={tool.artifactId} />}
                    {/* 入参 */}
                    <div className="rounded-lg bg-muted/40 p-2 border border-border/40">
                      <div className="text-[10px] font-sans font-semibold text-muted-foreground mb-1">
                        查询参数：
                      </div>
                      <pre className="overflow-x-auto text-muted-foreground whitespace-pre-wrap break-all">
                        {typeof tool.args === "string"
                          ? tool.args
                          : JSON.stringify(tool.args ?? {}, null, 2)}
                      </pre>
                    </div>

                    {/* 返回结果 */}
                    {tool.result && (
                      <div className="rounded-lg bg-muted/40 p-2 border border-border/40 relative">
                        <div className="flex items-center justify-between text-[10px] font-sans font-semibold text-muted-foreground mb-1">
                          <span>诊断／返回详情：</span>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              copyResult(tool.id, tool.result || "");
                            }}
                            className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground text-[10px]"
                          >
                            <CopyIcon className="size-2.5" />
                            {copiedId === tool.id ? "已复制" : "复制"}
                          </button>
                        </div>
                        <pre className="max-h-56 overflow-auto text-muted-foreground whitespace-pre-wrap break-all">
                          {tool.result}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
