"use client";

import { useEffect, useState } from "react";
import { GRAPH_OPTIONS, type GraphId } from "@/app/MyRuntimeProvider";
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

/** Graph 切换器（顶栏右上角）
 *
 *  - 弹窗 + 卡片式 Radio 选择（避免引入缺失的 @radix-ui/dropdown-menu）
 *  - 与 MyRuntimeProvider 的通信通过 window.__AIR_AGENT_SWITCH_GRAPH
 *    （provider mount 后注入，切 graph 会重建整个 Runtime，合理隔离）
 */
export function GraphSwitcher() {
  const defaultGraph = GRAPH_OPTIONS[0].id;
  const [current, setCurrent] = useState<GraphId>(defaultGraph);
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const read = () => {
      const w = window as unknown as {
        __AIR_AGENT_SWITCH_GRAPH?: (id: GraphId) => void;
        __AIR_AGENT_CURRENT_GRAPH?: GraphId;
      };
      if (w.__AIR_AGENT_CURRENT_GRAPH) {
        setCurrent(w.__AIR_AGENT_CURRENT_GRAPH);
        setAvailable(true);
      } else {
        setAvailable(false);
      }
    };
    read();
    const id = window.setInterval(read, 500);
    return () => window.clearInterval(id);
  }, []);

  const selected = GRAPH_OPTIONS.find((g) => g.id === current) || GRAPH_OPTIONS[0];

  const onSelect = (gid: GraphId) => {
    const w = window as unknown as { __AIR_AGENT_SWITCH_GRAPH?: (id: GraphId) => void };
    if (!w.__AIR_AGENT_SWITCH_GRAPH) return;
    setCurrent(gid);
    w.__AIR_AGENT_SWITCH_GRAPH(gid);
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="gap-2 px-3 max-w-[16rem]"
          disabled={!available}
          title={available ? "切换模型能力（Graph）" : "运行时尚未就绪"}
        >
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: colorFor(selected.id) }}
            aria-hidden
          />
          <span className="truncate">{selected.label}</span>
          <span className="hidden sm:inline text-[10px] text-muted-foreground">
            {selected.id}
          </span>
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[34rem]">
        <DialogHeader>
          <DialogTitle>选择 Agent Graph</DialogTitle>
          <DialogDescription>
            不同 Graph 对应不同的能力与工作流，切换后会进入一个全新的会话。
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 mt-2">
          {GRAPH_OPTIONS.map((g) => {
            const active = g.id === current;
            return (
              <button
                key={g.id}
                type="button"
                onClick={() => onSelect(g.id)}
                className={cn(
                  "group flex items-start text-left gap-3 rounded-lg border p-3 transition-colors",
                  "focus:outline-none focus:ring-2 focus:ring-ring",
                  active
                    ? "border-primary bg-primary/5"
                    : "hover:bg-accent/50 hover:border-accent-foreground/20",
                )}
              >
                <span
                  className="mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ background: colorFor(g.id) }}
                  aria-hidden
                />
                <div className="min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-medium text-sm">{g.label}</p>
                    <code className="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded whitespace-nowrap">
                      {g.id}
                    </code>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">{g.desc}</p>
                </div>
              </button>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** 给每个 graph 分配一个稳定的小圆点颜色，方便快速区分 */
const COLORS: Record<GraphId, string> = {
  "basic-qa": "#16a34a", // green-600
  "intelligent-analysis": "#2563eb", // blue-600
  "intelligent-tracing": "#7c3aed", // violet-600
  "intelligent-report": "#db2777", // pink-600
  "deep-research": "#ea580c", // orange-600
  "data-analysis": "#0891b2", // cyan-600
};
function colorFor(id: string): string {
  return COLORS[id as GraphId] ?? "#6b7280";
}

