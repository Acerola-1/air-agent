"use client";

import { useCallback, useMemo, useState } from "react";

import { CurrencySummary, MetricState } from "@/components/operations/MetricState";
import { QueryToolbar } from "@/components/operations/QueryToolbar";
import { TrendBars } from "@/components/operations/TrendBars";
import { operationsApi } from "@/lib/operations/client";
import { useOperationQuery } from "@/lib/operations/hooks";
import {
  formatDateTime,
  GRAPH_LABELS,
  type WindowPreset,
  windowQuery,
} from "@/lib/operations/query";
import type { CapabilityGroup } from "@/lib/operations/types";

export default function OperationsOverviewPage() {
  const [preset, setPreset] = useState<WindowPreset>("last_7_days");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [businessCode, setBusinessCode] = useState("");

  const load = useCallback(
    async (signal: AbortSignal) => {
      const window = windowQuery(preset, startDate, endDate);
      const params = { ...window, business_code: businessCode || undefined };
      const [overview, trends, businesses] = await Promise.all([
        operationsApi.overview(params, signal),
        operationsApi.trends({ ...params, metric: "all" }, signal),
        operationsApi.capabilities(
          { ...window, group_by: "business", limit: 50 },
          signal
        ),
      ]);
      return { overview, trends, businesses };
    },
    [preset, startDate, endDate, businessCode]
  );
  const { data, loading, error, reload } = useOperationQuery(load, [
    preset,
    startDate,
    endDate,
    businessCode,
  ]);

  const businessOptions = useMemo(() => {
    const items = data?.businesses.items ?? [];
    const seen = new Map<string, string>();
    for (const item of items as CapabilityGroup[]) {
      if (typeof item.group === "string" && item.group) {
        seen.set(item.group, GRAPH_LABELS[item.group] ?? item.group);
      }
    }
    return Array.from(seen, ([value, label]) => ({ value, label }));
  }, [data?.businesses.items]);

  const overview = data?.overview;
  const trends = data?.trends;

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
      />

      {error && (
        <div className="text-destructive rounded border p-3 text-sm" role="alert">
          {error.message}
          <button className="underline" onClick={reload}>
            重试
          </button>
        </div>
      )}

      {overview?.meta.limitations.length ? (
        <ul className="text-muted-foreground space-y-1 rounded border border-dashed p-2 text-xs">
          {overview.meta.limitations.map((item) => (
            <li key={item}>· {item}</li>
          ))}
        </ul>
      ) : null}

      <section aria-labelledby="overview-requests" className="space-y-2">
        <h2 id="overview-requests" className="text-sm font-semibold">
          提问与运行
        </h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricState label="提问数" metric={overview?.requests.questions} />
          <MetricState label="活跃用户" metric={overview?.requests.active_users} />
          <MetricState label="运行尝试" metric={overview?.attempts.attempts} />
          <MetricState
            label="技术完成率"
            metric={overview?.attempts.technical_completion_rate}
            format="percent"
            hint="成功 / 已结束尝试"
          />
          <MetricState label="成功" metric={overview?.attempts.succeeded} />
          <MetricState
            label="失败"
            metric={overview?.attempts.failed}
            tone="danger"
          />
          <MetricState label="取消" metric={overview?.attempts.cancelled} />
          <MetricState label="恢复" metric={overview?.attempts.resumes} />
        </div>
      </section>

      <section aria-labelledby="overview-experience" className="space-y-2">
        <h2 id="overview-experience" className="text-sm font-semibold">
          体验与反馈
        </h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricState
            label="满意率"
            metric={overview?.feedback.satisfaction_rate}
            format="percent"
            hint="未评价/撤销不计入分母"
          />
          <MetricState
            label="评价覆盖率"
            metric={overview?.feedback.rating_coverage}
            format="percent"
          />
          <MetricState label="点赞（当前）" metric={overview?.feedback.positive} />
          <MetricState
            label="点踩（当前）"
            metric={overview?.feedback.negative}
            tone="danger"
          />
          <MetricState
            label="首可见正文 P50"
            metric={overview?.performance.first_visible_answer_ms}
            hint="浏览器单调时钟"
          />
          <MetricState
            label="模型 TTFT P50"
            metric={overview?.performance.model_ttft_ms}
          />
          <MetricState
            label="正文采集覆盖"
            metric={overview?.content.capture_coverage}
            format="percent"
            hint="确认回答中有快照的比例"
          />
          <MetricState
            label="未采集回答"
            metric={overview?.content.not_collected}
            tone={
              Number(overview?.content.not_collected.value ?? 0) > 0
                ? "warning"
                : "default"
            }
          />
        </div>
      </section>

      <section aria-labelledby="overview-usage" className="space-y-2">
        <h2 id="overview-usage" className="text-sm font-semibold">
          用量与费用
        </h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetricState label="模型调用" metric={overview?.usage.model_calls} />
          <MetricState label="逻辑调用" metric={overview?.usage.logical_calls} />
          <MetricState label="重试尝试" metric={overview?.usage.retry_attempts} />
          <MetricState label="Token 总量" metric={overview?.usage.total_tokens} />
        </div>
        <div className="bg-card flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3">
          <div>
            <div className="text-muted-foreground text-xs">费用（按币种，最新计费修订）</div>
            <CurrencySummary byCurrency={overview?.cost.by_currency} className="mt-1" />
          </div>
          <div className="text-muted-foreground text-xs">
            {overview?.cost.complete_cost_known
              ? "全部调用 usage 与价格已知"
              : `未计价 ${overview?.cost.unpriced_calls.value ?? "—"}；未知不补零`}
          </div>
        </div>
      </section>

      <section aria-labelledby="overview-trends" className="space-y-3">
        <h2 id="overview-trends" className="text-sm font-semibold">
          日趋势（Asia/Shanghai）
        </h2>
        <div className="grid gap-4 lg:grid-cols-2">
          <TrendBars
            title="每日提问"
            points={(trends?.items ?? []).map((item) => ({
              label: item.day,
              value: item.requests,
            }))}
          />
          <TrendBars
            title="每日运行尝试"
            points={(trends?.items ?? []).map((item) => ({
              label: item.day,
              value: item.attempts,
            }))}
          />
          <TrendBars
            title="每日点踩（当前评价）"
            points={(trends?.items ?? []).map((item) => ({
              label: item.day,
              value: item.negative,
            }))}
          />
          <TrendBars
            title="每日 Token（已知分量）"
            points={(trends?.items ?? []).map((item) => ({
              label: item.day,
              value: item.tokens,
            }))}
          />
        </div>
      </section>

      <footer className="text-muted-foreground text-xs">
        数据截止 {formatDateTime(overview?.meta.as_of)} · 窗口{" "}
        {overview?.meta.window.kind ?? "—"}
        {overview?.meta.window.partial_day ? "（含未完成业务日）" : ""}
      </footer>
    </div>
  );
}
