import type {
  RemoteThreadListAdapter,
  RemoteThreadListPageOptions,
  RemoteThreadListResponse,
  RemoteThreadMetadata,
  RemoteThreadInitializeResponse,
} from "@assistant-ui/core";
import { Client } from "@langchain/langgraph-sdk";
import type { AssistantStream } from "assistant-stream";
import type { ThreadMessage } from "@assistant-ui/core";
import { generateUuidV7, isValidUuid } from "@/lib/uuidv7";

/** LangGraph SDK Thread Search 返回 item 形状 */
type LGThreadItem = {
  thread_id: string;
  metadata?: Record<string, unknown> & { graph_id?: string; title?: string };
  created_at?: string;
  updated_at?: string;
};

/** 创建 LangGraph API 适配的 ThreadListAdapter
 *
 *  关键设计（解决 422 Invalid UUID 问题）：
 *   - 使用 unstable_threadListAdapter (自定义 adapter) 时, assistant-ui 的
 *     "+new thread" 流程: RemoteThreadListThreadListRuntimeCore._switchToNewThread
 *     先生成 __LOCALID_xxx 占位 ID → 用户发首条消息时调 adapter.initialize(占位ID).
 *     此时 create 钩子 / adapter.create 都不会被调用 (它们只挂 cloudAdapter).
 *     所以真正的 thread 创建逻辑必须放在 initialize() 里: 收到非 UUID 占位 ID 时,
 *     用 generateUuidV7() 生成合法 UUID + SDK threads.create 创建后端 thread,
 *     返回 { remoteId: uuid } —— 全程不出现 __LOCALID_, 后端 UUID 校验 100% 通过.
 *
 *   - initialize() 对 UUID 形式的 threadId (历史会话) 直接同步返回, 不做网络请求.
 *
 *   - list() 搜索时按 `metadata.graph_id` 过滤, 不同 graph 会话隔离.
 */
export function makeLangGraphThreadListAdapter(opts: {
  apiUrl: string;
  /** 返回当前选中的 graph_id, list/create 时 metadata 写入 */
  getCurrentAssistantId: () => string | undefined;
  titleStreamFn?: (remoteId: string, messages: readonly ThreadMessage[]) => Promise<AssistantStream>;
}): RemoteThreadListAdapter {
  const { apiUrl, getCurrentAssistantId, titleStreamFn } = opts;
  const sdk = () => new Client({ apiUrl });

  const toRemote = (t: LGThreadItem): RemoteThreadMetadata => {
    const meta = t.metadata || {};
    const custom = { graph_id: meta.graph_id };
    return {
      remoteId: t.thread_id,
      externalId: t.thread_id,
      title: typeof meta.title === "string" && meta.title ? meta.title : undefined,
      lastMessageAt: t.updated_at ? new Date(t.updated_at) : t.created_at ? new Date(t.created_at) : undefined,
      status: "regular",
      custom,
    };
  };

  return {
    async list(params?: RemoteThreadListPageOptions): Promise<RemoteThreadListResponse> {
      const graphId = getCurrentAssistantId();
      const body: Record<string, unknown> = { limit: 20 };
      if (graphId) body.metadata = { graph_id: graphId };
      if (params?.after) body.offset = params.after;

      const res = (await sdk().threads.search(body as unknown as Parameters<Client["threads"]["search"]>[0])) as unknown as
        | LGThreadItem[]
        | { threads?: LGThreadItem[]; next_page_token?: string };

      let items: LGThreadItem[] = [];
      let nextCursor: string | undefined;
      if (Array.isArray(res)) {
        items = res;
      } else if (res && typeof res === "object") {
        items = (res as { threads?: LGThreadItem[] }).threads || [];
        nextCursor = (res as { next_page_token?: string }).next_page_token;
      }
      return { threads: items.map(toRemote), nextCursor };
    },

    /** 初始化 threadId 到 runtime
     *
     *  三种情形:
     *   1) threadId 是合法 UUID —— 历史会话点进来, 直接放行, 不做网络请求.
     *   2) threadId 是 assistant-ui 的 __LOCALID_xxx 占位 ID —— 这是 "+new thread"
     *      按钮的流程: RemoteThreadListThreadListRuntimeCore._switchToNewThread 先生成
     *      本地占位 ID, 用户发首条消息时调 initialize(占位ID). 此时必须在这里**真正创建**
     *      后端 thread 并返回合法 UUID, 否则 runtime 会用 __LOCALID_ 去请求
     *      /thread/:id/state 触发 422 "Invalid thread ID: must be a UUID".
     *
     *      关键认知: 使用 unstable_threadListAdapter (自定义 adapter) 时, cloudAdapter
     *      被完全替换, 因此 useStreamRuntime 的 create 钩子和 adapter.create() 都**不会**
     *      被调用 —— 唯一的 thread 创建入口就是这里的 initialize. 所以创建逻辑必须放这里.
     *   3) 其它非法 ID —— 兜底也走创建流程, 保证返回的一定是合法 UUID.
     */
    async initialize(threadId: string): Promise<RemoteThreadInitializeResponse> {
      if (isValidUuid(threadId)) {
        return { remoteId: threadId, externalId: threadId };
      }
      // __LOCALID_xxx 或任何非 UUID —— 真正创建后端 thread, 返回合法 UUID
      const graphId = getCurrentAssistantId();
      const newThreadId = generateUuidV7();
      await sdk().threads.create({
        threadId: newThreadId,
        metadata: { graph_id: graphId },
      } as Parameters<Client["threads"]["create"]>[0]);
      return { remoteId: newThreadId, externalId: newThreadId };
    },

    async fetch(threadId: string): Promise<RemoteThreadMetadata> {
      const t = (await sdk().threads.get(threadId)) as unknown as LGThreadItem;
      return toRemote(t);
    },

    async rename(remoteId: string, newTitle: string) {
      try {
        const current = (await sdk().threads.get(remoteId)) as unknown as LGThreadItem;
        const metadata = { ...(current.metadata || {}), title: newTitle };
        await sdk().threads.update(remoteId, { metadata } as Parameters<Client["threads"]["update"]>[1]);
      } catch (_e) {
        /* rename 软失败 */
      }
    },

    async updateCustom(remoteId: string, custom: Record<string, unknown> | undefined) {
      try {
        const current = (await sdk().threads.get(remoteId)) as unknown as LGThreadItem;
        const metadata = { ...(current.metadata || {}), ...(custom || {}) };
        await sdk().threads.update(remoteId, { metadata } as Parameters<Client["threads"]["update"]>[1]);
      } catch (_e) {
        /* ignore */
      }
    },

    async archive() {
      /* LangGraph dev 模式无 archive 概念，软忽略 */
    },
    async unarchive() {
      /* 同上 */
    },

    async delete(remoteId: string) {
      await sdk().threads.delete(remoteId);
    },

    async generateTitle(remoteId: string, messages: readonly ThreadMessage[]): Promise<AssistantStream> {
      if (titleStreamFn) return titleStreamFn(remoteId, messages);
      return new ReadableStream() as unknown as AssistantStream;
    },
  };
}
