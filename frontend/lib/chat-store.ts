"use client";

import { create } from "zustand";
import { generateUuidV7 } from "@/lib/uuidv7";
import { getUserIdentity } from "@/lib/user-identity";
import {
  searchThreads,
  createThread,
  getThreadState,
  updateThreadMetadata,
  deleteThread,
  streamRun,
  deriveTitle,
  type LgThread,
} from "@/lib/langgraph-api";

/** 前端渲染用消息模型（由 LangChain BaseMessage 解析而来） */
export type ToolCallInfo = { id: string; name: string; args: unknown };
export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  name?: string;
  toolCalls?: ToolCallInfo[];
};

type ChatState = {
  graphId: string;
  threads: LgThread[];
  activeThreadId: string | null;
  messages: ChatMessage[];
  isStreaming: boolean;
  /** 流式增量文本（messages channel 打字机效果；values 快照到达时清空） */
  streamingText: string;
  error: string | null;
  loadingThreads: boolean;

  /** 切换 graph（清空当前会话并加载该 graph 的线程列表） */
  switchGraph: (graphId: string) => Promise<void>;
  loadThreads: () => Promise<void>;
  /** 打开历史线程：读取完整消息 */
  openThread: (threadId: string) => Promise<void>;
  /** 新建会话（不创建后端线程，首条消息发送时才创建） */
  newThread: () => void;
  /** 发送消息：无活动线程则先创建（带 graph_id/user_id/标题），再流式 run */
  sendMessage: (text: string, chatMode: string) => Promise<void>;
  stopStreaming: () => void;
  removeThread: (threadId: string) => Promise<void>;
};

let abortRef: AbortController | null = null;

export const useChatStore = create<ChatState>((set, get) => ({
  graphId: "basic-qa",
  threads: [],
  activeThreadId: null,
  messages: [],
  isStreaming: false,
  streamingText: "",
  error: null,
  loadingThreads: false,

  switchGraph: async (graphId) => {
    abortRef?.abort();
    abortRef = null;
    set({ graphId, activeThreadId: null, messages: [], isStreaming: false, streamingText: "", error: null });
    await get().loadThreads();
  },

  loadThreads: async () => {
    const { graphId } = get();
    set({ loadingThreads: true });
    try {
      const threads = await searchThreads(graphId, getUserIdentity());
      set({ threads, loadingThreads: false });
    } catch (e) {
      set({ loadingThreads: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  openThread: async (threadId) => {
    if (get().isStreaming) abortRef?.abort();
    set({ activeThreadId: threadId, messages: [], isStreaming: false, streamingText: "", error: null });
    try {
      const raw = await getThreadState(threadId);
      set({ messages: parseMessages(raw) });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },

  newThread: () => {
    abortRef?.abort();
    set({ activeThreadId: null, messages: [], isStreaming: false, streamingText: "", error: null });
  },

  sendMessage: async (text, chatMode) => {
    const s = get();
    const trimmed = text.trim();
    if (s.isStreaming || !trimmed) return;

    const userId = getUserIdentity();
    let threadId = s.activeThreadId;
    if (!threadId) {
      try {
        threadId = await createThread(s.graphId, userId, deriveTitle(trimmed));
        set({ activeThreadId: threadId });
      } catch (e) {
        set({ error: e instanceof Error ? e.message : String(e) });
        return;
      }
    }

    const userMsg: ChatMessage = { id: generateUuidV7(), role: "user", content: trimmed };
    set({
      messages: [...get().messages, userMsg],
      isStreaming: true,
      streamingText: "",
      error: null,
    });

    const abort = new AbortController();
    abortRef = abort;
    await streamRun({
      threadId,
      graphId: s.graphId,
      messages: [{ type: "human", content: trimmed }],
      config: { configurable: { chat_mode: chatMode } },
      signal: abort.signal,
      handlers: {
        onValues: (msgs) => {
          set({ messages: parseMessages(msgs), streamingText: "" });
        },
        onMessagesDelta: (chunks) => {
          let acc = "";
          for (const [msg] of chunks) {
            const t = textOfContent((msg as { content?: unknown })?.content);
            if (t) acc += t;
          }
          if (acc) set({ streamingText: get().streamingText + acc });
        },
        onDone: (err) => {
          abortRef = null;
          if (err) {
            set({ error: err.message, isStreaming: false, streamingText: "" });
          } else {
            set({ isStreaming: false, streamingText: "" });
            // 完成后刷新列表：标题 / updated_at 排序最新
            void get().loadThreads();
          }
        },
      },
    });
  },

  stopStreaming: () => {
    abortRef?.abort();
    abortRef = null;
    set({ isStreaming: false, streamingText: "" });
  },

  removeThread: async (threadId) => {
    try {
      await deleteThread(threadId);
    } catch {
      /* 删除软失败，列表照常刷新 */
    }
    if (get().activeThreadId === threadId) get().newThread();
    await get().loadThreads();
  },
}));

/** 更新线程标题（供列表重命名等场景复用；当前由创建时写入） */
export async function renameThread(threadId: string, title: string): Promise<void> {
  await updateThreadMetadata(threadId, { title });
}

// ───────────────────────── 消息解析 ─────────────────────────

function textOfContent(c: unknown): string {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) {
    return c
      .filter((p) => p && typeof p === "object" && typeof (p as { text?: unknown }).text === "string")
      .map((p) => (p as { text: string }).text)
      .join("");
  }
  return "";
}

function collectToolCalls(m: Record<string, unknown>): ToolCallInfo[] {
  const calls: ToolCallInfo[] = [];
  const content = m.content;
  if (Array.isArray(content)) {
    for (const p of content) {
      if (p && typeof p === "object" && (p as { type?: string }).type === "tool_call") {
        const t = p as { id?: string; name?: string; args?: unknown };
        calls.push({ id: t.id ?? "", name: t.name ?? "", args: t.args });
      }
    }
  }
  const toolCalls = m.tool_calls;
  if (Array.isArray(toolCalls)) {
    for (const tc of toolCalls) {
      const t = tc as { id?: string; name?: string; args?: unknown; input?: unknown };
      calls.push({ id: t.id ?? "", name: t.name ?? "", args: t.args ?? t.input });
    }
  }
  return calls;
}

/** 把后端原始消息数组（LangChain BaseMessage / AIMessageChunk / ToolMessage）解析为前端渲染模型 */
export function parseMessages(raw: unknown[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const m of raw) {
    if (!m || typeof m !== "object") continue;
    const mm = m as { type?: string; _getType?: () => string; id?: string; name?: string; content?: unknown };
    const type = mm.type ?? mm._getType?.() ?? "";
    const id = mm.id && typeof mm.id === "string" ? mm.id : generateUuidV7();
    if (type === "human" || type === "user") {
      out.push({ id, role: "user", content: textOfContent(mm.content) });
    } else if (type === "ai" || type === "AIMessageChunk") {
      out.push({
        id,
        role: "assistant",
        content: textOfContent(mm.content),
        toolCalls: collectToolCalls(mm),
      });
    } else if (type === "tool" || type === "ToolMessage") {
      out.push({ id, role: "tool", content: textOfContent(mm.content), name: mm.name });
    }
  }
  return out;
}
