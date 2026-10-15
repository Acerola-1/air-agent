import { Client } from "@langchain/langgraph-sdk";

/** LangGraph API 直连层（原生 fetch 语义，不走 assistant-ui）。
 *
 *  apiUrl 固定走同源代理 /api（见 app/api/[..._path]/route.ts），浏览器免 CORS，
 *  且代理已注入 x-api-key。持久化全部由后端 Postgres checkpointer 承担：
 *  - 线程列表：threads.search 按 metadata.{graph_id, user_id} 过滤
 *  - 历史：threads.getState 返回完整 messages
 *  - 标题/归属：threads.update / threads.create 写 metadata
 *  - 对话：runs.stream（SSE，values + messages 双流）
 */

/** LangGraph Thread Search / Get 返回的线程项 */
export type LgThread = {
  thread_id: string;
  metadata?:
    | (Record<string, unknown> & { graph_id?: string; title?: string; user_id?: string })
    | null;
  created_at?: string;
  updated_at?: string;
};

/** 后端 API 地址：浏览器端固定走同源代理 /api（见 app/api/[..._path]/route.ts），
 *  免 CORS 且代理已注入 x-api-key。langgraph-sdk 内部用 URL 构造，必须给绝对地址。 */
function apiBaseUrl(): string {
  if (typeof window !== "undefined") return new URL("/api", window.location.href).href;
  return "http://localhost:2024";
}

/** R02：页面感知的企业上下文（会话 store 写入）。每个业务请求携带
 *  `x-tenant-id` / `x-tenant-context-version`，由服务端与实时会话比对——
 *  旧标签页或上下文丢失的请求不可能再悄悄落入切换后的企业。 */
export type TenantRequestContext = { tenantId: string; contextVersion: number };
let tenantRequestContext: TenantRequestContext | null = null;

export function setTenantRequestContext(next: TenantRequestContext | null): void {
  tenantRequestContext = next;
}

export function getTenantRequestContext(): TenantRequestContext | null {
  return tenantRequestContext;
}

/** 稳定提交键上下文（台账受理幂等锚点）。
 *
 *  浏览器生成 `submission_key` 与 `user_message_id` 作为**去重候选**（不是可信
 *  身份）：同一业务提交的重试必须复用同一键，服务端据可信上下文 + 线程归属计算
 *  规范摘要做幂等判定；同键不同内容返回 409。 */
export type SubmissionContext = {
  submissionKey: string;
  userMessageId?: string | null;
  operationKind?: "initial" | "retry" | "regenerate" | "resume";
  capabilitySelection?: Record<string, unknown> | null;
};
let submissionContext: SubmissionContext | null = null;

export function setSubmissionContext(next: SubmissionContext | null): void {
  submissionContext = next;
}

/** 生成稳定提交键（同一次用户提交的重试复用同一键）。 */
export function newSubmissionKey(): string {
  return crypto.randomUUID().replace(/-/g, "");
}

function tenantHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (tenantRequestContext) {
    headers["x-tenant-id"] = tenantRequestContext.tenantId;
    headers["x-tenant-context-version"] = String(tenantRequestContext.contextVersion);
  }
  if (submissionContext) {
    headers["x-submission-key"] = submissionContext.submissionKey;
    if (submissionContext.userMessageId) headers["x-user-message-id"] = submissionContext.userMessageId;
    if (submissionContext.operationKind) headers["x-operation-kind"] = submissionContext.operationKind;
    if (submissionContext.capabilitySelection) {
      headers["x-capability-selection"] = JSON.stringify(submissionContext.capabilitySelection);
    }
  }
  return headers;
}

/** R02：服务端 409 context_changed 的订阅回调（session-client 注册）。 */
let conflictListener: (() => void) | null = null;

export function onTenantContextConflict(fn: (() => void) | null): void {
  conflictListener = fn;
}

export function isContextConflictError(err: unknown): boolean {
  const status = (err as { status?: number })?.status;
  return status === 409;
}

/** 错误路径统一入口：409 上下文冲突触发订阅方刷新，其余原样上抛。 */
export function notifyIfContextConflict(err: unknown): void {
  if (isContextConflictError(err)) conflictListener?.();
}

function apiClient(): Client {
  // SDK v1（本项目锁定版本）经 `onRequest` 钩子注入每次请求头
  return new Client({
    apiUrl: apiBaseUrl(),
    onRequest: (_url: URL, init: RequestInit) => {
      const headers = new Headers(init.headers);
      for (const [key, value] of Object.entries(tenantHeaders())) headers.set(key, value);
      return { ...init, headers };
    },
  });
}

/** 按 graph_id + user_id 搜索当前用户的线程（无 user_id 时退化为仅按 graph 过滤） */
export async function searchThreads(
  graphId: string,
  userId?: string,
): Promise<LgThread[]> {
  const metadata: Record<string, string> = { graph_id: graphId };
  if (userId) metadata.user_id = userId;
  const res = await apiClient().threads.search({ metadata, limit: 50 });
  return Array.isArray(res) ? res : (res as { threads?: LgThread[] }).threads ?? [];
}

/** 创建线程并写入 graph_id + user_id（+ 可选 title），返回新 threadId.
 *
 *  F01：线程 ID 由聊天代理铸造并以代理响应为准——客户端不再自选 ID，
 *  因此"自选 ID 吸收未知历史线程"经 BFF 不可达；后续一切读写/run 都使用
 *  响应中的真实 thread_id。graph_id 必须放 metadata（容器版 langgraph-api
 *  忽略顶层 graphId 参数，只认 metadata.graph_id）。 */
export async function createThread(
  graphId: string,
  userId?: string,
  title?: string,
): Promise<string> {
  const metadata: Record<string, unknown> = { graph_id: graphId };
  if (userId) metadata.user_id = userId;
  if (title) metadata.title = title;
  const created = (await apiClient().threads.create({ metadata })) as unknown as {
    thread_id?: string;
  };
  const threadId = String(created?.thread_id ?? "");
  if (!threadId) throw new Error("会话创建未返回标识");
  return threadId;
}

export type ContextMetrics = {
  model: string; window_tokens: number; input_budget_tokens: number; input_tokens: number;
  output_reserved_tokens: number; safety_tokens: number; measurement_source: string;
  input_breakdown_tokens?: { system: number; conversation: number; tools: number; framing: number };
  tool_count?: number;
  latest_usage?: { input_tokens?: number; output_tokens?: number; total_tokens?: number };
};
export type ProcessingStatus = {
  type: string; stage: "history" | "data"; status: "started" | "completed" | "failed";
  thread_id?: string; run_id?: string; input_before?: number; input_after?: number; kept_turns?: number; message?: string;
  processing_id?: string; artifact_id?: string;
  artifacts?: Record<string, { status: "started" | "completed" | "failed"; message?: string }>;
};
/** 资源归属拒绝（跨企业/已删除）：与网络故障区分文案，避免误导"重试即可"。 */
export class ThreadAccessError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export type ThreadValues = { messages: unknown[]; context_metrics?: ContextMetrics; context_status?: ProcessingStatus; data_status?: ProcessingStatus };

/** 摘要为内部 state，界面仅消费消息和公开统计。 */
export async function getThreadState(threadId: string): Promise<ThreadValues> {
  let state: Awaited<ReturnType<ReturnType<typeof apiClient>["threads"]["getState"]>>;
  try {
    state = await apiClient().threads.getState(threadId);
  } catch (err) {
    const status = (err as { status?: number })?.status;
    if (status === 404 || status === 403) {
      throw new ThreadAccessError("此会话属于其他企业或已被删除。", status);
    }
    throw err;
  }
  const values = (state as unknown as { values?: ThreadValues }).values;
  return { messages: Array.isArray(values?.messages) ? values.messages : [], context_metrics: values?.context_metrics, context_status: values?.context_status, data_status: values?.data_status };
}

export async function cancelRun(threadId: string, runId: string): Promise<void> {
  await apiClient().runs.cancel(threadId, runId, false, "interrupt");
}

/** R10：读取单个运行的权威状态（停止确认轮询用）。404 视为未找到。 */
export type RunObservation = { found: boolean; status: string | null };

export async function getRunStatus(threadId: string, runId: string): Promise<RunObservation> {
  try {
    const run = await apiClient().runs.get(threadId, runId);
    return { found: true, status: (run as { status?: string }).status ?? null };
  } catch (err) {
    if ((err as { status?: number })?.status === 404) return { found: false, status: null };
    notifyIfContextConflict(err);
    throw err;
  }
}

/** F05：停止未拿到 run_id 时，从运行列表找回仍在执行的运行。 */
export async function listActiveRunIds(threadId: string): Promise<string[]> {
  return (await listRecentRuns(threadId))
    .filter((r) => ["pending", "running"].includes(String(r.status)))
    .map((r) => String(r.run_id ?? ""))
    .filter(Boolean);
}

/** F05：运行列表（含状态）——空活动列表不能当作已取消，需要看到可信状态。 */
export type RunSummary = { run_id: string; status: string | null; submission_key?: string | null };

export async function listRecentRuns(threadId: string): Promise<RunSummary[]> {
  const runs = (await apiClient().runs.list(threadId, { limit: 5 })) as unknown as {
    run_id?: string;
    status?: string;
    metadata?: { ledger_submission_key?: unknown };
  }[];
  return (Array.isArray(runs) ? runs : [])
    .map((r) => ({ run_id: String(r.run_id ?? ""), status: r.status == null ? null : String(r.status),
      submission_key: typeof r.metadata?.ledger_submission_key === "string" ? r.metadata.ledger_submission_key : null }))
    .filter((r) => r.run_id);
}

export type ArchivePage = { content: string; offset: number; next_offset: number | null; char_length: number };
export async function readArchive(threadId: string, artifactId: string, offset = 0): Promise<ArchivePage> {
  const response = await fetch(`/api/archives/${encodeURIComponent(threadId)}/${encodeURIComponent(artifactId)}?offset=${offset}`, {
    cache: "no-store",
    headers: tenantHeaders(),
  });
  if (response.status === 409) {
    notifyIfContextConflict({ status: 409 });
    throw new Error("企业上下文已变更，请刷新后重试。");
  }
  if (!response.ok) throw new Error(response.status === 410 ? "资料已过期，请重新查询。" : response.status === 403 || response.status === 404 ? "此会话无法读取该资料。" : "资料暂时无法读取，请重试。");
  return response.json();
}

/** 更新线程 metadata（标题/用户归属等） */
export async function updateThreadMetadata(
  threadId: string,
  metadata: Record<string, unknown>,
): Promise<void> {
  await apiClient().threads.update(threadId, { metadata });
}

export async function deleteThread(threadId: string): Promise<void> {
  await apiClient().threads.delete(threadId);
}

export type StreamHandlers = {
  onValues: (values: ThreadValues) => void;
  onMessagesDelta?: (messages: [unknown, unknown][]) => void;
  onCustom?: (event: Record<string, unknown>) => void;
  onRun?: (runId: string) => void;
  onDone?: (error?: Error) => void;
  onTransportDisconnect?: () => void;
};

export async function streamRun(opts: {
  threadId: string; graphId: string; messages: { id?: string; type: string; content: unknown }[];
  config?: { configurable?: Record<string, unknown> }; signal?: AbortSignal; handlers: StreamHandlers;
}): Promise<void> {  const { threadId, graphId, messages, config, signal, handlers } = opts;
  let upstreamError = false;
  try {
    const stream = await apiClient().runs.stream(threadId, graphId, {
      input: { messages }, config, streamMode: ["values", "messages-tuple", "custom"], signal,
      onDisconnect: "cancel",
    });
    for await (const chunk of stream) {
      // 即使用户已停止，也需接收首次 metadata 以取消已创建的 run。
      if (chunk.event === "metadata") {
        const data = chunk.data as { run_id?: string };
        if (data?.run_id) handlers.onRun?.(data.run_id);
      }
      if (signal?.aborted) break;
      if (chunk.event === "error") { upstreamError = true; throw new Error("本次处理失败，已确认的内容仍保留。请重试。"); }
      if (chunk.event === "values") {
        const values = chunk.data as ThreadValues;
        if (Array.isArray(values?.messages)) handlers.onValues(values);
      } else if (chunk.event === "custom") {
        const data = chunk.data;
        if (data && typeof data === "object") handlers.onCustom?.(data as Record<string, unknown>);
      } else if (chunk.event === "messages") {
        const tuple = chunk.data as [unknown, unknown];
        if (Array.isArray(tuple)) handlers.onMessagesDelta?.([tuple]);
      }
    }
    handlers.onDone?.();
  } catch (error) {
    if (!signal?.aborted && !upstreamError) handlers.onTransportDisconnect?.();
    handlers.onDone?.(signal?.aborted ? undefined : error instanceof Error ? error : new Error("本次处理失败，请重试。"));
  }
}

/** 台账受理关联确认（流式运行拿到 run_id 后调用同源 BFF）。
 *
 *  服务端会回读平台 run metadata 校验本次台账标记后才确认；客户端伪造/错配不会
 *  被接受。失败不抛（保持"受理中"，由校准处理），也不改写本地终态。
 */
export async function confirmRunAdmission(
  threadId: string,
  runId: string,
  onConfirmedAttempt?: (attemptId: string, serverSelection?: Record<string, unknown> | null) => void,
): Promise<{ confirmed: boolean; attempt_id?: string; capability_selection?: Record<string, unknown> | null } | boolean> {
  try {
    const res = await fetch("/api/agent-runs/confirm", {
      method: "POST",
      headers: { "content-type": "application/json", ...tenantHeaders() },
      body: JSON.stringify({ thread_id: threadId, run_id: runId }),
      cache: "no-store",
    });
    if (!res.ok) return false;
    const data = (await res.json()) as { confirmed?: boolean; attempt_id?: string; capability_selection?: Record<string, unknown> | null };
    const confirmed = Boolean(data.confirmed);
    if (confirmed && typeof data.attempt_id === "string") {
      onConfirmedAttempt?.(data.attempt_id, data.capability_selection ?? null);
    }
    return {
      confirmed,
      attempt_id: data.attempt_id,
      capability_selection: data.capability_selection ?? null,
    };
  } catch {
    return false;
  }
}

/** 记录停止意图（独立于实际终态；不冒充已取消）。 */
export async function recordStopIntent(input: {
  threadId?: string | null;
  runId?: string | null;
  origin: "user_stop" | "navigation" | "tenant_switch" | "transport_disconnect" | "permission_revocation" | "unknown";
  phase: "queued" | "processing" | "output" | "unknown";
}): Promise<boolean> {
  try {
    const res = await fetch("/api/agent-runs/stop-intent", {
      method: "POST",
      headers: { "content-type": "application/json", ...tenantHeaders() },
      body: JSON.stringify({
        thread_id: input.threadId ?? null,
        platform_run_id: input.runId ?? null,
        origin: input.origin,
        phase: input.phase,
      }),
      cache: "no-store",
    });
    return res.ok;
  } catch {
    return false;
  }
}

/** 页面卸载时的停止意图（导航离开/刷新）：sendBeacon 尽力送达，失败不阻塞卸载。
 *
 * 与 `recordStopIntent` 的区别：卸载期间无法读取响应，因此不回传结果；服务端仍按
 * 可信会话 + 企业上下文记录**独立停止意图**（不冒充已取消）。origin=navigation
 * 表示"页面导航离开"，与用户主动点击停止（user_stop）区分。
 */
export function recordNavigationStopBeacon(input: {
  threadId?: string | null;
  runId?: string | null;
  origin?: "navigation" | "transport_disconnect";
}): boolean {
  if (typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") return false;
  const body = JSON.stringify({
    thread_id: input.threadId ?? null,
    platform_run_id: input.runId ?? null,
    origin: input.origin ?? "navigation",
    phase: input.runId ? "processing" : "unknown",
  });
  try {
    return navigator.sendBeacon(
      "/api/agent-runs/stop-intent",
      new Blob([body], { type: "application/json" })
    );
  } catch {
    return false;
  }
}

/** 从首条用户消息推导会话标题（与旧版前端一致：清理无意义前缀 + 截断 20 字） */
export function deriveTitle(text: string): string {
  const cleaned = text
    .replace(/^(请|帮我|麻烦|想问|请问|我想知道|我想要|我需要|帮忙|求)[，。！？\s、]*/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const effective = cleaned || text.trim();
  return effective.length > 20 ? `${effective.slice(0, 20)}…` : effective;
}
