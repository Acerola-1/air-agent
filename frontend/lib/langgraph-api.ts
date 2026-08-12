import { Client } from "@langchain/langgraph-sdk";
import { generateUuidV7 } from "@/lib/uuidv7";

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

function apiClient(): Client {
  return new Client({ apiUrl: apiBaseUrl() });
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
 *  注意：graph_id 必须放 metadata（容器版 langgraph-api 忽略顶层 graphId 参数，
 *  只认 metadata.graph_id；venv langgraph dev 0.12.3 两者都接受，统一用 metadata 兼容）。 */
export async function createThread(
  graphId: string,
  userId?: string,
  title?: string,
): Promise<string> {
  const threadId = generateUuidV7();
  const metadata: Record<string, unknown> = { graph_id: graphId };
  if (userId) metadata.user_id = userId;
  if (title) metadata.title = title;
  await apiClient().threads.create({ threadId, metadata });
  return threadId;
}

/** 读取线程完整状态（含历史 messages） */
export async function getThreadState(threadId: string): Promise<unknown[]> {
  const state = await apiClient().threads.getState(threadId);
  const messages = (state as { values?: { messages?: unknown[] } }).values?.messages;
  return Array.isArray(messages) ? messages : [];
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

/** runs.stream 流式回调 */
export type StreamHandlers = {
  /** values 事件：完整 messages 快照（节点结束触发，可靠渲染基准） */
  onValues: (messages: unknown[]) => void;
  /** messages 事件：token 级增量 [[message, meta], ...]，用于打字机效果 */
  onMessagesDelta: (messages: [unknown, unknown][]) => void;
  /** 流结束 / 出错 */
  onDone?: (error?: Error) => void;
};

/** 提交一轮对话（增量 messages，历史由后端 checkpointer 累积）并流式消费 */
export async function streamRun(opts: {
  threadId: string;
  graphId: string;
  messages: { type: string; content: unknown }[];
  config?: { configurable?: Record<string, unknown> };
  signal?: AbortSignal;
  handlers: StreamHandlers;
}): Promise<void> {
  const { threadId, graphId, messages, config, signal, handlers } = opts;
  try {
    const stream = await apiClient().runs.stream(threadId, graphId, {
      input: { messages },
      config,
      streamMode: ["values", "messages-tuple"],
      signal,
    });
    for await (const chunk of stream) {
      if (signal?.aborted) break;
      if (chunk.event === "values") {
        const values = chunk.data as { messages?: unknown[] };
        if (Array.isArray(values?.messages)) handlers.onValues(values.messages);
      } else if (chunk.event === "messages") {
        // messages-tuple 事件：data = [message, metadata]
        const tuple = chunk.data as [unknown, unknown] | undefined;
        if (tuple && Array.isArray(tuple)) handlers.onMessagesDelta([tuple]);
      }
    }
    handlers.onDone?.();
  } catch (error) {
    if (signal?.aborted) {
      handlers.onDone?.();
    } else {
      handlers.onDone?.(error instanceof Error ? error : new Error(String(error)));
    }
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
