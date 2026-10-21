"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { DataTable, type Column } from "@/components/operations/DataTable";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { demandApi, newOperationId } from "@/lib/operations/demand-client";
import { useOperationQuery } from "@/lib/operations/hooks";
import { formatDateTime, formatNumber } from "@/lib/operations/query";
import type { GapItem, UnclassifiedItem } from "@/lib/operations/demand-types";
import { useSession } from "@/lib/session";

export default function UnclassifiedInsightsPage() {
  const { can } = useSession();
  const canManage = can(
    "agent:operations:read",
    "agent:content:read",
    "agent:insights:manage"
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [target, setTarget] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const data = useOperationQuery(
    useCallback(
      (signal: AbortSignal) =>
        demandApi.unclassified({ window: "last_30_days", limit: 200 }, signal),
      []
    ),
    []
  );

  const selectedIds = Array.from(selected);

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function assign(useTarget: boolean) {
    if (busy || selectedIds.length === 0) return;
    if (useTarget && !target.trim()) {
      setError("请填写目标标准问题 ID");
      return;
    }
    if (!useTarget && !newName.trim()) {
      setError("请填写新标准问题名称");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      // 指派动作以归属修订做 CAS；先从元数据读取当前 assignment_revision。
      const revision = data.data?.meta.assignment_revision ?? 0;
      const result = await demandApi.questionAction(
        "00000000-0000-0000-0000-000000000000",
        {
          operation_id: newOperationId(),
          expected_revision: revision,
          action: "assign",
          intent_ids: selectedIds,
          target_cluster_id: useTarget ? target.trim() : null,
          new_cluster_name: useTarget ? null : newName.trim(),
        } as never
      );
      setNotice(result.replayed ? "指派已受理，复用原结果。" : "指派已完成，正在刷新。");
      setSelected(new Set());
      data.reload();
    } catch (err) {
      const message = err instanceof Error ? err.message : "指派失败";
      const code =
        err && typeof err === "object" && "code" in err
          ? String((err as { code?: string }).code ?? "")
          : "";
      setError(code ? `${message}（${code}）` : message);
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<UnclassifiedItem>[] = [
    {
      key: "select",
      label: "选择",
      render: (row) => (
        <button
          type="button"
          role="checkbox"
          aria-checked={selected.has(row.intent_id)}
          aria-label={`选择未归类意图 ${row.intent_id}`}
          onClick={() => toggle(row.intent_id)}
          disabled={!canManage}
          className="bg-background rounded border px-2 py-1 text-xs disabled:opacity-50"
        >
          {selected.has(row.intent_id) ? "已选" : "选择"}
        </button>
      ),
    },
    { key: "intent_summary", label: "脱敏意图" },
    { key: "unclassified_reason", label: "未归类原因" },
    { key: "action", label: "动作" },
    { key: "object", label: "对象" },
    {
      key: "slots",
      label: "参数",
      render: (row) =>
        Object.entries(row.slots)
          .filter(([, value]) => value !== null && value !== "")
          .map(([key, value]) => `${key}=${String(value)}`)
          .join("，") || "—",
    },
    { key: "confidence", label: "置信度", render: (row) => row.confidence.toFixed(3) },
    { key: "admitted_at", label: "受理时间", render: (row) => formatDateTime(row.admitted_at) },
    {
      key: "content",
      label: "原问题",
      render: (row) => (
        <Link className="underline underline-offset-2" href={row.content_path}>
          授权查看
        </Link>
      ),
    },
  ];

  const gapColumns: Column<GapItem>[] = [
    { key: "request_id", label: "请求" },
    { key: "reason", label: "缺口原因" },
    { key: "attempts", label: "尝试", render: (row) => formatNumber(row.attempts) },
    { key: "admitted_at", label: "受理时间", render: (row) => formatDateTime(row.admitted_at) },
    { key: "detail", label: "安全说明", render: (row) => row.detail ?? "—" },
  ];

  return (
    <div className="space-y-4">
      <div>
        <Link className="text-muted-foreground text-xs" href="/operations/insights">
          ← 返回需求洞察
        </Link>
        <h1 className="text-xl font-semibold">未归类与缺源</h1>
        <p className="text-muted-foreground text-sm">
          低置信/歧义进入未归类；未确认、缺正文、过期与删除分别列，不以 0 补齐。
        </p>
      </div>

      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}
      {notice && <p className="text-sm text-emerald-700">{notice}</p>}
      {data.error && (
        <p className="text-destructive text-sm" role="alert">
          {data.error.message}
        </p>
      )}

      <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span>未归类意图：{formatNumber(data.data?.meta.unclassified_intents)}</span>
        <span>缺源：{formatNumber(data.data?.meta.missing_content_requests)}</span>
        <span>待确认：{formatNumber(data.data?.meta.pending_confirmation_requests)}</span>
        <span>归属修订：{data.data?.meta.assignment_revision ?? "—"}</span>
      </div>

      <DataTable
        caption="未归类意图（运营+正文权限）；勾选后可指派到已有簇或新建簇。"
        columns={columns}
        rows={data.data?.items ?? []}
        rowKey={(row) => row.intent_id}
        loading={data.loading}
        empty="当前窗口没有未归类意图"
      />

      {canManage && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">指派选中意图</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-3">
            <label className="space-y-1 text-xs">
              <span className="text-muted-foreground block">目标标准问题 ID</span>
              <Input value={target} onChange={(event) => setTarget(event.target.value)} />
            </label>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || selectedIds.length === 0}
              onClick={() => assign(true)}
            >
              指派到已有
            </Button>
            <label className="space-y-1 text-xs">
              <span className="text-muted-foreground block">或新建名称</span>
              <Input value={newName} onChange={(event) => setNewName(event.target.value)} />
            </label>
            <Button
              size="sm"
              disabled={busy || selectedIds.length === 0}
              onClick={() => assign(false)}
            >
              新建并指派
            </Button>
            <span className="text-muted-foreground text-xs">已选 {selectedIds.length} 项</span>
          </CardContent>
        </Card>
      )}

      <div className="space-y-2">
        <h2 className="text-sm font-medium">缺源/待确认队列</h2>
        <DataTable
          caption="未确认受理、缺正文、过期/删除、敏感/上下文不足分别记录；worker 会重看可恢复缺口。"
          columns={gapColumns}
          rows={data.data?.gaps ?? []}
          rowKey={(row) => `${row.request_id}-${row.reason}`}
          loading={data.loading}
          empty="没有缺口"
        />
      </div>
    </div>
  );
}
