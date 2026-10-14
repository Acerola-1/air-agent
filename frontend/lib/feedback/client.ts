"use client";

/** 回答反馈客户端（change add-chat-feedback-interactions）。
 *
 * 职责（design §3/§4）：
 * - 回答映射：通过台账本人回读接口把 UI 消息（message_id）映射到服务端权威
 *   answer_id；只有已确认映射才开放评价/复制统计；歧义/无匹配 → 不可评价，
 *   不按数组下标或"最近 attempt"猜测。
 * - 反馈操作：operation_id 幂等 + expected_revision CAS；失败保留本地选择与
 *   意见，重试复用原 operation_id；企业/用户切换丢弃缓存并忽略迟到回调。
 * - 采集队列：有界投递（最多 100 条），只投递浏览器三类型；切换身份立即清空。
 */

export type FeedbackRating = "positive" | "negative" | "none";
export type FeedbackReasonCode =
  | "answer_incorrect"
  | "irrelevant"
  | "incomplete"
  | "slow"
  | "other";

export type AnswerMapping = {
  answer_id: string;
  message_id: string | null;
  attempt_id: string;
  request_id: string;
  thread_id: string;
  content_version: number;
  rating: FeedbackRating | null;
  reason_code: FeedbackReasonCode | null;
  revision: number;
  feedbackable: boolean;
};

export type FeedbackState = {
  rating: FeedbackRating | null;
  reasonCode: FeedbackReasonCode | null;
  revision: number;
  saving: boolean;
  error: "network" | "conflict" | "rejected" | null;
  /** 保存失败时保留的待提交意见（用户输入不丢） */
  pendingComment: string | null;
};

type FeedbackScopeKey = string; // `${tenantId}:${userId}`

const MAX_QUEUE = 100;
const BATCH_LIMIT = 50;

// ------------------------------------------------------------------ 会话作用域

let scopeKey: FeedbackScopeKey = "";
let scopeEpoch = 0;
let contextVersion = 0;
let answerCache = new Map<string, AnswerMapping>(); // key: message_id or answer_id
let opCounter = 0;
let collectionEnabled = false;
const policyListeners = new Set<() => void>();
export function feedbackEnabled(): boolean { return collectionEnabled; }
export function subscribeFeedbackPolicy(listener: () => void): () => void {
  policyListeners.add(listener);
  return () => { policyListeners.delete(listener); };
}
async function refreshCollectionPolicy(): Promise<void> {
  const epoch = scopeEpoch;
  try {
    const response = await fetch("/api/feedback/policy", {cache: "no-store", headers: scopeHeaders()});
    const body = response.ok ? await response.json() : null;
    if (epoch !== scopeEpoch) return;
    collectionEnabled = body?.enabled === true;
    if (!collectionEnabled) { queue.length = 0; measurements.clear(); }
    policyListeners.forEach(listener => listener());
  } catch { /* 请求失败保持关闭，下一次映射回读可重试。 */ }
}

export function setFeedbackScope(tenantId: string, userId: string, version = 0): void {
  const next = `${tenantId}:${userId}`;
  if (next !== scopeKey || version !== contextVersion) {
    // 企业/用户切换：清空映射/缓存/队列，迟到异步结果一律丢弃
    scopeKey = next;
    contextVersion = version;
    scopeEpoch += 1;
    measurements.clear();
    opIds.clear();
    answerCache = new Map();
    queue.length = 0;
    inFlight.clear();
    collectionEnabled = false;
    policyListeners.forEach(listener => listener());
    if (tenantId && userId) void refreshCollectionPolicy();
  }
}

export function currentFeedbackScope(): FeedbackScopeKey {
  return scopeKey;
}

function scopeHeaders(): Record<string, string> {
  return {"x-feedback-scope": scopeKey, "x-feedback-context-version": String(contextVersion)};
}
export function currentFeedbackEpoch(): number { return scopeEpoch; }

// ------------------------------------------------------------------ 映射回读

export function cachedAnswerByMessage(messageId: string, threadId?: string | null): AnswerMapping | null {
  return answerCache.get(`m:${threadId ?? ""}:${messageId}`) ?? null;
}

export function cachedAnswerById(answerId: string): AnswerMapping | null {
  return answerCache.get(`a:${answerId}`) ?? null;
}

/** 从台账本人回读接口刷新映射（最多 50 个 message_id）。 */
export async function loadAnswerMappings(
  messageIds: string[], force = false, threadId?: string | null
): Promise<void> {
  if (!collectionEnabled) void refreshCollectionPolicy();
  const ids = messageIds.filter(Boolean).slice(0, 50);
  if (!ids.length) return;
  const missing = force ? ids : ids.filter((id) => !answerCache.has(`m:${threadId ?? ""}:${id}`));
  if (!missing.length) return;
  const epochAtCall = scopeEpoch;
  try {
    const res = await fetch(
      `/api/feedback/answers?ids=${encodeURIComponent(missing.join(","))}${threadId ? `&thread_id=${encodeURIComponent(threadId)}` : ""}`,
      { cache: "no-store", headers: scopeHeaders() }
    );
    if (!res.ok) return;
    const body = (await res.json()) as { items?: AnswerMapping[] };
    if (epochAtCall !== scopeEpoch) return;
    const groups = new Map<string, AnswerMapping[]>();
    for (const item of body.items ?? []) {
      if (threadId && item.thread_id !== threadId) continue;
      if (item.message_id) groups.set(item.message_id, [...(groups.get(item.message_id) ?? []), item]);
      answerCache.set(`a:${item.answer_id}`, item);
    }
    for (const [messageId, items] of groups) {
      const key = `m:${threadId ?? ""}:${messageId}`;
      if (items.length === 1) answerCache.set(key, items[0]);
      else answerCache.delete(key); // 无法证明具体版本时禁用，不能取最后一项。
    }
  } catch {
    // 网络失败：保持未映射状态（禁用评价），下次刷新重试
  }
}

/** 服务端权威映射刷新（例如刚生成完的回答）。 */
export function putAnswerMapping(item: AnswerMapping): void {
  if (item.message_id) answerCache.set(`m:${item.thread_id}:${item.message_id}`, item);
  answerCache.set(`a:${item.answer_id}`, item);
}

// ------------------------------------------------------------------ 反馈操作

export type FeedbackOutcome =
  | { ok: true; rating: FeedbackRating; revision: number }
  | { ok: false; reason: "network" | "conflict" | "rejected" | "unmapped" };

const opIds = new Map<string, string>(); // answer_id → 最新未完成 operation_id

export function newOperationId(): string {
  opCounter += 1;
  return `op-${Date.now().toString(36)}-${opCounter}-${Math.random().toString(36).slice(2, 8)}`;
}

export async function submitFeedback(input: {
  answerId: string;
  rating: FeedbackRating;
  reasonCode?: FeedbackReasonCode | null;
  comment?: string | null;
  expectedRevision: number;
  operationId: string;
}): Promise<FeedbackOutcome> {
  const epochAtCall = scopeEpoch;
  try {
    const res = await fetch(`/api/feedback/answers/${encodeURIComponent(input.answerId)}`, {
      method: "PUT",
      headers: { "content-type": "application/json", ...scopeHeaders() },
      body: JSON.stringify({
        rating: input.rating,
        reason_code: input.reasonCode ?? null,
        comment: input.comment ?? null,
        expected_revision: input.expectedRevision,
        operation_id: input.operationId,
      }),
      cache: "no-store",
    });
    if (res.status === 409) {
      const detail = (await res.json().catch(() => null)) as { detail?: { code?: string } } | null;
      if (epochAtCall !== scopeEpoch) return { ok: false as const, reason: "network" as const };
      return {
        ok: false as const,
        reason: detail?.detail?.code === "event_conflict" ? ("rejected" as const) : ("conflict" as const),
      };
    }
    if (!res.ok) {
      if (epochAtCall !== scopeEpoch) return { ok: false as const, reason: "network" as const };
      return { ok: false as const, reason: "rejected" as const };
    }
    const body = (await res.json()) as {
      rating: FeedbackRating;
      revision: number;
      replayed: boolean;
      reason_code?: FeedbackReasonCode | null;
    };
    if (epochAtCall !== scopeEpoch) return { ok: false as const, reason: "network" as const };
    const cached = answerCache.get(`a:${input.answerId}`);
    if (cached && body.revision >= cached.revision) {
      const fresh = {...cached, rating: body.rating, revision: body.revision, reason_code: body.reason_code ?? null};
      answerCache.set(`a:${input.answerId}`, fresh);
      for (const [key, value] of answerCache) {
        if (key.startsWith("m:") && value.answer_id === input.answerId) answerCache.set(key, fresh);
      }
    }
    return { ok: true as const, rating: body.rating, revision: body.revision };
  } catch {

    return { ok: false as const, reason: "network" as const };
  }
}

// ------------------------------------------------------------------ 事件投递队列

type QueueEvent = {
  schema_version: string;
  event_id: string;
  event_type: "browser.copy" | "browser.timing" | "browser.feedback";
  occurred_at: string;
  payload: Record<string, unknown>;
  attempts: number;
  scopeEpoch: number;
};

const queue: QueueEvent[] = [];
const inFlight = new Map<string, string>(); // operation_id → answer_id
let flushing = false;

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function enqueueBrowserEvent(
  eventType: QueueEvent["event_type"],
  payload: Record<string, unknown>
): void {
  if (!collectionEnabled || queue.length >= MAX_QUEUE) return; // 有界：满则丢弃新事件（不积压）
  queue.push({
    schema_version: "1.0",
    event_id: uuid(),
    event_type: eventType,
    occurred_at: new Date().toISOString(),
    payload,
    attempts: 0,
    scopeEpoch,
  });
  void flushQueue();
}

const MAX_RETRIES = 3;

async function flushQueue(): Promise<void> {
  if (flushing || !queue.length) return;
  flushing = true;
  const epochAtCall = scopeEpoch;
  try {
    while (queue.length && epochAtCall === scopeEpoch) {
      const batch: QueueEvent[] = [];
      for (const item of queue.slice(0, BATCH_LIMIT)) {
        const proposal = [...batch, item];
        if (new TextEncoder().encode(JSON.stringify({events: proposal.map(({attempts: _a, scopeEpoch: _s, ...e}) => e)})).length > 256*1024) {
          if (!batch.length) queue.shift();
          break;
        }
        batch.push(item);
      }
      if (!batch.length) continue;
      let temporaryFailure = false;
      try {
        const res = await fetch("/api/agent-events/browser", {
          method: "POST",
          headers: { "content-type": "application/json", ...scopeHeaders() },
          body: JSON.stringify({ events: batch.map(({ attempts: _a, scopeEpoch: _s, ...e }) => e) }),
          cache: "no-store",
        });
        if (epochAtCall !== scopeEpoch) break;
        if (res.status === 401 || res.status === 403 || res.status === 409) {
          // 授权失效：永久失败，清队列（不重试，不重绑身份）
          queue.length = 0;
          break;
        }
        if (!res.ok) {
          temporaryFailure = true;
        } else {
          const body = (await res.json().catch(() => null)) as {
            items?: { valid: boolean }[];
          } | null;
          if (epochAtCall !== scopeEpoch) break;
          const accepted = new Set(batch.map(item => item.event_id));
          for (let i = queue.length-1; i>=0; i--) {
            if (accepted.has(queue[i].event_id)) queue.splice(i, 1);
          }
          void body;

        }
      } catch {
        temporaryFailure = true;
      }
      if (epochAtCall !== scopeEpoch) break;
      if (temporaryFailure) {
        for (const item of batch) item.attempts += 1;
        const exhausted = batch.filter((item) => item.attempts > MAX_RETRIES);
        for (const item of exhausted) {
          const idx = queue.indexOf(item);
          if (idx >= 0) queue.splice(idx, 1);
        }
        if (exhausted.length < batch.length && batch.some((item) => item.attempts <= MAX_RETRIES)) {
          await new Promise((resolve) => setTimeout(resolve, 1500));
          continue;
        }
        break;
      }
    }
  } finally {
    flushing = false;
    if (queue.length && epochAtCall !== scopeEpoch) void flushQueue();
  }
}

export function queueSize(): number {
  return queue.length;
}

/** 复制采集：clipboard 成功后调用（正文不上传）。 */
export function trackCopy(answerId: string): void {
  enqueueBrowserEvent("browser.copy", {
    answer_id: answerId,
    copy_scope: "full",
    client_operation_id: newOperationId(),
  });
}

// ------------------------------------------------------------------ 计时

export type TimingMeasurement = {
  measurementId: string;
  startMs: number; // performance.now() 起点
  firstVisibleAtMs: number | null;
  completedAtMs: number | null;
  progressSeen: boolean;
  attemptId: string | null;
  generation: number;
  scopeEpoch: number;
  finished?: boolean;
  abandoned?: boolean;
};

const measurements = new Map<string, TimingMeasurement>();

/** 页面重开新测量 ID；不跨页面相减。 */
export function startTiming(generation: number): string {
  if (!collectionEnabled) return "";
  // 没有受理关联的失败请求不伪造计时；迟到确认最多保留100段。
  while (measurements.size >= 100) measurements.delete(measurements.keys().next().value!);
  const id = `m-${uuid()}`;
  measurements.set(id, {
    measurementId: id,
    startMs: performance.now(),
    firstVisibleAtMs: null,
    completedAtMs: null,
    progressSeen: false,
    attemptId: null,
    generation,
    scopeEpoch,
  });
  return id;
}

export function timingProgressSeen(measurementId: string): void {
  const m = measurements.get(measurementId);
  if (m) m.progressSeen = true;
}

/** 首段实际可见终答正文渲染完成（DOM 可见）时调用；进度/思考/工具不触发。 */
export function timingFirstVisibleAnswer(measurementId: string): void {
  const m = measurements.get(measurementId);
  if (m && m.scopeEpoch === scopeEpoch && m.firstVisibleAtMs === null) {
    m.firstVisibleAtMs = performance.now();
  }
}

export function timingDisplayComplete(measurementId: string): void {
  const m = measurements.get(measurementId);
  if (m && m.scopeEpoch === scopeEpoch && m.completedAtMs === null) {
    m.completedAtMs = performance.now();
  }
}

export function timingAttachAttempt(measurementId: string, attemptId: string | null): void {
  const m = measurements.get(measurementId);
  if (m && m.scopeEpoch === scopeEpoch && attemptId) {
    m.attemptId = attemptId;
    if (m.finished) finishTiming(measurementId, m.abandoned);
  }
}

/** 运行结束：投递计时事件（未测值 null；无 attempt 不投递）。 */
export function finishTiming(measurementId: string, abandoned = false): void {
  const m = measurements.get(measurementId);
  if (!m || m.scopeEpoch !== scopeEpoch) return;
  m.finished = true;
  m.abandoned = abandoned;
  if (!m.attemptId) return; // 保留局部测量等待可信受理确认，绝不把平台run当attempt。
  measurements.delete(measurementId);
  const firstVisible = m.firstVisibleAtMs === null ? null : Math.round(m.firstVisibleAtMs - m.startMs);
  const displayComplete = m.completedAtMs === null ? null : Math.round(m.completedAtMs - m.startMs);
  enqueueBrowserEvent("browser.timing", {
    attempt_id: m.attemptId,
    measurement_id: m.measurementId,
    clock: "monotonic",
    first_visible_answer_ms: firstVisible,
    display_complete_ms: displayComplete,
    progress_seen: m.progressSeen,
    completion_state: abandoned ? "abandoned" : m.completedAtMs !== null ? "complete" : "incomplete",
  });
}

/** 页面卸载：投递局部事实（含未完成原因）；不伪造完成耗时。 */
export function abandonTimingsOnUnload(): void {
  for (const id of [...measurements.keys()]) finishTiming(id, true);
  // 页面离开不能等待普通fetch；复用事件ID尽力重投，服务端事务去重。
  // keepalive总预算通常为64KiB，优先保留计时并限定单批60KiB。
  if (!collectionEnabled) return;
  const batch: QueueEvent[] = [];
  const candidates = [...queue].sort((a,b) => Number(b.event_type === "browser.timing") - Number(a.event_type === "browser.timing"));
  for (const item of candidates) {
    if (item.scopeEpoch !== scopeEpoch || batch.length >= BATCH_LIMIT) continue;
    const proposal = [...batch,item].map(({attempts: _a,scopeEpoch: _s,...event}) => event);
    if (new TextEncoder().encode(JSON.stringify({events:proposal})).length <= 60*1024) batch.push(item);
  }
  if (!batch.length) return;
  void fetch("/api/agent-events/browser", {method:"POST",headers:{"content-type":"application/json",...scopeHeaders()},body:JSON.stringify({events:batch.map(({attempts:_a,scopeEpoch:_s,...event})=>event)}),cache:"no-store",keepalive:true}).catch(()=>{});
}

/** 测试辅助：清空内部状态。 */
export function resetFeedbackClientForTests(): void {
  answerCache = new Map();
  queue.length = 0;
  measurements.clear();
  opCounter = 0;
}


export function hasVisibleTiming(measurementId: string | null): boolean {
  const measurement=measurementId ? measurements.get(measurementId) : null;
  return Boolean(measurement && measurement.scopeEpoch===scopeEpoch && measurement.firstVisibleAtMs!==null);
}
