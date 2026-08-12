"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { memo } from "react";
import { cn } from "@/lib/utils";

/** 轻量 Markdown 渲染（react-markdown + remark-gfm）。
 *  样式沿用原 assistant-ui aui-md 的 Tailwind 类，去掉 aui- 前缀。 */
export const Markdown = memo(function Markdown({ content }: { content: string }) {
  return (
    <div className="aui-md text-foreground leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (p) => <h1 className={cn("mt-5 mb-2 scroll-m-20 text-xl font-semibold first:mt-0 last:mb-0", p.className)} {...p} />,
          h2: (p) => <h2 className={cn("mt-5 mb-2 scroll-m-20 text-lg font-semibold first:mt-0 last:mb-0", p.className)} {...p} />,
          h3: (p) => <h3 className={cn("mt-4 mb-1.5 scroll-m-20 text-base font-semibold first:mt-0 last:mb-0", p.className)} {...p} />,
          h4: (p) => <h4 className={cn("mt-3.5 mb-1 scroll-m-20 text-base font-medium first:mt-0 last:mb-0", p.className)} {...p} />,
          p: (p) => <p className={cn("my-3 leading-relaxed first:mt-0 last:mb-0", p.className)} {...p} />,
          a: (p) => (
            <a className={cn("text-primary hover:text-primary/80 underline underline-offset-2", p.className)} {...p} />
          ),
          blockquote: (p) => (
            <blockquote className={cn("border-muted-foreground/30 text-muted-foreground my-3 border-s-2 ps-4", p.className)} {...p} />
          ),
          ul: (p) => <ul className={cn("marker:text-muted-foreground my-3 ms-5 list-disc [&>li]:mt-1", p.className)} {...p} />,
          ol: (p) => <ol className={cn("marker:text-muted-foreground my-3 ms-5 list-decimal [&>li]:mt-1", p.className)} {...p} />,
          li: (p) => <li className={cn("leading-relaxed", p.className)} {...p} />,
          hr: (p) => <hr className={cn("border-muted-foreground/20 my-3", p.className)} {...p} />,
          table: (p) => (
            <table className={cn("my-3 w-full border-separate border-spacing-0 overflow-y-auto", p.className)} {...p} />
          ),
          th: (p) => (
            <th
              className={cn(
                "bg-muted px-3 py-1.5 text-start font-medium first:rounded-ss-lg last:rounded-se-lg",
                p.className,
              )}
              {...p}
            />
          ),
          td: (p) => (
            <td
              className={cn(
                "border-muted-foreground/20 border-s border-b px-3 py-1.5 text-start last:border-e",
                p.className,
              )}
              {...p}
            />
          ),
          tr: (p) => <tr className={cn("m-0 border-b p-0 first:border-t", p.className)} {...p} />,
          strong: (p) => <strong className={cn("font-semibold", p.className)} {...p} />,
          pre: (p) => (
            <pre
              className={cn(
                "border-border/50 bg-muted/30 overflow-x-auto rounded-b-xl border border-t-0 p-3.5 text-[13px] leading-relaxed",
                p.className,
              )}
              {...p}
            />
          ),
          code: (p) => (
            <code
              className={cn(
                "bg-muted rounded-md px-1.5 py-0.5 font-mono text-[0.85em]",
                p.className,
              )}
              {...p}
            />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});
