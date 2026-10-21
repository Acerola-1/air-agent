"use client";

import { create } from "zustand";
import { generateUuidV7 } from "@/lib/uuidv7";
import { getUserIdentity } from "@/lib/user-identity";
import {
  finishTiming,
  startTiming,
  timingAttachAttempt,
  timingProgressSeen,
  hasVisibleTiming,
} from "@/lib/feedback/client";
import {
  searchThreads, createThread, getThreadState, updateThreadMetadata, deleteThread,
  streamRun, cancelRun, deriveTitle, ThreadAccessError,
  getRunStatus, listRecentRuns, notifyIfContextConflict,
  newSubmissionKey, setSubmissionContext, confirmRunAdmission, recordStopIntent,
  recordNavigationStopBeacon,
  type LgThread, type ContextMetrics, type ProcessingStatus, type ThreadValues,
} from "@/lib/langgraph-api";
import { acceptedSnapshot, useCapabilityStore } from "@/lib/capability-store";
import {
  AgentOutputStream,
  isFinalOutputEvent,
  type FinalOutputEvent,
} from "@/lib/agent-output-stream";

export type ToolExecution = {
  id: string; name: string; args: unknown; result?: string;
  status: "pending" | "running" | "completed" | "error";
  artifactId?: string; errorMessage?: string;
};
export type ChatMessage = {
  id: string; role: "user" | "assistant"; content: string;
  reasoningContent?: string; reasoningTokens?: number; tools?: ToolExecution[];
  usage?: { inputTokens: number; outputTokens: number; totalTokens: number };
  modelName?: string; timestamp?: number;
  /** 正文已显示但未获最终确认（中断/失败）：不开放反馈，明确标注未完成。 */
  incomplete?: boolean;
  /** 产生终答正文的真实 AI 消息 id（台账 answer.message_id 权威映射键） */
  sourceMessageId?: string;
  /** 服务端权威回答关联（映射成功后填充；仅已确认最终回答开放评价/复制） */
  answerId?: string | null;
};

type ChatState = {
  graphId: string; threads: LgThread[]; activeThreadId: string | null; messages: ChatMessage[];
  /** 企业切换：丢弃当前视图与列表缓存（不得残留旧企业数据） */
  clearForTenant: () => void;
  activeTimingId: string | null;
  activeTimingMessageId: string | null;
  activeTimingOutputComplete: boolean;
  isStreaming: boolean; streamingText: string; streamingReasoning: string; totalContextTokens: number;
  /** agent-output-stream-v1: 正文生命周期状态（处理中/已校验部分/完整待确认/中断）。 */
  streamingOutputStatus: "idle" | "streaming" | "partial" | "complete" | "aborted";
  streamingOutputMessageId: string | null;
  streamingDeliveryMode: string | null;
  firstDeltaSource: "new_delta" | "legacy_done" | null;
  stopPending: boolean;
  contextMetrics: ContextMetrics | null; contextStatus: ProcessingStatus | null; dataStatus: ProcessingStatus | null;
  error: string | null; failedInput: string | null; loadingThreads: boolean; loadingThread: boolean;
  switchGraph: (graphId: string) => Promise<void>; loadThreads: () => Promise<void>;
  openThread: (threadId: string) => Promise<void>; newThread: () => void;
  sendMessage: (
    text: string,
    chatMode: string,
    operationKind?: "initial" | "retry" | "regenerate",
  ) => Promise<void>;
  /** 重新生成：对上一条用户问题发起 regenerate（关联原 request，新增 attempt）。 */
  regenerateLast: (chatMode: string) => Promise<void>;
  /** R10：停止并等待后端取消确认；confirmed=false 表示未确认（不得谎称已停止） */
  stopStreaming: () => Promise<StopOutcome>;
  /** 页面卸载（导航离开/刷新）：记录 origin=navigation 的停止意图，尽力送达。 */
  recordNavigationStop: () => void;
  removeThread: (threadId: string) => Promise<void>;
};
let generation = 0;
let listRequest = 0;
type ActiveRun = {
  generation: number;
  controller: AbortController;
  threadId: string | null;
  runId: string | null;
  submissionKey?: string;
  streamStarted?: boolean;
  phase?: "queued" | "processing" | "unknown";
  admitted?: boolean;
  submittedSelection?: Record<string, unknown>;
  graphId?: string;
};

function sha256Sync(ascii: string): string {
  function rightRotate(value: number, amount: number): number {
    return (value >>> amount) | (value << (32 - amount));
  }
  const words: number[] = [];
  const asciiBitLength = ascii.length * 8;
  let hash = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ];
  const k = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  for (let i = 0; i < ascii.length; i++) {
    const code = ascii.charCodeAt(i);
    words[i >> 2] |= (code & 0xff) << (8 * (3 - (i % 4)));
  }
  words[asciiBitLength >> 5] |= 0x80 << (24 - (asciiBitLength % 32));
  words[(((asciiBitLength + 64) >> 9) << 4) + 15] = asciiBitLength;

  for (let block = 0; block < words.length; block += 16) {
    const w: number[] = [];
    for (let i = 0; i < 16; i++) {
      w[i] = words[block + i] | 0;
    }
    for (let i = 16; i < 64; i++) {
      const s0 = rightRotate(w[i - 15], 7) ^ rightRotate(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rightRotate(w[i - 2], 17) ^ rightRotate(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (((w[i - 16] + s0) | 0) + ((w[i - 7] + s1) | 0)) | 0;
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let i = 0; i < 64; i++) {
      const S1 = rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = ((((h + S1) | 0) + ((ch + k[i]) | 0)) | 0) + w[i] | 0;
      const S0 = rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) | 0;
      h = g; g = f; f = e; e = (d + temp1) | 0; d = c; c = b; b = a; a = (temp1 + temp2) | 0;
    }
    hash[0] = (hash[0] + a) | 0; hash[1] = (hash[1] + b) | 0; hash[2] = (hash[2] + c) | 0; hash[3] = (hash[3] + d) | 0;
    hash[4] = (hash[4] + e) | 0; hash[5] = (hash[5] + f) | 0; hash[6] = (hash[6] + g) | 0; hash[7] = (hash[7] + h) | 0;
  }
  let result = "";
  for (let i = 0; i < 8; i++) {
    for (let j = 3; j >= 0; j--) {
      const byte = (hash[i] >> (8 * j)) & 255;
      result += (byte < 16 ? "0" : "") + byte.toString(16);
    }
  }
  return result;
}

function computeCanonicalSelectionDigest(graphId: string, selection: Record<string, unknown>, materialVersion: string = "auto"): string {
  const skill = (selection.skill as Record<string, unknown> | undefined) ?? {};
  const mcp = (selection.mcp as Record<string, unknown> | undefined) ?? {};
  const skillMode = (skill.mode as string) || (selection.skill_mode as string) || "auto";
  const skillId = (skill.skill_id as string | null) ?? (selection.skill_id as string | null) ?? null;
  const mcpMode = (mcp.mode as string) || (selection.mcp_mode as string) || "auto";
  const rawTools = (mcp.tool_ids as string[]) || (selection.tool_ids as string[]) || [];
  const uniqueTools = Array.from(new Set(rawTools)).sort();

  const payload: Record<string, unknown> = {
    graph_id: graphId,
    material_version: materialVersion,
    mcp_mode: mcpMode,
    skill_id: skillMode === "auto" ? null : skillId,
    skill_mode: skillMode,
    tool_ids: mcpMode === "auto" ? [] : uniqueTools,
  };
  const canonicalJson = JSON.stringify(payload, Object.keys(payload).sort());
  return sha256Sync(canonicalJson);
}

function deriveSelectionId(selectionDigest: string): string {
  const h = selectionDigest.slice(0, 32).padEnd(32, "0");
  return [
    h.slice(0, 8),
    h.slice(8, 12),
    "5" + h.slice(13, 16),
    "a" + h.slice(17, 20),
    h.slice(20, 32),
  ].join("-");
}

export type StopOutcome = { confirmed: boolean; reason?: "backend" | "context_changed" | "unknown" };
// F05：可信终态 = 服务端 run.get 返回的运行状态进入这些值；
// 404/不可见/状态缺失/查询失败都不是"已取消"的事实。
// interrupted 表示本次执行已停止，可以恢复；不等同于业务cancelled事实。
const STOPPED_RUN_STATUSES = new Set(["cancelled", "success", "error", "failed", "timeout", "interrupted"]);
const STOP_CONFIRM_TIMEOUT_MS = 15000;
const STOP_LIST_RESOLVE_MS = 3000;

async function sleep(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

/** F05：轮询服务端到可信终态才确认；一切不明确结果返回未确认。 */
async function waitForTerminalStatus(
  threadId: string,
  runId: string,
  deadlineMs: number
): Promise<StopOutcome> {
  const deadline = Date.now() + deadlineMs;
  for (;;) {
    try {
      const obs = await getRunStatus(threadId, runId);
      if (!obs.found) return { confirmed: false, reason: "unknown" }; // 不可见 ≠ 已取消
      if (obs.status === null) return { confirmed: false, reason: "unknown" };
      if (STOPPED_RUN_STATUSES.has(obs.status)) return { confirmed: true };
      if (Date.now() >= deadline) return { confirmed: false, reason: "backend" };
    } catch (err) {
      notifyIfContextConflict(err);
      const status = (err as { status?: number })?.status;
      if (status === 409) return { confirmed: false, reason: "context_changed" };
      return { confirmed: false, reason: "backend" };
    }
    await sleep(300);
  }
}

/** F05：向服务端请求停止并等待**可信终态**。
 *
 * - cancel 接受（202）与拒绝一律不单独作数，终态必须来自 runs.get 轮询；
 * - 409（本栈唯一现实来源=代理 context_changed）→ 未确认并触发上下文刷新；
 * - 无 run_id 时先短窗口从运行列表找回活动运行；列表为空且最近运行不处于
 *   终态（提交/迟到元数据竞争）→ 未确认，绝不当作已取消事实。
 */
async function confirmRunStopped(threadId: string | null, run: ActiveRun | null): Promise<StopOutcome> {
  if (!threadId) return { confirmed: true }; // 从未发起本地流：无外部运行需要确认
  if (!run) return { confirmed: true };
  // 创建空线程尚在返回时停止：generation已失效，后续不会提交运行。
  if (!run.streamStarted && !run.runId) return { confirmed: true };
  let target = run.runId;
  if (!target) {
    const deadline = Date.now() + STOP_LIST_RESOLVE_MS;
    for (;;) {
      try {
        if (run.runId) { target = run.runId; break; }
        const recent = await listRecentRuns(threadId);
        // 断流可在首条 metadata 到达前将本次 run 变为 interrupted。
        // 仅凭“最近终态”会串到上一轮；必须核对 BFF 固定的本次提交键。
        const current = run.submissionKey && recent.find((r) => r.submission_key === run.submissionKey);
        if (current) { target = current.run_id; break; }
        const active = recent.find((r) => !r.submission_key && ["pending", "running"].includes(String(r.status)));
        if (active) { target = active.run_id; break; }
        // 没有本次 run_id 时，上一轮的终态不能证明本次尚在受理的提交已停止。
      } catch (err) {
        notifyIfContextConflict(err);
        const status = (err as { status?: number })?.status;
        if (status === 409) return { confirmed: false, reason: "context_changed" };
        return { confirmed: false, reason: "backend" };
      }
      if (Date.now() >= deadline) return { confirmed: false, reason: "unknown" };
      await sleep(400);
    }
  }
  if (!target) return { confirmed: false, reason: "unknown" };
  run.runId = target;
  void confirmRunAdmission(threadId, target).catch(() => {});
  try {
    await cancelRun(threadId, target);
  } catch (err) {
    const status = (err as { status?: number })?.status;
    if (status === 409) {
      notifyIfContextConflict(err);
      return { confirmed: false, reason: "context_changed" };
    }
    // 404 = "no matching runs to cancel"（多半已终态）——仍需 runs.get 见证终态
  }
  return waitForTerminalStatus(threadId, target, STOP_CONFIRM_TIMEOUT_MS);
}
let activeRun: ActiveRun | null = null;
let pendingStopRun: ActiveRun | null = null;
let stopPromise: Promise<StopOutcome> | null = null;
const emptyView = { activeTimingOutputComplete: false, activeTimingMessageId: null as string | null, activeTimingId: null as string | null, messages: [] as ChatMessage[], isStreaming: false, streamingText: "", streamingReasoning: "",
  streamingOutputStatus: "idle" as const, streamingOutputMessageId: null as string | null, streamingDeliveryMode: null as string | null, firstDeltaSource: null as "new_delta" | "legacy_done" | null,
  totalContextTokens: 0, contextMetrics: null, contextStatus: null, dataStatus: null, error: null, failedInput: null, loadingThread: false };

function cancelActive() {
  const cancelEpoch = generation;
  const run = activeRun;
  const timing = useChatStore.getState().activeTimingId;
  if (timing) finishTiming(timing, true);
  activeRun = null;
  run?.controller.abort();
  if (run?.threadId && run.runId) {
    void cancelRun(run.threadId, run.runId).catch(() => {
      // SDK onDisconnect=cancel 仍会取消断开的运行；界面仅提示未确认的状态。
      if (generation === cancelEpoch && useChatStore.getState().activeThreadId === run.threadId) useChatStore.setState({ error: "停止请求尚未确认，请刷新会话状态。" });
    });
  }
}
function fromValues(values: ThreadValues) {
  return { messages: parseMessages(values.messages), loadingThread: false, contextMetrics: values.context_metrics ?? null,
    contextStatus: processingStatus(values.context_status), dataStatus: processingStatus(values.data_status), totalContextTokens: values.context_metrics?.input_tokens ?? 0 };
}
function processingStatus(value: ProcessingStatus | null | undefined): ProcessingStatus | null {
  return value && ['history', 'data'].includes(value.stage) && ['started', 'completed', 'failed'].includes(value.status) ? value : null;
}
function mergeDataStatus(previous: ProcessingStatus | null, current: ProcessingStatus): ProcessingStatus {
  const same = previous?.processing_id === current.processing_id;
  const artifacts = { ...(same ? previous?.artifacts : current.artifacts) };
  const key = current.artifact_id || 'phase';
  artifacts[key] = current.artifacts?.[key] ?? { status: current.status, message: current.message };
  const statuses = Object.values(artifacts).map((value) => value.status);
  return { ...current, artifacts, status: statuses.includes('failed') ? 'failed' : statuses.includes('started') ? 'started' : 'completed' };
}
function sameRun(run: ActiveRun) {
  return generation === run.generation && activeRun === run && !run.controller.signal.aborted;
}

export const useChatStore = create<ChatState>((set, get) => ({
  graphId: "basic-qa", threads: [], activeThreadId: null, ...emptyView, loadingThreads: false, stopPending: false,
  clearForTenant: () => {
    cancelActive();
    generation += 1;
    listRequest += 1;
    pendingStopRun = null;
    stopPromise = null;
    useCapabilityStore.getState().clearForScopeSwitch(get().graphId);
    set({ threads: [], activeThreadId: null, ...emptyView, stopPending: false });
  },
  switchGraph: async (graphId) => {
    generation++; cancelActive();
    useCapabilityStore.getState().clearForScopeSwitch(graphId);
    void useCapabilityStore.getState().fetchCatalog(graphId);
    set({ graphId, activeThreadId: null, threads: [], ...emptyView });
    await get().loadThreads();
  },
  loadThreads: async () => {
    const graphId = get().graphId;
    const request = ++listRequest;
    set({ loadingThreads: true });
    try {
      const threads = await searchThreads(graphId, getUserIdentity());
      if (request === listRequest && get().graphId === graphId) set({ threads, loadingThreads: false });
    } catch (err) {
      notifyIfContextConflict(err);
      if (request === listRequest && get().graphId === graphId) set({ loadingThreads: false, error: "会话列表暂时无法读取，请重试。" });
    }
  },
  openThread: async (threadId) => {
    const epoch = ++generation; cancelActive();
    useCapabilityStore.getState().setAcceptedSelection(null);
    set({ activeThreadId: threadId, ...emptyView, loadingThread: true });
    try {
      const values = await getThreadState(threadId);
      if (generation === epoch && get().activeThreadId === threadId) set(fromValues(values));
      const res = await fetch(`/api/agent-runs/confirm?thread_id=${encodeURIComponent(threadId)}`, { cache: "no-store" });
      const body = res.ok ? await res.json() : null;
      if (generation === epoch && get().activeThreadId === threadId) {
        useCapabilityStore.getState().setAcceptedSelection(acceptedSnapshot(body?.capability_selection));
      }
    } catch (error) {
      notifyIfContextConflict(error);
      if (generation === epoch) set({ loadingThread: false,
        error: error instanceof ThreadAccessError ? error.message : "会话暂时无法读取，请重试。" });
    }
  },
  newThread: () => {
    generation++; cancelActive();
    useCapabilityStore.getState().setAcceptedSelection(null);
    set({ activeThreadId: null, ...emptyView });
  },
  sendMessage: async (text, chatMode, operationKind = "initial") => {
    const previous = get(); const trimmed = text.trim();
    if (previous.isStreaming || previous.stopPending || !trimmed) return;
    const capabilitySelection = useCapabilityStore.getState().toPayload();
    const run: ActiveRun = {
      generation: ++generation,
      controller: new AbortController(),
      threadId: previous.activeThreadId,
      runId: null,
      submittedSelection: capabilitySelection,
      graphId: previous.graphId,
    };
    activeRun = run;
    const userMsg: ChatMessage = { id: generateUuidV7(), role: "user", content: trimmed, timestamp: Date.now() };
    // 稳定提交键：同一次提交的重试复用同一键（服务端按同键同内容幂等、不同内容 409）。
    const submissionKey = newSubmissionKey();
    run.submissionKey = submissionKey;
    // 操作类型参与台账受理：initial/retry/regenerate。retry/regenerate 由服务端
    // 解析来源尝试并关联原 request（提问量不增加）。
    setSubmissionContext({ submissionKey, userMessageId: userMsg.id, operationKind, capabilitySelection });
    // 浏览器单调计时：同页面同测量段（页面重开新 ID，不跨页相减）。
    if (previous.activeTimingId) finishTiming(previous.activeTimingId);
    const measurementId = startTiming(run.generation);
    set({ activeTimingId: measurementId, activeTimingMessageId: null, activeTimingOutputComplete: false });
    set({ messages: [...previous.messages, userMsg], isStreaming: true, streamingText: "", streamingReasoning: "", error: null, failedInput: null,
      contextStatus: null, dataStatus: null, contextMetrics: null });

    let streamedMessageId: string | null = null;
    let outputStatus: "idle" | "streaming" | "partial" | "complete" | "aborted" = "idle";
    let outputText = "";
    let outputMessageId: string | null = null;
    let output: AgentOutputStream | null = null;

    try {
      if (!run.threadId) {
        run.threadId = await createThread(previous.graphId, getUserIdentity(), deriveTitle(trimmed));
        if (!sameRun(run)) return;
        set({ activeThreadId: run.threadId });
      }
      const threadId = run.threadId;
      output = new AgentOutputStream({ threadId, graphId: previous.graphId });
      run.streamStarted = true;
      await streamRun({ threadId, graphId: previous.graphId,
        messages: [{ id: userMsg.id, type: "human", content: trimmed }],
        config: { configurable: { chat_mode: chatMode } }, signal: run.controller.signal,
        handlers: {
          onRun: (runId) => {
            run.runId = runId;
            run.phase = "queued";
            output?.setScope({ runId });
            if (!sameRun(run)) { void cancelRun(threadId, runId).catch(() => {}); return; }
            // 台账受理关联确认：服务端回读平台 run metadata 校验标记后才确认；
            // 失败保持"受理中"，不改写本地终态。
            void confirmRunAdmission(threadId, runId, (attemptId, serverSelection) => {
              // 回调在任何状态写入前先核对本次运行，防切图/换企业的迟到响应。
              if (generation !== run.generation || get().activeThreadId !== threadId || get().graphId !== run.graphId) return;
              timingAttachAttempt(measurementId, attemptId);
              run.admitted = true;
              useCapabilityStore.getState().setAcceptedSelection(acceptedSnapshot(serverSelection));
            }).catch(() => {});
          },
          onValues: (values) => {
            run.phase = "processing";
            if (!sameRun(run) || get().activeThreadId !== threadId) return;
            const view = fromValues(values);
            const snapshot = output!.snapshot();
            const confirmed = Boolean(
              (streamedMessageId || snapshot.messageId) &&
              values.messages.some((item) => {
                const message = item as { id?: string; additional_kwargs?: { ui_visible?: boolean } };
                return message?.id === (streamedMessageId || snapshot.messageId) && message.additional_kwargs?.ui_visible !== false;
              })
            );
            if (confirmed) outputStatus = "complete";
            set({
              ...view,
              ...(confirmed ? { streamingText: "", streamingOutputStatus: "complete" as const } : {}),
            });
          },
          // messages-tuple 包括工具、内部模型和未确认草稿，绝不追加为正文。
          onMessagesDelta: () => {},
          onCustom: (event) => {
            if (!sameRun(run) || get().activeThreadId !== threadId) return;
            if (event.thread_id && event.thread_id !== threadId) return;
            if (event.run_id && run.runId && event.run_id !== run.runId) return;
            if (isFinalOutputEvent(event)) {
              const action = output!.apply(event as FinalOutputEvent);
              if (action.action === "start") {
                outputStatus = "streaming";
                outputMessageId = action.messageId;
                outputText = "";
                set({
                  streamingText: "",
                  streamingOutputStatus: "streaming",
                  streamingOutputMessageId: action.messageId,
                  streamingDeliveryMode: action.deliveryMode,
                  firstDeltaSource: null,
                });
              } else if (action.action === "append") {
                outputStatus = "streaming";
                outputText += action.text;
                set((state) => ({
                  streamingText: state.streamingText + action.text,
                  streamingOutputStatus: "streaming" as const,
                  firstDeltaSource: output!.snapshot().firstDeltaSource,
                }));
              } else if (action.action === "done") {
                streamedMessageId = action.messageId;
                outputStatus = "complete";
                outputText = action.text;
                set({
                  activeTimingMessageId: action.messageId,
                  activeTimingOutputComplete: Boolean(action.messageId),
                  streamingText: action.text,
                  streamingOutputStatus: "complete",
                  streamingOutputMessageId: action.messageId,
                  firstDeltaSource: output!.snapshot().firstDeltaSource,
                });
              } else if (action.action === "abort") {
                outputStatus = "aborted";
                outputText = action.text;
                set({
                  streamingText: action.text,
                  streamingOutputStatus: "aborted",
                  streamingOutputMessageId: output!.snapshot().messageId,
                });
              } else if (action.action === "resync") {
                outputStatus = "partial";
                set({ streamingOutputStatus: "partial" });
                void getThreadState(threadId).then((values) => {
                  if (sameRun(run) && get().activeThreadId === threadId) {
                    set({ ...fromValues(values), streamingOutputStatus: "partial" });
                  }
                }).catch(() => {});
              } else if (action.action === "legacy") {
                streamedMessageId = action.messageId || null;
                outputStatus = "complete";
                outputText = action.text;
                set({
                  activeTimingMessageId: streamedMessageId,
                  activeTimingOutputComplete: Boolean(streamedMessageId),
                  streamingText: action.text,
                  streamingOutputStatus: "complete",
                  firstDeltaSource: "legacy_done",
                });
              }
              return;
            }
            if (event.type === "context_metrics") set({ contextMetrics: event as unknown as ContextMetrics, totalContextTokens: Number(event.input_tokens) || 0 });
            if (event.type === "tool_status" && typeof event.tool_call_id === "string") {
              // 工具/进度先到不触发首正文计时（spec：进度不能提前结束计时）
              run.phase = "processing";
              timingProgressSeen(measurementId);
              set((state) => ({ messages: state.messages.map((message) => ({ ...message, tools: message.tools?.map((tool) => tool.id === event.tool_call_id ? { ...tool, status: event.status === "failed" ? "error" : event.status === "completed" ? "completed" : "running", errorMessage: event.status === "failed" && typeof event.message === "string" ? event.message : tool.errorMessage } : tool) })) }));
            }
            if (event.type === "context_status") {
              const status = processingStatus(event as unknown as ProcessingStatus);
              if (!status) return;
              run.phase = "processing";
              timingProgressSeen(measurementId);
              set((state) => status.stage === "history" ? { contextStatus: status } : { dataStatus: mergeDataStatus(state.dataStatus, status) });
            }
          },
          onTransportDisconnect: () => {
            if (!sameRun(run)) return;
            void recordStopIntent({threadId, runId: run.runId, origin: "transport_disconnect", phase: hasVisibleTiming(measurementId) ? "output" : run.phase ?? "unknown"});
          },
          onDone: (error) => {
            if (!sameRun(run)) return;
            if (error) set({ error: error.message, failedInput: trimmed });
          },
        },
      });
      if (!sameRun(run)) return;
      try {
        const values = await getThreadState(threadId);
        if (sameRun(run)) {
          set({
            ...fromValues(values),
            streamingText: "",
            streamingOutputStatus: (outputStatus as string) === "complete" ? "complete" : outputStatus,
          });
        }
      } catch {
        // 完成事件已经确认的正文仍保留；不丢弃已确认答案。
        if (sameRun(run) && get().streamingText && streamedMessageId) {
          const confirmedText = get().streamingText;
          set((state) => ({ messages: [...state.messages, { id: streamedMessageId!, role: "assistant", content: confirmedText }], streamingText: "" }));
        }
      }
    } catch {
      if (sameRun(run)) set({ error: "消息暂未发送成功，请重试。", failedInput: trimmed });
    } finally {
      if (sameRun(run)) {
        activeRun = null;
        setSubmissionContext(null);

        // 计时收尾在 finally：正常结束/失败都要投递局部事实（未完成原因如实在列）。
        // 已显示但未获最终确认的正文保留为明确的"未完成"消息，不冒充完整答案。
        const partial = outputText || get().streamingText;
        const partialId = outputMessageId || streamedMessageId;
        if (partial && (outputStatus as string) !== "complete" && (outputStatus as string) !== "idle") {
          const id = partialId || generateUuidV7();
          set((state) => ({
            messages: state.messages.some((message) => message.id === id)
              ? state.messages
              : [...state.messages, { id, role: "assistant" as const, content: partial, incomplete: true }],
            isStreaming: false,
            streamingText: "",
            streamingReasoning: "",
            streamingOutputStatus: outputStatus === "aborted" ? "aborted" : "partial",
          }));
        } else {
          set({ isStreaming: false, streamingText: "", streamingReasoning: "" });
        }
        void get().loadThreads();
      }
    }
  },
  /** 页面卸载（导航离开/刷新）时的停止意图：独立于用户主动停止。
   *
   * 运行中或停止未确认时记录 origin=navigation；无活动运行则不产生事实。
   */
  recordNavigationStop: () => {
    const run = activeRun ?? pendingStopRun;
    if (!run) return;
    recordNavigationStopBeacon({
      threadId: run.threadId,
      runId: run.runId,
      origin: "navigation",
    });
  },
  regenerateLast: async (chatMode: string) => {
    const state = get();
    if (state.isStreaming || state.stopPending || !state.activeThreadId) return;
    const local = [...state.messages].reverse().find((m) => m.role === "user");
    let content = local?.content ?? "";
    if (!content) {
      // 刷新/重开线程后本地可能尚无消息：回读会话取最后一条用户问题，
      // 仍以 regenerate 受理（关联原 request）。取不到则不发起，避免凭空提问。
      try {
        const values = await getThreadState(state.activeThreadId);
        const view = fromValues(values);
        content =
          [...view.messages].reverse().find((m) => m.role === "user")?.content ?? "";
      } catch {
        return;
      }
    }
    if (!content) return;
    await get().sendMessage(content, chatMode, "regenerate");
  },
  stopStreaming: async () => {
    if (stopPromise) return stopPromise;
    const run = activeRun ?? pendingStopRun;
    const threadId = run?.threadId ?? get().activeThreadId; const epoch = ++generation;
    pendingStopRun = run;
    activeRun = null;
    run?.controller.abort();
    // 停止意图独立记录（不冒充已取消）：run_id 可能尚未到达，服务端按 attempt/thread 关联。
    void recordStopIntent({
      threadId,
      runId: run?.runId ?? null,
      origin: "user_stop",
      phase: hasVisibleTiming(get().activeTimingId) ? "output" : run?.phase ?? "unknown",
    });
    const timing = get().activeTimingId;
    if (timing) finishTiming(timing);
    // 停止意图已记录，但服务端取消尚未确认：已显示正文标为未完成，绝不当作完整答案。
    const partial = get().streamingText;
    const partialId = get().streamingOutputMessageId;
    const partialStatus = get().streamingOutputStatus;
    if (partial && partialStatus !== "complete" && partialStatus !== "idle") {
      const id = partialId || generateUuidV7();
      set((state) => ({
        messages: state.messages.some((message) => message.id === id)
          ? state.messages
          : [...state.messages, { id, role: "assistant" as const, content: partial, incomplete: true }],
      }));
    }
    set({ activeTimingOutputComplete: false, activeTimingId: null, activeTimingMessageId: null, isStreaming: false, stopPending: run !== null, streamingText: "", streamingReasoning: "", streamingOutputStatus: "aborted" });
    // R10：等待服务端取消终态确认后才返回；未确认由调用方（企业切换）拒绝继续
    const confirmation = confirmRunStopped(threadId, run);
    stopPromise = confirmation;
    let stopped: StopOutcome;
    try {
      stopped = await confirmation;
    } finally {
      if (stopPromise === confirmation) stopPromise = null;
    }
    if (pendingStopRun === run) {
      if (stopped.confirmed) pendingStopRun = null;
      set({ stopPending: !stopped.confirmed, ...(!stopped.confirmed
        ? { error: "停止尚未确认，请再次点击停止后重试；确认前不能切换企业或发送新问题。" }
        : { error: null }) });
    }
    if (threadId && epoch === generation && get().activeThreadId === threadId) {
      void getThreadState(threadId).then((values) => {
        if (epoch === generation && get().activeThreadId === threadId) {
          // 服务端中止后 state 没有可见终答：保留本地已标"未完成"的部分正文，
          // 不能被一次空快照刷新抹掉。
          set((state) => {
            const view = fromValues(values);
            const known = new Set(view.messages.map((message) => message.id));
            const locals = state.messages.filter(
              (message) => message.incomplete && !known.has(message.id)
            );
            return { ...view, messages: [...view.messages, ...locals] };
          });
        }
      }).catch((err: unknown) => {
        notifyIfContextConflict(err);
        if (epoch === generation) set({ error: "已请求停止；会话状态暂时无法刷新，请重试。" });
      });
    }
    return stopped;
  },
  removeThread: async (threadId) => {
    if (get().activeThreadId === threadId) void get().stopStreaming();
    try {
      await deleteThread(threadId);
      if (get().activeThreadId === threadId) get().newThread();
      await get().loadThreads();
    } catch {
      set({ error: "删除未完全确认，请刷新会话列表后重试。" });
    }
  },
}));

export async function renameThread(threadId: string, title: string): Promise<void> {
  await updateThreadMetadata(threadId, { title });
}

function textOfContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.filter((part) => part?.type === "text" && typeof part.text === "string").map((part) => part.text).join("");
  return "";
}
function callsOf(message: Record<string, unknown>): ToolExecution[] {
  const calls = new Map<string, ToolExecution>();
  const list = [ ...(Array.isArray(message.tool_calls) ? message.tool_calls : []),
    ...(Array.isArray(message.content) ? message.content.filter((part) => part?.type === "tool_call") : []) ];
  for (const call of list) {
    const id = String(call.id ?? generateUuidV7());
    calls.set(id, { id, name: String(call.name ?? "工具"), args: call.args ?? call.input, status: "running" });
  }
  return [...calls.values()];
}

/** 每轮一个终答，工具与内部草稿只留调用记录。历史无标记终答兼容。 */
export function parseMessages(raw: unknown[]): ChatMessage[] {
  const result: ChatMessage[] = []; let turnId: string | undefined; let assistant: ChatMessage | undefined;
  const ensureAssistant = (id: string) => {
    if (!assistant) { assistant = { id: turnId ? `${turnId}:assistant` : id, role: "assistant", content: "", tools: [] }; result.push(assistant); }
    return assistant;
  };
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const message = item as Record<string, unknown>;
    const type = String(message.type ?? ""); const id = String(message.id ?? generateUuidV7());
    if (type === "human" || type === "user") {
      assistant = undefined; turnId = id; result.push({ id, role: "user", content: textOfContent(message.content) }); continue;
    }
    if (type === "ai" || type === "assistant") {
      const kwargs = message.additional_kwargs as Record<string, unknown> ?? {};
      const calls = callsOf(message);
      if (calls.length) {
        const target = ensureAssistant(id); const known = new Set(target.tools?.map((tool) => tool.id));
        target.tools!.push(...calls.filter((tool) => !known.has(tool.id)));
      }
      if (kwargs.ui_visible === false || calls.length) continue;
      let content = textOfContent(message.content).replace(/<think>[\s\S]*?<\/think>/g, "").replace(/<think>[\s\S]*$/, "").trim();
      try { const data = JSON.parse(content); if (data && typeof data === "object" && (data.success === false || Number(data.code) >= 400)) content = "查询未完成，请调整条件或稍后重试。"; } catch {}
      if (!content) continue;
      const target = ensureAssistant(id); target.content = content;
      // 台账 answer.confirmed 记录的是真实 AI 消息 id：终答正文来源消息就是权威映射键
      if (turnId && id) target.sourceMessageId = id;
      const usage = message.usage_metadata as Record<string, number> | undefined;
      if (usage) target.usage = { inputTokens: usage.input_tokens ?? 0, outputTokens: usage.output_tokens ?? 0, totalTokens: usage.total_tokens ?? 0 };
      const metadata = message.response_metadata as Record<string, unknown> | undefined;
      if (metadata?.model_name) target.modelName = String(metadata.model_name);
    }
    if (type === "tool" || type === "ToolMessage") {
      const target = ensureAssistant(id); const callId = String(message.tool_call_id ?? id);
      let tool = target.tools!.find((candidate) => candidate.id === callId);
      if (!tool) { tool = { id: callId, name: String(message.name ?? "工具"), args: {}, status: "running" }; target.tools!.push(tool); }
      tool.result = textOfContent(message.content);
      const kwargs = message.additional_kwargs as Record<string, unknown> ?? {};
      let businessFailure = false;
      let correctionAllowed = false;
      try { const data = JSON.parse(tool.result); businessFailure = data.success === false || Number(data.code) >= 400; correctionAllowed = data.correction_allowed === true; } catch {}
      tool.status = message.status === "error" || businessFailure ? "error" : "completed";
      if (tool.status === "error") tool.errorMessage = tool.name === "compute_artifact" && correctionAllowed ? "首次计算未完成，后续修正结果见本轮工具记录。" : typeof kwargs.error_message === "string" ? kwargs.error_message : "查询未完成，请调整条件或稍后重试。";
      const archive = message.artifact as Record<string, unknown> | undefined;
      if (archive?.storage_status === "stored" && typeof archive.artifact_id === "string" && archive.artifact_id.startsWith("art_")) tool.artifactId = archive.artifact_id;
      // 派生计算也有已提交的归档引用。
      try { const computed = JSON.parse(tool.result); if (tool.name === "compute_artifact" && typeof computed.derived_artifact_id === "string" && computed.derived_artifact_id.startsWith("art_")) tool.artifactId = computed.derived_artifact_id; } catch {}
    }
  }
  return result.filter((message) => message.role === "user" || message.content || message.tools?.length);
}

/** 仅作为离线历史粗估，容量展示优先使用后端有效输入。 */
export function calculateTotalTokens(messages: ChatMessage[]): number {
  return messages.reduce((total, message) => total + new TextEncoder().encode(message.content).length, 0);
}
