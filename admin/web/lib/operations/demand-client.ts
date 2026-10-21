/** 需求洞察客户端：同源 BFF、企业声明头、no-store、可取消。
 *
 * 与 6 的 console client 独立（7 不导入 6 的 router/SQL），但复用其错误语义：
 * 只有 context_changed 触发企业上下文刷新提示，revision_conflict 等留在页面。
 */

import { getTenantRequestClaim, notifyTenantContextConflict } from "@/lib/api";
import { ConsoleApiError } from "./client";
import type {
  InsightsActionResponse,
  JobActionIn,
  JobCreateIn,
  JobItem,
  JobsResponse,
  OverviewResponse,
  ProfileActionIn,
  QuestionActionIn,
  QuestionDetail,
  QuestionsResponse,
  SourcesResponse,
  TopicsResponse,
  UnclassifiedResponse,
} from "./demand-types";

function messageOf(body: unknown): { message: string; code?: string } {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return { message: detail };
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const nested = detail as { error?: unknown; code?: unknown };
    return {
      message: typeof nested.error === "string" ? nested.error : "请求失败，请稍后重试",
      code: typeof nested.code === "string" ? nested.code : undefined,
    };
  }
  return { message: "请求失败，请稍后重试" };
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {}
): Promise<T> {
  const headers = new Headers();
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  const claim = getTenantRequestClaim();
  if (claim) {
    headers.set("x-tenant-id", claim.tenantId || "none");
    headers.set("x-tenant-context-version", String(claim.version));
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "include",
      cache: "no-store",
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ConsoleApiError(0, "无法连接后台服务，请确认 admin API 已启动");
  }
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) {
    const { message, code } = messageOf(body);
    const contextChanged =
      response.status === 409 &&
      (code === "context_changed" || (!code && message.startsWith("企业上下文")));
    if (contextChanged) notifyTenantContextConflict();
    throw new ConsoleApiError(response.status, message, code);
  }
  return body as T;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export type WindowQuery = { window: string; start?: string; end?: string };
export type Rank = "intents" | "users" | "requests";

export type ListFilters = WindowQuery & {
  business_code?: string;
  graph_id?: string;
  action?: string;
  pollutant?: string;
  city_code?: string;
  topic_id?: string;
  analysis_version?: string;
  assignment_revision?: number;
  rank?: Rank;
  cursor?: string;
  limit?: number;
};

export const demandApi = {
  overview: (params: WindowQuery & { business_code?: string; graph_id?: string }, signal?: AbortSignal) =>
    request<OverviewResponse>(`/agent/insights/overview${query(params)}`, { signal }),
  questions: (params: ListFilters, signal?: AbortSignal) =>
    request<QuestionsResponse>(`/agent/insights/questions${query(params)}`, { signal }),
  topics: (params: ListFilters, signal?: AbortSignal) =>
    request<TopicsResponse>(`/agent/insights/topics${query(params)}`, { signal }),
  question: (clusterId: string, params: WindowQuery, signal?: AbortSignal) =>
    request<QuestionDetail>(
      `/agent/insights/questions/${encodeURIComponent(clusterId)}${query(params)}`,
      { signal }
    ),
  sources: (clusterId: string, params: { limit?: number; offset?: number }, signal?: AbortSignal) =>
    request<SourcesResponse>(
      `/agent/insights/questions/${encodeURIComponent(clusterId)}/sources${query(params)}`,
      { signal }
    ),
  unclassified: (params: WindowQuery & { limit?: number; offset?: number }, signal?: AbortSignal) =>
    request<UnclassifiedResponse>(`/agent/insights/unclassified${query(params)}`, { signal }),
  jobs: (signal?: AbortSignal) => request<JobsResponse>("/agent/insights/jobs", { signal }),
  job: (jobId: string, signal?: AbortSignal) =>
    request<JobItem>(`/agent/insights/jobs/${encodeURIComponent(jobId)}`, { signal }),
  createJob: (payload: JobCreateIn) =>
    request<InsightsActionResponse>("/agent/insights/jobs", { method: "POST", body: payload }),
  jobAction: (jobId: string, payload: JobActionIn) =>
    request<InsightsActionResponse>(`/agent/insights/jobs/${encodeURIComponent(jobId)}/actions`, {
      method: "POST",
      body: payload,
    }),
  questionAction: (clusterId: string, payload: QuestionActionIn) =>
    request<InsightsActionResponse>(
      `/agent/insights/questions/${encodeURIComponent(clusterId)}/actions`,
      { method: "POST", body: payload }
    ),
  rebuildProfile: (profileId: string, payload: ProfileActionIn) =>
    request<InsightsActionResponse>(
      `/agent/insights/profiles/${encodeURIComponent(profileId)}/rebuild`,
      { method: "POST", body: payload }
    ),
  activateProfile: (profileId: string, payload: ProfileActionIn) =>
    request<InsightsActionResponse>(
      `/agent/insights/profiles/${encodeURIComponent(profileId)}/activate`,
      { method: "POST", body: payload }
    ),
};

export function newOperationId(): string {
  return crypto.randomUUID();
}
