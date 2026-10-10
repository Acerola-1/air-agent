"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { auditApi } from "@/lib/api";
import type { AuditLog } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const PAGE_SIZE = 20;

const ACTION_LABEL: Record<string, string> = {
  "auth.login": "登录",
  "auth.logout": "登出",
  "user.create": "新建用户",
  "user.update": "修改用户",
  "user.delete": "删除用户",
  "user.reset_password": "重置密码",
  "user.assign_role": "分配角色",
  "user.set_followed_regions": "设置关注城市",
  "profile.set_followed_regions": "用户改自己的关注城市",
  "role.create": "新建角色",
  "role.update": "修改角色",
  "role.delete": "删除角色",
  "role.assign_permission": "配置角色权限",
};

export default function AuditPage() {
  const [rows, setRows] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await auditApi.list({ page, size: PAGE_SIZE, action: action || undefined });
      setRows(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载审计日志失败");
    } finally {
      setLoading(false);
    }
  }, [page, action]);

  useEffect(() => {
    void load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">审计日志</h1>
          <p className="text-muted-foreground mt-1 text-sm">共 {total} 条操作记录</p>
        </div>
        <Select
          className="w-52"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部动作</option>
          {Object.entries(ACTION_LABEL).map(([code, label]) => (
            <option key={code} value={code}>
              {label}
            </option>
          ))}
        </Select>
      </div>

      {error && <p className="text-destructive text-sm">{error}</p>}

      <Card className="py-0">
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>操作人</TableHead>
                <TableHead>动作</TableHead>
                <TableHead>对象</TableHead>
                <TableHead>来源 IP</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground h-24 text-center">
                    加载中…
                  </TableCell>
                </TableRow>
              )}
              {!loading && rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground h-24 text-center">
                    暂无记录
                  </TableCell>
                </TableRow>
              )}
              {!loading &&
                rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="text-muted-foreground text-xs whitespace-nowrap">
                      {formatDateTime(row.created_at)}
                    </TableCell>
                    <TableCell>
                      {row.actor_account ? (
                        <span className="font-mono text-xs">{row.actor_account}</span>
                      ) : (
                        // 冗余存的账号快照保证用户被删后仍能追溯；真正取不到才显示未知
                        <span className="text-muted-foreground text-xs">未知（账号已删除）</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{ACTION_LABEL[row.action] ?? row.action}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground font-mono text-[11px]">
                      {row.target_type ? `${row.target_type}:${row.target_id?.slice(0, 8) ?? ""}` : "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">{row.ip ?? "—"}</TableCell>
                    <TableCell className="text-right">
                      {Object.keys(row.detail ?? {}).length > 0 && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                        >
                          {expanded === row.id ? "收起" : "详情"}
                        </Button>
                      )}
                      {expanded === row.id && (
                        <pre className="bg-muted mt-2 max-w-md overflow-x-auto rounded-md p-2 text-left text-[11px]">
                          {JSON.stringify(row.detail, null, 2)}
                        </pre>
                      )}
                    </TableCell>
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
    </div>
  );
}
