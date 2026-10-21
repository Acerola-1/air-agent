"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { DataTable, type Column } from "@/components/operations/DataTable";
import { TrendBars } from "@/components/operations/TrendBars";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { demandApi, newOperationId } from "@/lib/operations/demand-client";
import { useOperationQuery } from "@/lib/operations/hooks";
import { formatDateTime, formatNumber, formatPercent, shortId } from "@/lib/operations/query";
import type { QuestionDetail, SourceItem } from "@/lib/operations/demand-types";
import { useSession } from "@/lib/session";

export default function InsightQuestionDetailPage() {
  const params = useParams<{ clusterId: string }>();
  const clusterId = params.clusterId;
  const { can } = useSession();
  const canManage = can(
    "agent:operations:read",
    "agent:content:read",
    "agent:insights:manage"
  );
  const [newName, setNewName] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  const [assignTarget, setAssignTarget] = useState("");
  const [assignName, setAssignName] = useState("");
  const [splitName, setSplitName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const detail = useOperationQuery<QuestionDetail>(
    useCallback(
      (signal: AbortSignal) =>
        demandApi.question(clusterId, { window: "last_30_days" }, signal),
      [clusterId]
    ),
    [clusterId]
  );
  const sources = useOperationQuery(
    useCallback(
      (signal: AbortSignal) => demandApi.sources(clusterId, { limit: 200 }, signal),
      [clusterId]
    ),
    [clusterId]
  );

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  function toggle(intentId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(intentId)) next.delete(intentId);
      else next.add(intentId);
      return next;
    });
  }

  async function act(
    action: "rename" | "assign" | "merge" | "split",
    payload: Record<string, unknown>
  ) {
    if (!detail.data || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await demandApi.questionAction(clusterId, {
        operation_id: newOperationId(),
        expected_revision:
          action === "assign"
            ? detail.data.meta.assignment_revision
            : detail.data.revision,
        action,
        ...payload,
      } as never);
      setNotice(
        result.replayed ? "操作已受理，复用原结果。" : `操作 ${action} 已完成，正在刷新。`
      );
      detail.reload();
      sources.reload();
    } catch (err) {
      const message = err instanceof Error ? err.message : "操作失败";
      const code =
        err && typeof err === "object" && "code" in err
          ? String((err as { code?: string }).code ?? "")
          : "";
      setError(code ? `${message}（${code}）` : message);
    } finally {
      setBusy(false);
    }
  }

  async function previewAndSplit() {
    if (!detail.data || busy) return;
    const available = (sources.data?.items ?? []).map((item) => item.intent_id);
    const splitIds = selectedIds.filter((id) => available.includes(id));
    if (!splitName.trim()) {
      setError("请填写拆出的新标准问题名称");
      return;
    }
    if (splitIds.length === 0 || splitIds.length === available.length) {
      setError("拆分必须选择部分来源意图，且不能全选");
      return;
    }
    const remaining = available.filter((id) => !splitIds.includes(id));
    const groups = [
      { name: detail.data.canonical_name, intent_ids: remaining },
      { name: splitName.trim(), intent_ids: splitIds },
    ];
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const preview = await demandApi.questionAction(clusterId, {
        operation_id: newOperationId(),
        expected_revision: detail.data.revision,
        action: "split",
        preview: true,
        groups,
      } as never);
      // 预览期间切企业或离开详情，不得用新scope声明继续旧拆分。
      if (!mounted.current) return;
      const token = (preview.result as { preview_token?: string })?.preview_token;
      if (!token) {
        setError("预览未返回 token，请刷新后重试");
        return;
      }
      await demandApi.questionAction(clusterId, {
        operation_id: newOperationId(),
        expected_revision: detail.data.revision,
        action: "split",
        preview: false,
        preview_token: token,
        groups,
      } as never);
      setNotice("拆分已完成，正在刷新。");
      setSelected(new Set());
      setSplitName("");
      detail.reload();
      sources.reload();
    } catch (err) {
      const message = err instanceof Error ? err.message : "拆分失败";
      const code =
        err && typeof err === "object" && "code" in err
          ? String((err as { code?: string }).code ?? "")
          : "";
      setError(code ? `${message}（${code}）` : message);
    } finally {
      setBusy(false);
    }
  }

  const sourceColumns: Column<SourceItem>[] = [
    {
      key: "select",
      label: "选择",
      render: (row) => (
        <button
          type="button"
          role="checkbox"
          aria-checked={selected.has(row.intent_id)}
          aria-label={`选择意图 ${row.intent_id}`}
          onClick={() => toggle(row.intent_id)}
          disabled={!canManage}
          className="bg-background rounded border px-2 py-1 text-xs disabled:opacity-50"
        >
          {selected.has(row.intent_id) ? "已选" : "选择"}
        </button>
      ),
    },
    { key: "intent_summary", label: "脱敏意图" },
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
      key: "content_path",
      label: "原问题",
      render: (row) => (
        <Link className="underline underline-offset-2" href={row.content_path}>
          授权查看
        </Link>
      ),
    },
  ];

  const data = detail.data;
  const stats = data?.statistics;
  const trend = (data?.trend ?? []).map((point) => ({
    label: point.day,
    value: point.intents,
  }));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link className="text-muted-foreground text-xs" href="/operations/insights">
            ← 返回需求洞察
          </Link>
          <h1 className="text-xl font-semibold">{data?.canonical_name ?? "标准问题"}</h1>
          <p className="text-muted-foreground text-sm">
            {data ? `${data.action} / ${data.object} / ${data.time_kind}` : "加载中…"}
            {data?.topic_name ? ` · 主题：${data.topic_name}` : ""}
          </p>
        </div>
        <div className="text-muted-foreground text-xs">
          稳定 ID：{shortId(data?.cluster_id, 12)}
          {data ? ` · 修订 ${data.revision}` : ""}
        </div>
      </div>

      {detail.error && (
        <p className="text-destructive text-sm" role="alert">
          {detail.error.message}
        </p>
      )}
      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}
      {notice && <p className="text-sm text-emerald-700">{notice}</p>}
      {data?.status === "merged" && (
        <p className="bg-muted rounded border p-3 text-sm">
          该标准问题已合并到 {data.merged_into}，统计请查看目标。
        </p>
      )}
      {data && !data.statistics && data.status !== "merged" && (
        <p className="bg-muted rounded border p-3 text-sm">
          当前窗口没有该标准问题的统计；不代表零需求，可能尚未处理。
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-muted-foreground text-xs font-medium">原子意图</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {formatNumber(stats?.atomic_intent_count)}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-muted-foreground text-xs font-medium">去重请求</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {formatNumber(stats?.distinct_requests)}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-muted-foreground text-xs font-medium">独立用户</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {formatNumber(stats?.distinct_users)}
          </CardContent>
        </Card>
      </div>

      <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span>占比：{formatPercent(stats?.share.value)}</span>
        <span>
          请求级负反馈：{formatNumber(stats?.request_level_negative_feedback)}（不推断每个意图错误）
        </span>
        <span>覆盖：{formatPercent(typeof data?.coverage.coverage === "number" ? data.coverage.coverage : null)}</span>
        <span>摘要来源：{data?.summary_source ?? "deterministic"}</span>
      </div>

      {data?.summary && <p className="bg-muted rounded border p-3 text-sm">{data.summary}</p>}

      {trend.length > 0 && <TrendBars title="每日原子意图" points={trend} />}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">约束与参数</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          <pre className="bg-muted overflow-x-auto rounded p-2 text-xs">
            {JSON.stringify(data?.constraints ?? {}, null, 2)}
          </pre>
        </CardContent>
      </Card>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-medium">授权来源（脱敏意图）</h2>
          <span className="text-muted-foreground text-xs">
            原问题正文按内容权限、精确版本和保留期再读取；本页不预取全文。
          </span>
        </div>
        <DataTable
          caption="来源按受理时间倒序；勾选意图用于指派或拆分。"
          columns={sourceColumns}
          rows={sources.data?.items ?? []}
          rowKey={(row) => `${row.request_id}-${row.intent_id}`}
          loading={sources.loading}
          empty="没有可读来源；可能未处理、已删除或超出正文权限"
        />
        {sources.error && (
          <p className="text-destructive text-sm" role="alert">
            {sources.error.message}
          </p>
        )}
      </div>

      {canManage && data && data.status !== "merged" && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">人工纠错</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <label className="block text-xs font-medium" htmlFor="rename-input">
                改名（稳定 ID 不变）
              </label>
              <div className="flex gap-2">
                <Input
                  id="rename-input"
                  value={newName}
                  placeholder={data.canonical_name}
                  onChange={(event) => setNewName(event.target.value)}
                />
                <Button
                  size="sm"
                  disabled={busy || !newName.trim()}
                  onClick={() => act("rename", { new_name: newName.trim() })}
                >
                  保存改名
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <label className="block text-xs font-medium" htmlFor="merge-input">
                合并到目标标准问题 ID
              </label>
              <div className="flex gap-2">
                <Input
                  id="merge-input"
                  value={mergeTarget}
                  placeholder="目标 cluster UUID"
                  onChange={(event) => setMergeTarget(event.target.value)}
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !mergeTarget.trim()}
                  onClick={() => act("merge", { target_cluster_id: mergeTarget.trim() })}
                >
                  合并
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <label className="block text-xs font-medium" htmlFor="assign-target">
                指派选中意图到已有标准问题
              </label>
              <div className="flex gap-2">
                <Input
                  id="assign-target"
                  value={assignTarget}
                  placeholder="目标 cluster UUID"
                  onChange={(event) => setAssignTarget(event.target.value)}
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !assignTarget.trim() || selectedIds.length === 0}
                  onClick={() =>
                    act("assign", {
                      intent_ids: selectedIds,
                      target_cluster_id: assignTarget.trim(),
                    })
                  }
                >
                  指派
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <label className="block text-xs font-medium" htmlFor="assign-name">
                或为选中意图新建标准问题
              </label>
              <div className="flex gap-2">
                <Input
                  id="assign-name"
                  value={assignName}
                  placeholder="新标准问题名称"
                  onChange={(event) => setAssignName(event.target.value)}
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !assignName.trim() || selectedIds.length === 0}
                  onClick={() =>
                    act("assign", {
                      intent_ids: selectedIds,
                      new_cluster_name: assignName.trim(),
                    })
                  }
                >
                  新建并指派
                </Button>
              </div>
            </div>

            <div className="space-y-2 md:col-span-2">
              <label className="block text-xs font-medium" htmlFor="split-name">
                拆分选中意图为新标准问题（其余保留在当前簇）
              </label>
              <div className="flex flex-wrap gap-2">
                <Input
                  id="split-name"
                  value={splitName}
                  placeholder="拆出的新标准问题名称"
                  onChange={(event) => setSplitName(event.target.value)}
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !splitName.trim() || selectedIds.length === 0}
                  onClick={previewAndSplit}
                >
                  预览并提交拆分
                </Button>
              </div>
              <p className="text-muted-foreground text-xs">
                拆分必须覆盖全部来源意图；预览 token 绑定企业、分析版本、来源集合与修订。
              </p>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
