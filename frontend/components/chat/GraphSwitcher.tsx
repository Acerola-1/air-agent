"use client";

import { useState } from "react";
import { GRAPH_OPTIONS, colorForGraph } from "@/lib/graph-options";
import { useChatStore } from "@/lib/chat-store";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/** Graph 切换器（顶栏/输入区常驻）——原生版：直接驱动 chat-store.switchGraph。
 *  切换 graph 会清空当前会话并加载该 graph 的线程列表（按 user_id 隔离）。 */
export function GraphSwitcher() {
  const graphId = useChatStore((s) => s.graphId);
  const switchGraph = useChatStore((s) => s.switchGraph);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const [open, setOpen] = useState(false);

  const selected = GRAPH_OPTIONS.find((g) => g.id === graphId) || GRAPH_OPTIONS[0];

  const onSelect = (gid: string) => {
    if (gid === graphId) {
      setOpen(false);
      return;
    }
    void switchGraph(gid);
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="gap-2 px-3 max-w-[16rem]"
          disabled={isStreaming}
          title={isStreaming ? "回答中，暂不能切换" : "切换模型能力（Graph）"}
        >
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: colorForGraph(selected.id) }}
            aria-hidden
          />
          <span className="truncate">{selected.label}</span>
          <span className="hidden sm:inline text-[10px] text-muted-foreground">{selected.id}</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[34rem]">
        <DialogHeader>
          <DialogTitle>选择 Agent Graph</DialogTitle>
          <DialogDescription>
            不同 Graph 对应不同的能力与工作流，切换后会进入一个全新的会话。
          </DialogDescription>
        </DialogHeader>
        <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {GRAPH_OPTIONS.map((g) => {
            const active = g.id === graphId;
            return (
              <button
                key={g.id}
                type="button"
                onClick={() => onSelect(g.id)}
                className={cn(
                  "group flex items-start text-left gap-3 rounded-lg border p-3 transition-colors",
                  "focus:outline-none focus:ring-2 focus:ring-ring",
                  active ? "border-primary bg-primary/5" : "hover:bg-accent/50 hover:border-accent-foreground/20",
                )}
              >
                <span
                  className="mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ background: colorForGraph(g.id) }}
                  aria-hidden
                />
                <div className="min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium">{g.label}</p>
                    <code className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] whitespace-nowrap">
                      {g.id}
                    </code>
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs">{g.desc}</p>
                </div>
              </button>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
