"use client";

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { RegionPicker } from "@/components/region-picker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { authApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import { formatDateTime } from "@/lib/utils";

export default function ProfilePage() {
  const { user, ctx, refresh } = useSession();
  const [codes, setCodes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const regions = await authApi.myFollowedRegions();
      setCodes(regions.map((r) => r.code));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await authApi.setMyFollowedRegions(codes);
      setSaved(true);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">我的资料</h1>
        <p className="text-muted-foreground mt-1 text-sm">管理你自己的账号信息与关注城市</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">账号信息</CardTitle>
          <CardDescription>账号与角色由管理员维护，这里只能查看。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="flex gap-3">
            <span className="text-muted-foreground w-20 shrink-0">账号</span>
            <span className="font-mono">{user?.account}</span>
          </div>
          <div className="flex gap-3">
            <span className="text-muted-foreground w-20 shrink-0">用户名</span>
            <span>{user?.display_name}</span>
          </div>
          <div className="flex gap-3">
            <span className="text-muted-foreground w-20 shrink-0">角色</span>
            <div className="flex flex-wrap gap-1">
              {user?.roles.length ? (
                user.roles.map((role) => (
                  <Badge key={role.id} variant="secondary">
                    {role.name}
                  </Badge>
                ))
              ) : (
                <span className="text-muted-foreground">无角色</span>
              )}
            </div>
          </div>
          <div className="flex gap-3">
            <span className="text-muted-foreground w-20 shrink-0">注册时间</span>
            <span>{formatDateTime(user?.created_at)}</span>
          </div>
          <div className="flex gap-3">
            <span className="text-muted-foreground w-20 shrink-0">最后登录</span>
            <span>{user?.last_login_at ? formatDateTime(user.last_login_at) : "—"}</span>
          </div>
          <div className="flex gap-3">
            <span className="text-muted-foreground w-20 shrink-0">权限数</span>
            <span className="tabular-nums">{ctx?.permissions.length ?? 0}</span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">我的关注城市</CardTitle>
          <CardDescription>
            最多 5 个，可选到区县。设置后对话助手会结合这些城市理解你的关注范围。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading ? (
            <p className="text-muted-foreground text-sm">加载中…</p>
          ) : (
            <RegionPicker value={codes} onChange={setCodes} />
          )}

          {error && <p className="text-destructive text-sm">{error}</p>}
          {saved && <p className="text-sm text-emerald-600">已保存</p>}

          <Button onClick={save} disabled={busy || loading}>
            {busy && <LoaderCircle className="size-4 animate-spin" />}
            保存关注城市
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
