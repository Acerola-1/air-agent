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
import type { CapabilityGroup } from "@/lib/operations/types";

type GroupBy = "business" | "skill" | "tool";

export default function OperationsCapabilitiesPage() {
  const [preset, setPreset] = useState<WindowPreset>("last_7_days");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [businessCode, setBusinessCode] = useState("");
  const [groupBy, setGroupBy] = useState<GroupBy>("business");
  const [cursor, setCursor] = useState("");
  const [detail, setDetail] = useState<{
    kind: "skill_span" | "tool_call";
    filters: { skill_id?: string; service_key?: string; tool_id?: string };
  } | null>(null);

  const load = useCallback(
    async (signal: AbortSignal) => {
      const window = windowQuery(preset, startDate, endDate);
      const [list, details] = await Promise.all([
        operationsApi.capabilities(
          {
            ...window,
            business_code: businessCode || undefined,
            group_by: groupBy,
            cursor: cursor || undefined,
            limit: 50,
          },
          signal
        ),
        detail
          ? operationsApi.capabilityDetails(
              {
                ...window,
                kind: detail.kind,
                business_code: businessCode || undefined,
                ...detail.filters,
                limit: 50,
              },
              signal
            )
          : Promise.resolve(null),
      ]);
      return { list, details };
    },
    [preset, startDate, endDate, businessCode, groupBy, cursor, detail]
  );
  const { data, loading, error, reload } = useOperationQuery(load, [
    preset,
    startDate,
    endDate,
    businessCode,
    groupBy,
    cursor,
    detail,
  ]);

  const businessOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const item of data?.list.items ?? []) {
      if (groupBy === "business" && typeof item.group === "string") {
        seen.set(item.group, GRAPH_LABELS[item.group] ?? item.group);
      }
    }
    return Array.from(seen, ([value, label]) => ({ value, label }));
  }, [data?.list.items, groupBy]);

  const columns: Column<CapabilityGroup>[] =
    groupBy === "business"
      ? [
          {
            key: "group",
            label: "业务",
            render: (row) => GRAPH_LABELS[row.group] ?? row.group,
          },
          { key: "attempts", label: "尝试", render: (row) => formatNumber(row.attempts) },
          { key: "succeeded", label: "成功", render: (row) => formatNumber(row.succeeded) },
          { key: "failed", label: "失败", render: (row) => formatNumber(row.failed) },
          { key: "cancelled", label: "取消", render: (row) => formatNumber(row.cancelled) },
          { key: "timed_out", label: "超时", render: (row) => formatNumber(row.timed_out) },
          {
            key: "users",
            label: "去重用户",
            render: (row) => formatNumber(row.distinct_users),
          },
        ]
      : groupBy === "skill"
        ? [
            { key: "group", label: "Skill" },
            { key: "attempts", label: "尝试", render: (row) => formatNumber(row.attempts) },
            {
              key: "matched",
              label: "匹配",
              render: (row) => formatNumber(row.matched_spans),
            },
            {
              key: "selected",
              label: "选中",
              render: (row) => formatNumber(row.selected_spans),
            },
            {
              key: "loaded",
              label: "加载（成功/失败）",
              render: (row) =>
                `${formatNumber(row.loaded_succeeded)} / ${formatNumber(row.loaded_failed)}`,
            },
            {
              key: "executed",
              label: "执行",
              render: (row) => formatNumber(row.executed_spans),
            },
            {
              key: "not_executed",
              label: "未执行",
              render: (row) => formatNumber(row.not_executed),
            },
            {
              key: "p95",
              label: "P95 (ms)",
              render: (row) => formatNumber(row.duration_p95_ms),
            },
            {
              key: "detail",
              label: "明细",
              render: (row) => (
                <button
                  className="underline"
                  onClick={() =>
                    setDetail({
                      kind: "skill_span",
                      filters: { skill_id: String(row.group) },
                    })
                  }
                >
                  查看
                </button>
              ),
            },
          ]
        : [
            { key: "group", label: "服务:工具" },
            {
              key: "logical",
              label: "逻辑调用",
              render: (row) => formatNumber(row.logical_calls),
            },
            {
              key: "attempts",
              label: "尝试记录",
              render: (row) => formatNumber(row.attempt_records),
            },
            {
              key: "network",
              label: "网络尝试",
              render: (row) => formatNumber(row.network_attempts),
            },
            {
              key: "auth",
              label: "授权拒绝",
              render: (row) => formatNumber(row.permission_denied),
            },
            {
              key: "validation",
              label: "校验拒绝",
              render: (row) => formatNumber(row.validation_rejected),
            },
            {
              key: "technical",
              label: "传输失败",
              render: (row) => formatNumber(row.technical_error),
            },
            {
              key: "business",
              label: "业务空结果",
              render: (row) => formatNumber(row.business_empty),
            },
            {
              key: "cancelled",
              label: "取消/超时",
              render: (row) =>
                `${formatNumber(row.cancelled)} / ${formatNumber(row.timed_out)}`,
            },
            {
              key: "p95",
              label: "P95 (ms)",
              render: (row) => formatNumber(row.duration_p95_ms),
            },
            {
              key: "detail",
              label: "明细",
              render: (row) => {
                const raw = String(row.group);
                const separator = raw.indexOf(":");
                const serviceKey = separator >= 0 ? raw.slice(0, separator) : undefined;
                const toolId = separator >= 0 ? raw.slice(separator + 1) : raw;
                return (
                  <button
                    className="underline"
                    onClick={() =>
                      setDetail({
                        kind: "tool_call",
                        filters: { service_key: serviceKey, tool_id: toolId },
                      })
                    }
                  >
                    查看
                  </button>
                );
              },
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
              setDetail(null);
            }}
          >
            <option value="business">业务</option>
            <option value="skill">Skill 阶段</option>
            <option value="tool">工具调用</option>
          </select>
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

      <p className="text-muted-foreground text-xs">
        Skill 的匹配/选中/加载/执行分列，内部专员与阶段不会成为新的提问；工具逻辑调用与重试尝试分开计数。
      </p>

      <DataTable
        caption="窗口内全量聚合（不是首屏前 200 项相加）"
        columns={columns}
        rows={data?.list.items ?? []}
        rowKey={(row) => `${row.group_by}:${row.group}`}
        loading={loading}
      />

      <div className="flex items-center justify-between">
        <span className="text-muted-foreground text-xs">
          本页 {data?.list.items.length ?? 0} 条
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

      {detail && (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">
              {detail.kind === "skill_span" ? "Skill 阶段明细" : "工具调用明细"}
            </h3>
            <button className="rounded border px-2 py-1 text-xs" onClick={() => setDetail(null)}>
              收起
            </button>
          </div>
          <DataTable
            caption="按时间倒序；本页最多 50 条；授权事实边界与企业上下文每次重查"
            columns={
              detail.kind === "skill_span"
                ? [
                    { key: "started_at", label: "时间" },
                    { key: "stage", label: "阶段" },
                    { key: "status", label: "状态" },
                    { key: "skill_id", label: "Skill" },
                    { key: "duration_ms", label: "耗时 (ms)" },
                    { key: "attempt_id", label: "尝试" },
                  ]
                : [
                    { key: "started_at", label: "时间" },
                    { key: "service_key", label: "服务" },
                    { key: "tool_name", label: "工具" },
                    { key: "retry_index", label: "重试" },
                    { key: "status", label: "状态" },
                    { key: "duration_ms", label: "耗时 (ms)" },
                    { key: "network_attempted", label: "网络" },
                    { key: "attempt_id", label: "尝试" },
                  ]
            }
            rows={(data?.details?.items ?? []) as Array<Record<string, unknown>>}
            rowKey={(row) => String(row.id)}
            empty="没有明细"
            loading={loading}
          />
        </section>
      )}
    </div>
  );
}
