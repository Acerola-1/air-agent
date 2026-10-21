/** 运营后台 API 客户端：同源 BFF、企业声明头、no-store、可取消。
 *
 * 与 lib/api.ts 的区别：运营端点的 409 有明确业务 code（revision_conflict /
 * feedback_changed / event_conflict），不能像旧客户端那样把所有 409 当成企业切换；
 * 只有 `context_changed` 才提示刷新当前会话。
 */

import { getTenantRequestClaim, notifyTenantContextConflict } from "@/lib/api";
import type {
  CapabilityGroup,
  ContentPage,
  CostsResponse,
  FeedbackActionResponse,
  FeedbackItem,
  ListResponse,
  OverviewResponse,
  PerformanceResponse,
  RunDetail,
  RunItem,
  TrendsResponse,
  UserUsage,
} from "./types";

export class ConsoleApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = "ConsoleApiError";
    this.status = status;
    this.code = code;
  }
}

function messageOf(body: unknown): { message: string; code?: string } {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return { message: detail };
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const nested = detail as { error?: unknown; code?: unknown };
    return {
      message:
        typeof nested.error === "string"
          ? nested.error
          : "请求失败，请稍后重试",
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
    // 兼容身份依赖的既有字符串响应；普通反馈修订409不触发会话刷新。
    const contextChanged = response.status === 409 &&
      (code === "context_changed" || (!code && message.startsWith("企业上下文")));
    if (contextChanged) notifyTenantContextConflict();
    throw new ConsoleApiError(response.status, message, code);
  }
  return body as T;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export type WindowQuery = {
  window: string;
  start?: string;
  end?: string;
};

export const operationsApi = {
  overview: (params: WindowQuery & { business_code?: string; graph_id?: string }, signal?: AbortSignal) =>
    request<OverviewResponse>(
      `/agent/console/overview${query(params)}`,
      { signal }
    ),
  trends: (
    params: WindowQuery & { business_code?: string; graph_id?: string; metric?: string },
    signal?: AbortSignal
  ) => request<TrendsResponse>(`/agent/console/trends${query(params)}`, { signal }),
  users: (
    params: WindowQuery & {
      business_code?: string;
      graph_id?: string;
      user_id?: string;
      cursor?: string;
      limit?: number;
    },
    signal?: AbortSignal
  ) => request<ListResponse<UserUsage>>(`/agent/console/users${query(params)}`, { signal }),
  runs: (
    params: WindowQuery & {
      business_code?: string;
      graph_id?: string;
      user_id?: string;
      state?: string;
      thread_id?: string;
      cursor?: string;
      limit?: number;
    },
    signal?: AbortSignal
  ) => request<ListResponse<RunItem>>(`/agent/console/runs${query(params)}`, { signal }),
  run: (attemptId: string, signal?: AbortSignal) =>
    request<RunDetail>(`/agent/console/runs/${encodeURIComponent(attemptId)}`, { signal }),
  runObservations: (attemptId: string, signal?: AbortSignal) =>
    request<Record<string, unknown>>(
      `/agent/observability/runs/${encodeURIComponent(attemptId)}`,
      { signal }
    ),
  requestContent: (requestId: string, offset: number, limit: number, signal?: AbortSignal) =>
    request<ContentPage>(
      `/agent/console/requests/${encodeURIComponent(requestId)}/content${query({ offset, limit })}`,
      { signal }
    ),
  answerContent: (
    answerId: string,
    contentVersion: number,
    offset: number,
    limit: number,
    signal?: AbortSignal
  ) =>
    request<ContentPage>(
      `/agent/console/answers/${encodeURIComponent(answerId)}/content${query({
        content_version: contentVersion,
        offset,
        limit,
      })}`,
      { signal }
    ),
  feedback: (
    params: WindowQuery & {
      business_code?: string;
      graph_id?: string;
      rating?: string;
      reason?: string;
      case_state?: string;
      cursor?: string;
      limit?: number;
    },
    signal?: AbortSignal
  ) => request<ListResponse<FeedbackItem>>(`/agent/console/feedback${query(params)}`, { signal }),
  feedbackHistory: (
    answerId: string,
    params: { kind: "rating" | "case"; cursor?: string; limit?: number },
    signal?: AbortSignal
  ) =>
    request<ListResponse<Record<string, string | number | boolean | null>>>(
      `/agent/console/feedback/${encodeURIComponent(answerId)}/history${query(params)}`,
      { signal }
    ),
  feedbackContent: (
    answerId: string,
    params: {
      kind: "comment" | "note";
      ref?: string;
      offset?: number;
      limit?: number;
    },
    signal?: AbortSignal
  ) =>
    request<ContentPage>(
      `/agent/console/feedback/${encodeURIComponent(answerId)}/content${query(params)}`,
      { signal }
    ),
  feedbackAction: (
    answerId: string,
    body: {
      operation_id: string;
      expected_revision: number;
      expected_feedback_revision: number;
      action: "start" | "resolve" | "reopen";
      note?: string | null;
    }
  ) =>
    request<FeedbackActionResponse>(
      `/agent/console/feedback/${encodeURIComponent(answerId)}/actions`,
      { method: "POST", body }
    ),
  capabilities: (
    params: WindowQuery & {
      business_code?: string;
      graph_id?: string;
      group_by: "business" | "skill" | "tool";
      cursor?: string;
      limit?: number;
    },
    signal?: AbortSignal
  ) => request<ListResponse<CapabilityGroup>>(`/agent/console/capabilities${query(params)}`, { signal }),
  capabilityDetails: (
    params: WindowQuery & {
      kind: "skill_span" | "tool_call";
      business_code?: string;
      graph_id?: string;
      skill_id?: string;
      service_key?: string;
      tool_id?: string;
      cursor?: string;
      limit?: number;
    },
    signal?: AbortSignal
  ) =>
    request<ListResponse<Record<string, string | number | boolean | null>>>(
      `/agent/console/capabilities/details${query(params)}`,
      { signal }
    ),
  performance: (
    params: WindowQuery & {
      business_code?: string;
      graph_id?: string;
      model?: string;
      provider?: string;
      source?: string;
    },
    signal?: AbortSignal
  ) => request<PerformanceResponse>(`/agent/console/performance${query(params)}`, { signal }),
  costs: (
    params: WindowQuery & {
      business_code?: string;
      graph_id?: string;
      model?: string;
      provider?: string;
      group_by?: string;
      cursor?: string;
      limit?: number;
    },
    signal?: AbortSignal
  ) => request<CostsResponse>(`/agent/console/costs${query(params)}`, { signal }),
};

/** 浏览器可用的 operation_id（UUID v4）。 */
export function newOperationId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const random = (Math.random() * 16) | 0;
    const value = char === "x" ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}
