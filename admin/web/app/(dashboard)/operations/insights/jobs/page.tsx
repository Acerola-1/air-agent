"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { DataTable, type Column } from "@/components/operations/DataTable";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { demandApi, newOperationId } from "@/lib/operations/demand-client";
import { useOperationQuery } from "@/lib/operations/hooks";
import { formatDateTime, formatNumber, shortId } from "@/lib/operations/query";
import type { JobItem } from "@/lib/operations/demand-types";
import { useSession } from "@/lib/session";

const JOB_STATE_LABELS: Record<string, string> = {
  pending: "待运行",
  running: "运行中",
  paused_user: "已暂停",
  paused_authorization: "授权暂停",
  paused_budget: "预算暂停",
  paused_failure: "故障暂停",
  completed: "已完成",
  cancelled: "已取消",
  failed: "失败",
};

export default function InsightJobsPage() {
  const { can } = useSession();
  const canManage = can(
    "agent:operations:read",
    "agent:content:read",
    "agent:insights:manage"
  );
  const [maxSources, setMaxSources] = useState("500");
  const [maxTokens, setMaxTokens] = useState("200000");
  const [continuous, setContinuous] = useState(true);
  const [backfillStart, setBackfillStart] = useState("");
  const [backfillEnd, setBackfillEnd] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const jobs = useOperationQuery(
    useCallback((signal: AbortSignal) => demandApi.jobs(signal), []),
    []
  );

  function messageOf(err: unknown): string {
    if (!(err instanceof Error)) return "操作失败";
    const code =
      err && typeof err === "object" && "code" in err
        ? String((err as { code?: string }).code ?? "")
        : "";
    return code ? `${err.message}（${code}）` : err.message;
  }

  async function createJob(backfill: boolean) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await demandApi.createJob({
        operation_id: newOperationId(),
        job_kind: backfill ? "backfill" : "incremental",
        window_start: backfill && backfillStart ? `${backfillStart}T00:00:00+08:00` : null,
        window_end: backfill && backfillEnd ? `${backfillEnd}T00:00:00+08:00` : null,
        continuous,
        max_sources: Number(maxSources) || 500,
        max_tokens: Number(maxTokens) || 200000,
        acknowledge_backfill: backfill ? acknowledge : false,
        delegation_ttl_hours: 12,
      });
      setNotice(
        result.replayed
          ? `作业已存在：${String((result.result as { job_id?: string }).job_id ?? "")}`
          : `作业已创建：${String((result.result as { job_id?: string }).job_id ?? "")}`
      );
      jobs.reload();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  async function act(job: JobItem, action: "retry" | "pause" | "renew" | "cancel") {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await demandApi.jobAction(job.job_id, {
        operation_id: newOperationId(),
        expected_revision: job.state_revision,
        action,
        renew_ttl_hours: 12,
      });
      setNotice(
        result.replayed
          ? "操作已受理，复用原结果。"
          : `作业 ${shortId(job.job_id)} 已执行 ${action}。`
      );
      jobs.reload();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<JobItem>[] = [
    {
      key: "job_id",
      label: "作业",
      render: (row) => (
        <span title={row.job_id} className="font-mono text-xs">
          {shortId(row.job_id, 12)}
        </span>
      ),
    },
    { key: "job_kind", label: "类型" },
    {
      key: "state",
      label: "状态",
      render: (row) => JOB_STATE_LABELS[row.state] ?? row.state,
    },
    {
      key: "window",
      label: "窗口",
      render: (row) =>
        `${formatDateTime(row.window_start)} → ${formatDateTime(row.window_end)}`,
    },
    {
      key: "progress",
      label: "处理/失败",
      render: (row) =>
        `${formatNumber(row.processed_sources)} / ${formatNumber(row.failed_sources)}`,
    },
    {
      key: "intents",
      label: "已分类/未归类",
      render: (row) =>
        `${formatNumber(row.classified_intents)} / ${formatNumber(row.unclassified_intents)}`,
    },
    {
      key: "tokens",
      label: "Token",
      render: (row) =>
        row.usage_missing ? "未采集" : row.actual_tokens === null ? "—" : formatNumber(row.actual_tokens),
    },
    {
      key: "expires",
      label: "授权到期",
      render: (row) => formatDateTime(row.authorization_expires_at),
    },
    {
      key: "error",
      label: "安全错误码",
      render: (row) => row.last_error_code ?? "—",
    },
    {
      key: "actions",
      label: "操作",
      render: (row) =>
        canManage ? (
          <div className="flex flex-wrap gap-1">
            {(row.state === "paused_failure" ||
              row.state === "paused_user" ||
              row.state === "paused_authorization" ||
              row.state === "failed") && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => act(row, "retry")}>
                重试
              </Button>
            )}
            {row.state === "running" || row.state === "pending" ? (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => act(row, "pause")}>
                暂停
              </Button>
            ) : null}
            {row.state === "paused_authorization" ? (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => act(row, "renew")}>
                续期授权
              </Button>
            ) : null}
            {row.state !== "cancelled" && row.state !== "completed" ? (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => act(row, "cancel")}>
                取消
              </Button>
            ) : null}
          </div>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <div className="space-y-4">
      <div>
        <Link className="text-muted-foreground text-xs" href="/operations/insights">
          ← 返回需求洞察
        </Link>
        <h1 className="text-xl font-semibold">需求分析作业</h1>
        <p className="text-muted-foreground text-sm">
          异步增量扫描已确认受理的问题；委托最长 12 小时，撤权/切企业/过期立即暂停。
        </p>
      </div>

      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}
      {notice && <p className="text-sm text-emerald-700">{notice}</p>}

      {canManage && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">创建作业</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-3">
            <label className="space-y-1 text-xs">
              <span className="text-muted-foreground block">单作业最多来源</span>
              <Input
                value={maxSources}
                onChange={(event) => setMaxSources(event.target.value)}
                inputMode="numeric"
              />
            </label>
            <label className="space-y-1 text-xs">
              <span className="text-muted-foreground block">Token 预算（估算上限）</span>
              <Input
                value={maxTokens}
                onChange={(event) => setMaxTokens(event.target.value)}
                inputMode="numeric"
              />
            </label>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={continuous}
                onChange={(event) => setContinuous(event.target.checked)}
              />
              完成后自动续建增量作业（委托仍有效时）
            </label>
            <label className="space-y-1 text-xs">
              <span className="text-muted-foreground block">回填开始（可选）</span>
              <Input
                type="date"
                value={backfillStart}
                onChange={(event) => setBackfillStart(event.target.value)}
              />
            </label>
            <label className="space-y-1 text-xs">
              <span className="text-muted-foreground block">回填结束（可选）</span>
              <Input
                type="date"
                value={backfillEnd}
                onChange={(event) => setBackfillEnd(event.target.value)}
              />
            </label>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={acknowledge}
                onChange={(event) => setAcknowledge(event.target.checked)}
              />
              我确认回填预算（最长 30 个业务日）
            </label>
            <Button size="sm" disabled={busy} onClick={() => createJob(false)}>
              创建增量作业
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || !backfillStart || !backfillEnd || !acknowledge}
              onClick={() => createJob(true)}
            >
              创建回填作业
            </Button>
          </CardContent>
        </Card>
      )}

      <DataTable
        caption="同企业作业状态；不展示委托凭据。"
        columns={columns}
        rows={jobs.data?.items ?? []}
        rowKey={(row) => row.job_id}
        loading={jobs.loading}
        empty="还没有分析作业"
      />
      {jobs.error && (
        <p className="text-destructive text-sm" role="alert">
          {jobs.error.message}
        </p>
      )}

      <p className="text-muted-foreground text-xs">
        模型/Embedding 未配置或提供商故障时作业暂停而非静默换供应商；usage 缺失不记 0。
      </p>
    </div>
  );
}
