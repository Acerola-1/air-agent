"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

import { useChatStore } from "@/lib/chat-store";
import { useSessionStore } from "@/lib/session-client";

/** 当前企业徽章与切换器。
 *
 * 切换流程（spec 3.x/9.2）：活动回答存在时先请求停止并等待确认；
 * 切换成功后线程/归档缓存被清空并重载当前图列表。
 */
export function TenantBadge() {
  const ctx = useSessionStore((s) => s.ctx);
  const switchTenant = useSessionStore((s) => s.switchTenant);
  const conflict = useSessionStore((s) => s.conflict);
  const clearConflict = () => useSessionStore.setState({ conflict: null });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingTenant, setPendingTenant] = useState<string | null>(null);
  const selectRef = useRef<HTMLSelectElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const stopStreaming = useChatStore((s) => s.stopStreaming);

  if (!ctx) return null;
  const current = ctx.current_tenant;

  async function handleSwitch(tenantId: string) {
    if (!current || tenantId === current.id || busy) return;
    setError(null);
    setBusy(true);
    const run = useChatStore.getState();
    if (run.isStreaming || run.stopPending) {
      // R10：等待后端取消终态确认；未确认不切换、不虚称已停止
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

  if (ctx.tenants.length === 0) {
    return (
      <span className="rounded-full bg-muted px-2 py-0.5 text-foreground" title="当前账号未加入任何企业">
        {current?.name ?? "未加入企业"}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1">
      {ctx.tenants.length > 1 ? (
        <label className="inline-flex items-center gap-1">
          <span className="sr-only">切换企业</span>
          <select
            ref={selectRef}
            value={current?.id ?? ""}
            disabled={busy}
            onChange={(event) => selectTenant(event.target.value)}
            className="max-w-32 truncate rounded-full bg-muted px-2 py-0.5 text-foreground text-[11px] outline-none"
            title={`当前企业：${current?.name ?? "未选择"}`}
          >
            {ctx.tenants.map((item) => (
              <option key={item.tenant.id} value={item.tenant.id}>
                {item.tenant.name}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <span className="max-w-32 truncate rounded-full bg-muted px-2 py-0.5 text-foreground" title={current?.name}>
          {current?.name ?? "企业"}
        </span>
      )}
      {error && (
        <button type="button" className="text-red-500 underline" onClick={() => setError(null)} title={error}>
          切换失败
        </button>
      )}
      {conflict && (
        <button type="button" className="text-amber-500 underline" onClick={clearConflict} title={conflict}>
          企业上下文已刷新
        </button>
      )}
      {busy && <span className="text-muted-foreground">切换中…</span>}
      <Dialog open={pendingTenant !== null} onOpenChange={(open) => { if (!open && !busy) setPendingTenant(null); }}>
        <DialogContent showCloseButton={false}
          onOpenAutoFocus={(event) => { event.preventDefault(); cancelRef.current?.focus(); }}
          onCloseAutoFocus={(event) => { event.preventDefault(); selectRef.current?.focus(); }}
          onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}
          onPointerDownOutside={(event) => { if (busy) event.preventDefault(); }}>
          <DialogHeader>
            <DialogTitle>停止回答并切换企业</DialogTitle>
            <DialogDescription>确认当前回答已停止后，将切换到{ctx.tenants.find((item) => item.tenant.id === pendingTenant)?.tenant.name ?? "所选企业"}，并清空当前页面的会话列表。</DialogDescription>
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
    </span>
  );
}
