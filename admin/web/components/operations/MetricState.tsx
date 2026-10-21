"use client";

import { cn } from "@/lib/utils";
import {
  formatNumber,
  formatPercent,
  metricStatusLabel,
  metricTitle,
  metricValue,
} from "@/lib/operations/query";
import type { Metric } from "@/lib/operations/types";

type Props = {
  label: string;
  metric?: Metric | null;
  format?: "number" | "percent" | "text";
  hint?: string;
  tone?: "default" | "danger" | "warning";
};

/** 指标卡：真实 0 显示 0；缺失/部分/未知与 0 明确区分。 */
export function MetricState({ label, metric, format = "number", hint, tone }: Props) {
  const formatter =
    format === "percent" ? formatPercent : format === "text" ? String : formatNumber;
  const value = metricValue(metric, formatter);
  const status = metricStatusLabel(metric);
  const missing = metric?.status !== "measured";
  return (
    <div
      className={cn(
        "bg-card min-w-0 rounded-lg border p-3",
        tone === "danger" && "border-destructive/40",
        tone === "warning" && "border-amber-500/50"
      )}
      title={metricTitle(metric)}
    >
      <div className="text-muted-foreground truncate text-xs">{label}</div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-2xl font-semibold tabular-nums">{value}</span>
        {missing && (
          <span className="text-muted-foreground text-[11px]">{status}</span>
        )}
      </div>
      {hint && <div className="text-muted-foreground mt-0.5 text-[11px]">{hint}</div>}
      {metric?.missing_count ? (
        <div className="text-muted-foreground mt-0.5 text-[11px]">
          缺失 {metric.missing_count}
        </div>
      ) : null}
    </div>
  );
}

export function CurrencySummary({
  byCurrency,
  className,
}: {
  byCurrency: Record<string, string> | undefined;
  className?: string;
}) {
  const entries = Object.entries(byCurrency ?? {});
  if (!entries.length) {
    return <span className="text-muted-foreground text-sm">无已计价费用</span>;
  }
  return (
    <div className={cn("flex flex-wrap gap-2", className)}>
      {entries.map(([currency, amount]) => (
        <span
          key={currency}
          className="bg-muted inline-flex items-baseline gap-1 rounded px-2 py-1 text-sm tabular-nums"
        >
          <span className="text-muted-foreground text-xs">{currency}</span>
          {amount}
        </span>
      ))}
    </div>
  );
}
