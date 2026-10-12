"use client";
import { Loader2Icon } from "lucide-react";
import { type ProcessingStatus } from "@/lib/langgraph-api";
import { cn } from "@/lib/utils";

/** 历史整理／数据处理共用的公开状态，不接收内部摘要。 */
export function ProcessingNotice({ status }: { status: ProcessingStatus | null }) {
  if (!status) return null;
  const title = status.stage === "history" ? "历史整理" : "资料处理";
  return <div role="status" aria-live="polite" className={cn("rounded-lg border px-3 py-2 text-xs", status.status === "failed" ? "border-destructive/30 text-destructive" : "border-border/60 text-muted-foreground")}>
    <p className="flex items-center gap-1.5">{status.status === "started" && <Loader2Icon aria-hidden="true" className="size-3 animate-spin motion-reduce:animate-none" />}
      {title}{status.status === "started" ? "进行中…" : status.status === "completed" ? "已完成" : "未完成"}</p>
    {status.status === "completed" && status.input_before != null && status.input_after != null && <p className="mt-1 tabular-nums">有效输入：{status.input_before.toLocaleString()} → {status.input_after.toLocaleString()}；保留最近 {status.kept_turns} 轮。</p>}
    {status.status === "failed" && <p className="mt-1">{status.message || "尚未获得完整结果，请重试或缩小资料范围。"}</p>}
  </div>;
}
