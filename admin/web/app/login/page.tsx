"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { authApi } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function LoginPage() {
  const router = useRouter();
  const { refresh } = useSession();
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [recoveryHint, setRecoveryHint] = useState<string | null>(null);

  useEffect(() => {
    const url = new URL(window.location.href);
    const reason = url.searchParams.get("reason");
    const hints: Record<string, string> = {
      "session-expired": "登录已过期，请使用账号和密码重新登录。",
      "sso-failed": "统一登录未完成，请使用账号和密码登录，或重新发起统一登录。",
      "identity-unavailable": "身份服务暂时不可用，请稍后重试。",
    };
    if (reason && hints[reason]) {
      setRecoveryHint(hints[reason]);
      document.getElementById("account")?.focus();
      url.searchParams.delete("reason");
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    }
  }, []);


  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const result = await authApi.login(account.trim(), password);
      await refresh();
      // 多企业且未选择当前企业：进入选择页，不自动落到第一家
      if (result.needs_tenant_selection) {
        router.replace("/select-tenant");
        return;
      }
      // 从 window 读取跳转目标，避免 useSearchParams 带来的 Suspense 要求
      const next = new URLSearchParams(window.location.search).get("next");
      router.replace(next && next.startsWith("/") ? next : "/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="bg-primary text-primary-foreground mx-auto mb-3 flex size-10 items-center justify-center rounded-lg text-sm font-bold">
            AA
          </div>
          <h1 className="text-lg font-semibold">air_agent 后台管理</h1>
          <p className="text-muted-foreground mt-1 text-sm">请使用管理账号登录</p>
        </div>

        <Card className="py-6">
          <CardContent>
            <form onSubmit={onSubmit} className="space-y-4">
              {recoveryHint && <p role="status" className="text-muted-foreground text-sm">{recoveryHint}</p>}
              <div className="space-y-1.5">
                <Label htmlFor="account">账号</Label>
                <Input
                  id="account"
                  name="account"
                  autoComplete="username"
                  autoFocus
                  required
                  placeholder="3-32 位字母/数字/下划线/连字符"
                  value={account}
                  onChange={(e) => setAccount(e.target.value)}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password">密码</Label>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>

              {error && (
                <p className="text-destructive text-sm" role="alert">
                  {error}
                </p>
              )}

              <Button type="submit" className="w-full" disabled={submitting}>
                {submitting && <LoaderCircle className="size-4 animate-spin" />}
                {submitting ? "登录中…" : "登录"}
              </Button>
              <Button
                type="button"
                disabled={submitting}
                variant="outline"
                className="w-full"
                onClick={() => {
                  // I2 remote：SSO 发起（admin API 门面 303 中央授权）；state 由门面 HttpOnly cookie 绑定
                setError(null);
                setSubmitting(true);
                fetch("/api/auth/iam/start", { method: "POST" })
                  .then(async (res) => {
                    const data = await res.json();
                    if (!res.ok || typeof data.url !== "string") throw new Error(data.error ?? "统一登录暂不可用");
                    window.location.assign(data.url);
                  })
                  .catch((err) => { setError(err instanceof Error ? err.message : "统一登录暂不可用"); setSubmitting(false); });
              }}
              >
                使用统一身份登录
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
