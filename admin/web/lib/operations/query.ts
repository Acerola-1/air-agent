/** 运营后台查询参数与展示格式（业务日 Asia/Shanghai）。 */

import type { Metric } from "./types";
import type { WindowQuery } from "./client";

export const WINDOW_OPTIONS = [
  { value: "today", label: "今日" },
  { value: "last_7_days", label: "近 7 天" },
  { value: "last_30_days", label: "近 30 天" },
  { value: "custom", label: "自定义" },
] as const;

export type WindowPreset = (typeof WINDOW_OPTIONS)[number]["value"];

/**
 * 自定义日期按“包含结束日”转半开区间：start 00:00 +08:00，
 * end 为结束日次日 00:00 +08:00。不使用浏览器本地时区猜测业务日。
 */
export function customWindow(startDate: string, endDate: string): WindowQuery {
  // 缺日期不静默回退到预设窗口：由服务端明确 422，页面显示“必须同时提供”。
  if (!startDate || !endDate) return { window: "custom" };
  const start = new Date(`${startDate}T00:00:00+08:00`);
  const end = new Date(`${endDate}T00:00:00+08:00`);
  end.setDate(end.getDate() + 1);
  // 结束日包含“今天”时，次日 00:00 会超过当前时刻；截止到请求时刻，
  // 不把未来时间当数据窗口（服务端也会拒绝未来边界）。
  const endIso = end.getTime() > Date.now() ? new Date().toISOString() : end.toISOString();
  return {
    window: "custom",
    start: start.toISOString(),
    end: endIso,
  };
}

export function windowQuery(
  preset: WindowPreset,
  startDate: string,
  endDate: string
): WindowQuery {
  return preset === "custom"
    ? customWindow(startDate, endDate)
    : { window: preset };
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatNumber(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function formatPercent(value: number | string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return `${(numeric * 100).toFixed(1)}%`;
}

export function metricStatusLabel(metric: Metric | null | undefined): string {
  if (!metric) return "未采集";
  switch (metric.status) {
    case "measured":
      return "已采集";
    case "partial":
      return "部分缺失";
    case "not_collected":
      return "未采集";
    case "unknown":
      return "未知";
    case "estimated":
      return "估算";
    case "not_applicable":
      return "不适用";
    default:
      return metric.status;
  }
}

export function metricValue(
  metric: Metric | null | undefined,
  format: (value: number | string | null | undefined) => string = formatNumber
): string {
  if (!metric) return "—";
  if (metric.status === "unknown" || metric.status === "not_collected") {
    return "—";
  }
  return format(metric.value);
}

export function metricTitle(metric: Metric | null | undefined): string {
  if (!metric) return "未采集";
  const parts = [
    `状态：${metricStatusLabel(metric)}`,
    metric.cohort ? `队列：${metric.cohort}` : null,
    metric.sample_count !== undefined ? `样本：${metric.sample_count}` : null,
    metric.missing_count ? `缺失：${metric.missing_count}` : null,
    metric.measurement_source ? `来源：${metric.measurement_source}` : null,
    metric.note ?? null,
  ].filter(Boolean);
  return parts.join("；");
}

export function shortId(value: string | null | undefined, length = 8): string {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

export const RUN_STATES = [
  "admitting",
  "admission_unknown",
  "queued",
  "running",
  "waiting_input",
  "succeeded",
  "failed",
  "cancelled",
  "timed_out",
  "status_unknown",
] as const;

export const RATING_LABELS: Record<string, string> = {
  positive: "点赞",
  negative: "点踩",
  none: "已撤销",
};

export const REASON_LABELS: Record<string, string> = {
  answer_incorrect: "回答有误",
  irrelevant: "答非所问",
  incomplete: "不完整",
  slow: "太慢",
  other: "其他",
};

export const CASE_STATE_LABELS: Record<string, string> = {
  pending: "待处理",
  in_progress: "处理中",
  resolved: "已处理",
};

export const CONTENT_STATUS_LABELS: Record<string, string> = {
  available: "可读",
  truncated: "受控截断",
  capture_failed: "采集失败",
  not_collected: "未采集",
  expired: "已过期",
};

export const GRAPH_LABELS: Record<string, string> = {
  "basic-qa": "基础问答",
  "data-analysis": "数据分析",
  "intelligent-tracing": "智能溯源",
};

/** 只把非敏感维度/游标写入 URL；原文与自由文本搜索词不进入 URL。 */
export function readUrlFilter(key: string): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(key) ?? "";
}

export function replaceUrlFilters(filters: Record<string, string | null | undefined>): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(filters)) {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  }
  window.history.replaceState(null, "", url.toString());
}
