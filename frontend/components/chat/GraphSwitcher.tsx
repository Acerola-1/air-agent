"use client";

import { useEffect, useState } from "react";
import { availableGraphOptions, colorForGraph } from "@/lib/graph-options";
import { useChatStore } from "@/lib/chat-store";
import { useSessionStore } from "@/lib/session-client";
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

/** 业务切换器（顶栏/输入区常驻）——按 IAM 实时业务权限生成可用业务。
 *
 *  - 未开通/未授权的业务不出现在选择列表；
 *  - 切换业务会清空当前会话并加载该业务的线程列表（按 user_id 隔离）；
 *  - 业务无权（403）与外部连接故障是两类状态：后者在能力目录中单独显示。
 */
export function GraphSwitcher() {
  const graphId = useChatStore((s) => s.graphId);
  const switchGraph = useChatStore((s) => s.switchGraph);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const permissions = useSessionStore((s) => s.ctx?.permissions);
  const [open, setOpen] = useState(false);

  const available = availableGraphOptions(permissions);
  const selected =
    available.find((option) => option.id === graphId) ?? available[0];

  // 权限/企业变化后当前业务不再可用：自动落到第一个可用业务，避免停留在
  // 一个服务端必然拒绝的入口。无任何业务时保持当前视图并显示空状态。
  useEffect(() => {
    if (available.length > 0 && !available.some((option) => option.id === graphId)) {
      void switchGraph(available[0].id);
    }
  }, [available, graphId, switchGraph]);

  const onSelect = (gid: string) => {
    if (gid === graphId) {
      setOpen(false);
      return;
    }
    void switchGraph(gid);
    setOpen(false);
  };

  if (!selected) {
    return (
      <Button
        variant="outline"
        size="sm"
        className="gap-2 px-3 max-w-[16rem]"
        disabled
        title="当前企业未开通或未授予任何 Agent 业务"
      >
        <span className="inline-block h-2 w-2 rounded-full bg-muted-foreground/40" aria-hidden />
        <span className="truncate">无可用业务</span>
      </Button>
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="gap-2 px-3 max-w-[16rem]"
          disabled={isStreaming}
          title={isStreaming ? "回答中，暂不能切换" : "切换业务（按当前企业开通与本人授权）"}
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
          <DialogTitle>选择 Agent 业务</DialogTitle>
          <DialogDescription>
            仅展示当前企业已开通且本人已获授的业务。外部数据连接状态在能力目录中单独显示；
            业务无权与连接故障不是同一状态。
          </DialogDescription>
        </DialogHeader>
        <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {available.map((option) => {
            const active = option.id === graphId;
            return (
              <button
                key={option.id}
                type="button"
                onClick={() => onSelect(option.id)}
                className={cn(
                  "group flex items-start text-left gap-3 rounded-lg border p-3 transition-colors",
                  "focus:outline-none focus:ring-2 focus:ring-ring",
                  active ? "border-primary bg-primary/5" : "hover:bg-accent/50 hover:border-accent-foreground/20",
                )}
              >
                <span
                  className="mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ background: colorForGraph(option.id) }}
                  aria-hidden
                />
                <div className="min-w-0">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium">{option.label}</p>
                    <code className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] whitespace-nowrap">
                      {option.id}
                    </code>
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs">{option.desc}</p>
                </div>
              </button>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
