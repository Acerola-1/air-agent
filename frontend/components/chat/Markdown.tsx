"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { memo, useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/** 带有 Mac 风格顶栏和复制按钮的现代代码块 */
function CodeBlock({
  children,
  className,
  ...props
}: React.ComponentProps<"code">) {
  const [copied, setCopied] = useState(false);

  // 判断是否为行内代码
  const isInline = !className && typeof children === "string" && !children.includes("\n");

  if (isInline) {
    return (
      <code
        className={cn(
          "rounded-md bg-muted px-1.5 py-0.5 font-mono text-[0.85em] text-foreground font-medium",
          className
        )}
        {...props}
      >
        {children}
      </code>
    );
  }

  // 提取语言
  const match = /language-(\w+)/.exec(className || "");
  const language = match ? match[1] : "text";
  const codeContent = String(children).replace(/\n$/, "");

  const handleCopy = () => {
    void navigator.clipboard.writeText(codeContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="my-4 overflow-hidden rounded-xl border border-border/70 bg-muted/40 shadow-xs">
      {/* 顶部标题栏 */}
      <div className="flex items-center justify-between border-b border-border/50 bg-muted/60 px-3.5 py-1.5 text-xs text-muted-foreground select-none">
        <div className="flex items-center gap-2">
          <div className="flex gap-1.5">
            <span className="size-2 rounded-full bg-red-400/80" />
            <span className="size-2 rounded-full bg-yellow-400/80" />
            <span className="size-2 rounded-full bg-green-400/80" />
          </div>
          <span className="font-mono text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
            {language}
          </span>
        </div>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-background/80 hover:text-foreground"
          aria-label="复制代码"
        >
          {copied ? (
            <>
              <CheckIcon className="size-3 text-emerald-500" />
              <span className="text-emerald-500 font-sans">已复制</span>
            </>
          ) : (
            <>
              <CopyIcon className="size-3" />
              <span className="font-sans">复制</span>
            </>
          )}
        </button>
      </div>

      {/* 代码内容 */}
      <pre className="overflow-x-auto p-3.5 font-mono text-[13px] leading-relaxed text-foreground/90">
        <code className={className} {...props}>
          {children}
        </code>
      </pre>
    </div>
  );
}

/** 现代 Markdown 渲染器（增强表格、引用、链接与代码块样式） */
export const Markdown = memo(function Markdown({ content }: { content: string }) {
  return (
    <div className="text-foreground leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (p) => (
            <h1
              className={cn(
                "mt-6 mb-3 scroll-m-20 text-xl font-bold tracking-tight text-foreground first:mt-0 last:mb-0",
                p.className
              )}
              {...p}
            />
          ),
          h2: (p) => (
            <h2
              className={cn(
                "mt-5 mb-2.5 scroll-m-20 text-lg font-semibold tracking-tight text-foreground first:mt-0 last:mb-0",
                p.className
              )}
              {...p}
            />
          ),
          h3: (p) => (
            <h3
              className={cn(
                "mt-4 mb-2 scroll-m-20 text-base font-semibold text-foreground first:mt-0 last:mb-0",
                p.className
              )}
              {...p}
            />
          ),
          h4: (p) => (
            <h4
              className={cn(
                "mt-3.5 mb-1.5 scroll-m-20 text-sm font-semibold text-foreground first:mt-0 last:mb-0",
                p.className
              )}
              {...p}
            />
          ),
          p: (p) => <p className={cn("my-2.5 leading-7 text-foreground/90 first:mt-0 last:mb-0", p.className)} {...p} />,
          a: (p) => (
            <a
              className={cn(
                "text-primary font-medium underline underline-offset-4 decoration-primary/40 transition-colors hover:decoration-primary",
                p.className
              )}
              target="_blank"
              rel="noopener noreferrer"
              {...p}
            />
          ),
          blockquote: (p) => (
            <blockquote
              className={cn(
                "my-3 border-s-2 border-primary/40 bg-muted/20 py-1 ps-4 pe-2 italic text-muted-foreground rounded-r-md",
                p.className
              )}
              {...p}
            />
          ),
          ul: (p) => <ul className={cn("my-2.5 ms-6 list-disc space-y-1 text-foreground/90", p.className)} {...p} />,
          ol: (p) => <ol className={cn("my-2.5 ms-6 list-decimal space-y-1 text-foreground/90", p.className)} {...p} />,
          li: (p) => <li className={cn("leading-relaxed", p.className)} {...p} />,
          hr: (p) => <hr className={cn("my-4 border-border/60", p.className)} {...p} />,
          table: (p) => (
            <div className="my-4 w-full overflow-x-auto rounded-xl border border-border/70 shadow-2xs">
              <table className={cn("w-full border-collapse text-xs", p.className)} {...p} />
            </div>
          ),
          thead: (p) => <thead className={cn("bg-muted/70 text-foreground font-semibold", p.className)} {...p} />,
          th: (p) => (
            <th
              className={cn(
                "border-b border-border/70 px-3.5 py-2 text-start font-semibold tracking-wider",
                p.className
              )}
              {...p}
            />
          ),
          td: (p) => (
            <td
              className={cn(
                "border-b border-border/40 px-3.5 py-2 text-start text-foreground/90 last:border-b-0",
                p.className
              )}
              {...p}
            />
          ),
          strong: (p) => <strong className={cn("font-semibold text-foreground", p.className)} {...p} />,
          pre: ({ children }) => <>{children}</>,
          code: CodeBlock,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
