"use client";

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, Lock, Pencil, Plus, Trash2 } from "lucide-react";

import { PermissionTreePicker } from "@/components/permission-tree-picker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { permissionsApi, rolesApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { PermissionNode, Role, RoleDetail } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

export default function RolesPage() {
  const { can } = useSession();

  const [roles, setRoles] = useState<Role[]>([]);
  const [tree, setTree] = useState<PermissionNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Role | null>(null);
  const [editingPermissions, setEditingPermissions] = useState<RoleDetail | null>(null);
  const [deleting, setDeleting] = useState<Role | null>(null);

  // 新建/编辑表单
  const [form, setForm] = useState({ code: "", name: "", description: "", sort: 0 });
  const [busy, setBusy] = useState(false);

  // 权限勾选
  const [checked, setChecked] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRoles(await rolesApi.list());
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载角色失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (can("permission:read")) {
      void permissionsApi.tree().then(setTree).catch(() => setTree([]));
    }
  }, [can]);

  async function submitForm(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (editing) {
        await rolesApi.update(editing.id, {
          name: form.name.trim(),
          description: form.description.trim(),
          sort: Number(form.sort) || 0,
        });
        setEditing(null);
      } else {
        await rolesApi.create({
          code: form.code.trim(),
          name: form.name.trim(),
          description: form.description.trim() || undefined,
          sort: Number(form.sort) || 0,
        });
        setCreateOpen(false);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function openPermissionEditor(role: Role) {
    setError(null);
    try {
      const detail = await rolesApi.get(role.id);
      setEditingPermissions(detail);
      setChecked(detail.permission_codes);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取角色权限失败");
    }
  }

  async function savePermissions() {
    if (!editingPermissions) return;
    setBusy(true);
    setError(null);
    try {
      await rolesApi.setPermissions(editingPermissions.id, checked);
      setEditingPermissions(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存权限失败");
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setBusy(true);
    setError(null);
    try {
      await rolesApi.remove(deleting.id);
      setDeleting(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">角色管理</h1>
          <p className="text-muted-foreground mt-1 text-sm">共 {roles.length} 个角色</p>
        </div>
        {can("role:create") && (
          <Button
            onClick={() => {
              setForm({ code: "", name: "", description: "", sort: 0 });
              setCreateOpen(true);
            }}
          >
            <Plus className="size-4" />
            新建角色
          </Button>
        )}
      </div>

      {error && <p className="text-destructive text-sm">{error}</p>}

      <Card className="py-0">
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>角色</TableHead>
                <TableHead>标识</TableHead>
                <TableHead>描述</TableHead>
                <TableHead>权限</TableHead>
                <TableHead>用户</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground h-24 text-center">
                    加载中…
                  </TableCell>
                </TableRow>
              )}
              {!loading &&
                roles.map((role) => (
                  <TableRow key={role.id}>
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-2">
                        {role.name}
                        {role.is_system && (
                          <Badge variant="muted" className="gap-1">
                            <Lock className="size-3" />
                            内置
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{role.code}</TableCell>
                    <TableCell className="text-muted-foreground max-w-64 truncate text-sm">
                      {role.description || "—"}
                    </TableCell>
                    <TableCell className="tabular-nums">{role.permission_count}</TableCell>
                    <TableCell className="tabular-nums">{role.user_count}</TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {formatDateTime(role.created_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {can("role:assign_permission") && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => void openPermissionEditor(role)}
                          >
                            配置权限
                          </Button>
                        )}
                        {can("role:update") && (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            title="编辑"
                            onClick={() => {
                              setForm({
                                code: role.code,
                                name: role.name,
                                description: role.description ?? "",
                                sort: role.sort,
                              });
                              setEditing(role);
                            }}
                          >
                            <Pencil className="size-4" />
                          </Button>
                        )}
                        {can("role:delete") && !role.is_system && (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            title="删除"
                            className="text-destructive hover:text-destructive"
                            onClick={() => setDeleting(role)}
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* 新建 / 编辑 */}
      <Dialog
        open={createOpen || editing !== null}
        onOpenChange={(open) => {
          if (!open) {
            setCreateOpen(false);
            setEditing(null);
            setError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? `编辑角色：${editing.code}` : "新建角色"}</DialogTitle>
            <DialogDescription>
              {editing ? "内置角色不可修改标识，只能改名称、描述与排序。" : "标识用于代码引用，创建后不可修改。"}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={submitForm} className="space-y-4">
            {!editing && (
              <div className="space-y-1.5">
                <Label htmlFor="role-code">标识</Label>
                <Input
                  id="role-code"
                  required
                  minLength={2}
                  maxLength={32}
                  pattern="[a-z0-9_]+"
                  placeholder="例如 auditor"
                  value={form.code}
                  onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                />
                <p className="text-muted-foreground text-xs">只能用小写字母、数字、下划线</p>
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="role-name">名称</Label>
              <Input
                id="role-name"
                required
                maxLength={64}
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="role-desc">描述</Label>
              <Textarea
                id="role-desc"
                maxLength={255}
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="role-sort">排序</Label>
              <Input
                id="role-sort"
                type="number"
                value={form.sort}
                onChange={(e) => setForm((f) => ({ ...f, sort: Number(e.target.value) }))}
              />
            </div>
            {error && <p className="text-destructive text-sm">{error}</p>}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setCreateOpen(false);
                  setEditing(null);
                }}
              >
                取消
              </Button>
              <Button type="submit" disabled={busy}>
                {busy && <LoaderCircle className="size-4 animate-spin" />}
                保存
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 配置权限 */}
      <Dialog
        open={editingPermissions !== null}
        onOpenChange={(open) => !open && setEditingPermissions(null)}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>配置权限：{editingPermissions?.name}</DialogTitle>
            <DialogDescription>
              勾选该角色可使用的菜单与操作。保存后，属于该角色的用户立即生效。
            </DialogDescription>
          </DialogHeader>

          {tree.length ? (
            <PermissionTreePicker nodes={tree} value={checked} onChange={setChecked} />
          ) : (
            <p className="text-muted-foreground text-sm">没有可配置的权限。</p>
          )}

          {error && <p className="text-destructive text-sm">{error}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditingPermissions(null)}>
              取消
            </Button>
            <Button onClick={savePermissions} disabled={busy}>
              {busy && <LoaderCircle className="size-4 animate-spin" />}
              保存权限
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除角色</DialogTitle>
            <DialogDescription>
              将删除角色「{deleting?.name}」。
              {deleting && deleting.user_count > 0
                ? `当前有 ${deleting.user_count} 个用户持有该角色，他们的这部分权限会被一并解除。`
                : "该角色尚未分配给任何用户。"}
            </DialogDescription>
          </DialogHeader>
          {error && <p className="text-destructive text-sm">{error}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>
              取消
            </Button>
            <Button variant="destructive" onClick={confirmDelete} disabled={busy}>
              {busy && <LoaderCircle className="size-4 animate-spin" />}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
