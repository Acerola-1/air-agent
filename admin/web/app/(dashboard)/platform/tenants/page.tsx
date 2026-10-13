"use client";

/** 平台企业列表：创建、停用/恢复、关联成员（member:link 为平台专属）。 */

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, membersApi, tenantsApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Tenant } from "@/lib/types";

export default function PlatformTenantsPage() {
  const { can } = useSession();
  const [items, setItems] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [linking, setLinking] = useState<Tenant | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await tenantsApi.list());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载企业列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (can("tenant:read")) void load();
    else setLoading(false);
  }, [can, load]);

  async function toggleSuspend(tenant: Tenant) {
    try {
      await tenantsApi.update(tenant.id, {
        status: tenant.status === "active" ? "suspended" : "active",
      });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "操作失败");
    }
  }

  if (!can("tenant:read")) {
    return <p className="text-muted-foreground text-sm">你没有平台企业管理权限。</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">企业列表</h1>
        {can("tenant:create") && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="size-4" /> 新建企业
          </Button>
        )}
      </div>
      {error && <p className="text-destructive text-sm">{error}</p>}
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="text-muted-foreground p-6 text-sm">加载中…</div>
          ) : items.length === 0 ? (
            <div className="text-muted-foreground p-6 text-sm">还没有企业。点击右上角新建。</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>编码</TableHead>
                  <TableHead>名称</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>授权修订</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((tenant) => (
                  <TableRow key={tenant.id}>
                    <TableCell className="font-mono text-xs">{tenant.code}</TableCell>
                    <TableCell>{tenant.name}</TableCell>
                    <TableCell>{tenant.status === "active" ? "正常" : "已停用"}</TableCell>
                    <TableCell className="tabular-nums">{tenant.auth_revision}</TableCell>
                    <TableCell>
                      {can("tenant:update") && (
                        <div className="flex gap-2">
                          <Button size="sm" variant="outline" onClick={() => void toggleSuspend(tenant)}>
                            {tenant.status === "active" ? "停用" : "恢复"}
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => setLinking(tenant)}>
                            关联成员
                          </Button>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <CreateTenantDialog open={creating} onOpenChange={setCreating} onDone={() => void load()} />
      <LinkMemberDialog tenant={linking} onClose={() => setLinking(null)} onDone={() => void load()} />
    </div>
  );
}

function CreateTenantDialog({ open, onOpenChange, onDone }: { open: boolean; onOpenChange: (v: boolean) => void; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await tenantsApi.create({ code, name });
      onOpenChange(false);
      setCode("");
      setName("");
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建企业</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="t-code">企业编码（ASCII）</Label>
            <Input id="t-code" required value={code} onChange={(e) => setCode(e.target.value)} placeholder="如 beijing-air" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="t-name">企业名称</Label>
            <Input id="t-name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="如 北京市空气质量中心" />
          </div>
          {error && <p className="text-destructive text-sm">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={busy}>
              {busy && <LoaderCircle className="size-4 animate-spin" />} 创建
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function LinkMemberDialog({ tenant, onClose, onDone }: { tenant: Tenant | null; onClose: () => void; onDone: () => void }) {
  const [account, setAccount] = useState("");
  const [adminRole, setAdminRole] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!tenant) return;
    setBusy(true);
    setError(null);
    try {
      await membersApi.link(tenant.id, {
        account,
        role_codes: adminRole ? ["tenant_admin"] : [],
      });
      onClose();
      setAccount("");
      setAdminRole(false);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "关联失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={tenant !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>关联已有账号到{tenant?.name}</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="m-account">全局账号名（必须已存在）</Label>
            <Input id="m-account" required value={account} onChange={(e) => setAccount(e.target.value)} />
            <p className="text-muted-foreground text-xs">不会按同名自动关联；账号不存在时请先到“平台账号”创建。</p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={adminRole} onChange={(e) => setAdminRole(e.target.checked)} />
            授予企业管理员角色（tenant_admin）
          </label>
          {error && <p className="text-destructive text-sm">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={busy}>
              {busy && <LoaderCircle className="size-4 animate-spin" />} 关联
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
