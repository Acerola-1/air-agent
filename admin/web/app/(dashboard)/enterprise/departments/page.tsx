"use client";

/** 部门管理：当前企业的部门树（平铺 + 父级名）；新增/改名/删除。 */

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, departmentsApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Department } from "@/lib/types";

export default function DepartmentsPage() {
  const { ctx, can } = useSession();
  const tenantId = ctx?.current_tenant?.id;
  const [items, setItems] = useState<Department[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await departmentsApi.list());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载部门失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tenantId && can("department:read")) void load();
    else setLoading(false);
  }, [tenantId, can, load]);

  if (!tenantId) return <p className="text-muted-foreground text-sm">尚未选择企业。</p>;
  if (!can("department:read")) return <p className="text-muted-foreground text-sm">你没有部门查看权限。</p>;

  const nameOf = (id: string | null) => (id ? items.find((d) => d.id === id)?.name ?? "？" : "—");

  async function remove(dept: Department) {
    if (!window.confirm(`删除部门「${dept.name}」？`)) return;
    try {
      await departmentsApi.remove(dept.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "删除失败");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">部门管理 · {ctx?.current_tenant?.name}</h1>
        {can("department:manage") && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="size-4" /> 新建部门
          </Button>
        )}
      </div>
      {error && <p className="text-destructive text-sm">{error}</p>}
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="text-muted-foreground p-6 text-sm">加载中…</div>
          ) : items.length === 0 ? (
            <div className="text-muted-foreground p-6 text-sm">还没有部门。成员可按部门归类。</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>编码</TableHead>
                  <TableHead>名称</TableHead>
                  <TableHead>父部门</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((dept) => (
                  <TableRow key={dept.id}>
                    <TableCell className="font-mono text-xs">{dept.dept_code}</TableCell>
                    <TableCell>{dept.name}</TableCell>
                    <TableCell>{nameOf(dept.parent_id)}</TableCell>
                    <TableCell>
                      {can("department:manage") && (
                        <Button size="sm" variant="outline" onClick={() => void remove(dept)}>删除</Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <CreateDeptDialog open={creating} onOpenChange={setCreating} onDone={() => void load()} parents={items} />
    </div>
  );
}

function CreateDeptDialog({ open, onOpenChange, onDone, parents }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onDone: () => void;
  parents: Department[];
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [parent, setParent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await departmentsApi.create({ dept_code: code, name, parent_id: parent || null });
      onOpenChange(false);
      setCode("");
      setName("");
      setParent("");
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
          <DialogTitle>新建部门</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="d-code">部门编码（企业内唯一）</Label>
            <Input id="d-code" required value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="d-name">部门名称</Label>
            <Input id="d-name" required value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="d-parent">父部门（可选，不允许成环）</Label>
            <select id="d-parent" className="border-input bg-background w-full rounded-md border px-3 py-2 text-sm" value={parent} onChange={(e) => setParent(e.target.value)}>
              <option value="">（顶级部门）</option>
              {parents.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              ))}
            </select>
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
