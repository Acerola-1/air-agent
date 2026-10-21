"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { DataTable, type Column } from "@/components/operations/DataTable";
import { QueryToolbar } from "@/components/operations/QueryToolbar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { demandApi, type Rank } from "@/lib/operations/demand-client";
import { useOperationQuery } from "@/lib/operations/hooks";
import {
  formatDateTime,
  formatNumber,
  formatPercent,
  GRAPH_LABELS,
  type WindowPreset,
  windowQuery,
} from "@/lib/operations/query";
import type { OverviewResponse, QuestionItem, TopicItem } from "@/lib/operations/demand-types";
import { useSession } from "@/lib/session";

const RANK_OPTIONS: Array<{ value: Rank; label: string }> = [
  { value: "intents", label: "原子意图" },
  { value: "users", label: "独立用户" },
  { value: "requests", label: "去重请求" },
];

function countValue(counts: Record<string, unknown> | undefined, key: string): number | null {
  const value = counts?.[key];
  return typeof value === "number" ? value : null;
}

function growthLabel(row: QuestionItem): string {
  if (row.new) return "新出现";
  if (row.growth_rate === null) return "—";
  return `${(row.growth_rate * 100).toFixed(1)}%`;
}

export default function QuestionDemandInsightsPage() {
  const { can } = useSession();
  const canManage = can(
    "agent:operations:read",
    "agent:content:read",
    "agent:insights:manage"
  );
  const [tab, setTab] = useState<"questions" | "topics">("questions");
  const [preset, setPreset] = useState<WindowPreset>("last_7_days");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [businessCode, setBusinessCode] = useState("");
  const [action, setAction] = useState("");
  const [pollutant, setPollutant] = useState("");
  const [rank, setRank] = useState<Rank>("intents");
  const [cursor, setCursor] = useState("");

  const overview = useOperationQuery<OverviewResponse>(
    useCallback(
      async (signal: AbortSignal) => {
        const window = windowQuery(preset, startDate, endDate);
        return demandApi.overview(
          { ...window, business_code: businessCode || undefined },
          signal
        );
      },
      [preset, startDate, endDate, businessCode]
    ),
    [preset, startDate, endDate, businessCode]
  );

  const questions = useOperationQuery(
    useCallback(
      async (signal: AbortSignal) => {
        const window = windowQuery(preset, startDate, endDate);
        return demandApi.questions(
          {
            ...window,
            business_code: businessCode || undefined,
            action: action || undefined,
            pollutant: pollutant || undefined,
            rank,
            cursor: cursor || undefined,
            limit: 50,
          },
          signal
        );
      },
      [preset, startDate, endDate, businessCode, action, pollutant, rank, cursor]
    ),
    [preset, startDate, endDate, businessCode, action, pollutant, rank, cursor]
  );

  const topics = useOperationQuery(
    useCallback(
      async (signal: AbortSignal) => {
        const window = windowQuery(preset, startDate, endDate);
        return demandApi.topics(
          {
            ...window,
            business_code: businessCode || undefined,
            action: action || undefined,
            pollutant: pollutant || undefined,
            rank,
            cursor: cursor || undefined,
            limit: 50,
          },
          signal
        );
      },
      [preset, startDate, endDate, businessCode, action, pollutant, rank, cursor]
    ),
    [preset, startDate, endDate, businessCode, action, pollutant, rank, cursor]
  );

  const businessOptions = useMemo(
    () => Object.entries(GRAPH_LABELS).map(([value, label]) => ({ value, label })),
    []
  );

  function resetCursor() {
    setCursor("");
  }

  const meta = tab === "questions" ? questions.data?.meta : topics.data?.meta;
  const counts = overview.data?.counts;
  const coverage = typeof meta?.coverage === "number" ? meta.coverage : null;

  const questionColumns: Column<QuestionItem>[] = [
    {
      key: "canonical_name",
      label: "标准问题",
      render: (row) => (
        <Link
          className="font-medium underline-offset-2 hover:underline"
          href={`/operations/insights/${encodeURIComponent(row.cluster_id)}`}
        >
          {row.canonical_name}
        </Link>
      ),
    },
    {
      key: "status",
      label: "状态",
      render: (row) => (
        <Badge variant={row.status === "active" ? "default" : "secondary"}>
          {row.status === "candidate" ? "候选" : row.status === "active" ? "稳定" : row.status}
        </Badge>
      ),
    },
    { key: "atomic_intent_count", label: "原子意图", render: (row) => formatNumber(row.atomic_intent_count) },
    { key: "distinct_requests", label: "去重请求", render: (row) => formatNumber(row.distinct_requests) },
    { key: "distinct_users", label: "独立用户", render: (row) => formatNumber(row.distinct_users) },
    {
      key: "share",
      label: "占比",
      render: (row) => formatPercent(row.share.value),
    },
    {
      key: "previous_count",
      label: "前期",
      render: (row) =>
        row.comparison_status === "not_comparable"
          ? "不可比"
          : row.comparison_status === "not_requested"
            ? "未请求"
            : row.previous_count === null
              ? "—"
              : formatNumber(row.previous_count),
    },
    {
      key: "growth",
      label: "增长",
      render: (row) => (
        <span title={`比较状态：${row.comparison_status}`}>{growthLabel(row)}</span>
      ),
    },
    {
      key: "feedback",
      label: "负反馈/请求",
      render: (row) =>
        row.request_level_negative_feedback
          ? `${formatNumber(row.request_level_negative_feedback)} / ${formatNumber(row.distinct_requests)}`
          : "0",
    },
  ];

  const topicColumns: Column<TopicItem>[] = [
    { key: "name", label: "主题" },
    { key: "atomic_intent_count", label: "原子意图", render: (row) => formatNumber(row.atomic_intent_count) },
    { key: "distinct_requests", label: "去重请求", render: (row) => formatNumber(row.distinct_requests) },
    { key: "distinct_users", label: "独立用户", render: (row) => formatNumber(row.distinct_users) },
    { key: "share", label: "占比", render: (row) => formatPercent(row.share.value) },
    { key: "status", label: "状态", render: (row) => row.status },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">需求洞察</h1>
          <p className="text-muted-foreground text-sm">
            标准问题与主题来自已确认受理的用户原问题；未评价不等于未解决。
          </p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline" size="sm">
            <Link href="/operations/insights/unclassified">未归类</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/operations/insights/jobs">分析作业</Link>
          </Button>
        </div>
      </div>

      <QueryToolbar
        preset={preset}
        onPreset={(value) => {
          setPreset(value);
          resetCursor();
        }}
        startDate={startDate}
        endDate={endDate}
        onStartDate={(value) => {
          setStartDate(value);
          resetCursor();
        }}
        onEndDate={(value) => {
          setEndDate(value);
          resetCursor();
        }}
        businessCode={businessCode}
        onBusinessCode={(value) => {
          setBusinessCode(value);
          resetCursor();
        }}
        businessOptions={businessOptions}
        onRefresh={() => {
          overview.reload();
          questions.reload();
          topics.reload();
        }}
        loading={overview.loading || questions.loading || topics.loading}
      >
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">动作</span>
          <select
            className="bg-background h-8 rounded border px-2 text-sm"
            value={action}
            onChange={(event) => {
              setAction(event.target.value);
              resetCursor();
            }}
          >
            <option value="">全部动作</option>
            <option value="query">查询</option>
            <option value="trend">趋势</option>
            <option value="cause">原因</option>
            <option value="forecast">预测</option>
            <option value="advice">建议</option>
            <option value="other">其他</option>
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">污染物</span>
          <select
            className="bg-background h-8 rounded border px-2 text-sm"
            value={pollutant}
            onChange={(event) => {
              setPollutant(event.target.value);
              resetCursor();
            }}
          >
            <option value="">全部污染物</option>
            {["PM2.5", "PM10", "O3", "NO2", "SO2", "CO", "AQI"].map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">排序</span>
          <select
            className="bg-background h-8 rounded border px-2 text-sm"
            value={rank}
            onChange={(event) => {
              setRank(event.target.value as Rank);
              resetCursor();
            }}
          >
            {RANK_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </QueryToolbar>

      {overview.error && (
        <p className="text-destructive text-sm" role="alert">
          {overview.error.message}
        </p>
      )}
      {meta?.status === "not_initialized" && (
        <p className="bg-muted rounded border p-3 text-sm">
          尚未启用需求洞察分析版本。{canManage ? "请到“分析作业”创建首个增量作业。" : "请联系管理员启用。"}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["合格请求", "eligible_requests"],
          ["已处理", "processed_requests"],
          ["未归类意图", "unclassified_intents"],
          ["待处理/失败", "pending_requests"],
        ].map(([label, key]) => (
          <Card key={key}>
            <CardHeader className="pb-1">
              <CardTitle className="text-muted-foreground text-xs font-medium">{label}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold">{formatNumber(countValue(counts, key))}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span>覆盖：{formatPercent(coverage)}</span>
        <span>已分类意图：{formatNumber(meta?.classified_intents)}</span>
        <span>缺源：{formatNumber(meta?.missing_content_requests)}</span>
        <span>待确认：{formatNumber(meta?.pending_confirmation_requests)}</span>
        <span>截止：{formatDateTime(meta?.source_cutoff)}</span>
        <span>分析版本：{meta?.analysis_version ?? "—"}</span>
        <span>归属修订：{meta?.assignment_revision ?? "—"}</span>
        {!meta?.comparable && <span>当前窗口覆盖不足，增长比较仅供参考</span>}
      </div>

      <div className="flex gap-2">
        <Button
          size="sm"
          variant={tab === "questions" ? "default" : "outline"}
          onClick={() => setTab("questions")}
        >
          标准问题
        </Button>
        <Button
          size="sm"
          variant={tab === "topics" ? "default" : "outline"}
          onClick={() => setTab("topics")}
        >
          主题
        </Button>
      </div>

      {tab === "questions" ? (
        <div className="space-y-2">
          <DataTable
            caption="按原始问题受理业务日入窗；占比分母为当前已处理原子意图（含未归类）。"
            columns={questionColumns}
            rows={questions.data?.items ?? []}
            rowKey={(row) => row.cluster_id}
            loading={questions.loading}
            empty="当前窗口没有标准问题"
          />
          {questions.error && (
            <p className="text-destructive text-sm" role="alert">
              {questions.error.message}
            </p>
          )}
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!questions.data?.has_more || !questions.data.next_cursor}
              onClick={() => {
                const next = questions.data?.next_cursor;
                if (next) setCursor(next);
              }}
            >
              下一页
            </Button>
            {cursor && (
              <Button variant="ghost" size="sm" onClick={() => setCursor("")}>
                回到第一页
              </Button>
            )}
            {questions.data?.previous_comparable === false && (
              <span className="text-muted-foreground text-xs">前期覆盖不足，增长标为不可比</span>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <DataTable
            caption="主题的请求/用户直接去重，不累加子簇。"
            columns={topicColumns}
            rows={topics.data?.items ?? []}
            rowKey={(row) => row.topic_id}
            loading={topics.loading}
            empty="当前窗口没有主题数据"
          />
          {topics.error && (
            <p className="text-destructive text-sm" role="alert">
              {topics.error.message}
            </p>
          )}
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!topics.data?.has_more || !topics.data.next_cursor}
              onClick={() => {
                const next = topics.data?.next_cursor;
                if (next) setCursor(next);
              }}
            >
              下一页
            </Button>
            {cursor && (
              <Button variant="ghost" size="sm" onClick={() => setCursor("")}>
                回到第一页
              </Button>
            )}
          </div>
        </div>
      )}

      {overview.data?.unclassified_reasons?.length ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">未归类原因</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2 text-sm">
            {overview.data.unclassified_reasons.map((reason) => (
              <span key={reason.reason} className="bg-muted rounded px-2 py-1">
                {reason.reason}：{formatNumber(reason.count)}
              </span>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {overview.data?.topics?.length ? (
        <div className="text-muted-foreground text-xs">
          主题示例：
          {overview.data.topics.map((topic) => (
            <span key={topic.topic_id} className="mr-3">
              {topic.name}（{formatNumber(topic.atomic_intent_count)}）
            </span>
          ))}
        </div>
      ) : null}

      <p className="text-muted-foreground text-xs">
        数据来源为企业内已确认受理的问题快照；不展示原句、向量或用户标识。业务：
        {businessCode ? GRAPH_LABELS[businessCode] ?? businessCode : "全部"}。
      </p>
    </div>
  );
}
