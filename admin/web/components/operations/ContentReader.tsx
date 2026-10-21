"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CONTENT_STATUS_LABELS } from "@/lib/operations/query";
import type { ContentPage } from "@/lib/operations/types";
import { useScopeKey } from "@/lib/operations/hooks";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  loadPage: (offset: number, signal: AbortSignal) => Promise<ContentPage>;
  onSelectRef?: (ref: string) => void;
};

/**
 * 受控正文阅读器：按需分页（≤8000 字符/页），纯文本安全呈现。
 * 不执行 HTML/Markdown，不加载远程图片；关闭/切换立即取消旧请求。
 */
export function ContentReader({
  open,
  onOpenChange,
  title,
  description,
  loadPage,
  onSelectRef,
}: Props) {
  const [offset, setOffset] = useState(0);
  const scopeKey = useScopeKey();
  const [storedPage, setPage] = useState<ContentPage | null>(null);
  const [pageOwner, setPageOwner] = useState<{ scope: string; load: Props["loadPage"] } | null>(null);
  const page = pageOwner?.scope === scopeKey && pageOwner.load === loadPage ? storedPage : null;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const epoch = useRef(0);
  const trigger = useRef<HTMLElement | null>(null);

  // 关闭后把焦点还给触发按钮（ContentReader 由外部按钮打开，不使用 DialogTrigger）。
  useEffect(() => {
    if (open) {
      trigger.current = document.activeElement as HTMLElement | null;
      return;
    }
    const element = trigger.current;
    trigger.current = null;
    if (element) requestAnimationFrame(() => element.focus());
  }, [open]);

  useEffect(() => {
    const current = ++epoch.current;
    if (!open) {
      setOffset(0);
      setPage(null);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setPage(null);
    loadPage(offset, controller.signal)
      .then((value) => {
        if (current !== epoch.current || controller.signal.aborted) return;
        setPage(value);
        setPageOwner({ scope: scopeKey, load: loadPage });
      })
      .catch((err: unknown) => {
        if (current !== epoch.current || controller.signal.aborted) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "正文读取失败");
      })
      .finally(() => {
        if (current === epoch.current && !controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, offset, loadPage, scopeKey]);

  const refs = page?.items ?? [];
  const hasText = page?.text !== null && page?.text !== undefined;
  const statusLabel = page
    ? CONTENT_STATUS_LABELS[page.status] ?? page.status
    : loading
      ? "读取中"
      : "—";

  const selectRef = useCallback(
    (ref: string) => {
      setOffset(0);
      onSelectRef?.(ref);
    },
    [onSelectRef]
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <div className="space-y-3">
          <div className="text-muted-foreground flex flex-wrap items-center justify-between gap-2 text-xs">
            <span>
              状态：{statusLabel}
              {page?.content_sha256 ? ` · SHA256 ${page.content_sha256.slice(0, 12)}…` : ""}
            </span>
            {page?.total_chars ? (
              <span>
                {page.offset + 1}–{page.offset + (page.text?.length ?? 0)} /{" "}
                {page.total_chars} 字符
              </span>
            ) : null}
          </div>
          {page?.message && (
            <div className="text-muted-foreground rounded border border-dashed p-2 text-xs">
              {page.message}
            </div>
          )}
          {error && (
            <div className="text-destructive text-sm" role="alert">
              {error}
            </div>
          )}
          {refs.length > 0 && !hasText && (
            <ul className="divide-y rounded border">
              {refs.map((item) => (
                <li key={item.ref} className="flex items-center justify-between gap-2 p-2">
                  <span className="text-muted-foreground text-xs">
                    {item.created_at ?? "—"} · {item.actor_user_id}
                  </span>
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={item.deleted}
                    onClick={() => selectRef(item.ref)}
                  >
                    {item.deleted ? "已失效" : "读取说明"}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {hasText && (
            <pre className="bg-muted/40 max-h-[55vh] overflow-auto rounded border p-3 text-sm whitespace-pre-wrap break-words">
              {page?.text}
            </pre>
          )}
          {!hasText && refs.length === 0 && !loading && !error && (
            <div className="text-muted-foreground rounded border border-dashed p-6 text-center text-sm">
              {page?.status === "not_collected"
                ? "该正文未采集；历史回答不从事后 Trace 或 checkpoint 回填。"
                : "当前没有可读取的正文。"}
            </div>
          )}
          <p className="text-muted-foreground text-[11px]">
            正文按纯文本安全呈现，不执行 HTML/Markdown，不加载远程图片；所有响应 no-store。
          </p>
          <div className="flex justify-between">
            <Button
              variant="outline"
              size="sm"
              disabled={loading || offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 8000))}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={loading || page?.next_offset === null || page?.next_offset === undefined}
              onClick={() => setOffset(page?.next_offset ?? offset)}
            >
              下一页
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
