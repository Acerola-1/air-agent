"use client";

import { memo } from "react";
import { useShallow } from "zustand/react/shallow";
import { ContentRenderer } from "@/components/artifact/ContentRenderer";
import { useArtifactStore } from "@/components/artifact/ArtifactProvider";
import type { Artifact } from "@/types/artifact";
import { cn } from "@/lib/utils";
import { XIcon, Maximize2Icon } from "lucide-react";

/** inline_below 模式卡片：单条内联 Artifact 的渲染 + 操作.
 *
 *  - 右侧两个操作：升级到画布窗口、关闭卡片
 *  - 卡片高度限制在 60vh，内部通过 ContentRenderer 自行滚动
 */
function InlineCard({ artifact }: { artifact: Artifact }) {
  if (artifact.closed) return null;
  const store = useArtifactStore();

  /** 升级到画布窗口：先删除 inline，再 add canvas_window 模式的同内容条目. */
  const promote = () => {
    const state = store.getState();
    state.remove(artifact.id);
    state.add({
      title: artifact.title,
      content_type: artifact.content_type,
      content: artifact.content,
      open_in: "canvas_window",
      auto_detected: artifact.auto_detected,
    });
  };

  return (
    <div
      className={cn(
        "group relative mt-3 overflow-hidden rounded-xl border bg-card shadow-sm",
      )}
    >
      <header className="flex items-center justify-between gap-2 border-b bg-muted/40 px-3 py-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-card-foreground">
            {artifact.title}
          </div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {artifact.auto_detected ? "自动识别代码块" : "Agent 生成"} ·{" "}
            <span className="uppercase">{artifact.content_type}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={promote}
            className="rounded p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
            title="在画布窗口中打开"
            aria-label="在画布窗口中打开"
          >
            <Maximize2Icon className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => store.getState().closeArtifact(artifact.id)}
            className="rounded p-1.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
            title="折叠"
            aria-label="折叠"
          >
            <XIcon className="h-4 w-4" />
          </button>
        </div>
      </header>
      <div className="h-[min(60vh,420px)] w-full">
        <ContentRenderer
          content_type={artifact.content_type}
          content={artifact.content}
        />
      </div>
    </div>
  );
}

function InlineContainerImpl() {
  // 用 listByOpenIn selector 订阅 inline 列表变化；排序 helper 内部按 createdAt 升序
  // useShallow(fn) 返回一个"浅比较 selector"： zustand 用 Object.is 比较首尾引用，
  // 内部若引用不等则对数组项逐一比较（避免 SSR 下的 infinite loop）
  const items = useArtifactStore(
    useShallow((s) => s.listByOpenIn("inline_below")),
  );
  if (items.length === 0) return null;
  return (
    <div className="mx-auto w-full max-w-(--thread-max-width) px-4 pb-4">
      {items.map((a) => (
        <InlineCard key={a.id} artifact={a} />
      ))}
    </div>
  );
}

export const InlineContainer = memo(InlineContainerImpl);
