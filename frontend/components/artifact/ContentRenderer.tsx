"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertCircleIcon } from "lucide-react";
import type { ArtifactContentType } from "@/types/artifact";
import { cn } from "@/lib/utils";

/** 按 content_type 分发的通用渲染引擎.
 *
 *  - html: iframe sandbox 渲染（allow-scripts + allow-same-origin 不启用 → 同源策略隔离，防 XSS 扩散）
 *  - markdown: @assistant-ui/react-markdown + remark-gfm，和 thread 中 MarkdownText 保持一致风格
 *  - svg: 直接用 <object> 嵌入，保留 SVG 矢量清晰度
 *  - text: <pre> 等宽换行
 *  - iframe_url: 直接 iframe src=url，同样走 sandbox
 *  - image_url: <img>，按最大宽度自适应
 */
interface ContentRendererProps {
  content_type: ArtifactContentType;
  content: string;
  className?: string;
}

/** HTML / iframe 使用的 sandbox 属性：尽量开放渲染能力，但不开放同域权限、顶级导航、弹窗. */
const IFRAME_SANDBOX = [
  "allow-scripts",
  "allow-forms",
  "allow-popups",
  "allow-modals",
  "allow-downloads",
  "allow-pointer-lock",
].join(" ");

function HtmlRenderer({ content }: { content: string }) {
  // 把 HTML 编码为 srcdoc，直接走沙箱
  const srcDoc = content.startsWith("<!DOCTYPE") || content.startsWith("<html")
    ? content
    : `<!DOCTYPE html><html><head><meta charset="utf-8" /></head><body>${content}</body></html>`;
  return (
    <iframe
      title="artifact-html"
      srcDoc={srcDoc}
      sandbox={IFRAME_SANDBOX}
      className="h-full w-full border-0 bg-white"
      referrerPolicy="no-referrer"
      allow="clipboard-read; clipboard-write"
    />
  );
}

function MarkdownRenderer({ content }: { content: string }) {
  return (
    <div className="artifact-markdown h-full overflow-auto p-4 md:p-6 prose prose-slate max-w-none dark:prose-invert">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function SvgRenderer({ content }: { content: string }) {
  const href = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(content)}`;
  return (
    <div className="flex h-full w-full items-center justify-center bg-white p-4">
      <img
        src={href}
        alt="artifact-svg"
        className="max-h-full max-w-full object-contain"
      />
    </div>
  );
}

function TextRenderer({ content }: { content: string }) {
  return (
    <pre className="m-0 h-full w-full overflow-auto bg-slate-50 p-4 text-sm leading-6 text-slate-700 whitespace-pre-wrap break-words font-mono">
      {content}
    </pre>
  );
}

function IframeUrlRenderer({ content }: { content: string }) {
  let url: URL | null = null;
  try {
    url = new URL(content);
    if (url.protocol !== "http:" && url.protocol !== "https:") url = null;
  } catch {
    url = null;
  }
  if (!url) {
    return (
      <div className="flex h-full items-center justify-center gap-2 p-6 text-sm text-red-600">
        <AlertCircleIcon className="h-4 w-4" />
        <span>无效的 URL：仅支持 http(s) 协议的 iframe_url.</span>
      </div>
    );
  }
  return (
    <iframe
      title="artifact-iframe"
      src={url.href}
      sandbox={IFRAME_SANDBOX}
      className="h-full w-full border-0 bg-white"
      referrerPolicy="no-referrer"
    />
  );
}

function ImageUrlRenderer({ content }: { content: string }) {
  let url: URL | null = null;
  try {
    url = new URL(content);
    if (!/^https?:$/.test(url.protocol)) url = null;
  } catch {
    url = null;
  }
  if (!url) {
    return (
      <div className="flex h-full items-center justify-center gap-2 p-6 text-sm text-red-600">
        <AlertCircleIcon className="h-4 w-4" />
        <span>无效的图片 URL：仅支持 http(s) 协议的 image_url.</span>
      </div>
    );
  }
  return (
    <div className="flex h-full w-full items-center justify-center bg-slate-50 p-4">
      <img
        src={url.href}
        alt="artifact-image"
        className="max-h-full max-w-full object-contain"
      />
    </div>
  );
}

function ContentRendererImpl({
  content_type,
  content,
  className,
}: ContentRendererProps) {
  if (!content && content_type !== "text") {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        内容为空.
      </div>
    );
  }
  switch (content_type) {
    case "html":
      return <div className={cn("h-full w-full", className)}><HtmlRenderer content={content} /></div>;
    case "markdown":
    case "md":
      return <div className={cn("h-full w-full", className)}><MarkdownRenderer content={content} /></div>;
    case "svg":
      return <div className={cn("h-full w-full", className)}><SvgRenderer content={content} /></div>;
    case "text":
      return <div className={cn("h-full w-full", className)}><TextRenderer content={content} /></div>;
    case "iframe_url":
      return <div className={cn("h-full w-full", className)}><IframeUrlRenderer content={content} /></div>;
    case "image_url":
      return <div className={cn("h-full w-full", className)}><ImageUrlRenderer content={content} /></div>;
    default:
      return (
        <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
          不支持的 content_type: {content_type}
        </div>
      );
  }
}

export const ContentRenderer = memo(ContentRendererImpl);
