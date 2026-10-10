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

/** 单个工具调用的执行信息模型 */
export type ToolExecution = {
  id: string; // tool_call_id
  name: string; // 工具函数名
  args: unknown; // 调用入参
  result?: string; // 工具执行返回结果字符串
  status: "pending" | "running" | "completed" | "error";
  artifactId?: string; // 专属表存储引用的 artifact_id (若有)
};

/** 前端渲染用聚合消息模型（将思考、工具调用与正文回答有机统一） */
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoningContent?: string; // 深度思考内容（思维链）
  reasoningTokens?: number; // 思考消耗的 Token 数量
  tools?: ToolExecution[]; // 本轮调用的所有工具执行集合（聚合卡片）
  usage?: {
    inputTokens: number;
    outputTokens: number;
    totalTokens: number;
  };
  modelName?: string;
  timestamp?: number;
};

type ChatState = {
  graphId: string;
  threads: LgThread[];
  activeThreadId: string | null;
  messages: ChatMessage[];
  isStreaming: boolean;
  /** 流式增量正文文本 */
  streamingText: string;
  /** 流式增量思考过程文本 */
  streamingReasoning: string;
  /** 当前活跃线程累计的 Token 估算总数 */
  totalContextTokens: number;
  error: string | null;
  loadingThreads: boolean;

  /** 切换 graph（清空当前会话并加载该 graph 的线程列表） */
  switchGraph: (graphId: string) => Promise<void>;
  loadThreads: () => Promise<void>;
  /** 打开历史线程：读取完整消息并聚合计算 */
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
  streamingReasoning: "",
  totalContextTokens: 0,
  error: null,
  loadingThreads: false,

  switchGraph: async (graphId) => {
    abortRef?.abort();
    abortRef = null;
    set({
      graphId,
      activeThreadId: null,
      messages: [],
      isStreaming: false,
      streamingText: "",
      streamingReasoning: "",
      totalContextTokens: 0,
      error: null,
    });
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
    set({
      activeThreadId: threadId,
      messages: [],
      isStreaming: false,
      streamingText: "",
      streamingReasoning: "",
      totalContextTokens: 0,
      error: null,
    });
    try {
      const raw = await getThreadState(threadId);
      const parsed = parseMessages(raw);
      const tokens = calculateTotalTokens(parsed);
      set({ messages: parsed, totalContextTokens: tokens });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e) });
    }
  },

  newThread: () => {
    abortRef?.abort();
    set({
      activeThreadId: null,
      messages: [],
      isStreaming: false,
      streamingText: "",
      streamingReasoning: "",
      totalContextTokens: 0,
      error: null,
    });
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

    const userMsg: ChatMessage = {
      id: generateUuidV7(),
      role: "user",
      content: trimmed,
      timestamp: Date.now(),
    };
    const nextMsgs = [...get().messages, userMsg];
    set({
      messages: nextMsgs,
      isStreaming: true,
      streamingText: "",
      streamingReasoning: "",
      totalContextTokens: calculateTotalTokens(nextMsgs),
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
          const parsed = parseMessages(msgs);
          const tokens = calculateTotalTokens(parsed);
          set({
            messages: parsed,
            streamingText: "",
            streamingReasoning: "",
            totalContextTokens: tokens,
          });
        },
        onMessagesDelta: (chunks) => {
          let textAcc = "";
          let reasoningAcc = "";
          for (const [msg] of chunks) {
            const rawMsg = msg as Record<string, unknown>;
            const reasoning =
              (rawMsg?.additional_kwargs as Record<string, unknown>)?.reasoning_content ||
              rawMsg?.reasoning_content;
            if (typeof reasoning === "string" && reasoning) {
              reasoningAcc += reasoning;
            }

            const t = textOfContent(rawMsg?.content);
            if (t) {
              // 检查是否有 <think> 标签流
              if (t.includes("<think>")) {
                const parts = t.split("</think>");
                if (parts.length > 1) {
                  reasoningAcc += parts[0].replace("<think>", "");
                  textAcc += parts[1];
                } else {
                  reasoningAcc += t.replace("<think>", "");
                }
              } else {
                textAcc += t;
              }
            }
          }

          if (reasoningAcc) {
            set((prev) => ({ streamingReasoning: prev.streamingReasoning + reasoningAcc }));
          }
          if (textAcc) {
            set((prev) => ({ streamingText: prev.streamingText + textAcc }));
          }
        },
        onDone: (err) => {
          abortRef = null;
          if (err) {
            set({
              error: err.message,
              isStreaming: false,
              streamingText: "",
              streamingReasoning: "",
            });
          } else {
            set({
              isStreaming: false,
              streamingText: "",
              streamingReasoning: "",
            });
            void get().loadThreads();
          }
        },
      },
    });
  },

  stopStreaming: () => {
    abortRef?.abort();
    abortRef = null;
    set({ isStreaming: false, streamingText: "", streamingReasoning: "" });
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

/** 更新线程标题 */
export async function renameThread(threadId: string, title: string): Promise<void> {
  await updateThreadMetadata(threadId, { title });
}

// ───────────────────────── 消息解析与上下文估算 ─────────────────────────

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

function collectToolExecutions(m: Record<string, unknown>): ToolExecution[] {
  const executions: ToolExecution[] = [];
  const content = m.content;
  if (Array.isArray(content)) {
    for (const p of content) {
      if (p && typeof p === "object" && (p as { type?: string }).type === "tool_call") {
        const t = p as { id?: string; name?: string; args?: unknown };
        executions.push({
          id: t.id ?? generateUuidV7(),
          name: t.name ?? "tool",
          args: t.args,
          status: "running",
        });
      }
    }
  }
  const toolCalls = m.tool_calls;
  if (Array.isArray(toolCalls)) {
    for (const tc of toolCalls) {
      const t = tc as { id?: string; name?: string; args?: unknown; input?: unknown };
      executions.push({
        id: t.id ?? generateUuidV7(),
        name: t.name ?? "tool",
        args: t.args ?? t.input,
        status: "running",
      });
    }
  }
  return executions;
}

/** 把后端原始消息数组聚合解析为现代清晰的 ChatMessage 列表 */
export function parseMessages(raw: unknown[]): ChatMessage[] {
  const out: ChatMessage[] = [];

  for (const m of raw) {
    if (!m || typeof m !== "object") continue;
    const mm = m as Record<string, unknown>;
    const type = String(mm.type ?? (typeof mm._getType === "function" ? mm._getType() : ""));
    const id = mm.id && typeof mm.id === "string" ? mm.id : generateUuidV7();

    if (type === "human" || type === "user") {
      out.push({
        id,
        role: "user",
        content: textOfContent(mm.content),
        timestamp: typeof mm.created_at === "number" ? mm.created_at : Date.now(),
      });
    } else if (type === "ai" || type === "AIMessageChunk") {
      let contentStr = textOfContent(mm.content);
      let reasoningContent = "";

      // 提取思维链来源 1: additional_kwargs.reasoning_content
      const addKw = (mm.additional_kwargs as Record<string, unknown>) ?? {};
      if (typeof addKw.reasoning_content === "string" && addKw.reasoning_content) {
        reasoningContent = addKw.reasoning_content;
      }

      // 提取思维链来源 2: content 中的 <think>...</think> 标签
      if (!reasoningContent && contentStr.includes("<think>")) {
        const match = contentStr.match(/<think>([\s\S]*?)<\/think>/);
        if (match) {
          reasoningContent = match[1].trim();
          contentStr = contentStr.replace(/<think>[\s\S]*?<\/think>/, "").trim();
        }
      }

      // 提取 Token 用量与思考 Token 数量
      const usageMeta = (mm.usage_metadata as Record<string, unknown>) ?? {};
      const respMeta = (mm.response_metadata as Record<string, unknown>) ?? {};
      const tokenUsage = (respMeta.token_usage as Record<string, unknown>) ?? {};
      const tokenDetails =
        (usageMeta.output_token_details as Record<string, unknown>) ??
        (tokenUsage.completion_tokens_details as Record<string, unknown>) ??
        {};

      const reasoningTokens =
        Number(tokenDetails.reasoning ?? tokenDetails.reasoning_tokens) || undefined;

      const inputTokens = Number(usageMeta.input_tokens ?? tokenUsage.prompt_tokens) || 0;
      const outputTokens = Number(usageMeta.output_tokens ?? tokenUsage.completion_tokens) || 0;
      const totalTokens = Number(usageMeta.total_tokens ?? tokenUsage.total_tokens) || 0;

      const modelName = String(respMeta.model_name ?? "");

      const tools = collectToolExecutions(mm);

      // 如果前一条也是 assistant，且前一条纯为 tool_calls 而当前条输出正文，优雅合并为一个统一回合
      const lastMsg = out[out.length - 1];
      if (lastMsg && lastMsg.role === "assistant" && (!lastMsg.content || !lastMsg.content.trim()) && tools.length === 0) {
        lastMsg.content = contentStr;
        if (reasoningContent && !lastMsg.reasoningContent) lastMsg.reasoningContent = reasoningContent;
        if (reasoningTokens && !lastMsg.reasoningTokens) lastMsg.reasoningTokens = reasoningTokens;
        if (totalTokens) {
          lastMsg.usage = { inputTokens, outputTokens, totalTokens };
        }
        continue;
      }

      out.push({
        id,
        role: "assistant",
        content: contentStr,
        reasoningContent: reasoningContent || undefined,
        reasoningTokens,
        tools: tools.length > 0 ? tools : undefined,
        usage: totalTokens ? { inputTokens, outputTokens, totalTokens } : undefined,
        modelName: modelName || undefined,
        timestamp: Date.now(),
      });
    } else if (type === "tool" || type === "ToolMessage") {
      // 关键优化：Tool 消息不单独占位一排聊天气泡，而是归入前序最近的 Assistant 消息中
      const toolCallId = String(mm.tool_call_id ?? "");
      const resultContent = textOfContent(mm.content);
      const isError = mm.status === "error";

      let artifactId: string | undefined;
      const artifactObj = mm.artifact as Record<string, unknown> | undefined;
      if (artifactObj && typeof artifactObj.artifact_id === "string") {
        artifactId = artifactObj.artifact_id;
      }

      // 从后往前寻找最近的 assistant 消息
      let matched = false;
      for (let i = out.length - 1; i >= 0; i--) {
        const item = out[i];
        if (item.role === "assistant") {
          if (!item.tools) item.tools = [];
          const existingTool = item.tools.find((t) => t.id === toolCallId);
          if (existingTool) {
            existingTool.result = resultContent;
            existingTool.status = isError ? "error" : "completed";
            existingTool.artifactId = artifactId;
            matched = true;
            break;
          }
        }
      }

      // 如果未严格匹配到 tool_call_id，追加挂在最后一个 assistant 消息下
      if (!matched) {
        for (let i = out.length - 1; i >= 0; i--) {
          const item = out[i];
          if (item.role === "assistant") {
            if (!item.tools) item.tools = [];
            item.tools.push({
              id: toolCallId || generateUuidV7(),
              name: String(mm.name ?? "工具执行"),
              args: {},
              result: resultContent,
              status: isError ? "error" : "completed",
              artifactId,
            });
            break;
          }
        }
      }
    }
  }

  return out;
}

/** 计算当前会话的上下文 Token 占用 */
export function calculateTotalTokens(messages: ChatMessage[]): number {
  // 优先从最后一条带有真实 usage 的 assistant 消息获取 totalTokens
  for (let i = messages.length - 1; i >= 0; i--) {
    const u = messages[i].usage;
    if (u && u.totalTokens > 0) {
      return u.totalTokens;
    }
  }

  // 兜底方案：遍历全量消息字符数按加权估算（中英文 1 字符 ≈ 0.55 tokens）
  let totalChars = 0;
  for (const m of messages) {
    totalChars += m.content.length;
    if (m.reasoningContent) totalChars += m.reasoningContent.length;
    if (m.tools) {
      for (const t of m.tools) {
        totalChars += String(t.name).length;
        totalChars += typeof t.args === "string" ? t.args.length : JSON.stringify(t.args ?? {}).length;
        if (t.result) totalChars += t.result.length;
      }
    }
  }
  return Math.round(totalChars * 0.55);
}
