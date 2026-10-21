"use client";

import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ArrowUpIcon,
  BotIcon,
  CheckIcon,
  CopyIcon,
  SquareIcon,
  SparklesIcon,
  BarChart3Icon,
  SearchIcon,
  FileTextIcon,
  RotateCcwIcon,
} from "lucide-react";
import { useChatStore, type ChatMessage } from "@/lib/chat-store";
import { useChatMode } from "@/lib/chat-mode-store";
import { useSessionStore } from "@/lib/session-client";
import { Markdown } from "@/components/chat/Markdown";
import { ModeSwitcher } from "@/components/assistant-ui/mode-switcher";
import { GraphSwitcher } from "@/components/chat/GraphSwitcher";
import { CapabilitySelector } from "@/components/chat/CapabilitySelector";
import { ProcessingNotice } from "@/components/chat/ProcessingNotice";
import { ContextMeter } from "@/components/chat/ContextMeter";
import { TenantBadge } from "@/components/chat/TenantBadge";
import { ThinkingBlock } from "@/components/chat/ThinkingBlock";
import { ToolStepsBlock } from "@/components/chat/ToolStepsBlock";
import { FeedbackBar } from "@/components/chat/feedback/FeedbackBar";
import {
  cachedAnswerByMessage,
  loadAnswerMappings,
  trackCopy,
  timingDisplayComplete,
  timingFirstVisibleAnswer,
  finishTiming,
  abandonTimingsOnUnload,
  currentFeedbackEpoch,
} from "@/lib/feedback/client";
import { useArtifactStore } from "@/components/artifact/ArtifactProvider";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const EXAMPLE_PROMPTS = [
  {
    icon: SearchIcon,
    title: "今日空气质量",
    desc: "查询石家庄今天实时 AQI 与六因子浓度",
    query: "查询石家庄今天实时 AQI 与六因子浓度",
  },
  {
    icon: BarChart3Icon,
    title: "区域竞比对标",
    desc: "对比北京与天津本月的空气质量优劣与排名",
    query: "对比北京与天津本月的空气质量优劣与排名",
  },
  {
    icon: SparklesIcon,
    title: "达标分析研判",
    desc: "研判某城市今天是否有望实现 PM2.5 保良达标",
    query: "研判今天某城市是否有望实现 PM2.5 保良达标",
  },
  {
    icon: FileTextIcon,
    title: "环保标准法规",
    desc: "解答《环境空气质量标准》各因子等级分界限值",
    query: "请告诉我新版《环境空气质量标准》中六因子的等级分界标准",
  },
];

export function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const stopPending = useChatStore((s) => s.stopPending);
  const streamingText = useChatStore((s) => s.streamingText);
  const streamingOutputStatus = useChatStore((s) => s.streamingOutputStatus);
  const streamingReasoning = useChatStore((s) => s.streamingReasoning);
  const error = useChatStore((s) => s.error);
  const failedInput = useChatStore((s) => s.failedInput);
  const contextStatus = useChatStore((s) => s.contextStatus);
  const dataStatus = useChatStore((s) => s.dataStatus);
  const activeThreadId = useChatStore((s) => s.activeThreadId);
  const loadingThread = useChatStore((s) => s.loadingThread);
  const openThread = useChatStore((s) => s.openThread);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const stopStreaming = useChatStore((s) => s.stopStreaming);
  const recordNavigationStop = useChatStore((s) => s.recordNavigationStop);
  const regenerateLast = useChatStore((s) => s.regenerateLast);

  const activeTimingMessageId = useChatStore((s) => s.activeTimingMessageId);
  const activeTimingId = useChatStore((s) => s.activeTimingId);
  const activeTimingOutputComplete = useChatStore((s) => s.activeTimingOutputComplete);
  const scrollRef = useRef<HTMLDivElement>(null);
  const chatMode = useChatMode();
  const lastAssistantIndex = (() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === "assistant") return i;
    }
    return -1;
  })();

  const following = useRef(true);
  useEffect(() => { following.current = true; }, [activeThreadId]);

  // 服务端权威回答映射：消息变化时对未映射的助手消息批量回读（最多 50 条/次）。
  // 查询键是**真实 AI 消息 id**（台账 answer.message_id），不是轮次合成 ID。
  const sessionCtx = useSessionStore((s) => s.ctx);
  useEffect(() => {
    const unmapped = messages
      .filter((m) => m.role === "assistant" && m.content && m.answerId === undefined)
      .map((m) => m.sourceMessageId || m.id);
    if (!unmapped.length) return;
    let cancelled = false;
    void loadAnswerMappings(unmapped, true, activeThreadId).then(() => {
      if (cancelled) return;
      // 用缓存结果回填消息的 answerId（含"确认无映射"的空值，避免重复查询）
      useChatStore.setState((state) => ({
        messages: state.messages.map((m) => {
          if (m.role !== "assistant" || m.answerId !== undefined) return m;
          const found = cachedAnswerByMessage(m.sourceMessageId || m.id, activeThreadId);
          if (!found) return m;
          return { ...m, answerId: found.answer_id || null };
        }),
      }));
    });
    return () => { cancelled = true; };
  }, [messages, activeThreadId]);

  // 页面导航离开/刷新：把"离开"写成独立停止意图（origin=navigation，不冒充已取消）。
  // 用 pagehide（含 bfcache）而非 beforeunload；sendBeacon 尽力送达，不阻塞卸载。
  useEffect(() => {
    const onPageHide = () => { abandonTimingsOnUnload(); recordNavigationStop(); };
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, [recordNavigationStop]);

  // 仅在用户接近底部时跟随，用户上翻时保持阅读位置。
  useEffect(() => {
    const el = scrollRef.current;
    if (el && following.current) {
      el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
    }
  }, [messages, streamingText, streamingReasoning, isStreaming]);

  // React commit之后等实际正文完成布局和一轮绘制；历史消息没有新测量。
  useEffect(() => {
    if (!activeTimingId) return;
    const epoch = currentFeedbackEpoch();
    let frame2 = 0;
    const frame1 = requestAnimationFrame(() => {
      frame2 = requestAnimationFrame(() => {
        if (epoch !== currentFeedbackEpoch() || useChatStore.getState().activeTimingId !== activeTimingId) return;
        const candidates = scrollRef.current?.querySelectorAll<HTMLElement>(isStreaming ? "[data-streaming-answer-body]" : "[data-answer-body]");
        const node = isStreaming ? candidates?.[candidates.length-1] : Array.from(candidates ?? []).find(el => el.dataset.answerMessageId === activeTimingMessageId);
        const rect = node?.getBoundingClientRect();
        const parentRect = scrollRef.current?.getBoundingClientRect();
        const style = node ? getComputedStyle(node) : null;
        const visible = node?.textContent?.trim() && rect && parentRect && rect.width>0 && rect.height>0 && rect.bottom>Math.max(0,parentRect.top) && rect.top<Math.min(window.innerHeight,parentRect.bottom) && style?.visibility !== "hidden" && style?.display !== "none" && document.visibilityState === "visible";
        if (visible) timingFirstVisibleAnswer(activeTimingId);
        if (!isStreaming) {
          if (visible && activeTimingOutputComplete && !error && !useChatStore.getState().stopPending) timingDisplayComplete(activeTimingId);
          finishTiming(activeTimingId);
          useChatStore.setState({activeTimingId: null, activeTimingMessageId: null});
        }
      });
    });
    return () => { cancelAnimationFrame(frame1); cancelAnimationFrame(frame2); };
  }, [activeTimingId, activeTimingMessageId, activeTimingOutputComplete, streamingText, messages, isStreaming, error]);

  // 画布扫描：检测工具结果中的 artifact 引用
  const artifactStore = useArtifactStore();
  const processedArtifactRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const m of messages) {
      if (m.role !== "assistant" || !m.tools) continue;
      for (const t of m.tools) {
        if (!t.result || processedArtifactRef.current.has(t.id)) continue;
        try {
          const obj = JSON.parse(t.result) as Record<string, unknown>;
          const ref = obj.artifact_ref ?? obj.artifact;
          if (ref && typeof ref === "object" && "title" in (ref as Record<string, unknown>)) {
            processedArtifactRef.current.add(t.id);
            artifactStore.getState().add(ref as Parameters<ReturnType<typeof artifactStore.getState>["add"]>[0]);
          }
        } catch {
          // 忽略非 JSON 工具结果
        }
      }
    }
  }, [messages, artifactStore]);

  const isEmpty = messages.length === 0 && !isStreaming && !loadingThread && !error;

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      {/* 滚动消息展示区 */}
      <div ref={scrollRef} onScroll={(event) => { const element = event.currentTarget; following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; }} className="min-h-0 flex-1 overflow-y-auto px-4 pt-6 pb-4">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
          {isEmpty ? (
            <div className="flex flex-1 flex-col items-center justify-center px-4 py-12 text-center">
              <div className="flex size-12 items-center justify-center rounded-2xl bg-gradient-to-tr from-emerald-500/20 to-cyan-500/20 text-emerald-600 dark:text-emerald-400 mb-4 ring-1 ring-emerald-500/30">
                <SparklesIcon className="size-6" />
              </div>
              <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-foreground">
                空气质量智能专家 Agent
              </h1>
              <p className="mt-2 text-sm text-muted-foreground max-w-md">
                基于 LangGraph 状态图与专属 MCP 工具，支持实时查询、达标研判、综合分析与报告生成。
              </p>

              {/* 推荐问题快捷卡片 */}
              <div className="mt-8 grid w-full grid-cols-1 sm:grid-cols-2 gap-3 text-left">
                {EXAMPLE_PROMPTS.map((item, idx) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => void sendMessage(item.query, chatMode)}
                      className="group flex flex-col gap-1 rounded-xl border border-border/60 bg-muted/20 p-3.5 text-left transition-all hover:border-emerald-500/50 hover:bg-muted/40 hover:shadow-xs"
                    >
                      <div className="flex items-center gap-2 font-medium text-xs text-foreground group-hover:text-emerald-600 dark:group-hover:text-emerald-400">
                        <Icon className="size-3.5" />
                        <span>{item.title}</span>
                      </div>
                      <span className="text-[12px] text-muted-foreground leading-snug line-clamp-2">
                        {item.desc}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : (
            messages.map((m, index) => (
              <MessageItem
                key={m.id}
                message={m}
                // 重新生成只对**最后一轮**助手回答开放：按钮语义是"重做这次回答"，
                // 不支持对历史轮次回放（第 3 项反馈迭代时再扩展）。
                isLatestAssistant={index === lastAssistantIndex}
                feedbackEpoch={sessionCtx?.context_version ?? 0}
              />
            ))
          )}

          <ProcessingNotice status={contextStatus} />
          <ProcessingNotice status={dataStatus} />
          {loadingThread && <p role="status" aria-live="polite" className="text-sm text-muted-foreground">正在读取会话…</p>}
          {/* 流式生成中的响应卡片 */}
          {isStreaming && (streamingText || messages.at(-1)?.role !== "assistant" || !messages.at(-1)?.content) && (
            <StreamingAssistantCard
              streamingText={streamingText}
              streamingReasoning={streamingReasoning}
              status={streamingOutputStatus}
            />
          )}

          {/* 异常错误提示 */}
          {error && (
            <div role="alert" className="rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-xs text-destructive">
              <p className="font-semibold mb-0.5">请求处理遇到问题</p>
              <p className="opacity-90">{error}</p>
              {failedInput && <Button type="button" variant="outline" size="sm" className="mt-2" disabled={isStreaming} onClick={() => void sendMessage(failedInput, chatMode, "retry")}>重新发送</Button>}
              {!failedInput && activeThreadId && <Button type="button" variant="outline" size="sm" className="mt-2" disabled={loadingThread || isStreaming} onClick={() => void openThread(activeThreadId)}>重新读取会话</Button>}
            </div>
          )}
        </div>
      </div>

      {/* 底部悬浮输入面板 */}
      <div className="bg-background/95 backdrop-blur-md px-4 py-3 border-t border-border/40">
        <Composer
          disabled={isStreaming || stopPending || loadingThread}
          onSend={(text) => void sendMessage(text, chatMode)}
          onStop={() => stopStreaming()}
        />
      </div>
    </div>
  );
}

// ───────────────────────── 消息组件 ─────────────────────────

function MessageItem({
  message,
  isLatestAssistant,
  feedbackEpoch,
}: {
  message: ChatMessage;
  isLatestAssistant?: boolean;
  feedbackEpoch?: number;
}) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  // 重新生成需要知道当前是否可发起（流式中/停止未确认时禁用）
  const busy = useChatStore((s) => s.isStreaming || s.stopPending);
  const regenerateLast = useChatStore((s) => s.regenerateLast);
  const chatMode = useChatMode();
  const mapping = useAnswerMapping(message);

  if (message.role === "user") {
    return (
      <div className="flex justify-end px-1">
        <div className="max-w-[85%] rounded-2xl rounded-tr-xs bg-muted/80 text-foreground px-4 py-2.5 text-sm leading-relaxed border border-border/40 shadow-2xs">
          {message.content}
        </div>
      </div>
    );
  }

  // Assistant 助手回答
  // 复制：等待系统剪贴板写入**成功**后才显示成功并记录事件；拒绝/失败显示可恢复提示
  const copyFullContent = async () => {
    if (!message.content) return;
    const copyEpoch = currentFeedbackEpoch();
    setCopyFailed(false);
    try {
      await navigator.clipboard.writeText(message.content);
    } catch {
      if (copyEpoch !== currentFeedbackEpoch()) return;
      setCopied(false);
      setCopyFailed(true);
      setTimeout(() => setCopyFailed(false), 3000);
      return;
    }
    if (copyEpoch !== currentFeedbackEpoch()) return;
    setCopied(true);
    // 复制成功 → 采集复制事件（有映射才统计；正文不上传）
    if (mapping?.answer_id) trackCopy(mapping.answer_id);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="group flex gap-3 px-1">
      {/* 助手头像 */}
      <div className="flex size-7 shrink-0 select-none items-center justify-center rounded-lg bg-gradient-to-tr from-emerald-600 to-teal-500 text-white shadow-2xs">
        <BotIcon className="size-4" />
      </div>

      <div className="min-w-0 flex-1 space-y-2">
        {/* 思考过程折叠条 */}
        {message.reasoningContent && (
          <ThinkingBlock
            content={message.reasoningContent}
            reasoningTokens={message.reasoningTokens}
          />
        )}

        {/* 聚合工具调用折叠卡 */}
        {message.tools && message.tools.length > 0 && (
          <ToolStepsBlock tools={message.tools} />
        )}

        {/* 最终回答正文 */}
        {message.content && (
          <div data-answer-body data-answer-message-id={message.sourceMessageId || message.id} className="text-sm">
            <Markdown content={message.content} />
          </div>
        )}
        {message.incomplete && (
          <p role="status" className="text-[11px] text-amber-600 dark:text-amber-400">
            未完成 · 生成中断或尚未确认，此内容不是完整终答。
          </p>
        )}

        {/* 底部轻量操作工具栏 */}
        {message.content && (
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2 pt-1 text-[11px] text-muted-foreground opacity-60 group-hover:opacity-100 transition-opacity">
              {isLatestAssistant && (
                <button
                  type="button"
                  onClick={() => void regenerateLast(chatMode)}
                  disabled={busy}
                  className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-muted transition-colors text-muted-foreground hover:text-foreground disabled:opacity-40"
                  aria-label="重新生成"
                >
                  <RotateCcwIcon className="size-3" />
                  <span>重新生成</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => void copyFullContent()}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
              >
                {copied ? (
                  <>
                    <CheckIcon className="size-3 text-emerald-500" />
                    <span className="text-emerald-500">已复制</span>
                  </>
                ) : copyFailed ? (
                  <>
                    <CopyIcon className="size-3 text-red-500" />
                    <span className="text-red-500">复制失败，请重试</span>
                  </>
                ) : (
                  <>
                    <CopyIcon className="size-3" />
                    <span>复制</span>
                  </>
                )}
              </button>

              {message.usage && (
                <span className="font-mono text-[10px] text-muted-foreground/80">
                  · {message.usage.totalTokens} tokens
                </span>
              )}
            </div>
            {/* 评价栏：服务端确认映射才可评价；未完成正文不开放反馈 */}
            {!message.incomplete && (
              <FeedbackBar
                mapping={mapping}
                hasContent={Boolean(message.content)}
                epoch={feedbackEpoch ?? 0}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** 消息 → 服务端回答映射（权威，不猜测）：缓存命中返回，否则视为未映射。 */
function useAnswerMapping(message: ChatMessage) {
  const threadForFeedback = useChatStore((s) => s.activeThreadId);
  const [, force] = useState(0);
  const mappingKey = message.sourceMessageId || message.id;
  useEffect(() => {
    if (message.role !== "assistant" || !message.content) return;
    const found = cachedAnswerByMessage(mappingKey, threadForFeedback);
    if (found?.answer_id) return;
    let cancelled = false;
    const epoch = currentFeedbackEpoch();
    void (async () => {
      for (const delay of [0, 300, 800, 1500]) {
        if (delay) await new Promise(resolve => setTimeout(resolve, delay));
        if (cancelled || epoch !== currentFeedbackEpoch()) return;
        await loadAnswerMappings([mappingKey], true, threadForFeedback);
        if (cancelled || epoch !== currentFeedbackEpoch()) return;
        force(n => n + 1);
        if (cachedAnswerByMessage(mappingKey, threadForFeedback)) return;
      }
    })();
    return () => { cancelled = true; };
  }, [mappingKey, message.role, message.content, threadForFeedback]);
  if (message.role !== "assistant" || !message.content) return null;
  return cachedAnswerByMessage(mappingKey, threadForFeedback);
}

/** 流式生成中的占位与打字机卡片 */
function StreamingAssistantCard({
  streamingText,
  streamingReasoning,
  status,
}: {
  streamingText: string;
  streamingReasoning: string;
  status: "idle" | "streaming" | "partial" | "complete" | "aborted";
}) {
  return (
    <div className="flex gap-3 px-1 animate-in fade-in-50 duration-200">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-tr from-emerald-600 to-teal-500 text-white shadow-2xs">
        <BotIcon className="size-4" />
      </div>

      <div className="min-w-0 flex-1 space-y-2 text-sm">
        {status === "streaming" && streamingText && (
          <p className="text-[11px] text-emerald-600 dark:text-emerald-400">正在生成 · 内容尚未最终确认</p>
        )}
        {/* 若有流式思考，展示正在思考的动画组件 */}
        {streamingReasoning && (
          <ThinkingBlock content={streamingReasoning} isStreaming />
        )}

        {/* 流式正文 */}
        {streamingText ? (
          <div data-streaming-answer-body>
            <Markdown content={streamingText} />
            <span className="ml-1 inline-block h-3.5 w-1.5 animate-pulse rounded-sm bg-emerald-500 align-middle" />
          </div>
        ) : !streamingReasoning ? (
          <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
            <span className="size-2 animate-ping rounded-full bg-emerald-500" />
            <span>智能体正在调度模型与工具…</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ───────────────────────── 输入区 (Composer) ─────────────────────────

function Composer({
  disabled,
  onSend,
  onStop,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const canSend = text.trim().length > 0 && !disabled;
  const username = useSessionStore((s) => s.username);
  const logout = useSessionStore((s) => s.logout);
  const logoutPending = useSessionStore((s) => s.logoutPending);
  const sessionError = useSessionStore((s) => s.error);
  const refreshSession = useSessionStore((s) => s.refreshSession);

  const submit = useCallback(() => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  }, [text, disabled, onSend]);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !composingRef.current && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229) {
      e.preventDefault();
      submit();
    }
  };

  const onFormSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  return (
    <form onSubmit={onFormSubmit} className="mx-auto w-full max-w-3xl">
      <div className="flex flex-col gap-2 rounded-2xl border border-border/70 bg-muted/20 p-2.5 shadow-xs focus-within:border-emerald-500/50 focus-within:ring-2 focus-within:ring-emerald-500/10 transition-all">
        {sessionError && (
          <div role="alert" className="flex items-center gap-2 px-1 text-xs text-destructive">
            <span>{sessionError}</span>
            <button type="button" className="underline" onClick={() => void (username ? logout() : refreshSession())}>重试</button>
          </div>
        )}
        {/* 控制与上下文监控顶栏 */}
        <div className="flex flex-wrap items-center gap-2 px-1 pt-0.5">
          <GraphSwitcher />
          <ModeSwitcher />
          <CapabilitySelector />

          {/* 上下文容量监控胶囊指示器 */}
          <ContextMeter />

          {/* 登录用户、企业与登出 */}
          <div className="ml-auto inline-flex max-w-full flex-wrap items-center gap-2 text-[11px]">
            {username ? (
              <>
                <TenantBadge />
                <span className="hidden rounded-full bg-muted px-2 py-0.5 font-medium text-foreground sm:inline">
                  {username}
                </span>
                <button
                  type="button"
                  disabled={logoutPending}
                  onClick={() => void logout()}
                  className="text-muted-foreground hover:text-foreground underline underline-offset-2"
                >
                  {logoutPending ? "退出中…" : "退出"}
                </button>
              </>
            ) : null}
          </div>
        </div>

        {/* 多行自适应输入框 */}
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            const el = e.target;
            el.style.height = "auto";
            el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
          }}
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={() => { composingRef.current = false; }}
          onKeyDown={onKeyDown}
          placeholder="问问空气质量情况、达标研判、或者查询具体城市数据… (Shift+Enter 换行)"
          aria-label="消息输入框"
          rows={1}
          disabled={disabled}
          className="max-h-44 min-h-11 w-full resize-none bg-transparent px-2.5 py-1.5 text-sm outline-none placeholder:text-muted-foreground/60 disabled:opacity-60"
        />

        {/* 底部发送 / 停止按钮区 */}
        <div className="flex items-center justify-between px-1">
          <span className="text-[11px] text-muted-foreground/60">
            按 Enter 发送
          </span>

          {disabled ? (
            <Button
              type="button"
              size="icon"
              className="size-8 rounded-full bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={onStop}
              aria-label="停止生成"
            >
              <SquareIcon className="size-3.5 fill-current" />
            </Button>
          ) : (
            <Button
              type="submit"
              size="icon"
              className={cn(
                "size-8 rounded-full transition-all duration-200",
                canSend
                  ? "bg-gradient-to-tr from-emerald-600 to-teal-500 text-white hover:opacity-90 shadow-2xs"
                  : "bg-muted text-muted-foreground opacity-50 cursor-not-allowed"
              )}
              disabled={!canSend}
              aria-label="发送消息"
            >
              <ArrowUpIcon className="size-4 stroke-[2.5]" />
            </Button>
          )}
        </div>
      </div>
    </form>
  );
}
