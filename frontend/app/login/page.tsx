"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** 登录 / 注册页（简易本地账号密码）.
 *
 *  - 登录成功：后端签发 httpOnly cookie + 返回 { id, username }
 *  - 前端把 user.id 写 localStorage `air_agent_user_id`（与匿名 UUID 同一机制，
 *    线程 metadata.user_id 按账号隔离历史）
 *  - 未登录访问任何页面由 middleware 重定向到本页
 */
export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") || "/";
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 已登录直接跳走（防刷新停留在登录页）
  useEffect(() => {
    void fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => r.ok && router.replace("/"))
      .catch(() => {});
  }, [router]);

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      if (mode === "register" && password !== confirm) {
        setError("两次输入的密码不一致");
        return;
      }
      setLoading(true);
      try {
        const res = await fetch(`/api/auth/${mode}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, password }),
        });
        const data = (await res.json()) as { id?: string; username?: string; error?: string };
        if (!res.ok || !data.id) {
          setError(data.error ?? "操作失败，请重试");
          return;
        }
        // 登录/注册成功：用真实账号 id 覆盖匿名 UUID，历史按账号隔离
        try {
          localStorage.setItem("air_agent_user_id", data.id);
        } catch {
          /* localStorage 不可用时忽略 */
        }
        router.replace(next);
        router.refresh();
      } catch {
        setError("网络错误，请重试");
      } finally {
        setLoading(false);
      }
    },
    [mode, username, password, confirm, next, router],
  );

  return (
    <div className="bg-muted/20 flex min-h-dvh items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 inline-block h-10 w-10 rounded-lg bg-gradient-to-br from-emerald-500 to-cyan-500" aria-hidden />
          <h1 className="text-xl font-semibold">Air Agent</h1>
          <p className="text-muted-foreground mt-1 text-sm">空气质量智能助手 · 登录后使用</p>
        </div>

        <form
          onSubmit={submit}
          className="border-border/60 bg-background rounded-2xl border p-5 shadow-sm"
        >
          <div className="mb-4 flex rounded-lg border p-0.5">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => {
                  setMode(m);
                  setError(null);
                }}
                className={cn(
                  "flex-1 rounded-md py-1.5 text-sm font-medium transition-colors",
                  mode === m
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent",
                )}
              >
                {m === "login" ? "登录" : "注册"}
              </button>
            ))}
          </div>

          <label className="mb-1 block text-xs font-medium text-muted-foreground">用户名</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="3-32 位字母/数字"
            autoFocus
            autoComplete="username"
            className="border-border/60 focus:border-ring mb-3 h-10 w-full rounded-lg border bg-transparent px-3 text-sm outline-none"
          />

          <label className="mb-1 block text-xs font-medium text-muted-foreground">密码</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="至少 6 位"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            className="border-border/60 focus:border-ring mb-3 h-10 w-full rounded-lg border bg-transparent px-3 text-sm outline-none"
          />

          {mode === "register" && (
            <>
              <label className="mb-1 block text-xs font-medium text-muted-foreground">确认密码</label>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="再次输入密码"
                autoComplete="new-password"
                className="border-border/60 focus:border-ring mb-3 h-10 w-full rounded-lg border bg-transparent px-3 text-sm outline-none"
              />
            </>
          )}

          {error && (
            <p className="text-destructive mb-3 text-xs" role="alert">
              {error}
            </p>
          )}

          <Button type="submit" className="w-full" disabled={loading || !username || !password}>
            {loading ? "请稍候…" : mode === "login" ? "登录" : "注册并登录"}
          </Button>
        </form>

        <p className="text-muted-foreground mt-4 text-center text-xs">
          简易本地账号系统 · 账号数据保存在服务端 data/users.json
        </p>
      </div>
    </div>
  );
}
