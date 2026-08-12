"use client";

import { useChatMode, useSetChatMode, type ChatMode } from "@/lib/chat-mode-store";
import { cn } from "@/lib/utils";

/** 快速 / 专家模式切换器（顶栏常驻）.
 *
 *  - 选项持久化到 localStorage(chat-mode-store), 刷新与切图后保持.
 *  - 选择结果通过 runConfig.custom.chat_mode 在每次发送时传给后端.
 */
const MODES: readonly { id: ChatMode; label: string; desc: string }[] = [
  { id: "fast", label: "快速", desc: "快速模式：简洁高效回答" },
  { id: "expert", label: "专家", desc: "专家模式：深度专业分析" },
];

export function ModeSwitcher() {
  const chatMode = useChatMode();
  const setChatMode = useSetChatMode();

  return (
    <div
      className="flex items-center gap-0.5 rounded-full border bg-background p-0.5"
      title="回答模式（快速 / 专家）"
    >
      {MODES.map((m) => {
        const active = chatMode === m.id;
        return (
          <button
            key={m.id}
            type="button"
            onClick={() => setChatMode(m.id)}
            title={m.desc}
            aria-pressed={active}
            className={cn(
              "rounded-full px-3 py-1 text-xs font-medium transition-colors",
              "focus:outline-none focus:ring-2 focus:ring-ring",
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent",
            )}
          >
            {m.label}
          </button>
        );
      })}
    </div>
  );
}
