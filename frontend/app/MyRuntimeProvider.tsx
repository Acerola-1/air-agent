"use client";

import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useStreamRuntime } from "@assistant-ui/react-langchain";
import { Client } from "@langchain/langgraph-sdk";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { makeLangGraphThreadListAdapter } from "./langgraph-thread-list-adapter";
import {
  ArtifactProvider,
} from "@/components/artifact/ArtifactProvider";
import { CanvasWindow } from "@/components/artifact/CanvasWindow";
import { InlineContainer } from "@/components/artifact/InlineContainer";

/** ArtifactToolInterceptor: 仅客户端渲染, 避免 SSR 时 stream 为 undefined 崩溃. */
const ArtifactToolInterceptor = dynamic(
  () =>
    import("@/components/artifact/ArtifactToolInterceptor").then(
      (m) => m.ArtifactToolInterceptor,
    ),
  { ssr: false },
);

/** 当前 air_agent 支持的 6 个 graph — 这里硬编码一份中文展示名, 下拉切换用
 *
 *  graph_id 必须和 langgraph.json graphs 的 key 完全一致:
 *    basic-qa / intelligent-analysis / data-analysis /
 *    intelligent-report / deep-research / intelligent-tracing
 */
export const GRAPH_OPTIONS = [
  { id: "basic-qa", label: "基础问答", desc: "标准 Q&A 对话流" },
  { id: "intelligent-analysis", label: "智能分析", desc: "多步推理 + 图表分析" },
  { id: "intelligent-tracing", label: "溯源追踪", desc: "数据来源与推导链路追踪" },
  { id: "intelligent-report", label: "报告生成", desc: "生成结构化报告" },
  { id: "deep-research", label: "深度研究", desc: "多轮检索 + 长文研究" },
  { id: "data-analysis", label: "数据分析助手", desc: "表格 / SQL / 可视化" },
] as const;

export type GraphId = (typeof GRAPH_OPTIONS)[number]["id"];

/** 前端直连 LangGraph 用的 URL
 *  - 若 NEXT_PUBLIC_LANGGRAPH_API_URL 显式设置, 浏览器直接直连后端
 *  - 否则走 Next Route Handler /api/* 同源代理 (推荐, 免 CORS)
 */
function buildApiUrl() {
  const envDirect = process.env.NEXT_PUBLIC_LANGGRAPH_API_URL;
  if (envDirect && envDirect.trim()) return envDirect;
  if (typeof window !== "undefined") return new URL("/api", window.location.href).href;
  return undefined;
}

export function MyRuntimeProvider({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // 默认从 NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID 或首个 graph 取默认 graph
  const defaultGraphId =
    (process.env.NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID as GraphId | undefined) || GRAPH_OPTIONS[0].id;

  const [assistantId, setAssistantId] = useState<GraphId>(defaultGraphId);
  const assistantIdRef = useRef<GraphId>(assistantId);
  // 直接在 render body 更新 ref，确保子组件（key={assistantId} 重建时）
  // 调 adapter.list() 读到的 ref 已是最新值；用 useEffect 会有竞态（effect 在 paint 后才执行）
  assistantIdRef.current = assistantId;

  const apiUrl = useMemo(() => buildApiUrl(), []);
  const apiUrlRef = useRef(apiUrl);
  apiUrlRef.current = apiUrl;

  /** 自定义 ThreadList Adapter — 切换 graph 后 list 会带 metadata.graph_id 过滤
   *
   *  adapter 本身不包含 reactive state, 但它每次 list() 时从闭包读 assistantIdRef.current,
   *  所以 React 这边只要在 setAssistantId 后主动让 ThreadList reload 即可
   *  (通过下方 providerKey 强制 AssistantRuntimeProvider 卸载-重挂载, 最干净)
   */
  const adapter = useMemo(
    () =>
      makeLangGraphThreadListAdapter({
        apiUrl:
          process.env.LANGGRAPH_API_URL ||
          (typeof window === "undefined" ? "http://localhost:2024" : apiUrl || "http://localhost:2024"),
        getCurrentAssistantId: () => assistantIdRef.current,
      }),
    [apiUrl],
  );

  /** 用 assistantId 作为 Provider 的 key —— 切 graph 就重建整个 Runtime
   *
   *  useStreamRuntime 内部用 useStream hook, 它的 options.assistantId 只在首次 mount 生效,
   *  后续切换 assistantId 不会让内部 stream 重建. 用 React key 换一个新实例, 干净可靠.
   *  代价是: 切 graph 时当前会话清空 —— 合理, 不同 graph 的 thread 不该混用.
   */
  return (
    <AssistantRuntimeProviderInner
      key={assistantId}
      assistantId={assistantId}
      apiUrl={apiUrl}
      adapter={adapter}
      onAssistantIdChange={setAssistantId}
    >
      {children}
    </AssistantRuntimeProviderInner>
  );
}

/** 内层: 接受稳定的 assistantId (通过 key 换 mount), 内部 useStreamRuntime 就能用对 graph_id
 *
 *  关于 create 与 adapter.create 的职责:
 *   - adapter.create = RemoteThreadListAdapter.create: 由 ThreadList 的"新建会话"按钮触发,
 *     我们在 langgraph-thread-list-adapter.ts 里实现了客户端 generateUuidV7() + SDK
 *     threads.create({ threadId: uuid }) —— 保证返回的 remoteId 就是合法 UUID, 没有 __LOCALID_.
 *   - 这里的 useStreamRuntime.create = 当 Thread 组件要发消息但当前还没有 active thread
 *     时的"懒创建 thread"钩子 —— 它现在用 SDK 调 create, 返回 externalId=真实 UUID,
 *     两端都 100% 合法 UUID, 前后端都不会 422.
 */
function AssistantRuntimeProviderInner({
  children,
  assistantId,
  apiUrl,
  adapter,
  onAssistantIdChange,
}: {
  children: React.ReactNode;
  assistantId: GraphId;
  apiUrl: string | undefined;
  adapter: ReturnType<typeof makeLangGraphThreadListAdapter>;
  onAssistantIdChange: (id: GraphId) => void;
}) {
  const runtime = useStreamRuntime({
    assistantId,
    apiUrl,
    unstable_threadListAdapter: adapter,
    /** 注意: 使用 unstable_threadListAdapter (自定义 adapter) 时, 这个 create 钩子
     *  **不会被调用** —— useStreamRuntime 内部用 `unstable_threadListAdapter ?? cloudAdapter`,
     *  而 create 钩子只挂在 cloudAdapter 上 (见 useCloudThreadListAdapter.initialize).
     *
     *  真正的 thread 创建入口在 adapter.initialize 里 (langgraph-thread-list-adapter.ts):
     *  assistant-ui 的 "+new thread" 流程会先生成 __LOCALID_ 占位 ID, 发首条消息时调
     *  adapter.initialize(__LOCALID_), 我们在那里生成 UUIDv7 并调用 SDK threads.create.
     *
     *  这里保留 create 仅作 cloud 模式接入时的预留, 当前自定义 adapter 模式下是死代码.
     */
    create: async () => {
      const { thread_id } = await new Client(apiUrl ? { apiUrl } : {}).threads.create({
        metadata: { graph_id: assistantId },
      } as unknown as Parameters<Client["threads"]["create"]>[0]);
      return { externalId: thread_id };
    },
  });

  // 把切 graph 的方法挂到 window, 方便顶栏组件调用 (避免创建 Context 增加复杂度)
  useEffect(() => {
    (window as unknown as { __AIR_AGENT_SWITCH_GRAPH?: (id: GraphId) => void }).__AIR_AGENT_SWITCH_GRAPH =
      onAssistantIdChange;
    (window as unknown as { __AIR_AGENT_CURRENT_GRAPH?: GraphId }).__AIR_AGENT_CURRENT_GRAPH = assistantId;
    return () => {
      const w = window as unknown as {
        __AIR_AGENT_SWITCH_GRAPH?: (id: GraphId) => void;
        __AIR_AGENT_CURRENT_GRAPH?: GraphId;
      };
      if (w.__AIR_AGENT_SWITCH_GRAPH === onAssistantIdChange) {
        delete w.__AIR_AGENT_SWITCH_GRAPH;
        delete w.__AIR_AGENT_CURRENT_GRAPH;
      }
    };
  }, [assistantId, onAssistantIdChange]);

  return (
    <ArtifactProvider>
      <AssistantRuntimeProvider runtime={runtime}>
        {children}
        <ArtifactToolInterceptor />
        <InlineContainer />
      </AssistantRuntimeProvider>
      <CanvasWindow />
    </ArtifactProvider>
  );
}
