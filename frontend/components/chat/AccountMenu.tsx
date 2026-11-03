"use client";

import { useRef, useState } from "react";
import { CheckIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useChatStore } from "@/lib/chat-store";
import { useSessionStore } from "@/lib/session-client";

function accountMark(name: string | null): string {
  const text = name?.trim();
  if (!text) return "";
  return Array.from(text)[0]?.toUpperCase() ?? "";
}

export function AccountMenu() {
  const ctx = useSessionStore((s) => s.ctx);
  const username = useSessionStore((s) => s.username);
  const logout = useSessionStore((s) => s.logout);
  const logoutPending = useSessionStore((s) => s.logoutPending);
  const switchTenant = useSessionStore((s) => s.switchTenant);
  const conflict = useSessionStore((s) => s.conflict);
  const clearConflict = () => useSessionStore.setState({ conflict: null });
  const stopStreaming = useChatStore((s) => s.stopStreaming);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingTenant, setPendingTenant] = useState<string | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const current = ctx?.current_tenant;
  const mark = accountMark(username);

  async function handleSwitch(tenantId: string) {
    if (!current || tenantId === current.id || busy) return;
    setError(null);
    setBusy(true);
    const run = useChatStore.getState();
    if (run.isStreaming || run.stopPending) {
      const stopped = await stopStreaming();
      if (!stopped.confirmed) {
        setError("未能确认停止当前回答（取消失败或超时），请稍后重试切换");
        setBusy(false);
        return;
      }
    }
    const result = await switchTenant(tenantId);
    setBusy(false);
    if (!result.ok) {
      if (!result.conflict) setError(result.error ?? "企业切换失败");
      return;
    }
    window.location.reload();
  }

  function selectTenant(tenantId: string) {
    if (!current || tenantId === current.id || busy) return;
    setError(null);
    const run = useChatStore.getState();
    if (run.isStreaming || run.stopPending) setPendingTenant(tenantId);
    else void handleSwitch(tenantId);
  }

  const pendingName = ctx?.tenants.find((item) => item.tenant.id === pendingTenant)?.tenant.name ?? "所选企业";

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            ref={triggerRef}
            type="button"
            aria-label="账号"
            title={username ?? "账号"}
            className="inline-flex size-8 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
          >
            {mark || "·"}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="min-w-44">
          <div className="px-2 py-1.5">
            <p className="truncate text-sm font-medium">{username ?? "正在读取账号"}</p>
            <p className="truncate text-xs text-muted-foreground">{current?.name ?? "未选择企业"}</p>
          </div>
          {ctx && ctx.tenants.length > 1 && (
            <>
              <DropdownMenuSeparator />
              {ctx.tenants.map((item) => {
                const selected = item.tenant.id === current?.id;
                return (
                  <DropdownMenuItem
                    key={item.tenant.id}
                    disabled={busy}
                    onSelect={() => selectTenant(item.tenant.id)}
                  >
                    <span className="truncate">{item.tenant.name}</span>
                    {selected && <CheckIcon className="ml-auto size-3.5" />}
                  </DropdownMenuItem>
                );
              })}
            </>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={!username || logoutPending} onSelect={() => void logout()}>
            {logoutPending ? "退出中…" : "退出"}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {error && <p role="alert" className="absolute bottom-full left-2 z-20 mb-1 max-w-56 rounded-md border bg-popover px-2 py-1 text-xs text-destructive shadow-md">{error}</p>}
      {conflict && (
        <button type="button" className="absolute bottom-full left-2 z-20 mb-1 max-w-56 rounded-md border bg-popover px-2 py-1 text-left text-xs text-amber-600 shadow-md" onClick={clearConflict}>
          企业上下文已刷新
        </button>
      )}
      <Dialog open={pendingTenant !== null} onOpenChange={(open) => { if (!open && !busy) setPendingTenant(null); }}>
        <DialogContent
          showCloseButton={false}
          onOpenAutoFocus={(event) => { event.preventDefault(); cancelRef.current?.focus(); }}
          onCloseAutoFocus={(event) => { event.preventDefault(); triggerRef.current?.focus(); }}
          onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}
          onPointerDownOutside={(event) => { if (busy) event.preventDefault(); }}
        >
          <DialogHeader>
            <DialogTitle>停止回答并切换企业</DialogTitle>
            <DialogDescription>确认当前回答已停止后，将切换到{pendingName}，并清空当前页面的会话列表。</DialogDescription>
          </DialogHeader>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button ref={cancelRef} variant="outline" disabled={busy} onClick={() => setPendingTenant(null)}>取消切换</Button>
            <Button disabled={busy} className="min-w-36" onClick={() => { if (pendingTenant) void handleSwitch(pendingTenant); }}>
              {busy ? "正在确认停止…" : "停止回答并切换"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
