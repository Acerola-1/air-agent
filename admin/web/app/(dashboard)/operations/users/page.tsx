"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { DataTable, type Column } from "@/components/operations/DataTable";
import { DebouncedInput, QueryToolbar } from "@/components/operations/QueryToolbar";
import { operationsApi } from "@/lib/operations/client";
import { useOperationQuery } from "@/lib/operations/hooks";
import {
  formatDateTime,
  formatNumber,
  GRAPH_LABELS,
  shortId,
  type WindowPreset,
  windowQuery,
} from "@/lib/operations/query";
import type { CapabilityGroup, UserUsage } from "@/lib/operations/types";

export default function OperationsUsersPage() {
  const [preset, setPreset] = useState<WindowPreset>("last_7_days");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [businessCode, setBusinessCode] = useState("");
  const [userId, setUserId] = useState("");
  const [cursor, setCursor] = useState("");

  const load = useCallback(
    async (signal: AbortSignal) => {
      const window = windowQuery(preset, startDate, endDate);
      const [list, capabilities] = await Promise.all([
        operationsApi.users(
          {
            ...window,
            business_code: businessCode || undefined,
            user_id: userId || undefined,
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
      return { list, capabilities };
    },
    [preset, startDate, endDate, businessCode, userId, cursor]
  );
  const { data, loading, error, reload } = useOperationQuery(load, [
    preset,
    startDate,
    endDate,
    businessCode,
    userId,
    cursor,
  ]);

  const businessOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const item of (data?.capabilities.items ?? []) as CapabilityGroup[]) {
      if (typeof item.group === "string" && item.group) {
        seen.set(item.group, GRAPH_LABELS[item.group] ?? item.group);
      }
    }
    return Array.from(seen, ([value, label]) => ({ value, label }));
  }, [data?.capabilities.items]);

  const columns: Column<UserUsage>[] = [
    {
      key: "user",
      label: "用户",
      render: (row) => (
        <div className="min-w-40">
          <div className="truncate">{row.display_name}</div>
          <div className="text-muted-foreground font-mono text-[11px]">
            {shortId(row.user_id, 12)}
          </div>
        </div>
      ),
    },
    { key: "requests", label: "提问", render: (row) => formatNumber(row.requests) },
    { key: "attempts", label: "尝试", render: (row) => formatNumber(row.attempts) },
    { key: "succeeded", label: "成功", render: (row) => formatNumber(row.succeeded) },
    {
      key: "unsuccessful",
      label: "失败/取消",
      render: (row) => formatNumber(row.unsuccessful),
    },
    { key: "model_calls", label: "模型调用", render: (row) => formatNumber(row.model_calls) },
    { key: "tokens", label: "Token", render: (row) => formatNumber(row.total_tokens) },
    {
      key: "cost",
      label: "费用（按币种）",
      render: (row) => {
        const entries = Object.entries(row.cost_by_currency ?? {});
        return entries.length
          ? entries.map(([currency, amount]) => `${currency} ${amount}`).join("；")
          : "—";
      },
    },
    {
      key: "unpriced",
      label: "未计价",
      render: (row) => (row.unpriced_cost_calls ? String(row.unpriced_cost_calls) : "0"),
    },
    {
      key: "last",
      label: "最近活跃",
      render: (row) => formatDateTime(row.last_active_at),
    },
    {
      key: "drill",
      label: "下钻",
      render: (row) => (
        <Link
          className="underline"
          href={`/operations/runs?user_id=${encodeURIComponent(row.user_id)}`}
        >
          运行
        </Link>
      ),
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
        onStartDate={(value) => {
          setStartDate(value);
          setCursor("");
        }}
        onEndDate={(value) => {
          setEndDate(value);
          setCursor("");
        }}
        businessCode={businessCode}
        onBusinessCode={(value) => {
          setBusinessCode(value);
          setCursor("");
        }}
        businessOptions={businessOptions}
        onRefresh={reload}
        loading={loading}
      >
        <DebouncedInput
          label="用户 ID（精确）"
          value={userId}
          placeholder="UUID"
          onValue={(value) => {
            setUserId(value.trim());
            setCursor("");
          }}
        />
      </QueryToolbar>

      {error && (
        <div className="text-destructive rounded border p-3 text-sm" role="alert">
          {error.message}
          <button className="underline" onClick={reload}>
            重试
          </button>
        </div>
      )}

      <DataTable
        caption="按用户聚合的提问、运行、Token 与费用；用户标识来自现有身份引用"
        columns={columns}
        rows={data?.list.items ?? []}
        rowKey={(row) => row.user_id}
        loading={loading}
      />

      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-muted-foreground text-xs">
          共 {data?.list.items.length ?? 0} 条本页 · 截止{" "}
          {formatDateTime(data?.list.meta.as_of)}
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
            disabled={!data?.list.has_more || !data.list.next_cursor}
            onClick={() => setCursor(data?.list.next_cursor ?? "")}
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  );
}
