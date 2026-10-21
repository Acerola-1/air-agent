"use client";

import { useCallback, useMemo, useState } from "react";

import { DataTable, type Column } from "@/components/operations/DataTable";
import { QueryToolbar } from "@/components/operations/QueryToolbar";
import { operationsApi } from "@/lib/operations/client";
import { useOperationQuery } from "@/lib/operations/hooks";
import {
  formatNumber,
  GRAPH_LABELS,
  type WindowPreset,
  windowQuery,
} from "@/lib/operations/query";
import { useSession } from "@/lib/session";
import type { CostsResponse } from "@/lib/operations/types";

type GroupBy = "model" | "user" | "business" | "currency";

export default function OperationsCostsPage() {
  const { can } = useSession();
  const canIdentifyUsers = can("agent:operations:read");
  const [preset, setPreset] = useState<WindowPreset>("last_7_days");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [businessCode, setBusinessCode] = useState("");
  const [model, setModel] = useState("");
  const [provider, setProvider] = useState("");
  const [groupBy, setGroupBy] = useState<GroupBy>("model");
  const [cursor, setCursor] = useState("");

  const load = useCallback(
    async (signal: AbortSignal) => {
      const window = windowQuery(preset, startDate, endDate);
      const [costs, capabilities] = await Promise.all([
        operationsApi.costs(
          {
            ...window,
            business_code: businessCode || undefined,
            model: model || undefined,
            provider: provider || undefined,
            group_by: groupBy,
            cursor: cursor || undefined,
            limit: 50,
          },
          signal
        ),
        operationsApi.capabilities(
          { ...window, group_by: "business", limit: 50 },
          signal
        ),
      ]);
      return { costs, capabilities };
    },
    [preset, startDate, endDate, businessCode, model, provider, groupBy, cursor]
  );
  const { data, loading, error, reload } = useOperationQuery(load, [
    preset,
    startDate,
    endDate,
    businessCode,
    model,
    provider,
    groupBy,
    cursor,
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

  const columns: Column<CostsResponse["items"][number]>[] = [
    {
      key: "group",
      label:
        groupBy === "model"
          ? "模型"
          : groupBy === "user"
            ? "用户 ID"
            : groupBy === "business"
              ? "业务"
              : "币种",
      render: (row) =>
        groupBy === "business" ? GRAPH_LABELS[row.group] ?? row.group : row.group,
    },
    { key: "currency", label: "币种" },
    {
      key: "subtotal",
      label: "小计（Decimal）",
      render: (row) => row.subtotal ?? "未计价",
    },
    {
      key: "priced",
      label: "已计价调用",
      render: (row) => formatNumber(row.priced_calls),
    },
    {
      key: "unknown",
      label: "未知/未计价",
      render: (row) => (row.unknown_calls ? formatNumber(row.unknown_calls) : "0"),
    },
  ];

  return (
    <div className="space-y-4">
      <QueryToolbar
        preset={preset}
        onPreset={(value) => {
          setPreset(value);
          setCursor("");
        }}
        startDate={startDate}
        endDate={endDate}
        onStartDate={setStartDate}
        onEndDate={setEndDate}
        businessCode={businessCode}
        onBusinessCode={(value) => {
          setBusinessCode(value);
          setCursor("");
        }}
        businessOptions={businessOptions}
        onRefresh={reload}
        loading={loading}
      >
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">分组</span>
          <select
            className="bg-background h-8 rounded border px-2 text-sm"
            value={groupBy}
            onChange={(event) => {
              setGroupBy(event.target.value as GroupBy);
              setCursor("");
            }}
          >
            <option value="model">模型</option>
            <option value="business">业务</option>
            <option value="currency">币种</option>
            <option value="user" disabled={!canIdentifyUsers}>
              用户{canIdentifyUsers ? "" : "（需运营权限）"}
            </option>
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">模型</span>
          <input
            className="bg-background h-8 w-40 rounded border px-2 text-sm"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder="model"
          />
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">提供方</span>
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

      <section className="bg-card flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3">
        <div>
          <div className="text-muted-foreground text-xs">费用小计（只计最新计费修订）</div>
          <p className="mt-1 text-xs">
            按币种和分组的小计见下表；服务端以 Decimal 聚合，浏览器不做求和。
          </p>
        </div>
        <div className="text-muted-foreground text-xs">
          {data?.costs.totals.complete_cost_known
            ? "全部调用 usage 与价格已知"
            : `未计价 ${data?.costs.totals.unpriced_calls ?? "—"} · usage 不完整 ${data?.costs.totals.usage_incomplete_calls ?? "—"}`}
        </div>
      </section>

      {data?.costs.meta.limitations.map((item) => (
        <p key={item} className="text-muted-foreground text-xs">
          · {item}
        </p>
      ))}

      <DataTable
        caption="多币种分开、未知不补零；共享 Skill 费用不重复分摊"
        columns={columns}
        rows={data?.costs.items ?? []}
        rowKey={(row) => `${row.group}:${row.currency}`}
        loading={loading}
      />

      <div className="flex items-center justify-between">
        <span className="text-muted-foreground text-xs">
          本页 {data?.costs.items.length ?? 0} 条
        </span>
        <div className="flex gap-2">
          <button
            className="rounded border px-2 py-1 text-xs disabled:opacity-50"
            disabled={!cursor}
            onClick={() => setCursor("")}
          >
            回到第一页
          </button>
          <button
            className="rounded border px-2 py-1 text-xs disabled:opacity-50"
            disabled={!data?.costs.has_more || !data.costs.next_cursor}
            onClick={() => setCursor(data?.costs.next_cursor ?? "")}
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  );
}
