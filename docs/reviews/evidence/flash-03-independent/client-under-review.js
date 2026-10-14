"use strict";
"use client";
Object.defineProperty(exports, "__esModule", { value: true });
exports.setFeedbackScope = setFeedbackScope;
exports.currentFeedbackScope = currentFeedbackScope;
exports.cachedAnswerByMessage = cachedAnswerByMessage;
exports.cachedAnswerById = cachedAnswerById;
exports.loadAnswerMappings = loadAnswerMappings;
exports.putAnswerMapping = putAnswerMapping;
exports.newOperationId = newOperationId;
exports.submitFeedback = submitFeedback;
exports.enqueueBrowserEvent = enqueueBrowserEvent;
exports.queueSize = queueSize;
exports.trackCopy = trackCopy;
exports.startTiming = startTiming;
exports.timingProgressSeen = timingProgressSeen;
exports.timingFirstVisibleAnswer = timingFirstVisibleAnswer;
exports.timingDisplayComplete = timingDisplayComplete;
exports.timingAttachAttempt = timingAttachAttempt;
exports.finishTiming = finishTiming;
exports.abandonTimingsOnUnload = abandonTimingsOnUnload;
exports.resetFeedbackClientForTests = resetFeedbackClientForTests;
const MAX_QUEUE = 100;
const BATCH_LIMIT = 50;
// ------------------------------------------------------------------ 会话作用域
let scopeKey = "";
let answerCache = new Map(); // key: message_id or answer_id
let opCounter = 0;
function setFeedbackScope(tenantId, userId) {
    const next = `${tenantId}:${userId}`;
    if (next !== scopeKey) {
        // 企业/用户切换：清空映射/缓存/队列，迟到异步结果一律丢弃
        scopeKey = next;
        answerCache = new Map();
        queue.length = 0;
        inFlight.clear();
    }
}
function currentFeedbackScope() {
    return scopeKey;
}
function scopedCheck() {
    // await 之后使用：作用域已变则忽略迟到回调
    return currentFeedbackScope() === scopeAtCall;
}
let scopeAtCall = "";
// ------------------------------------------------------------------ 映射回读
function cachedAnswerByMessage(messageId) {
    return answerCache.get(`m:${messageId}`) ?? null;
}
function cachedAnswerById(answerId) {
    return answerCache.get(`a:${answerId}`) ?? null;
}
/** 从台账本人回读接口刷新映射（最多 50 个 message_id）。 */
async function loadAnswerMappings(messageIds) {
    const ids = messageIds.filter(Boolean).slice(0, 50);
    if (!ids.length)
        return;
    const missing = ids.filter((id) => !answerCache.has(`m:${id}`));
    if (!missing.length)
        return;
    scopeAtCall = scopeKey;
    try {
        const res = await fetch(`/api/feedback/answers?ids=${encodeURIComponent(missing.join(","))}`, { cache: "no-store" });
        if (!res.ok)
            return;
        const body = (await res.json());
        for (const item of body.items ?? []) {
            if (item.message_id)
                answerCache.set(`m:${item.message_id}`, item);
            answerCache.set(`a:${item.answer_id}`, item);
        }
        // 无映射的消息也记为不可评价（避免重复查询）；真正的迟到映射由下次刷新覆盖
        for (const id of missing) {
            if (!answerCache.has(`m:${id}`)) {
                answerCache.set(`m:${id}`, {
                    answer_id: "",
                    message_id: id,
                    attempt_id: "",
                    request_id: "",
                    thread_id: "",
                    content_version: 0,
                    rating: null,
                    reason_code: null,
                    revision: 0,
                    feedbackable: false,
                });
            }
        }
    }
    catch {
        // 网络失败：保持未映射状态（禁用评价），下次刷新重试
    }
}
/** 服务端权威映射刷新（例如刚生成完的回答）。 */
function putAnswerMapping(item) {
    if (item.message_id)
        answerCache.set(`m:${item.message_id}`, item);
    answerCache.set(`a:${item.answer_id}`, item);
}
const opIds = new Map(); // answer_id → 最新未完成 operation_id
function newOperationId() {
    opCounter += 1;
    return `op-${Date.now().toString(36)}-${opCounter}-${Math.random().toString(36).slice(2, 8)}`;
}
async function submitFeedback(input) {
    scopeAtCall = scopeKey;
    const scopeBefore = scopeKey;
    try {
        const res = await fetch(`/api/feedback/answers/${encodeURIComponent(input.answerId)}`, {
            method: "PUT",
            headers: { "content-type": "application/json" },
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
            const detail = (await res.json().catch(() => null));
            if (!scopedCheck())
                return { ok: false, reason: "network" };
            return {
                ok: false,
                reason: detail?.detail?.code === "event_conflict" ? "rejected" : "conflict",
            };
        }
        if (!res.ok) {
            if (!scopedCheck())
                return { ok: false, reason: "network" };
            return { ok: false, reason: "rejected" };
        }
        const body = (await res.json());
        if (!scopedCheck())
            return { ok: false, reason: "network" };
        return { ok: true, rating: body.rating, revision: body.revision };
    }
    catch {
        void scopeBefore;
        return { ok: false, reason: "network" };
    }
}
const queue = [];
const inFlight = new Map(); // operation_id → answer_id
let flushing = false;
function uuid() {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto)
        return crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
function enqueueBrowserEvent(eventType, payload) {
    if (queue.length >= MAX_QUEUE)
        return; // 有界：满则丢弃新事件（不积压）
    queue.push({
        schema_version: "1.0",
        event_id: uuid(),
        event_type: eventType,
        occurred_at: new Date().toISOString(),
        payload,
        attempts: 0,
    });
    void flushQueue();
}
const MAX_RETRIES = 3;
async function flushQueue() {
    if (flushing || !queue.length)
        return;
    flushing = true;
    try {
        while (queue.length) {
            const batch = queue.slice(0, BATCH_LIMIT);
            let temporaryFailure = false;
            try {
                const res = await fetch("/api/agent-events/browser", {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: JSON.stringify({ events: batch.map(({ attempts: _a, ...e }) => e) }),
                    cache: "no-store",
                });
                if (res.status === 401 || res.status === 403) {
                    // 授权失效：永久失败，清队列（不重试，不重绑身份）
                    queue.length = 0;
                    break;
                }
                if (!res.ok) {
                    temporaryFailure = true;
                }
                else {
                    const body = (await res.json().catch(() => null));
                    // 逐项结果：非法项丢弃，合法项已受理
                    if (body?.items) {
                        for (let i = batch.length - 1; i >= 0; i -= 1) {
                            const verdict = body.items[i];
                            if (verdict?.valid)
                                queue.splice(i, 1);
                            else
                                queue.splice(i, 1); // 非法项不再重试（服务端明确拒绝）
                        }
                    }
                    else {
                        queue.splice(0, batch.length);
                    }
                }
            }
            catch {
                temporaryFailure = true;
            }
            if (temporaryFailure) {
                for (const item of batch)
                    item.attempts += 1;
                const exhausted = batch.filter((item) => item.attempts > MAX_RETRIES);
                for (const item of exhausted) {
                    const idx = queue.indexOf(item);
                    if (idx >= 0)
                        queue.splice(idx, 1);
                }
                if (exhausted.length < batch.length && batch.some((item) => item.attempts <= MAX_RETRIES)) {
                    await new Promise((resolve) => setTimeout(resolve, 1500));
                    continue;
                }
                break;
            }
        }
    }
    finally {
        flushing = false;
    }
}
function queueSize() {
    return queue.length;
}
/** 复制采集：clipboard 成功后调用（正文不上传）。 */
function trackCopy(answerId) {
    enqueueBrowserEvent("browser.copy", {
        answer_id: answerId,
        copy_scope: "full",
        client_operation_id: newOperationId(),
    });
}
const measurements = new Map();
/** 页面重开新测量 ID；不跨页面相减。 */
function startTiming(generation) {
    const id = `m-${uuid()}`;
    measurements.set(id, {
        measurementId: id,
        startMs: performance.now(),
        firstVisibleAtMs: null,
        completedAtMs: null,
        progressSeen: false,
        attemptId: null,
        generation,
    });
    return id;
}
function timingProgressSeen(measurementId) {
    const m = measurements.get(measurementId);
    if (m)
        m.progressSeen = true;
}
/** 首段实际可见终答正文渲染完成（DOM 可见）时调用；进度/思考/工具不触发。 */
function timingFirstVisibleAnswer(measurementId) {
    const m = measurements.get(measurementId);
    if (m && m.firstVisibleAtMs === null) {
        m.firstVisibleAtMs = performance.now();
    }
}
function timingDisplayComplete(measurementId) {
    const m = measurements.get(measurementId);
    if (m && m.completedAtMs === null) {
        m.completedAtMs = performance.now();
    }
}
function timingAttachAttempt(measurementId, attemptId) {
    const m = measurements.get(measurementId);
    if (m && attemptId)
        m.attemptId = attemptId;
}
/** 运行结束：投递计时事件（未测值 null；无 attempt 不投递）。 */
function finishTiming(measurementId) {
    const m = measurements.get(measurementId);
    if (!m)
        return;
    measurements.delete(measurementId);
    if (!m.attemptId)
        return; // 无本次运行归属：不投递
    const firstVisible = m.firstVisibleAtMs === null ? null : Math.round(m.firstVisibleAtMs - m.startMs);
    const displayComplete = m.completedAtMs === null ? null : Math.round(m.completedAtMs - m.startMs);
    enqueueBrowserEvent("browser.timing", {
        attempt_id: m.attemptId,
        measurement_id: m.measurementId,
        clock: "monotonic",
        first_visible_answer_ms: firstVisible,
        display_complete_ms: displayComplete,
        progress_seen: m.progressSeen,
        completion_state: m.completedAtMs !== null ? "complete" : "incomplete",
    });
}
/** 页面卸载：投递局部事实（含未完成原因）；不伪造完成耗时。 */
function abandonTimingsOnUnload() {
    for (const id of [...measurements.keys()])
        finishTiming(id);
}
/** 测试辅助：清空内部状态。 */
function resetFeedbackClientForTests() {
    answerCache = new Map();
    queue.length = 0;
    measurements.clear();
    opCounter = 0;
}
