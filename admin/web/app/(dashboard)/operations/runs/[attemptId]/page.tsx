"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { ContentReader } from "@/components/operations/ContentReader";
import { DataTable, type Column } from "@/components/operations/DataTable";
import { operationsApi } from "@/lib/operations/client";
import { useOperationQuery } from "@/lib/operations/hooks";
import {
  CONTENT_STATUS_LABELS,
  formatDateTime,
  formatNumber,
  GRAPH_LABELS,
  shortId,
} from "@/lib/operations/query";
import type { ContentPage, RunDetail } from "@/lib/operations/types";

type ObservationRow = {
  observation_id: string;
  role: string;
  model: string;
  status: string;
  duration_ms: number | null;
  ttft_ms: number | null;
  total_tokens: number | null;
  measurement_status: string;
  retry_index: number;
  usage?: {
    input_tokens: number | null;
    output_tokens: number | null;
    total_tokens: number | null;
  };
};

type ObservationDetails = {
  truncated?: boolean;
  model_observations?: ObservationRow[];
  skill_spans?: Array<Record<string, unknown>>;
  tool_calls?: Array<Record<string, unknown>>;
  trace_links?: Array<Record<string, unknown>>;
  coverage?: Record<string, unknown>;
};

export default function RunDetailPage() {
  const params = useParams<{ attemptId: string }>();
  const router = useRouter();
  const attemptId = Array.isArray(params.attemptId)
    ? params.attemptId[0]
    : params.attemptId ?? "";

  const load = useCallback(
    async (signal: AbortSignal) => {
      const [run, observations] = await Promise.all([
        operationsApi.run(attemptId, signal),
        operationsApi.runObservations(attemptId, signal).catch(() => null),
      ]);
      return { run, observations: observations as ObservationDetails | null };
    },
    [attemptId]
  );
  const { data, loading, error, reload } = useOperationQuery(load, [attemptId]);

  const [contentTarget, setContentTarget] = useState<
    | { kind: "request"; requestId: string }
    | { kind: "answer"; answerId: string; contentVersion: number }
    | null
  >(null);

  const loadContent = useCallback(
    async (offset: number, signal: AbortSignal): Promise<ContentPage> => {
      if (!contentTarget) throw new Error("未选择正文");
      if (contentTarget.kind === "request") {
        return operationsApi.requestContent(
          contentTarget.requestId,
          offset,
          8000,
          signal
        );
      }
      return operationsApi.answerContent(
        contentTarget.answerId,
        contentTarget.contentVersion,
        offset,
        8000,
        signal
      );
    },
    [contentTarget]
  );

  const run = data?.run;
  const observations = data?.observations;

  const answerColumns: Column<RunDetail["answers"][number]>[] = useMemo(
    () => [
      {
        key: "version",
        label: "版本",
        render: (row) => `v${row.content_version}`,
      },
      {
        key: "status",
        label: "正文状态",
        render: (row) => (
          <span title={row.capture_status ?? undefined}>
            {CONTENT_STATUS_LABELS[row.availability] ?? row.availability}
            {row.truncated ? "（截断）" : ""}
          </span>
        ),
      },
      {
        key: "chars",
        label: "字符",
        render: (row) => (row.total_chars === null ? "—" : formatNumber(row.total_chars)),
      },
      {
        key: "confirmed",
        label: "确认时间",
        render: (row) => formatDateTime(row.confirmed_at),
      },
      {
        key: "action",
        label: "正文",
        render: (row) =>
          row.availability === "available" || row.availability === "truncated" ? (
            <button
              className="underline"
              onClick={() =>
                setContentTarget({
                  kind: "answer",
                  answerId: row.answer_id,
                  contentVersion: row.content_version,
                })
              }
            >
              按需读取
            </button>
          ) : (
            <span className="text-muted-foreground text-xs">不可读</span>
          ),
      },
    ],
    []
  );

  const observationColumns: Column<ObservationRow>[] = [
    { key: "role", label: "角色" },
    { key: "model", label: "模型" },
    { key: "status", label: "状态" },
    { key: "retry", label: "重试", render: (row) => row.retry_index },
    {
      key: "duration",
      label: "耗时 (ms)",
      render: (row) => (row.duration_ms === null ? "—" : formatNumber(row.duration_ms)),
    },
    {
      key: "ttft",
      label: "TTFT (ms)",
      render: (row) => (row.ttft_ms === null ? "—" : formatNumber(row.ttft_ms)),
    },
    {
      key: "tokens",
      label: "Token",
      render: (row) => {
        const tokens = row.usage?.total_tokens ?? row.total_tokens;
        return tokens === null || tokens === undefined ? "—" : formatNumber(tokens);
      },
    },
    { key: "measure", label: "采集状态", render: (row) => row.measurement_status },
  ];

  if (loading) {
    return <div className="text-muted-foreground text-sm">正在加载运行详情…</div>;
  }
  if (error) {
    return (
      <div className="space-y-3">
        <div className="text-destructive rounded border p-3 text-sm" role="alert">
          {error.message}
        </div>
        <div className="flex gap-2">
          <button className="rounded border px-3 py-1 text-sm" onClick={reload}>
            重试
          </button>
          <Link className="rounded border px-3 py-1 text-sm" href="/operations/runs">
            返回运行列表
          </Link>
        </div>
      </div>
    );
  }
  if (!run) return null;

  const attempt = run.attempt;
  const selection = run.capability_selection as Record<string, unknown>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">运行详情</h2>
          <p className="text-muted-foreground text-xs">
            {GRAPH_LABELS[String(attempt.business_code)] ?? String(attempt.business_code)} ·{" "}
            <span className="font-mono">{shortId(String(attempt.attempt_id), 16)}</span>
          </p>
        </div>
        <div className="flex gap-2">
          <button
            className="rounded border px-3 py-1 text-sm"
            onClick={() => router.back()}
          >
            返回
          </button>
          <Link className="rounded border px-3 py-1 text-sm" href="/operations/runs">
            运行列表
          </Link>
        </div>
      </div>

      <section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["状态", String(attempt.state)],
          ["用户", String(attempt.user_id)],
          ["受理", formatDateTime(String(attempt.admitted_at ?? ""))],
          ["结束", formatDateTime(String(attempt.ended_at ?? ""))],
          ["平台运行", shortId(String(attempt.platform_run_id ?? ""), 18)],
          ["取消意图", attempt.cancel_requested ? "是" : "否"],
          ["恢复次数", String(run.resumes.length)],
          ["模型调用", formatNumber(run.observations.observations)],
        ].map(([label, value]) => (
          <div key={label} className="bg-card rounded border p-2">
            <div className="text-muted-foreground text-xs">{label}</div>
            <div className="mt-0.5 truncate text-sm">{value}</div>
          </div>
        ))}
      </section>

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">原问题</h3>
          <button
            className="rounded border px-2 py-1 text-xs"
            onClick={() =>
              setContentTarget({ kind: "request", requestId: String(attempt.request_id) })
            }
          >
            按需读取原问题
          </button>
        </div>
        <p className="text-muted-foreground text-xs">
          正文读取需运营与受控正文双权限；删除/跨企业优先 404；每页最多 8000 字符。
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">确认回答版本</h3>
        <DataTable
          caption="只读确认版本；历史未采集不从事后 Trace/checkpoint 回填"
          columns={answerColumns}
          rows={run.answers}
          rowKey={(row) => row.answer_id}
          empty="没有确认回答版本"
        />
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">能力选择快照</h3>
        <pre className="bg-muted/40 max-h-48 overflow-auto rounded border p-2 text-xs">
          {JSON.stringify(selection, null, 2)}
        </pre>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">模型观测</h3>
        <p className="text-muted-foreground text-xs">
          {observations
            ? `本页明细最多 200 项${observations.truncated ? "（已截断；聚合统计走完整授权事实）" : ""}`
            : "观测明细暂不可用；本地运行事实仍可查看。"}
        </p>
        <DataTable
          caption="逐调用观测（不含参数/结果正文）"
          columns={observationColumns}
          rows={observations?.model_observations ?? []}
          rowKey={(row) => row.observation_id}
          empty="没有模型观测"
        />
        {(observations?.skill_spans?.length ?? 0) > 0 && (
          <details className="rounded border p-2 text-xs">
            <summary className="cursor-pointer">Skill 阶段明细</summary>
            <pre className="mt-2 max-h-64 overflow-auto">
              {JSON.stringify(observations?.skill_spans, null, 2)}
            </pre>
          </details>
        )}
        {(observations?.tool_calls?.length ?? 0) > 0 && (
          <details className="rounded border p-2 text-xs">
            <summary className="cursor-pointer">工具逻辑调用与重试</summary>
            <pre className="mt-2 max-h-64 overflow-auto">
              {JSON.stringify(observations?.tool_calls, null, 2)}
            </pre>
          </details>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Trace 映射</h3>
        {run.traces.length === 0 ? (
          <p className="text-muted-foreground text-xs">没有登记的 Trace 映射。</p>
        ) : (
          <ul className="divide-y rounded border text-sm">
            {run.traces.map((trace) => (
              <li key={`${trace.trace_id}-${trace.provider}`} className="flex flex-wrap gap-2 p-2">
                <span>{trace.provider}</span>
                <span className="font-mono text-xs">{trace.trace_id}</span>
                <span className="text-muted-foreground text-xs">
                  {trace.is_primary ? "主映射" : "关联映射"} · {trace.link_source}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-muted-foreground text-xs">
          共享 Langfuse 项目不提供企业控制台直链或凭据；Trace 外部读取失败只影响本分区。
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">恢复记录</h3>
        <DataTable
          caption="人工恢复不增加提问量"
          columns={[
            { key: "resume_index", label: "序号" },
            { key: "platform_run_id", label: "平台运行" },
            { key: "created_at", label: "时间", render: (row) => formatDateTime(String(row.created_at ?? "")) },
          ]}
          rows={run.resumes as Array<Record<string, unknown>>}
          rowKey={(row) => String(row.resume_index)}
          empty="没有恢复记录"
        />
      </section>

      <p className="text-muted-foreground text-xs">
        数据截止 {formatDateTime(run.meta.as_of)} · {String(attempt.request_id)}
      </p>

      <ContentReader
        open={contentTarget !== null}
        onOpenChange={(open) => {
          if (!open) setContentTarget(null);
        }}
        title={contentTarget?.kind === "request" ? "原问题正文" : "确认回答正文"}
        description="按需分页读取；每页最多 8000 字符，响应 no-store。"
        loadPage={loadContent}
      />
    </div>
  );
}
