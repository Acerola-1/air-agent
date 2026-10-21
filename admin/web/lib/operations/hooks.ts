"use client";

/** 运营查询生命周期：scope epoch + AbortController 丢弃旧响应。
 *
 * scopeKey 覆盖 user/tenant/context_version/permissions：A→B→A 时旧 A 请求的
 * 迟到结果也不能写回；企业切换由现有 TenantSwitcher 整页刷新兜底。
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DependencyList,
} from "react";

import { useSession } from "@/lib/session";
import type { ConsoleApiError } from "./client";

export type QueryState<T> = {
  data: T | null;
  loading: boolean;
  error: Error | null;
  reload: () => void;
};

export function useScopeKey(): string {
  const { ctx, user } = useSession();
  if (!ctx || !user) return "anonymous";
  return [
    user.id,
    ctx.current_tenant?.id ?? "none",
    ctx.context_version,
    ctx.permissions.slice().sort().join(","),
  ].join("|");
}

export function useOperationQuery<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: DependencyList
): QueryState<T> {
  const scopeKey = useScopeKey();
  const epoch = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const [result, setResult] = useState<{ scope: string; data: T } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((value) => value + 1), []);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);

  useEffect(() => {
    const current = ++epoch.current;
    controller.current?.abort();
    const local = new AbortController();
    controller.current = local;
    setLoading(true);
    setError(null);
    // scope 变化时先清空旧企业/旧用户数据，避免短暂显示他人内容。
    setResult(null);
    run(local.signal)
      .then((value) => {
        if (current !== epoch.current || local.signal.aborted) return;
        setResult({ scope: scopeKey, data: value });
      })
      .catch((err: unknown) => {
        if (current !== epoch.current || local.signal.aborted) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err : new Error("查询失败"));
      })
      .finally(() => {
        if (current === epoch.current && !local.signal.aborted) setLoading(false);
      });
    return () => local.abort();
  }, [run, scopeKey, tick]);

  // scope变更的首次render就禁止旧数据，不能等effect清理后才隐藏。
  return { data: result?.scope === scopeKey ? result.data : null, loading, error, reload };
}

export function isContextChanged(error: Error | null): boolean {
  return (
    !!error &&
    "code" in error &&
    (error as ConsoleApiError).code === "context_changed"
  );
}

export function errorCode(error: Error | null): string | undefined {
  return error && "code" in error ? (error as ConsoleApiError).code : undefined;
}
