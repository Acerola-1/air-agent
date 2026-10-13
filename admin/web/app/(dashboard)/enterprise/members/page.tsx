"use client";

/** 企业成员管理：列表/搜索/分页、状态、部门、角色。只影响本企业的成员属性。 */

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, departmentsApi, membersApi, rolesApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Department, Member, Role } from "@/lib/types";

export default function EnterpriseMembersPage() {
  const { ctx, can } = useSession();
  const tenantId = ctx?.current_tenant?.id;
  const [items, setItems] = useState<Member[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Member | null>(null);
  const [roles, setRoles] = useState<Role[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const size = 20;

  const load = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    try {
      const data = await membersApi.list(tenantId, { page, size, q: q || undefined });
      setItems(data.items);
      setTotal(data.total);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载成员失败");
    } finally {
      setLoading(false);
    }
  }, [tenantId, page, q]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!tenantId) return;
    // 角色/部门选项仅在有管理权限时加载
    if (can("member:manage")) {
      void rolesApi.list().then((r) => setRoles(r.filter((x) => x.scope === "tenant")));
      void departmentsApi.list().then(setDepartments);
    }
  }, [tenantId, can]);

  if (!tenantId) {
    return <p className="text-muted-foreground text-sm">尚未选择企业。</p>;
  }
  if (!can("member:read")) {
    return <p className="text-muted-foreground text-sm">你没有成员管理权限。</p>;
  }

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">成员管理 · {ctx?.current_tenant?.name}</h1>
      <div className="flex gap-2">
        <Input
          className="max-w-xs"
          placeholder="搜索账号或用户名"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
      </div>
      {error && <p className="text-destructive text-sm">{error}</p>}
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="text-muted-foreground p-6 text-sm">加载中…</div>
          ) : items.length === 0 ? (
            <div className="text-muted-foreground p-6 text-sm">没有匹配的成员。</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>账号</TableHead>
                  <TableHead>用户名</TableHead>
                  <TableHead>角色</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((member) => (
                  <TableRow key={member.id}>
                    <TableCell className="font-mono text-xs">{member.account}</TableCell>
                    <TableCell>{member.display_name}</TableCell>
                    <TableCell className="text-xs">{member.role_codes.join("、") || "成员"}</TableCell>
                    <TableCell>{member.status === "active" ? "正常" : "已停用"}</TableCell>
                    <TableCell>
                      {can("member:manage") && (
                        <Button size="sm" variant="outline" onClick={() => setEditing(member)}>
                          编辑
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <div className="text-muted-foreground flex items-center gap-2 text-sm">
        共 {total} 条
        <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button>
        <span>第 {page} 页</span>
        <Button size="sm" variant="outline" disabled={page * size >= total} onClick={() => setPage(page + 1)}>下一页</Button>
      </div>
      <EditMemberDialog
        member={editing}
        roles={roles}
        departments={departments}
        onClose={() => setEditing(null)}
        onDone={() => void load()}
      />
    </div>
  );
}

function EditMemberDialog({ member, roles, departments, onClose, onDone }: {
  member: Member | null;
  roles: Role[];
  departments: Department[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [departmentId, setDepartmentId] = useState<string>("");
  const [status, setStatus] = useState<string>("active");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (member) {
      setSelectedRoles(member.role_codes);
      setDepartmentId(member.department_id ?? "");
      setStatus(member.status);
      setError(null);
    }
  }, [member]);

  if (!member) return null;
  const tenantId = member.tenant_id;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await membersApi.update(tenantId, member!.id, {
        status: status as "active" | "disabled",
        department_id: departmentId === "" ? null : departmentId,
      });
      await membersApi.setRoles(tenantId, member!.id, selectedRoles);
      onClose();
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑成员 · {member.display_name}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>部门</Label>
            <select
              className="border-input bg-background w-full rounded-md border px-3 py-2 text-sm"
              value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}
            >
              <option value="">（无部门）</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label>角色</Label>
            <div className="space-y-1">
              {roles.map((role) => (
                <label key={role.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={selectedRoles.includes(role.code)}
                    onChange={(e) =>
                      setSelectedRoles((prev) =>
                        e.target.checked ? [...prev, role.code] : prev.filter((c) => c !== role.code)
                      )
                    }
                  />
                  {role.name}（{role.code}）
                </label>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={status === "active"} onChange={(e) => setStatus(e.target.checked ? "active" : "disabled")} />
            成员身份有效
          </label>
          {error && <p className="text-destructive text-sm">{error}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={onClose}>取消</Button>
            <Button onClick={() => void save()} disabled={busy}>
              {busy && <LoaderCircle className="size-4 animate-spin" />} 保存
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
