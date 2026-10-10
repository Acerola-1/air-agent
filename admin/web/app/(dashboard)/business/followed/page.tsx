"use client";

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle, MapPin, Pencil, Search } from "lucide-react";

import { RegionPicker } from "@/components/region-picker";
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { regionsApi, usersApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { User } from "@/lib/types";

const PAGE_SIZE = 20;

export default function FollowedRegionsPage() {
  const { can } = useSession();
  const canManage = can("followed:manage");

  const [rows, setRows] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editing, setEditing] = useState<User | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await usersApi.list({
        page,
        size: PAGE_SIZE,
        q: keyword.trim() || undefined,
      });
      setRows(res.items);
      setTotal(res.total);

      // 表格里要显示城市名而不是编码，批量解析一次
      const allCodes = [...new Set(res.items.flatMap((row) => row.followed_region_codes))];
      const resolved: Record<string, string> = {};
      await Promise.all(
        allCodes.map(async (code) => {
          try {
            const res2 = await fetch(`/api/regions/${code}`, { credentials: "include" });
            if (res2.ok) resolved[code] = ((await res2.json()) as { name: string }).name;
          } catch {
            // 解析不出就显示编码
          }
        })
      );
      setNames((prev) => ({ ...prev, ...resolved }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, keyword]);

  useEffect(() => {
    void load();
  }, [load]);

  // 打开编辑时，用接口直接拉一次详情，避免依赖表格里可能过期的数据
  useEffect(() => {
    if (!editing) return;
    let cancelled = false;
    void usersApi
      .followedRegions(editing.id)
      .then((regions) => {
        if (cancelled) return;
        setCodes(regions.map((r) => r.code));
        setNames((prev) => {
          const next = { ...prev };
          for (const region of regions) next[region.code] = region.name;
          return next;
        });
      })
      .catch(() => {
        if (!cancelled) setCodes(editing.followed_region_codes);
      });
    return () => {
      cancelled = true;
    };
  }, [editing]);

  async function save() {
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      await usersApi.setFollowedRegions(editing.id, codes);
      setEditing(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">用户关注城市</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          每个用户最多 5 个关注城市，可选到区县。这些城市会作为上下文提供给对话助手。
        </p>
      </div>

      <div className="relative max-w-md">
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

      {error && <p className="text-destructive text-sm">{error}</p>}

      <Card className="py-0">
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>账号</TableHead>
                <TableHead>用户名</TableHead>
                <TableHead>关注城市</TableHead>
                {canManage && <TableHead className="text-right">操作</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && (
                <TableRow>
                  <TableCell colSpan={canManage ? 4 : 3} className="text-muted-foreground h-24 text-center">
                    加载中…
                  </TableCell>
                </TableRow>
              )}
              {!loading &&
                rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="font-mono text-xs">{row.account}</TableCell>
                    <TableCell className="font-medium">{row.display_name}</TableCell>
                    <TableCell>
                      {row.followed_region_codes.length ? (
                        <div className="flex flex-wrap gap-1">
                          {row.followed_region_codes.map((code) => (
                            <Badge key={code} variant="secondary" className="gap-1">
                              <MapPin className="size-3" />
                              {names[code] ?? code}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-xs">未设置</span>
                      )}
                    </TableCell>
                    {canManage && (
                      <TableCell className="text-right">
                        <Button variant="outline" size="sm" onClick={() => setEditing(row)}>
                          <Pencil className="size-3.5" />
                          设置
                        </Button>
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

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>设置关注城市</DialogTitle>
            <DialogDescription>
              {editing?.display_name}（<span className="font-mono">{editing?.account}</span>）
            </DialogDescription>
          </DialogHeader>
          <RegionPicker value={codes} onChange={setCodes} />
          {error && <p className="text-destructive text-sm">{error}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(null)}>
              取消
            </Button>
            <Button onClick={save} disabled={busy}>
              {busy && <LoaderCircle className="size-4 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
