"use client";

import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { ArrowUpIcon, ChevronDownIcon, SquareIcon, WrenchIcon } from "lucide-react";
import { useChatStore, type ChatMessage } from "@/lib/chat-store";
import { useChatMode } from "@/lib/chat-mode-store";
import { useSessionStore } from "@/lib/session-client";
import { Markdown } from "@/components/chat/Markdown";
import { ModeSwitcher } from "@/components/assistant-ui/mode-switcher";
import { GraphSwitcher } from "@/components/chat/GraphSwitcher";
import { useArtifactStore } from "@/components/artifact/ArtifactProvider";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/** 主对话区：消息列表 + 流式增量 + 输入框 + 画布推送.
 *
 *  数据流（全原生，不经 assistant-ui）：
 *   - 历史：chat-store.openThread 拉 getState → parseMessages
 *   - 流式：chat-store.sendMessage → runs.stream（values 快照 + messages 增量）
 *   - 画布：扫描 tool 消息中的 create_artifact 结果，推入 artifact store
 */
export function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const streamingText = useChatStore((s) => s.streamingText);
  const error = useChatStore((s) => s.error);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const stopStreaming = useChatStore((s) => s.stopStreaming);

  const scrollRef = useRef<HTMLDivElement>(null);
  const chatMode = useChatMode();

  // 自动滚动到底部
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streamingText, isStreaming]);

  // 画布：扫描 tool 消息中的 create_artifact / artifact_ref，推入 artifact store
  const artifactStore = useArtifactStore();
  const processedArtifactRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const m of messages) {
      if (m.role !== "tool" || processedArtifactRef.current.has(m.id)) continue;
      const text = m.content;
      if (!text) continue;
      let obj: { artifact_ref?: unknown; artifact?: unknown };
      try {
        obj = JSON.parse(text) as { artifact_ref?: unknown; artifact?: unknown };
      } catch {
        continue; // 非 JSON 的 ToolMessage，忽略
      }
      const ref = obj.artifact_ref ?? obj.artifact;
      if (ref && typeof ref === "object" && "title" in (ref as Record<string, unknown>)) {
        processedArtifactRef.current.add(m.id);
        artifactStore.getState().add(ref as Parameters<ReturnType<typeof artifactStore.getState>["add"]>[0]);
      }
    }
  }, [messages, artifactStore]);

  const isEmpty = messages.length === 0 && !isStreaming;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 pt-4">
          {isEmpty ? (
            <div className="flex flex-1 flex-col items-center justify-center px-4 text-center min-h-64">
              <h1 className="text-2xl font-semibold">今天想了解什么空气质量问题？</h1>
              <p className="text-muted-foreground mt-2 text-sm">
                会话自动持久化到本地 Graph 后端，刷新后历史仍在。
              </p>
            </div>
          ) : (
            messages.map((m) => <MessageItem key={m.id} message={m} />)
          )}

          {isStreaming && (
            <div className="px-2">
              {messages.length > 0 && (messages[messages.length - 1].role === "assistant" || streamingText) ? (
                <StreamingMessage text={streamingText} />
              ) : (
                <ThinkingIndicator />
              )}
            </div>
          )}

          {error && (
            <div className="border-destructive bg-destructive/10 text-destructive rounded-md border px-3 py-2 text-sm">
              {error}
            </div>
          )}
        </div>
      </div>

      <div className="border-t bg-background/95 backdrop-blur px-4 py-3">
        <Composer
          disabled={isStreaming}
          onSend={(text) => void sendMessage(text, chatMode)}
          onStop={() => stopStreaming()}
        />
      </div>
    </div>
  );
}

// ───────────────────────── 消息渲染 ─────────────────────────

function MessageItem({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end px-2">
        <div className="bg-muted text-foreground wrap-break-word max-w-[85%] rounded-xl px-4 py-2">
          {message.content}
        </div>
      </div>
    );
  }
  if (message.role === "tool") {
    return <ToolResultCard message={message} />;
  }
  // assistant
  return (
    <div className="text-foreground px-2">
      {message.toolCalls && message.toolCalls.length > 0 && (
        <div className="mb-2 flex flex-col gap-1.5">
          {message.toolCalls.map((tc, i) => (
            <ToolCallCard key={`${tc.id}-${i}`} name={tc.name} args={tc.args} />
          ))}
        </div>
      )}
      {message.content && <Markdown content={message.content} />}
    </div>
  );
}

/** 流式中的助手消息：增量文本 + 光标 */
function StreamingMessage({ text }: { text: string }) {
  return (
    <div className="text-foreground px-2">
      {text ? (
        <>
          <Markdown content={text} />
          <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-primary align-middle" />
        </>
      ) : (
        <ThinkingIndicator />
      )}
    </div>
  );
}

function ThinkingIndicator() {
  return (
    <div className="text-muted-foreground flex items-center gap-2 px-2 py-1 text-sm">
      <span className="size-2 animate-pulse rounded-full bg-current" />
      正在思考…
    </div>
  );
}

/** 工具调用折叠卡（assistant 消息里的 tool_call） */
function ToolCallCard({ name, args }: { name: string; args: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-border/60 bg-muted/30 rounded-md border text-xs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-muted-foreground flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left"
      >
        <WrenchIcon className="size-3" />
        <span className="font-medium">{name}</span>
        <ChevronDownIcon className={cn("ml-auto size-3 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <pre className="text-muted-foreground border-t border-border/50 overflow-x-auto p-2.5 font-mono">
          {typeof args === "string" ? args : JSON.stringify(args ?? {}, null, 2)}
        </pre>
      )}
    </div>
  );
}

/** 工具结果折叠卡（tool 消息）：默认只显示工具名一行，点击展开完整结果 */
function ToolResultCard({ message }: { message: ChatMessage }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-border/60 bg-muted/30 rounded-md border text-xs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="text-muted-foreground hover:bg-muted/50 flex w-full items-center gap-1.5 rounded-md px-2.5 py-1.5 text-left"
      >
        <WrenchIcon className="size-3 shrink-0" />
        <span className="min-w-0 flex-1 truncate font-medium">{message.name ?? "工具"}</span>
        <ChevronDownIcon
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-180")}
        />
      </button>
      {open && (
        <pre className="text-muted-foreground border-border/50 max-h-64 overflow-auto border-t p-2.5 font-mono whitespace-pre-wrap break-all">
          {message.content}
        </pre>
      )}
    </div>
  );
}

// ───────────────────────── 输入区 ─────────────────────────

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
  const canSend = text.trim().length > 0 && !disabled;
  // 当前登录用户名（session-store，refreshSession 后填充）
  const username = useSessionStore((s) => s.username);
  const logout = useSessionStore((s) => s.logout);

  const submit = useCallback(() => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  }, [text, disabled, onSend]);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
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
      <div className="flex flex-col gap-2 rounded-2xl border border-border/60 bg-muted/30 p-2 focus-within:border-border">
        <div className="flex flex-wrap items-center gap-2 px-1 pt-0.5">
          <GraphSwitcher />
          <ModeSwitcher />
          {/* 登录用户徽标 + 退出登录 */}
          <span className="text-muted-foreground/60 ml-auto hidden items-center gap-2 text-[11px] sm:inline-flex">
            {username ? (
              <>
                <span className="bg-muted text-foreground rounded-full px-2 py-0.5 font-medium">
                  {username}
                </span>
                <button
                  type="button"
                  onClick={() => void logout()}
                  className="hover:text-foreground underline underline-offset-2"
                  title="退出登录"
                >
                  退出
                </button>
              </>
            ) : (
              "登录中…"
            )}
          </span>
        </div>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            const el = e.target;
            el.style.height = "auto";
            el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
          }}
          onKeyDown={onKeyDown}
          placeholder="输入消息…"
          aria-label="消息输入框"
          rows={1}
          disabled={disabled}
          className="max-h-40 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base outline-none disabled:opacity-60"
        />
        <div className="flex items-center justify-end gap-1.5">
          {disabled ? (
            <Button type="button" size="icon" className="size-8 rounded-full" onClick={onStop} aria-label="停止生成">
              <SquareIcon className="size-3.5 fill-current" />
            </Button>
          ) : (
            <Button
              type="submit"
              size="icon"
              className="size-8 rounded-full"
              disabled={!canSend}
              aria-label="发送消息"
            >
              <ArrowUpIcon className="size-4" />
            </Button>
          )}
        </div>
      </div>
    </form>
  );
}
