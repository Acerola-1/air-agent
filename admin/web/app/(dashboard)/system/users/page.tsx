"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, Pencil, Plus, Search, Trash2 } from "lucide-react";

import {
  CreateUserDialog,
  DeleteUserDialog,
  EditUserDialog,
  ResetPasswordDialog,
} from "@/components/user-dialogs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { rolesApi, usersApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Role, User } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const PAGE_SIZE = 20;

export default function UsersPage() {
  const { can, user: currentUser } = useSession();

  const [rows, setRows] = useState<User[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");
  const [roleCode, setRoleCode] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [resetting, setResetting] = useState<User | null>(null);
  const [deleting, setDeleting] = useState<User | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await usersApi.list({
        page,
        size: PAGE_SIZE,
        q: keyword.trim() || undefined,
        is_active: status === "" ? undefined : status === "active",
        role_code: roleCode || undefined,
      });
      setRows(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载用户失败");
    } finally {
      setLoading(false);
    }
  }, [page, keyword, status, roleCode]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (can("role:read")) void rolesApi.list().then(setRoles).catch(() => setRoles([]));
  }, [can]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const canEdit = can("user:update");
  const canAssignRole = can("user:assign_role");
  const canManageCities = can("followed:manage");
  const showActions = canEdit || canAssignRole || canManageCities || can("user:reset_password") || can("user:delete");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">用户管理</h1>
          <p className="text-muted-foreground mt-1 text-sm">共 {total} 个账号</p>
        </div>
        {can("user:create") && (
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="size-4" />
            新建用户
          </Button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-56 flex-1">
          <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
          <Input
            className="pl-8"
            placeholder="搜索账号或用户名"
            value={keyword}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <Select
          className="w-32"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部状态</option>
          <option value="active">已启用</option>
          <option value="inactive">已停用</option>
        </Select>
        {can("role:read") && (
          <Select
            className="w-40"
            value={roleCode}
            onChange={(e) => {
              setRoleCode(e.target.value);
              setPage(1);
            }}
          >
            <option value="">全部角色</option>
            {roles.map((role) => (
              <option key={role.id} value={role.code}>
                {role.name}
              </option>
            ))}
          </Select>
        )}
      </div>

      {error && <p className="text-destructive text-sm">{error}</p>}

      <Card className="py-0">
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>账号</TableHead>
                <TableHead>用户名</TableHead>
                <TableHead>角色</TableHead>
                <TableHead>关注城市</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>最后登录</TableHead>
                {showActions && <TableHead className="text-right">操作</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && (
                <TableRow>
                  <TableCell colSpan={showActions ? 7 : 6} className="text-muted-foreground h-24 text-center">
                    加载中…
                  </TableCell>
                </TableRow>
              )}
              {!loading && rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={showActions ? 7 : 6} className="text-muted-foreground h-24 text-center">
                    没有匹配的用户
                  </TableCell>
                </TableRow>
              )}
              {!loading &&
                rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="font-mono text-xs">{row.account}</TableCell>
                    <TableCell className="font-medium">
                      {row.display_name}
                      {row.id === currentUser?.id && (
                        <Badge variant="muted" className="ml-2">
                          我
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {row.roles.length ? (
                        <div className="flex flex-wrap gap-1">
                          {row.roles.map((role) => (
                            <Badge key={role.id} variant="secondary">
                              {role.name}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-xs">无角色</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {row.followed_region_codes.length ? (
                        <span className="text-sm">{row.followed_region_codes.length} 个</span>
                      ) : (
                        <span className="text-muted-foreground text-xs">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      {row.is_active ? (
                        <Badge variant="success">已启用</Badge>
                      ) : (
                        <Badge variant="muted">已停用</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {row.last_login_at ? formatDateTime(row.last_login_at) : "从未登录"}
                    </TableCell>
                    {showActions && (
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          {canEdit && (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              title="编辑"
                              onClick={() => setEditing(row)}
                            >
                              <Pencil className="size-4" />
                            </Button>
                          )}
                          {can("user:reset_password") && (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              title="重置密码"
                              onClick={() => setResetting(row)}
                            >
                              <KeyRound className="size-4" />
                            </Button>
                          )}
                          {can("user:delete") && row.id !== currentUser?.id && (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              title="删除"
                              className="text-destructive hover:text-destructive"
                              onClick={() => setDeleting(row)}
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    )}
                  </TableRow>
                ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground text-sm">
            第 {page} / {totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      )}

      <CreateUserDialog open={createOpen} onOpenChange={setCreateOpen} onDone={load} />
      <EditUserDialog
        user={editing}
        roles={roles}
        canAssignRole={canAssignRole}
        canManageCities={canManageCities}
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        onDone={load}
      />
      <ResetPasswordDialog
        user={resetting}
        open={resetting !== null}
        onOpenChange={(open) => !open && setResetting(null)}
        onDone={load}
      />
      <DeleteUserDialog
        user={deleting}
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        onDone={load}
      />
    </div>
  );
}
