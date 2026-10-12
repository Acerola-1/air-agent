"use client";

import { GaugeIcon } from "lucide-react";
import { useChatStore } from "@/lib/chat-store";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function ContextMeter({ className }: { className?: string }) {
  const metrics = useChatStore((state) => state.contextMetrics);
  const actual = metrics?.latest_usage?.input_tokens;
  const hasActual = typeof actual === "number" && Number.isFinite(actual) && actual >= 0;
  const displayed = hasActual ? actual : metrics?.input_tokens ?? 0;
  const estimated = metrics && !hasActual && !metrics.measurement_source.startsWith("tokenizer:");
  const near = metrics && displayed >= metrics.input_budget_tokens * 0.9;
  const ratio = metrics ? Math.min(100, displayed / metrics.input_budget_tokens * 100) : 0;
  const format = (count: number) => count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
  return (
    <TooltipProvider><Tooltip><TooltipTrigger asChild>
      <button type="button" className={cn("inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring", near ? "border-amber-500/40 text-amber-600 dark:text-amber-400" : "border-border/60 text-muted-foreground", className)}
        aria-label={metrics ? `${hasActual ? "最近主模型实际输入" : estimated ? "最近准备的模型输入估算" : "最近准备的模型输入"} ${displayed}，可用容量 ${metrics.input_budget_tokens}` : "等待后端提供上下文用量"}>
        <GaugeIcon className="size-3" />
        <span className="font-mono tabular-nums">{metrics ? `${estimated ? "≈ " : ""}${format(displayed)} / ${format(metrics.input_budget_tokens)}` : "用量待更新"}</span>
      </button>
    </TooltipTrigger><TooltipContent side="top" align="end" className="w-64 space-y-2 p-3 text-xs">
      <p className="font-semibold">{hasActual ? "最近主模型实际输入" : "最近准备的模型输入"}</p>
      {metrics ? <>
        <p className="text-muted-foreground">{metrics.model} · {metrics.measurement_source.startsWith("tokenizer:") ? "模型分词计量" : "保守估算"}</p>
        <dl className="grid grid-cols-2 gap-1 tabular-nums">
          <dt>{metrics.measurement_source.startsWith("tokenizer:") ? "请求分词计量" : "请求输入估算"}</dt><dd className="text-right">{metrics.input_tokens.toLocaleString()}</dd>
          {metrics.input_breakdown_tokens && <>
            <dt>系统规则</dt><dd className="text-right">{metrics.input_breakdown_tokens.system.toLocaleString()}</dd>
            <dt>聊天与工具结果</dt><dd className="text-right">{metrics.input_breakdown_tokens.conversation.toLocaleString()}</dd>
            <dt>工具定义{metrics.tool_count !== undefined ? `（${metrics.tool_count} 个）` : ""}</dt><dd className="text-right">{metrics.input_breakdown_tokens.tools.toLocaleString()}</dd>
            <dt>请求开销</dt><dd className="text-right">{metrics.input_breakdown_tokens.framing.toLocaleString()}</dd>
          </>}
          <dt>可用输入容量</dt><dd className="text-right">{metrics.input_budget_tokens.toLocaleString()}</dd>
          <dt>模型窗口</dt><dd className="text-right">{metrics.window_tokens.toLocaleString()}</dd>
          <dt>生成预留</dt><dd className="text-right">{metrics.output_reserved_tokens.toLocaleString()}</dd>
          {metrics.latest_usage && <><dt>最近主模型输入</dt><dd className="text-right">{metrics.latest_usage.input_tokens?.toLocaleString() ?? "—"}</dd><dt>最近主模型生成</dt><dd className="text-right">{metrics.latest_usage.output_tokens?.toLocaleString() ?? "—"}</dd></>}
        </dl>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className={cn("h-full", near ? "bg-amber-500" : "bg-emerald-500")} style={{ width: `${ratio}%` }} /></div>
        <p className="text-muted-foreground">输入包含系统规则和工具定义，不只是聊天文字。回答完成后优先显示提供商实际输入；准备阶段显示带余量的估算，拆分也为估算。</p>
      </> : <p className="text-muted-foreground">模型准备好本次请求后显示容量。历史记录仍可查看。</p>}
    </TooltipContent></Tooltip></TooltipProvider>
  );
}
