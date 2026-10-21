"use client";

import { useCallback, useMemo, useState } from "react";

import { DataTable, type Column } from "@/components/operations/DataTable";
import { MetricState } from "@/components/operations/MetricState";
import { QueryToolbar } from "@/components/operations/QueryToolbar";
import { operationsApi } from "@/lib/operations/client";
import { useOperationQuery } from "@/lib/operations/hooks";
import {
  formatNumber,
  GRAPH_LABELS,
  type WindowPreset,
  windowQuery,
} from "@/lib/operations/query";
import type { Metric } from "@/lib/operations/types";

export default function OperationsPerformancePage() {
  const [preset, setPreset] = useState<WindowPreset>("last_7_days");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [businessCode, setBusinessCode] = useState("");
  const [model, setModel] = useState("");
  const [provider, setProvider] = useState("");
  const [source, setSource] = useState("all");

  const load = useCallback(
    async (signal: AbortSignal) => {
      const window = windowQuery(preset, startDate, endDate);
      const [performance, capabilities] = await Promise.all([
        operationsApi.performance(
          {
            ...window,
            business_code: businessCode || undefined,
            model: model || undefined,
            provider: provider || undefined,
            source,
          },
          signal
        ),
        operationsApi.capabilities(
          { ...window, group_by: "business", limit: 50 },
          signal
        ),
      ]);
      return { performance, capabilities };
    },
    [preset, startDate, endDate, businessCode, model, provider, source]
  );
  const { data, loading, error, reload } = useOperationQuery(load, [
    preset,
    startDate,
    endDate,
    businessCode,
    model,
    provider,
    source,
  ]);

  const businessOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const item of data?.capabilities.items ?? []) {
      if (typeof item.group === "string" && item.group) {
        seen.set(item.group, GRAPH_LABELS[item.group] ?? item.group);
      }
    }
    return Array.from(seen, ([value, label]) => ({ value, label }));
  }, [data?.capabilities.items]);

  const partitions = data?.performance.partitions ?? {};
  const modelPartition = partitions.model;
  const modelFiltered = Boolean(model || provider);

  const byModelColumns: Column<Record<string, string | number | null>>[] = [
    { key: "model", label: "模型" },
    { key: "provider", label: "提供方" },
    { key: "calls", label: "调用", render: (row) => formatNumber(row.calls as number) },
    {
      key: "ttft_samples",
      label: "TTFT 样本",
      render: (row) => formatNumber(row.ttft_samples as number),
    },
    {
      key: "p50_ms",
      label: "TTFT P50 (ms)",
      render: (row) => formatNumber(row.p50_ms as number),
    },
    {
      key: "p95_ms",
      label: "TTFT P95 (ms)",
      render: (row) => formatNumber(row.p95_ms as number),
    },
  ];

  return (
    <div className="space-y-4">
      <QueryToolbar
        preset={preset}
        onPreset={setPreset}
        startDate={startDate}
        endDate={endDate}
        onStartDate={setStartDate}
        onEndDate={setEndDate}
        businessCode={businessCode}
        onBusinessCode={setBusinessCode}
        businessOptions={businessOptions}
        onRefresh={reload}
        loading={loading}
      >
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">来源</span>
          <select
            className="bg-background h-8 rounded border px-2 text-sm"
            value={source}
            onChange={(event) => setSource(event.target.value)}
          >
            <option value="all">全部</option>
            <option value="service">服务</option>
            <option value="browser">浏览器</option>
            <option value="model">模型</option>
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">模型（仅模型分区）</span>
          <input
            className="bg-background h-8 w-40 rounded border px-2 text-sm"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="model"
          />
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">提供方（仅模型分区）</span>
          <input
            className="bg-background h-8 w-32 rounded border px-2 text-sm"
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
            placeholder="provider"
          />
        </label>
      </QueryToolbar>

      {error && (
        <div className="text-destructive rounded border p-3 text-sm" role="alert">
          {error.message}
          <button className="underline" onClick={reload}>
            重试
          </button>
        </div>
      )}

      {modelFiltered && (
        <p className="text-muted-foreground rounded border border-dashed p-2 text-xs">
          模型/提供方过滤只适用于模型分区；服务与浏览器分区标为不适用，不沿用筛选后假装同一队列。
        </p>
      )}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold">服务端耗时</h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricState label="P50" metric={partitions.service as Metric} />
          <MetricState
            label="P95"
            metric={
              partitions.service
                ? ({ ...partitions.service, value: partitions.service.p95_ms } as Metric)
                : null
            }
          />
          <MetricState
            label="P99"
            metric={
              partitions.service
                ? ({ ...partitions.service, value: partitions.service.p99_ms } as Metric)
                : null
            }
          />
          <MetricState
            label="排队等待 P95"
            metric={partitions.service?.queue_wait_ms as Metric | undefined}
          />
        </div>
        {partitions.service?.status === "not_applicable" && (
          <p className="text-muted-foreground text-xs">
            {partitions.service.reason ?? "不适用"}
          </p>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold">浏览器首可见正文</h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricState label="P50" metric={partitions.browser as Metric} />
          <MetricState
            label="P95"
            metric={
              partitions.browser
                ? ({ ...partitions.browser, value: partitions.browser.p95_ms } as Metric)
                : null
            }
          />
          <MetricState
            label="P99"
            metric={
              partitions.browser
                ? ({ ...partitions.browser, value: partitions.browser.p99_ms } as Metric)
                : null
            }
          />
          <MetricState
            label="样本"
            metric={
              partitions.browser
                ? ({
                    ...partitions.browser,
                    value: partitions.browser.sample_count ?? null,
                  } as Metric)
                : null
            }
          />
        </div>
        {partitions.browser?.status === "not_applicable" && (
          <p className="text-muted-foreground text-xs">
            {partitions.browser.reason ?? "不适用"}
          </p>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold">模型 TTFT</h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricState label="P50" metric={modelPartition as Metric} />
          <MetricState
            label="P95"
            metric={
              modelPartition
                ? ({ ...modelPartition, value: modelPartition.p95_ms } as Metric)
                : null
            }
          />
          <MetricState
            label="P99"
            metric={
              modelPartition
                ? ({ ...modelPartition, value: modelPartition.p99_ms } as Metric)
                : null
            }
          />
          <MetricState
            label="样本"
            metric={
              modelPartition
                ? ({ ...modelPartition, value: modelPartition.sample_count ?? null } as Metric)
                : null
            }
          />
        </div>
        {modelPartition?.status === "not_applicable" && (
          <p className="text-muted-foreground text-xs">
            {modelPartition.reason ?? "不适用"}
          </p>
        )}
        <DataTable
          caption="按模型/提供方分列；样本与缺失独立"
          columns={byModelColumns}
          rows={modelPartition?.by_model ?? []}
          rowKey={(row) => `${row.provider ?? ""}:${row.model ?? ""}`}
          empty="没有模型样本"
          loading={loading}
        />
        {modelPartition?.by_model_truncated && (
          <p className="text-muted-foreground text-xs">模型列表超过 20 项，当前为前 20 项。</p>
        )}
      </section>

      <p className="text-muted-foreground text-xs">
        服务/浏览器/模型是独立队列；人工等待单列；不以每日分位数平均或客户端首屏截断冒充全量。
      </p>
    </div>
  );
}
