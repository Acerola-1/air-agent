import "server-only";
import { adminFetch, AdminApiError } from "@/lib/admin-api";

/** 事件台账的服务端客户端（Next BFF → admin API）。
 *
 * 受理时序（对应 change add-agent-event-ledger 设计 §3）：
 *   可信授权 → 台账受理（request/attempt/事件/outbox 同事务）→ 受控创建平台运行
 *   → 回填平台 run 关联。台账受理失败一律视为"不得启动新运行"。
 *
 * 平台不提供调用方指定 run_id 与幂等创建（本 change 任务 1.2 实测），因此响应丢失
 * 时置 admission_unknown 并交由校准匹配，**绝不**自动重发。
 */

export type AdmissionResult = {
  request_id: string;
  attempt_id: string;
  submission_id: string;
  admission_status: string;
  operation_kind: string;
  reused: boolean;
  thread_id: string;
  graph_id: string;
  business_code?: string | null;
  business_policy_version?: string | null;
  ledger_marker: string;
  capability_selection?: Record<string, unknown> | null;
};

export type StopOrigin =
  | "user_stop"
  | "navigation"
  | "tenant_switch"
  | "transport_disconnect"
  | "permission_revocation"
  | "unknown";

/** 受理提问：返回台账关联 ID 与注入平台 run metadata 的关联标记。 */
export async function admitRequest(input: {
  cookie: string;
  threadId: string;
  submissionKey: string;
  graphId: string;
  operationKind: "initial" | "retry" | "regenerate" | "resume";
  content: string;
  userMessageId?: string | null;
  sourceAttemptId?: string | null;
  storeContent?: boolean;
  /** 受控原文保留期（天）；仅与 storeContent 同用，缺省由服务端按部署配置决定。 */
  retentionDays?: number;
  tenantId: string;
  contextVersion: number;
  menuName?: string | null;
  capabilitySelection?: Record<string, unknown> | null;
}): Promise<AdmissionResult> {
  const result = await adminFetch("/agent/requests", {
    method: "POST",
    cookie: input.cookie,
    tenantContext: { tenantId: input.tenantId, contextVersion: input.contextVersion },
    body: {
      thread_id: input.threadId,
      submission_key: input.submissionKey,
      graph_id: input.graphId,
      operation_kind: input.operationKind,
      content: input.content,
      user_message_id: input.userMessageId ?? null,
      source_attempt_id: input.sourceAttemptId ?? null,
      store_content: input.storeContent ?? false,
      retention_days: input.retentionDays ?? null,
      menu_name: input.menuName ?? null,
      capability_selection: input.capabilitySelection ?? null,
    },
  });
  if (result.status !== 200) {
    // 409（同键不同内容）语义化向上透传；其余按不可用处理
    throw new AdminApiError(result.status, "台账受理失败");
  }
  return result.json as AdmissionResult;
}

export type StartClaim = {
  status:
    | "claimed"
    | "already_started"
    | "already_confirmed"
    | "mismatch"
    | "terminal"
    | "not_found"
    | "submission_not_found"
    | "submission_mismatch";
  start_claim_token?: string | null;
  platform_run_id?: string | null;
  state?: string | null;
  detail?: string | null;
};

/** 启动权原子裁决（R01）：**创建平台运行之前**必须取得启动权。
 *
 * 并发/重放同一次业务提交时只有一个调用方拿到 `claimed`；其余返回
 * `already_started`（尚无运行映射，属"待确认"）或 `already_confirmed`（已有运行，
 * 直接复用）。调用方据此**不得**再创建第二个平台运行。
 */
export async function claimStart(input: {
  cookie: string;
  submissionId: string;
  attemptId: string;
  threadId: string;
  graphId: string;
  operationKind: "initial" | "retry" | "regenerate" | "resume";
  content: string;
}): Promise<StartClaim> {
  const internal = process.env.INTERNAL_API_TOKEN ?? "";
  if (!internal) throw new AdminApiError(503, "内部凭据未配置");
  const result = await adminFetch("/agent/runs/start-claim", {
    method: "POST",
    cookie: input.cookie,
    internalToken: internal,
    body: {
      submission_id: input.submissionId,
      attempt_id: input.attemptId,
      thread_id: input.threadId,
      graph_id: input.graphId,
      operation_kind: input.operationKind,
      content: input.content,
    },
  });
  if (result.status !== 200) throw new AdminApiError(result.status, "启动权裁决失败");
  return result.json as StartClaim;
}

/** 回填平台受理结果；outcome=admission_unknown 表示上游结果不确定（不重发）。 */
export async function confirmAdmission(input: {
  cookie: string;
  requestId: string;
  attemptId: string;
  /** F02：确认按**本次提交**作用域；恢复与初次共享 attempt 时不互相改写。 */
  submissionId?: string | null;
  outcome: "confirmed" | "admission_unknown";
  platformRunId?: string | null;
  reason?: string | null;
}): Promise<void> {
  const internal = process.env.INTERNAL_API_TOKEN ?? "";
  if (!internal) throw new AdminApiError(503, "内部凭据未配置");
  const result = await adminFetch("/agent/runs/admission-confirmed", {
    method: "POST",
    cookie: input.cookie,
    internalToken: internal,
    body: {
      request_id: input.requestId,
      attempt_id: input.attemptId,
      submission_id: input.submissionId ?? null,
      outcome: input.outcome,
      platform_run_id: input.platformRunId ?? null,
      reason: input.reason ?? null,
    },
  });
  if (result.status !== 200) throw new AdminApiError(result.status, "受理结果回填失败");
}

/** 读回受理详情（F01 复用判断）：按 request 取尝试状态与平台运行映射。 */
export async function getRequestDetail(input: {
  cookie: string;
  requestId: string;
  tenantId: string;
  contextVersion: number;
}): Promise<{
  attempts?: {
    attempt_id?: string;
    state?: string | null;
    platform_run_id?: string | null;
    operation_kind?: string | null;
  }[];
} | null> {
  const result = await adminFetch(`/agent/requests/${input.requestId}`, {
    method: "GET",
    cookie: input.cookie,
    tenantContext: { tenantId: input.tenantId, contextVersion: input.contextVersion },
  });
  if (result.status !== 200) return null;
  return result.json as {
    attempts?: {
      attempt_id?: string;
      state?: string | null;
      platform_run_id?: string | null;
      operation_kind?: string | null;
    }[];
  };
}

/** 停止意图（独立于实际终态）。 */
export async function recordStopIntent(input: {
  cookie: string;
  threadId?: string | null;
  attemptId?: string | null;
  origin: StopOrigin;
  phase: "queued" | "processing" | "output" | "unknown";
  platformRunId?: string | null;
  requestOrigin?: string | null;
  tenantId: string;
  contextVersion: number;
}): Promise<{ attempt_id?: string; cancel_requested?: boolean; origin?: string }> {
  const result = await adminFetch("/agent/runs/stop-intent", {
    method: "POST",
    cookie: input.cookie,
    tenantContext: { tenantId: input.tenantId, contextVersion: input.contextVersion },
    body: {
      attempt_id: input.attemptId ?? null,
      thread_id: input.threadId ?? null,
      origin: input.origin,
      phase: input.phase,
      platform_run_id: input.platformRunId ?? null,
    },
    origin: input.requestOrigin,
  });
  if (result.status !== 200) {
    throw new AdminApiError(result.status, "停止意图记录失败");
  }
  return result.json as { attempt_id?: string; cancel_requested?: boolean; origin?: string };
}

/** 会话删除标记（立即禁读 + 异步清理）。 */
export async function requestThreadDeletion(input: {
  cookie: string;
  threadId: string;
  tenantContext?: { tenantId: string; contextVersion: number } | null;
  requestOrigin?: string | null;
}): Promise<void> {
  const result = await adminFetch("/agent/threads/deletion", {
    method: "POST",
    cookie: input.cookie,
    tenantContext: input.tenantContext ?? null,
    body: { thread_id: input.threadId },
    origin: input.requestOrigin,
  });
  if (result.status !== 200) throw new AdminApiError(result.status, "删除标记记录失败");
}

/** 从 run metadata 构造台账关联标记（与 admin ledger_marker 形状一致）。 */
export function ledgerMarker(requestId: string, attemptId: string): string {
  return `lr:${requestId}:${attemptId}`;
}

/** 解析台账关联标记（校准与确认时校验用）。 */
export function parseLedgerMarker(marker: unknown): { requestId: string; attemptId: string } | null {
  if (typeof marker !== "string") return null;
  const parts = marker.split(":");
  if (parts.length !== 3 || parts[0] !== "lr" || !parts[1] || !parts[2]) return null;
  return { requestId: parts[1], attemptId: parts[2] };
}
