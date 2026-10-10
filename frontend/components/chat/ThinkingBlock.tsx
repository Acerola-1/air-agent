"use client";

import { useState } from "react";
import { ChevronDownIcon, SparklesIcon } from "lucide-react";
import { Markdown } from "@/components/chat/Markdown";
import { cn } from "@/lib/utils";

interface ThinkingBlockProps {
  content: string;
  reasoningTokens?: number;
  isStreaming?: boolean;
}

export function ThinkingBlock({
  content,
  reasoningTokens,
  isStreaming = false,
}: ThinkingBlockProps) {
  // 思考中时默认展开方便用户观察，完成后默认折叠保持界面清爽
  const [isOpen, setIsOpen] = useState(false);

  if (!content && !isStreaming) return null;

  return (
    <div className="my-2 overflow-hidden rounded-xl border border-indigo-500/20 bg-indigo-50/40 dark:border-indigo-900/40 dark:bg-indigo-950/20 text-xs transition-all">
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-muted-foreground hover:text-foreground transition-colors"
        aria-expanded={isOpen}
      >
        <span
          className={cn(
            "flex size-5 shrink-0 items-center justify-center rounded-md bg-indigo-500/10 text-indigo-500 dark:bg-indigo-400/10 dark:text-indigo-400",
            isStreaming && "animate-pulse"
          )}
        >
          <SparklesIcon className="size-3" />
        </span>

        <span className="font-medium text-foreground/80 flex items-center gap-1.5">
          {isStreaming ? (
            <span className="flex items-center gap-1 text-indigo-600 dark:text-indigo-400">
              <span className="inline-block size-1.5 animate-ping rounded-full bg-indigo-500" />
              正在深度思考…
            </span>
          ) : (
            <>
              深度思考完成
              {reasoningTokens ? (
                <span className="text-[10px] text-muted-foreground/80 font-mono">
                  ({reasoningTokens} tokens)
                </span>
              ) : null}
            </>
          )}
        </span>

        <span className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground/70">
          <span>{isOpen ? "收起" : "展开"}</span>
          <ChevronDownIcon
            className={cn("size-3.5 transition-transform duration-200", isOpen && "rotate-180")}
          />
        </span>
      </button>

      {isOpen && (
        <div className="border-t border-indigo-500/10 px-3.5 py-3 text-muted-foreground dark:text-muted-foreground/90 bg-background/50 leading-relaxed font-sans max-h-96 overflow-y-auto">
          {content ? (
            <Markdown content={content} />
          ) : (
            <div className="flex items-center gap-2 py-2 text-muted-foreground italic">
              <span className="size-1.5 animate-pulse rounded-full bg-indigo-400" />
              正在组织逻辑推理链…
            </div>
          )}
        </div>
      )}
    </div>
  );
}
