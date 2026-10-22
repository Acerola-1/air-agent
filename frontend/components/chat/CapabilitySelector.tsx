"use client";

import { useEffect, useState } from "react";
import {
  WrenchIcon,
  RotateCwIcon,
  CheckIcon,
  ShieldAlertIcon,
  CpuIcon,
} from "lucide-react";
import { useCapabilityStore, type CapabilitySelectionDraft } from "@/lib/capability-store";
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
import analysisMenus from "@/lib/data-analysis-menus.json";

export function CapabilitySelector() {
  const graphId = useChatStore((s) => s.graphId);
  const analysisMenu = useChatStore((s) => s.analysisMenuName);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const ctx = useSessionStore((s) => s.ctx);
  const currentTenant = ctx?.current_tenant;
  const stopPending = useChatStore((s) => s.stopPending);

  const catalog = useCapabilityStore((s) => s.catalog);
  const accepted = useCapabilityStore((s) => s.acceptedSelection);
  const loading = useCapabilityStore((s) => s.catalogLoading);
  const error = useCapabilityStore((s) => s.catalogError);
  const committedSelection = useCapabilityStore((s) => s.selection);
  const fetchCatalog = useCapabilityStore((s) => s.fetchCatalog);
  const [draft, setDraft] = useState<CapabilitySelectionDraft>(committedSelection);
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"skills" | "mcp">("skills");

  const selection = open ? draft : committedSelection;
  const menuSkillId = graphId === "data-analysis"
    ? analysisMenus.find((menu) => menu.value === analysisMenu)?.skillId : null;
  const skills = (catalog?.skills ?? []).filter((skill) => !menuSkillId || skill.id === menuSkillId);
  const setSkillMode = (mode: "auto" | "explicit", skillId: string | null = null) =>
    setDraft((s) => ({ ...s, skillMode: mode, selectedSkillId: skillId }));
  const setMcpMode = (mode: "auto" | "allowlist", ids: string[] = []) =>
    setDraft((s) => ({ ...s, mcpMode: mode, selectedToolIds: ids.slice(0, 20) }));
  const toggleTool = (id: string) => setDraft((s) => ({ ...s, mcpMode: "allowlist",
    selectedToolIds: s.selectedToolIds.includes(id) ? s.selectedToolIds.filter((x) => x !== id)
      : s.selectedToolIds.length < 20 ? [...s.selectedToolIds, id] : s.selectedToolIds }));
  const resetToDefault = () => setDraft({ skillMode: "auto", selectedSkillId: null, mcpMode: "auto", selectedToolIds: [] });
  useEffect(() => { setOpen(false); }, [graphId, ctx?.user.id, currentTenant?.id, ctx?.context_version, isStreaming, stopPending]);

  // 当会话图或企业变化时自动拉取目录
  useEffect(() => {
    if (graphId) {
      void fetchCatalog(graphId, currentTenant?.id);
    }
  }, [graphId, currentTenant?.id, ctx?.user.id, ctx?.context_version, fetchCatalog]);

  // 获得已选 Skill 标题
  const selectedSkill = catalog?.skills?.find((s) => s.id === selection.selectedSkillId);
  const skillLabel =
    selection.skillMode === "explicit" && selectedSkill
      ? (selectedSkill.title || selectedSkill.name || selectedSkill.id)
      : "自动匹配";

  // 获得已选 MCP 摘要
  let mcpLabel = "自动";
  if (selection.mcpMode === "allowlist") {
    if (selection.selectedToolIds.length === 0) {
      mcpLabel = "不使用";
    } else {
      mcpLabel = `${selection.selectedToolIds.length}项`;
    }
  }

  const triggerLabel = `技能: ${skillLabel} · 工具: ${mcpLabel}`;

  return (
    <>
    <Dialog open={open} onOpenChange={(next) => {
      if (next) setDraft({ ...committedSelection, selectedToolIds: [...committedSelection.selectedToolIds] });
      setOpen(next);
    }}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(
            "h-7 gap-1.5 px-2.5 rounded-full text-[11px] font-medium border-border/70 transition-all",
            selection.skillMode === "explicit" || selection.mcpMode === "allowlist"
              ? "border-emerald-500/40 bg-emerald-50/50 dark:bg-emerald-950/20 text-emerald-700 dark:text-emerald-300"
              : "text-muted-foreground hover:text-foreground"
          )}
          disabled={isStreaming || stopPending}
          title={isStreaming ? "运行中已锁定已受理选择，无法修改" : "配置本次执行的 Skill 与 MCP 工具"}
          aria-label="能力与工具选择"
        >
          <WrenchIcon className="size-3.5 stroke-[2]" />
          <span className="max-w-[14rem] truncate">{triggerLabel}</span>
        </Button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-[38rem] max-h-[85vh] flex flex-col p-5 overflow-hidden">
        <DialogHeader className="shrink-0 pb-3 border-b border-border/60">
          <div className="flex items-center justify-between pr-6">
            <DialogTitle className="text-base font-semibold flex items-center gap-2">
              <CpuIcon className="size-4 text-emerald-600" />
              能力与工具配置
            </DialogTitle>
            <Button
              variant="ghost"
              size="icon"
              className="size-7 text-muted-foreground hover:text-foreground"
              onClick={() => void fetchCatalog(graphId, currentTenant?.id)}
              disabled={loading}
              title="刷新能力目录"
              aria-label="刷新能力目录"
            >
              <RotateCwIcon className={cn("size-3.5", loading && "animate-spin")} />
            </Button>
          </div>
          <DialogDescription className="text-xs text-muted-foreground">
            选择本次回答使用的技能和外部工具。关闭或取消保留原设置，点击“完成”后应用。
          </DialogDescription>
        </DialogHeader>

        {loading && <p role="status" className="py-2 text-xs text-muted-foreground">正在加载能力目录…</p>}
        {/* 错误提示条 */}
        {error && (
          <div className="my-2 flex items-center gap-2 rounded-md bg-destructive/10 p-2.5 text-xs text-destructive shrink-0">
            <ShieldAlertIcon className="size-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <Button
              variant="outline"
              size="sm"
              className="h-6 text-[10px] px-2"
              onClick={() => void fetchCatalog(graphId, currentTenant?.id)}
            >
              重试
            </Button>
          </div>
        )}

        {/* 标签栏 */}
        <div className="flex gap-2 border-b border-border/60 pb-2 mt-2 shrink-0">
          <button
            type="button"
            onClick={() => setActiveTab("skills")}
            className={cn(
              "px-3 py-1 text-xs font-medium rounded-md transition-colors",
              activeTab === "skills"
                ? "bg-muted text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/40"
            )}
          >
            技能选择 ({skills.length})
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("mcp")}
            className={cn(
              "px-3 py-1 text-xs font-medium rounded-md transition-colors",
              activeTab === "mcp"
                ? "bg-muted text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/40"
            )}
          >
            MCP 工具 ({catalog?.tools.length ?? 0})
          </button>
        </div>

        {/* 滚动内容区 */}
        <div className="flex-1 overflow-y-auto py-3 pr-1 space-y-4">
          {activeTab === "skills" ? (
            <div className="space-y-2" role="radiogroup" aria-label="技能选择" onKeyDown={(e) => {
              if (e.nativeEvent.isComposing || !["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
              const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]'));
              const index = items.indexOf(document.activeElement as HTMLElement);
              const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1
                : (index + (e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 1) + items.length) % items.length;
              e.preventDefault(); items[next]?.focus(); items[next]?.click();
            }}>
              {/* 自动匹配选项 */}
              <div
                role="radio"
                aria-checked={selection.skillMode === "auto"}
                tabIndex={0}
                onClick={() => setSkillMode("auto")}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSkillMode("auto");
                  }
                }}
                className={cn(
                  "flex items-start gap-3 p-3 rounded-xl border text-left cursor-pointer transition-all focus:outline-none focus:ring-2 focus:ring-emerald-500/20",
                  selection.skillMode === "auto"
                    ? "border-emerald-500/50 bg-emerald-50/30 dark:bg-emerald-950/20 ring-1 ring-emerald-500/30"
                    : "border-border/60 hover:bg-muted/30"
                )}
              >
                <div className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-border">
                  {selection.skillMode === "auto" && (
                    <div className="size-2 rounded-full bg-emerald-600" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold">自动匹配（默认）</span>
                    <span className="text-[10px] rounded bg-muted px-1.5 py-0.5 text-muted-foreground">智能路由</span>
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-0.5">
                    根据用户提问内容，由语义路由器动态匹配并加载最符合业务诉求的规则。
                  </p>
                </div>
              </div>

              {/* Skill 列表 */}
              {skills.map((skill) => {
                const isSelected =
                  selection.skillMode === "explicit" &&
                  selection.selectedSkillId === skill.id;
                return (
                  <div
                    key={skill.id}
                    role="radio"
                    aria-checked={isSelected}
                    tabIndex={0}
                    onClick={() => setSkillMode("explicit", skill.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setSkillMode("explicit", skill.id);
                      }
                    }}
                    className={cn(
                      "flex items-start gap-3 p-3 rounded-xl border text-left cursor-pointer transition-all focus:outline-none focus:ring-2 focus:ring-emerald-500/20",
                      isSelected
                        ? "border-emerald-500/50 bg-emerald-50/30 dark:bg-emerald-950/20 ring-1 ring-emerald-500/30"
                        : "border-border/60 hover:bg-muted/30"
                    )}
                  >
                    <div className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-border">
                      {isSelected && (
                        <div className="size-2 rounded-full bg-emerald-600" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs font-semibold">{skill.title || skill.name || skill.id}</span>
                        <span className="text-[10px] font-mono text-muted-foreground/80">
                          {skill.name || skill.id}
                        </span>
                        <span className="text-[9px] font-mono rounded bg-muted/80 px-1 py-0.5 text-muted-foreground ml-auto">
                          {(skill.material_version || "").substring(0, 15)}…
                        </span>
                      </div>
                      <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">
                        {skill.description || "无详细描述"}
                      </p>
                      {((skill.required_tool_ids || skill.required_tools) ?? []).length > 0 && (
                        <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-amber-600 dark:text-amber-400">
                          <span className="font-medium">必需依赖:</span>
                          <span>{((skill.required_tool_ids || skill.required_tools) ?? []).join(", ")}</span>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}

              {skills.length === 0 && !loading && (
                <div className="py-6 text-center text-xs text-muted-foreground">
                  当前图暂无可用技能
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              {(catalog?.mcp_services ?? []).filter((service) => service.status !== "available").map((service) => (
                <p key={service.server_key} role="status" className="rounded-md bg-muted p-2 text-xs text-muted-foreground">
                  {service.display_name}：{service.status === "unverified" ? "企业授权协议尚未核验，暂不可使用"
                    : service.status === "not_configured" ? "尚未配置服务地址，请联系管理员"
                    : "服务暂不可用，可刷新重试"}
                </p>
              ))}
              {/* MCP 模式切换 */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                <button
                  type="button"
                  onClick={() => setMcpMode("auto")}
                  className={cn(
                    "p-2.5 rounded-lg border text-left text-xs transition-all",
                    selection.mcpMode === "auto"
                      ? "border-emerald-500/50 bg-emerald-50/30 dark:bg-emerald-950/20 font-medium text-emerald-800 dark:text-emerald-200"
                      : "border-border/60 text-muted-foreground hover:bg-muted/30"
                  )}
                >
                  <div className="font-semibold text-foreground">全部已授权（自动）</div>
                  <div className="text-[10px] text-muted-foreground mt-0.5">披露所有企业授权工具</div>
                </button>

                <button
                  type="button"
                  onClick={() => setMcpMode("allowlist", [])}
                  className={cn(
                    "p-2.5 rounded-lg border text-left text-xs transition-all",
                    selection.mcpMode === "allowlist" && selection.selectedToolIds.length === 0
                      ? "border-emerald-500/50 bg-emerald-50/30 dark:bg-emerald-950/20 font-medium text-emerald-800 dark:text-emerald-200"
                      : "border-border/60 text-muted-foreground hover:bg-muted/30"
                  )}
                >
                  <div className="font-semibold text-foreground">不使用 MCP 工具</div>
                  <div className="text-[10px] text-muted-foreground mt-0.5">禁用外部工具（纯本地）</div>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    if (selection.mcpMode !== "allowlist" || selection.selectedToolIds.length === 0) {
                      const allIds =
                        catalog?.tools
                          .map((t) => t.id || (t.server_key ? `${t.server_key}:${t.tool_name || t.name}` : t.name))
                          .filter((x): x is string => Boolean(x)) ?? [];
                      setMcpMode("allowlist", allIds);
                    }
                  }}
                  className={cn(
                    "p-2.5 rounded-lg border text-left text-xs transition-all",
                    selection.mcpMode === "allowlist" && selection.selectedToolIds.length > 0
                      ? "border-emerald-500/50 bg-emerald-50/30 dark:bg-emerald-950/20 font-medium text-emerald-800 dark:text-emerald-200"
                      : "border-border/60 text-muted-foreground hover:bg-muted/30"
                  )}
                >
                  <div className="font-semibold text-foreground">指定工具子集</div>
                  <div className="text-[10px] text-muted-foreground mt-0.5">按需自定义勾选</div>
                </button>
              </div>

              {/* 工具白名单勾选列表 */}
              {selection.mcpMode === "allowlist" && (
                <div className="mt-3 space-y-2 pt-2 border-t border-border/40">
                  <div className="flex items-center justify-between text-xs text-muted-foreground pb-1">
                    <span>可勾选工具列表 ({selection.selectedToolIds.length}/20 已选)</span>
                    <div className="flex gap-2 text-[11px]">
                      <button
                        type="button"
                        onClick={() => {
                          const allAvailable =
                            catalog?.tools
                              .filter((t) => t.status === "available")
                              .map((t) => t.id || (t.server_key ? `${t.server_key}:${t.tool_name || t.name}` : t.name))
                              .filter((x): x is string => Boolean(x)) ?? [];
                          setMcpMode("allowlist", allAvailable);
                        }}
                        className="text-emerald-600 hover:underline"
                      >
                        全选可用
                      </button>
                      <button
                        type="button"
                        onClick={() => setMcpMode("allowlist", [])}
                        className="text-muted-foreground hover:underline"
                      >
                        清空
                      </button>
                    </div>
                  </div>

                  {catalog?.tools.map((tool, idx) => {
                    const toolKey = tool.id || (tool.server_key ? `${tool.server_key}:${tool.tool_name || tool.name}` : `tool-${idx}`);
                    const isChecked = selection.selectedToolIds.includes(tool.id || toolKey);
                    const isUnavailable = tool.status === "unavailable";
                    return (
                      <div
                        key={toolKey}
                        role="checkbox"
                        aria-checked={isChecked}
                        tabIndex={isUnavailable ? -1 : 0}
                        onClick={() => {
                          if (!isUnavailable) toggleTool(tool.id || toolKey);
                        }}
                        onKeyDown={(e) => {
                          if (!isUnavailable && (e.key === "Enter" || e.key === " ")) {
                            e.preventDefault();
                            toggleTool(tool.id || toolKey);
                          }
                        }}
                        className={cn(
                          "flex items-start gap-3 p-2.5 rounded-lg border text-left transition-all",
                          isUnavailable
                            ? "opacity-50 border-dashed border-border/60 cursor-not-allowed bg-muted/20"
                            : isChecked
                            ? "border-emerald-500/50 bg-emerald-50/20 dark:bg-emerald-950/10 cursor-pointer"
                            : "border-border/60 hover:bg-muted/20 cursor-pointer"
                        )}
                      >
                        <div
                          className={cn(
                            "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border",
                            isChecked
                              ? "bg-emerald-600 border-emerald-600 text-white"
                              : "border-border"
                          )}
                        >
                          {isChecked && <CheckIcon className="size-3 stroke-[3]" />}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-semibold">{tool.display_name || tool.name || tool.tool_name || toolKey}</span>
                            <span className="text-[10px] font-mono text-muted-foreground">
                              {tool.tool_name || tool.name || toolKey}
                            </span>
                            <span className="text-[9px] font-mono px-1.5 py-0.2 rounded bg-muted text-muted-foreground ml-auto">
                              {tool.server_key || ""}
                            </span>
                            {isUnavailable && (
                              <span className="text-[10px] text-destructive bg-destructive/10 px-1 rounded">
                                服务不可用
                              </span>
                            )}
                          </div>
                          {tool.description && (
                            <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-1">
                              {tool.description}
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })}

                  {catalog?.tools.length === 0 && !loading && (
                    <div className="py-6 text-center text-xs text-muted-foreground">
                      当前企业暂无可用的授权 MCP 工具
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 底部摘要与操作 */}
        <div className="mt-auto pt-3 border-t border-border/60 flex items-center justify-between shrink-0 text-xs">
          <div className="text-muted-foreground text-[11px] truncate max-w-[20rem]">
            已选: <span className="font-medium text-foreground">{skillLabel}</span> ·{" "}
            <span className="font-medium text-foreground">{mcpLabel}</span>
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setOpen(false)}>取消</Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={resetToDefault}
              className="h-7 text-xs text-muted-foreground"
            >
              重置默认
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={loading || Boolean(error)}
              onClick={() => {
                const store = useCapabilityStore.getState();
                store.setSkillMode(draft.skillMode, draft.selectedSkillId);
                store.setMcpMode(draft.mcpMode, draft.selectedToolIds);
                setOpen(false);
              }}
              className="h-7 text-xs bg-emerald-600 hover:bg-emerald-700 text-white px-3"
            >
              完成
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
    {accepted && (
      <span className="max-w-full truncate text-[10px] text-muted-foreground" title="这是服务端已受理的计划，实际使用结果以回答和工具记录为准。" aria-live="polite">
        最近已受理：{accepted.skill_mode === "explicit" ? (catalog?.skills.find((s) => s.id === accepted.skill_id)?.title || "指定技能") : "自动匹配"}
        {" · "}{accepted.mcp_mode === "auto" ? "自动工具" : accepted.tool_ids.length ? `${accepted.tool_ids.length} 项工具` : "不使用外部工具"}
      </span>
    )}
    </>
  );
}
