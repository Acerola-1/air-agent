"use client";

import { create } from "zustand";
import { generateUuidV7 } from "@/lib/uuidv7";
import { getUserIdentity } from "@/lib/user-identity";
import {
  searchThreads, createThread, getThreadState, updateThreadMetadata, deleteThread,
  streamRun, cancelRun, deriveTitle,
  type LgThread, type ContextMetrics, type ProcessingStatus, type ThreadValues,
} from "@/lib/langgraph-api";

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
};

type ChatState = {
  graphId: string; threads: LgThread[]; activeThreadId: string | null; messages: ChatMessage[];
  isStreaming: boolean; streamingText: string; streamingReasoning: string; totalContextTokens: number;
  contextMetrics: ContextMetrics | null; contextStatus: ProcessingStatus | null; dataStatus: ProcessingStatus | null;
  error: string | null; failedInput: string | null; loadingThreads: boolean; loadingThread: boolean;
  switchGraph: (graphId: string) => Promise<void>; loadThreads: () => Promise<void>;
  openThread: (threadId: string) => Promise<void>; newThread: () => void;
  sendMessage: (text: string, chatMode: string) => Promise<void>; stopStreaming: () => void;
  removeThread: (threadId: string) => Promise<void>;
};
let generation = 0;
let listRequest = 0;
type ActiveRun = { generation: number; controller: AbortController; threadId: string | null; runId: string | null };
let activeRun: ActiveRun | null = null;
const emptyView = { messages: [] as ChatMessage[], isStreaming: false, streamingText: "", streamingReasoning: "",
  totalContextTokens: 0, contextMetrics: null, contextStatus: null, dataStatus: null, error: null, failedInput: null, loadingThread: false };

function cancelActive() {
  const cancelEpoch = generation;
  const run = activeRun;
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
  graphId: "basic-qa", threads: [], activeThreadId: null, ...emptyView, loadingThreads: false,
  switchGraph: async (graphId) => {
    generation++; cancelActive();
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
    } catch {
      if (request === listRequest && get().graphId === graphId) set({ loadingThreads: false, error: "会话列表暂时无法读取，请重试。" });
    }
  },
  openThread: async (threadId) => {
    const epoch = ++generation; cancelActive();
    set({ activeThreadId: threadId, ...emptyView, loadingThread: true });
    try {
      const values = await getThreadState(threadId);
      if (generation === epoch && get().activeThreadId === threadId) set(fromValues(values));
    } catch {
      if (generation === epoch) set({ loadingThread: false, error: "会话暂时无法读取，请重试。" });
    }
  },
  newThread: () => {
    generation++; cancelActive();
    set({ activeThreadId: null, ...emptyView });
  },
  sendMessage: async (text, chatMode) => {
    const previous = get(); const trimmed = text.trim();
    if (previous.isStreaming || !trimmed) return;
    const run: ActiveRun = { generation: ++generation, controller: new AbortController(), threadId: previous.activeThreadId, runId: null };
    activeRun = run;
    const userMsg: ChatMessage = { id: generateUuidV7(), role: "user", content: trimmed, timestamp: Date.now() };
    set({ messages: [...previous.messages, userMsg], isStreaming: true, streamingText: "", streamingReasoning: "", error: null, failedInput: null,
      contextStatus: null, dataStatus: null, contextMetrics: null });
    try {
      if (!run.threadId) {
        run.threadId = await createThread(previous.graphId, getUserIdentity(), deriveTitle(trimmed));
        if (!sameRun(run)) return;
        set({ activeThreadId: run.threadId });
      }
      const threadId = run.threadId;
      let streamedMessageId: string | null = null;
      await streamRun({ threadId, graphId: previous.graphId,
        messages: [{ id: userMsg.id, type: "human", content: trimmed }],
        config: { configurable: { chat_mode: chatMode } }, signal: run.controller.signal,
        handlers: {
          onRun: (runId) => {
            run.runId = runId;
            if (!sameRun(run)) void cancelRun(threadId, runId).catch(() => {});
          },
          onValues: (values) => {
            if (!sameRun(run) || get().activeThreadId !== threadId) return;
            const view = fromValues(values);
            const confirmed = streamedMessageId && values.messages.some((item) => {
              const message = item as { id?: string; additional_kwargs?: { ui_visible?: boolean } };
              return message?.id === streamedMessageId && message.additional_kwargs?.ui_visible !== false;
            });
            set({ ...view, ...(confirmed ? { streamingText: "" } : {}) });
          },
          // messages-tuple 包括工具、内部模型和未确认草稿，绝不追加为正文。
          onMessagesDelta: () => {},
          onCustom: (event) => {
            if (!sameRun(run) || get().activeThreadId !== threadId) return;
            if (event.thread_id && event.thread_id !== threadId) return;
            if (event.run_id && run.runId && event.run_id !== run.runId) return;
            if (event.type === "context_metrics") set({ contextMetrics: event as unknown as ContextMetrics, totalContextTokens: Number(event.input_tokens) || 0 });
            if (event.type === "tool_status" && typeof event.tool_call_id === "string") {
              set((state) => ({ messages: state.messages.map((message) => ({ ...message, tools: message.tools?.map((tool) => tool.id === event.tool_call_id ? { ...tool, status: event.status === "failed" ? "error" : event.status === "completed" ? "completed" : "running", errorMessage: event.status === "failed" && typeof event.message === "string" ? event.message : tool.errorMessage } : tool) })) }));
            }
            if (event.type === "context_status") {
              const status = processingStatus(event as unknown as ProcessingStatus);
              if (!status) return;
              set((state) => status.stage === "history" ? { contextStatus: status } : { dataStatus: mergeDataStatus(state.dataStatus, status) });
            }
            if (event.type === "final_output_delta" && typeof event.message === "string") set((state) => ({ streamingText: state.streamingText + event.message }));
            if (event.type === "final_output_done" && typeof event.message === "string") {
              streamedMessageId = typeof event.message_id === "string" ? event.message_id : null;
              set({ streamingText: event.message });
            }
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
        if (sameRun(run)) set({ ...fromValues(values), streamingText: "" });
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
        set({ isStreaming: false, streamingText: "", streamingReasoning: "" });
        void get().loadThreads();
      }
    }
  },
  stopStreaming: () => {
    const threadId = get().activeThreadId; const epoch = ++generation;
    cancelActive();
    set({ isStreaming: false, streamingText: "", streamingReasoning: "" });
    if (threadId) void getThreadState(threadId).then((values) => {
      if (generation === epoch && get().activeThreadId === threadId) set(fromValues(values));
    }).catch(() => {
      if (generation === epoch) set({ error: "已请求停止；会话状态暂时无法刷新，请重试。" });
    });
  },
  removeThread: async (threadId) => {
    if (get().activeThreadId === threadId) get().stopStreaming();
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
