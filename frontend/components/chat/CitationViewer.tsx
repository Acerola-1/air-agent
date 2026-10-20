"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useSessionStore } from "@/lib/session-client";
import { useChatStore } from "@/lib/chat-store";

type State =
  | { kind: "loading" }
  | { kind: "text"; content: string; next: number | null; offset: number; total: number }
  | { kind: "error"; message: string; retryable: boolean };

function currentScope() {
  const ctx = useSessionStore.getState().ctx;
  const chat = useChatStore.getState();
  return JSON.stringify([ctx?.user.id, ctx?.session.session_id, ctx?.current_tenant?.id,
    ctx?.context_version, chat.graphId, chat.activeThreadId]);
}

const errors: Record<number, string> = {
  401: "登录状态已失效，请重新登录。",
  403: "当前会话无权读取该来源。",
  404: "找不到该引用或当前会话不可访问，请重新检索。",
  409: "企业或会话上下文已变化，请刷新后重新检索。",
  410: "来源已失效或权限已变化，请重新检索。",
  503: "原文服务暂不可用，请稍后重试。",
  504: "原文读取超时，请重试。",
};

/** 原文与归档采用同一分页约定；每页重新核权，身份变化立即关闭。 */
export function CitationViewer({ citationId, onClose }: {
  citationId: string; onClose: () => void;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const scope = useRef(currentScope());
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const close = useRef(onClose);
  const returnFocus = useRef<HTMLElement | null>(
    typeof document !== "undefined" && document.activeElement instanceof HTMLElement
      ? document.activeElement : null
  );
  close.current = onClose;

  useEffect(() => {
    const changed = () => {
      if (scope.current === currentScope()) return;
      generation.current += 1;
      abort.current?.abort();
      close.current();
    };
    const session = useSessionStore.subscribe(changed);
    const chat = useChatStore.subscribe(changed);
    return () => { session(); chat(); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    abort.current = controller;
    const epoch = ++generation.current;
    const current = () => !controller.signal.aborted &&
      epoch === generation.current && scope.current === currentScope();
    setState({ kind: "loading" });
    void (async () => {
      try {
        const response = await fetch(
          `/api/knowledge/citations/${citationId}/text?offset=${offset}&limit=8000`,
          { cache: "no-store", signal: AbortSignal.any([controller.signal, AbortSignal.timeout(25_000)]) }
        );
        if (!current()) { void response.body?.cancel().catch(() => {}); return; }
        if (!response.ok) {
          void response.body?.cancel().catch(() => {});
          setState({ kind: "error", message: errors[response.status] ??
            "原文读取异常，请重试。", retryable: response.status >= 500 });
          return;
        }
        const content = await response.text();
        if (!current()) return;
        const next = response.headers.get("x-content-next-offset");
        setState({ kind: "text", content, offset,
          total: Number(response.headers.get("x-content-total-chars") ?? content.length),
          next: next !== null && /^\d+$/.test(next) && Number(next) > offset
            ? Number(next) : null });
      } catch {
        if (current()) setState({ kind: "error",
          message: "无法连接原文服务，请检查网络后重试。", retryable: true });
      }
    })();
    return () => { controller.abort(); };
  }, [citationId, offset, revision]);

  const changePage = useCallback((value: number) => {
    abort.current?.abort();
    generation.current += 1;
    setOffset(value);
  }, []);

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="flex max-h-[85dvh] flex-col gap-3 sm:max-w-3xl" aria-describedby={undefined}
        onCloseAutoFocus={(event) => {
          if (scope.current !== currentScope()) return;
          const origin = document.querySelector<HTMLButtonElement>(
            `button[data-knowledge-citation-id="${citationId}"]`
          ) ?? (returnFocus.current?.tagName !== "BODY" ? returnFocus.current : null);
          if (origin?.isConnected) { event.preventDefault(); origin.focus(); }
        }}>
        <div className="flex items-center justify-between gap-3 pr-6">
          <DialogTitle>知识库原文</DialogTitle>
          <a className="shrink-0 text-xs underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
            href={`/api/knowledge/citations/${citationId}/pdf`} target="_blank" rel="noopener noreferrer">打开 PDF</a>
        </div>
        <div className="min-h-24 overflow-y-auto whitespace-pre-wrap break-words text-sm leading-7" aria-live="polite">
          {state.kind === "loading" && <p className="text-muted-foreground">正在加载原文…</p>}
          {state.kind === "error" && <div role="alert">
            <p className="text-destructive">{state.message}</p>
            {state.retryable && <Button type="button" variant="outline" size="sm" className="mt-3"
              onClick={() => setRevision((value) => value + 1)}>重试</Button>}
          </div>}
          {state.kind === "text" && <p>{state.content || "（本页无更多内容）"}</p>}
        </div>
        {state.kind === "text" && <div className="flex flex-wrap items-center gap-2 border-t pt-3">
          <span className="mr-auto text-xs text-muted-foreground">
            第 {state.offset + (state.content.length ? 1 : 0)}—{state.offset + state.content.length} 字符，共 {state.total} 字符
          </span>
          <Button type="button" variant="outline" size="sm" disabled={state.offset === 0}
            onClick={() => changePage(Math.max(0, state.offset - 8000))}>上一段</Button>
          <Button type="button" variant="outline" size="sm" disabled={state.next === null}
            onClick={() => { if (state.next !== null) changePage(state.next); }}>下一段</Button>
        </div>}
      </DialogContent>
    </Dialog>
  );
}
