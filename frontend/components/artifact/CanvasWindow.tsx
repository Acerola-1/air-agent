"use client";

import {
  XIcon,
  ChevronRightIcon,
  Maximize2Icon,
  ExternalLinkIcon,
  DownloadIcon,
} from "lucide-react";
import { useEffect, useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import { ContentRenderer } from "@/components/artifact/ContentRenderer";
import { cn } from "@/lib/utils";
import { useArtifactStore } from "@/components/artifact/ArtifactProvider";
import type { Artifact } from "@/types/artifact";

/** 画布窗口：右侧抽屉式 Tab 容器.
 *
 *  - 显示 canvas_window 模式下的所有 Artifact
 *  - 顶部 Tab 条：可切换 / 关闭
 *  - 右上角操作栏：关闭窗口、在新标签页打开（仅 html）、下载 Markdown/SVG
 *  - 在没有 Artifact 时显示空态指引
 */
function CanvasTab({
  active,
  artifact,
  onClick,
  onClose,
}: {
  active: boolean;
  artifact: Artifact;
  onClick: () => void;
  onClose: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group flex items-center gap-2 rounded-t-md border border-b-0 px-3 py-1.5 text-xs font-medium transition-colors",
        active
          ? "bg-background text-foreground shadow-[0_1px_0_0_rgba(255,255,255,1)] border-border"
          : "bg-muted/50 text-muted-foreground hover:bg-muted border-transparent",
      )}
    >
      <span className="max-w-[180px] truncate">{artifact.title}</span>
      <span
        role="button"
        tabIndex={0}
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.stopPropagation();
            onClose();
          }
        }}
        className="rounded p-0.5 text-muted-foreground opacity-0 transition hover:bg-foreground/10 hover:text-foreground group-hover:opacity-100"
        aria-label={`关闭 ${artifact.title}`}
      >
        <XIcon className="h-3.5 w-3.5" />
      </span>
    </button>
  );
}

function EmptyCanvasHint({ onClose }: { onClose: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center text-muted-foreground">
      <div className="rounded-full border border-dashed p-4 opacity-60">
        <Maximize2Icon className="h-6 w-6" />
      </div>
      <div>
        <p className="text-sm font-medium text-foreground/80">当前没有画布内容</p>
        <p className="mt-1 text-xs">
          Agent 在生成报告、图表、长文分析时会自动在这里打开画布.
        </p>
        <p className="mt-2 text-xs text-muted-foreground/70">
          快捷键 <kbd className="rounded border bg-muted px-1.5 py-0.5 text-[10px]">⌘ ⇧ K</kbd> 随时切换画布.
        </p>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="mt-2 rounded-md border bg-background px-3 py-1.5 text-xs hover:bg-muted"
      >
        关闭窗口
      </button>
    </div>
  );
}

export function CanvasWindow() {
  // 拿原始 zustand store 实例，便于调用 .getState().xxx() 进行一次性 action
  const store = useArtifactStore();

  // selector 订阅数据变化，自动重渲染
  // artifacts 用 useShallow 包裹 selector：派生数组的引用变化（即便 store 层做了缓存）时，按内容比较而非引用
  const artifacts = useArtifactStore(
    useShallow((s) => s.listByOpenIn("canvas_window")),
  );
  const activeId = useArtifactStore((s) => s.canvasWindowActiveId);
  const open = useArtifactStore((s) => s.canvasWindowOpen);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Cmd/Ctrl + Shift + K → 切换画布窗口
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        store.getState().toggleCanvasWindow();
      }
      // Esc → 关闭画布
      if (e.key === "Escape" && store.getState().canvasWindowOpen) {
        store.getState().setCanvasWindowOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [store]);

  const active = useMemo<Artifact | null>(
    () => (activeId ? store.getState().getById(activeId) : null),
    [activeId, store],
  );

  /** 在新标签页打开 HTML：把 HTML 转成 blob URL. */
  const openInNewTab = (art: Artifact) => {
    if (art.content_type !== "html") return;
    const blob = new Blob([art.content], { type: "text/html;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    window.open(href, "_blank", "noopener,noreferrer");
    setTimeout(() => URL.revokeObjectURL(href), 30_000);
  };

  /** 下载：markdown / svg / html / text 下载原始内容文件. */
  const downloadArtifact = (art: Artifact) => {
    const extMap: Record<string, string> = {
      html: "html",
      markdown: "md",
      md: "md",
      svg: "svg",
      text: "txt",
    };
    const ext = extMap[art.content_type];
    if (!ext) return;
    const mime = ext === "html"
      ? "text/html;charset=utf-8"
      : ext === "svg"
        ? "image/svg+xml;charset=utf-8"
        : "text/plain;charset=utf-8";
    const blob = new Blob([art.content], { type: mime });
    const href = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = href;
    a.download = `${art.title.replace(/[^\w\u4e00-\u9fa5.-]+/g, "_") || "artifact"}.${ext}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(href), 30_000);
  };

  return (
    <>
      {/* 画布关闭时显示左侧拉手 */}
      {!open && (
        <button
          type="button"
          onClick={() => store.getState().setCanvasWindowOpen(true)}
          className="fixed right-0 top-1/2 z-40 -translate-y-1/2 rounded-l-md border border-r-0 bg-background/95 px-1.5 py-4 shadow-md backdrop-blur transition hover:bg-muted/70"
          aria-label="打开画布窗口"
          title="打开画布窗口 (⌘⇧K)"
        >
          <ChevronRightIcon className="h-4 w-4 text-muted-foreground" />
        </button>
      )}

      {/* 画布主面板（右侧抽屉式） */}
      <aside
        className={cn(
          "fixed right-0 top-0 z-40 h-dvh w-0 overflow-hidden shadow-2xl transition-[width] duration-300 ease-out bg-background",
          open ? "w-[min(92vw,60rem)] border-l" : "w-0",
        )}
        aria-hidden={!open}
      >
        <div className="flex h-dvh w-[min(92vw,60rem)] max-w-full flex-col">
          {/* 顶栏：Tab 条 + 操作按钮 */}
          <header className="flex shrink-0 items-center gap-2 border-b bg-background/95 px-3 py-2 backdrop-blur">
            <div className="flex min-w-0 flex-1 items-end gap-1 overflow-x-auto pb-0.5">
              {artifacts.map((a) => (
                <CanvasTab
                  key={a.id}
                  artifact={a}
                  active={a.id === activeId}
                  onClick={() => store.getState().activateCanvasTab(a.id)}
                  onClose={() => store.getState().closeCanvasTab(a.id)}
                />
              ))}
            </div>
            <div className="flex shrink-0 items-center gap-1 pl-2">
              {active && (
                <>
                  {active.content_type === "html" && (
                    <button
                      type="button"
                      onClick={() => openInNewTab(active)}
                      className="rounded p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                      title="在新标签页打开"
                      aria-label="在新标签页打开"
                    >
                      <ExternalLinkIcon className="h-4 w-4" />
                    </button>
                  )}
                  {["html", "markdown", "md", "svg", "text"].includes(active.content_type) && (
                    <button
                      type="button"
                      onClick={() => downloadArtifact(active)}
                      className="rounded p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                      title="下载原始内容"
                      aria-label="下载"
                    >
                      <DownloadIcon className="h-4 w-4" />
                    </button>
                  )}
                </>
              )}
              <button
                type="button"
                onClick={() => store.getState().setCanvasWindowOpen(false)}
                className="rounded p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                title="关闭画布 (Esc)"
                aria-label="关闭画布"
              >
                <XIcon className="h-4 w-4" />
              </button>
            </div>
          </header>

          {/* 内容区 */}
          <main className="min-h-0 flex-1 overflow-hidden">
            {active ? (
              <ContentRenderer
                content_type={active.content_type}
                content={active.content}
              />
            ) : (
              <EmptyCanvasHint
                onClose={() => store.getState().setCanvasWindowOpen(false)}
              />
            )}
          </main>
        </div>
      </aside>
    </>
  );
}
