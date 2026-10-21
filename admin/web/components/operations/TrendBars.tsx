"use client";

import { formatNumber } from "@/lib/operations/query";

type Point = { label: string; value: number | null };

/** 简单可访问柱状趋势；图形之外始终提供数据表替代。 */
export function TrendBars({
  title,
  points,
  unit = "",
}: {
  title: string;
  points: Point[];
  unit?: string;
}) {
  const max = Math.max(1, ...points.map((point) => point.value ?? 0));
  return (
    <figure className="space-y-2" aria-label={title}>
      <figcaption className="text-sm font-medium">{title}</figcaption>
      <div className="flex h-32 items-end gap-1 overflow-x-auto pb-1">
        {points.map((point) => {
          const height = point.value === null ? 0 : Math.max(2, (point.value / max) * 100);
          return (
            <div
              key={point.label}
              className="flex min-w-8 flex-1 flex-col items-center justify-end gap-1"
              title={`${point.label}: ${point.value ?? "缺失"}${unit}`}
            >
              <span className="text-muted-foreground text-[10px] tabular-nums">
                {point.value === null ? "—" : formatNumber(point.value)}
              </span>
              <div
                className="bg-primary/70 w-full rounded-t"
                style={{ height: `${height}%` }}
                aria-hidden="true"
              />
              <span className="text-muted-foreground text-[10px]">{point.label.slice(5)}</span>
            </div>
          );
        })}
      </div>
      <details className="text-xs">
        <summary className="text-muted-foreground cursor-pointer">数据表</summary>
        <div className="mt-1 overflow-x-auto">
          <table className="w-full text-left">
            <caption className="sr-only">{title}数据表</caption>
            <thead>
              <tr className="border-b">
                <th scope="col" className="py-1 pr-2 font-medium">
                  日期
                </th>
                <th scope="col" className="py-1 font-medium">
                  数值
                </th>
              </tr>
            </thead>
            <tbody>
              {points.map((point) => (
                <tr key={point.label} className="border-b last:border-0">
                  <td className="py-1 pr-2">{point.label}</td>
                  <td className="py-1 tabular-nums">
                    {point.value === null ? "缺失" : `${formatNumber(point.value)}${unit}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
