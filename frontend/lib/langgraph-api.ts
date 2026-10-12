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
export type ThreadValues = { messages: unknown[]; context_metrics?: ContextMetrics; context_status?: ProcessingStatus; data_status?: ProcessingStatus };

/** 摘要为内部 state，界面仅消费消息和公开统计。 */
export async function getThreadState(threadId: string): Promise<ThreadValues> {
  const state = await apiClient().threads.getState(threadId);
  const values = (state as unknown as { values?: ThreadValues }).values;
  return { messages: Array.isArray(values?.messages) ? values.messages : [], context_metrics: values?.context_metrics, context_status: values?.context_status, data_status: values?.data_status };
}

export async function cancelRun(threadId: string, runId: string): Promise<void> {
  await apiClient().runs.cancel(threadId, runId, false, "interrupt");
}

export type ArchivePage = { content: string; offset: number; next_offset: number | null; char_length: number };
export async function readArchive(threadId: string, artifactId: string, offset = 0): Promise<ArchivePage> {
  const response = await fetch(`/api/archives/${encodeURIComponent(threadId)}/${encodeURIComponent(artifactId)}?offset=${offset}`, { cache: "no-store" });
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
};

export async function streamRun(opts: {
  threadId: string; graphId: string; messages: { id?: string; type: string; content: unknown }[];
  config?: { configurable?: Record<string, unknown> }; signal?: AbortSignal; handlers: StreamHandlers;
}): Promise<void> {
  const { threadId, graphId, messages, config, signal, handlers } = opts;
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
      if (chunk.event === "error") throw new Error("本次处理失败，已确认的内容仍保留。请重试。");
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
    handlers.onDone?.(signal?.aborted ? undefined : error instanceof Error ? error : new Error("本次处理失败，请重试。"));
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
