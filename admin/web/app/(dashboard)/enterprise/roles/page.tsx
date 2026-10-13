"use client";

/** 企业角色：只列本企业角色（平台模板只读由列表接口保证）。可创建/改名/分配权限。 */

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PermissionTreePicker } from "@/components/permission-tree-picker";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, permissionsApi, rolesApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { PermissionNode, Role, RoleDetail } from "@/lib/types";

export default function EnterpriseRolesPage() {
  const { ctx, can } = useSession();
  const tenantId = ctx?.current_tenant?.id;
  const [roles, setRoles] = useState<Role[]>([]);
  const [permissionTree, setPermissionTree] = useState<PermissionNode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Role | null>(null);

  // R11：角色列表与权限目录解耦——修复前 Promise.all 让权限树 403 拖垮整个页面，
  // 企业管理员 roles 接口 200 也看不到角色。权限树改用"可授予目录"端点
  // （持有 role:assign_permission 即可读），且只在有编辑权限时加载。
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const all = await rolesApi.list();
      setRoles(all);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载角色失败");
    } finally {
      setLoading(false);
    }
    if (can("role:assign_permission")) {
      try {
        setPermissionTree(await permissionsApi.grantableTree());
      } catch {
        setPermissionTree([]);
      }
    }
  }, [can]);

  useEffect(() => {
    if (tenantId && can("role:read")) void load();
    else setLoading(false);
  }, [tenantId, can, load]);

  if (!tenantId) return <p className="text-muted-foreground text-sm">尚未选择企业。</p>;
  if (!can("role:read")) return <p className="text-muted-foreground text-sm">你没有角色查看权限。</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">企业角色 · {ctx?.current_tenant?.name}</h1>
        {can("role:create") && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="size-4" /> 新建企业角色
          </Button>
        )}
      </div>
      {error && <p className="text-destructive text-sm">{error}</p>}
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="text-muted-foreground p-6 text-sm">加载中…</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>编码</TableHead>
                  <TableHead>名称</TableHead>
                  <TableHead>范围</TableHead>
                  <TableHead>权限数</TableHead>
                  <TableHead>成员数</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {roles.map((role) => (
                  <TableRow key={role.id}>
                    <TableCell className="font-mono text-xs">{role.code}</TableCell>
                    <TableCell>{role.name}</TableCell>
                    <TableCell className="text-xs">{role.scope === "tenant" ? "本企业" : "平台模板"}</TableCell>
                    <TableCell className="tabular-nums">{role.permission_count}</TableCell>
                    <TableCell className="tabular-nums">{role.user_count}</TableCell>
                    <TableCell>
                      {can("role:assign_permission") && role.scope === "tenant" && !role.is_system && (
                        <Button size="sm" variant="outline" onClick={() => setEditing(role)}>权限</Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <CreateRoleDialog open={creating} onOpenChange={setCreating} onDone={() => void load()} />
      <EditRolePermsDialog role={editing} nodes={permissionTree} onClose={() => setEditing(null)} onDone={() => void load()} />
    </div>
  );
}

function CreateRoleDialog({ open, onOpenChange, onDone }: { open: boolean; onOpenChange: (v: boolean) => void; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await rolesApi.create({ code: code.toLowerCase(), name, scope: "tenant" } as Parameters<typeof rolesApi.create>[0]);
      onOpenChange(false);
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
        <DialogHeader><DialogTitle>新建企业角色</DialogTitle></DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="r-code">角色编码（小写字母数字下划线）</Label>
            <Input id="r-code" required value={code} onChange={(e) => setCode(e.target.value)} pattern="[a-z0-9_]{2,32}" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="r-name">角色名称</Label>
            <Input id="r-name" required value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          {error && <p className="text-destructive text-sm">{error}</p>}
          <DialogFooter><Button type="submit" disabled={busy}>{busy && <LoaderCircle className="size-4 animate-spin" />}创建</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditRolePermsDialog({ role, nodes, onClose, onDone }: {
  role: Role | null;
  nodes: PermissionNode[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [detail, setDetail] = useState<RoleDetail | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (role) {
      void rolesApi.get(role.id).then((d) => {
        setDetail(d);
        setSelected(d.permission_codes);
      });
    } else {
      setDetail(null);
    }
  }, [role]);

  if (!role || !detail) return null;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await rolesApi.setPermissions(role!.id, selected);
      onClose();
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "保存失败（不能授予超出操作者范围的权限）");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader><DialogTitle>{role.name} 的权限</DialogTitle></DialogHeader>
        <p className="text-muted-foreground text-xs">只能勾选本企业权限；平台专属权限会被服务端拒绝。</p>
        <PermissionTreePicker nodes={nodes} value={selected} onChange={setSelected} />
        {error && <p className="text-destructive text-sm">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={() => void save()} disabled={busy}>{busy && <LoaderCircle className="size-4 animate-spin" />}保存</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
