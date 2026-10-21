"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { DataTable, type Column } from "@/components/operations/DataTable";
import { DebouncedInput, QueryToolbar } from "@/components/operations/QueryToolbar";
import { operationsApi } from "@/lib/operations/client";
import { useOperationQuery } from "@/lib/operations/hooks";
import {
  CONTENT_STATUS_LABELS,
  formatDateTime,
  GRAPH_LABELS,
  readUrlFilter,
  replaceUrlFilters,
  RUN_STATES,
  shortId,
  type WindowPreset,
  windowQuery,
} from "@/lib/operations/query";
import type { RunItem } from "@/lib/operations/types";

export default function OperationsRunsPage() {
  const [preset, setPreset] = useState<WindowPreset>("last_7_days");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [businessCode, setBusinessCode] = useState("");
  const [state, setState] = useState("");
  const [userId, setUserId] = useState("");
  const [threadId, setThreadId] = useState("");
  const [cursor, setCursor] = useState("");
  const [businessOptions, setBusinessOptions] = useState<Array<{ value: string; label: string }>>(
    []
  );

  // 从 URL 恢复下钻条件（用户/线程 ID 是内部导航标识；原文与搜索词不进入 URL）。
  useEffect(() => {
    setUserId(readUrlFilter("user_id"));
    setThreadId(readUrlFilter("thread_id"));
    setState(readUrlFilter("state"));
    setBusinessCode(readUrlFilter("business_code"));
    setCursor(readUrlFilter("cursor"));
  }, []);

  const load = useCallback(
    async (signal: AbortSignal) => {
      const window = windowQuery(preset, startDate, endDate);
      const list = await operationsApi.runs(
        {
          ...window,
          business_code: businessCode || undefined,
          user_id: userId || undefined,
          thread_id: threadId || undefined,
          state: state || undefined,
          cursor: cursor || undefined,
          limit: 50,
        },
        signal
      );
      return list;
    },
    [preset, startDate, endDate, businessCode, state, userId, threadId, cursor]
  );
  const { data, loading, error, reload } = useOperationQuery(load, [
    preset,
    startDate,
    endDate,
    businessCode,
    state,
    userId,
    threadId,
    cursor,
  ]);

  useEffect(() => {
    const seen = new Map<string, string>();
    for (const row of data?.items ?? []) {
      if (row.business_code) {
        seen.set(row.business_code, GRAPH_LABELS[row.business_code] ?? row.business_code);
      }
    }
    if (businessCode) {
      seen.set(businessCode, GRAPH_LABELS[businessCode] ?? businessCode);
    }
    setBusinessOptions(Array.from(seen, ([value, label]) => ({ value, label })));
  }, [data?.items, businessCode]);

  function updateFilters(next: {
    user?: string;
    thread?: string;
    state?: string;
    business?: string;
    cursor?: string;
  }) {
    const value = {
      user_id: next.user ?? userId,
      thread_id: next.thread ?? threadId,
      state: next.state ?? state,
      business_code: next.business ?? businessCode,
      cursor: next.cursor ?? "",
    };
    replaceUrlFilters(value);
  }

  const columns: Column<RunItem>[] = [
    {
      key: "attempt",
      label: "运行尝试",
      render: (row) => (
        <div className="min-w-36">
          <Link
            className="underline"
            href={`/operations/runs/${row.attempt_id}`}
          >
            {shortId(row.attempt_id, 12)}
          </Link>
          <div className="text-muted-foreground text-[11px]">{row.state}</div>
        </div>
      ),
    },
    {
      key: "business",
      label: "业务",
      className: "whitespace-nowrap",
      render: (row) => GRAPH_LABELS[row.business_code] ?? row.business_code,
    },
    {
      key: "user",
      label: "用户",
      render: (row) => (
        <div className="min-w-32">
          <div className="truncate">{row.display_name}</div>
          <div className="text-muted-foreground font-mono text-[11px]">
            {shortId(row.user_id, 12)}
          </div>
        </div>
      ),
    },
    {
      key: "thread",
      label: "线程",
      render: (row) => (
        <span className="font-mono text-[11px]">{shortId(row.thread_id, 14)}</span>
      ),
    },
    {
      key: "time",
      label: "受理 / 结束",
      render: (row) => (
        <div className="text-xs">
          <div>{formatDateTime(row.admitted_at)}</div>
          <div className="text-muted-foreground">
            {formatDateTime(row.ended_at)}
          </div>
        </div>
      ),
    },
    { key: "resumes", label: "恢复", render: (row) => row.resume_count },
    {
      key: "answer",
      label: "确认回答",
      render: (row) =>
        row.answer ? (
          <span className="text-xs">
            v{row.answer.content_version} ·{" "}
            {CONTENT_STATUS_LABELS[row.answer.content_status] ?? row.answer.content_status}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
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
          updateFilters({ cursor: "" });
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
          updateFilters({ business: value });
        }}
        businessOptions={businessOptions}
        onRefresh={reload}
        loading={loading}
      >
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">状态</span>
          <select
            className="bg-background h-8 rounded border px-2 text-sm"
            value={state}
            onChange={(event) => {
              setState(event.target.value);
              setCursor("");
              updateFilters({ state: event.target.value });
            }}
          >
            <option value="">全部状态</option>
            {RUN_STATES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <DebouncedInput
          label="用户 ID（精确）"
          value={userId}
          placeholder="UUID"
          onValue={(value) => {
            setUserId(value.trim());
            setCursor("");
            updateFilters({ user: value.trim() });
          }}
        />
        <DebouncedInput
          label="线程 ID（精确）"
          value={threadId}
          placeholder="thread id"
          onValue={(value) => {
            setThreadId(value.trim());
            setCursor("");
            updateFilters({ thread: value.trim() });
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
        caption="逻辑问题与运行尝试分开计数；恢复不增加提问量；列表不含正文"
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(row) => row.attempt_id}
        loading={loading}
      />

      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-muted-foreground text-xs">
          本页 {data?.items.length ?? 0} 条 · 截止 {formatDateTime(data?.meta.as_of)}
        </span>
        <div className="flex gap-2">
          <button
            className="rounded border px-2 py-1 text-xs disabled:opacity-50"
            disabled={!cursor}
            onClick={() => {
              setCursor("");
              updateFilters({ cursor: "" });
            }}
          >
            回到第一页
          </button>
          <button
            className="rounded border px-2 py-1 text-xs disabled:opacity-50"
            disabled={!data?.has_more || !data.next_cursor}
            onClick={() => {
              const next = data?.next_cursor ?? "";
              setCursor(next);
              updateFilters({ cursor: next });
            }}
          >
            下一页
          </button>
        </div>
      </div>
    </div>
  );
}
