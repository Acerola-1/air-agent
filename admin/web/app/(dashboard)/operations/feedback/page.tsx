"use client";

import { useCallback, useMemo, useRef, useState } from "react";

import { ContentReader } from "@/components/operations/ContentReader";
import { DataTable, type Column } from "@/components/operations/DataTable";
import { QueryToolbar } from "@/components/operations/QueryToolbar";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { operationsApi, newOperationId, ConsoleApiError } from "@/lib/operations/client";
import { useOperationQuery } from "@/lib/operations/hooks";
import {
  CASE_STATE_LABELS,
  formatDateTime,
  GRAPH_LABELS,
  RATING_LABELS,
  REASON_LABELS,
  shortId,
  type WindowPreset,
  windowQuery,
} from "@/lib/operations/query";
import type { ContentPage, FeedbackItem } from "@/lib/operations/types";
import { useSession } from "@/lib/session";

type Draft = {
  action: "start" | "resolve" | "reopen";
  note: string;
};

export default function OperationsFeedbackPage() {
  const { can } = useSession();
  const canManage = can("agent:feedback:manage");
  const canContent = can("agent:content:read");

  const [preset, setPreset] = useState<WindowPreset>("last_7_days");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [businessCode, setBusinessCode] = useState("");
  const [rating, setRating] = useState("");
  const [reason, setReason] = useState("");
  const [caseState, setCaseState] = useState("");
  const [cursor, setCursor] = useState("");
  const [selected, setSelected] = useState<FeedbackItem | null>(null);
  const [draft, setDraft] = useState<Draft>({ action: "start", note: "" });
  const [operation, setOperation] = useState<{ id: string; signature: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [contentKind, setContentKind] = useState<"comment" | "note" | null>(null);
  const [noteRef, setNoteRef] = useState<string | null>(null);
  const dialogTrigger = useRef<HTMLElement | null>(null);

  const load = useCallback(
    async (signal: AbortSignal) => {
      const window = windowQuery(preset, startDate, endDate);
      return operationsApi.feedback(
        {
          ...window,
          business_code: businessCode || undefined,
          rating: rating || undefined,
          reason: reason || undefined,
          case_state: caseState || undefined,
          cursor: cursor || undefined,
          limit: 50,
        },
        signal
      );
    },
    [preset, startDate, endDate, businessCode, rating, reason, caseState, cursor]
  );
  const { data, loading, error, reload } = useOperationQuery(load, [
    preset,
    startDate,
    endDate,
    businessCode,
    rating,
    reason,
    caseState,
    cursor,
  ]);
  const history = useOperationQuery(
    useCallback(
      async (signal: AbortSignal) => {
        if (!selected) return null;
        const [ratingHistory, caseHistory] = await Promise.all([
          operationsApi.feedbackHistory(selected.answer_id, { kind: "rating", limit: 20 }, signal),
          operationsApi.feedbackHistory(selected.answer_id, { kind: "case", limit: 20 }, signal),
        ]);
        return { ratingHistory, caseHistory };
      },
      [selected]
    ),
    [selected?.answer_id]
  );

  const businessOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of data?.items ?? []) {
      if (row.business_code) {
        seen.set(row.business_code, GRAPH_LABELS[row.business_code] ?? row.business_code);
      }
    }
    return Array.from(seen, ([value, label]) => ({ value, label }));
  }, [data?.items]);

  function openRow(row: FeedbackItem) {
    dialogTrigger.current = document.activeElement as HTMLElement | null;
    setSelected(row);
    setActionError(null);
    setNotice(null);
    setDraft({
      action:
        row.case_state === "resolved"
          ? "reopen"
          : row.case_state === "in_progress"
            ? "resolve"
            : "start",
      note: "",
    });
    setOperation(null);
  }

  async function submit() {
    if (!selected || busy) return;
    if (draft.action === "resolve" && !draft.note.trim()) {
      setActionError("处理完成必须填写结果说明");
      return;
    }
    if (draft.note && !canContent) {
      setActionError("填写处理说明需要受控正文权限");
      return;
    }
    const signature = `${selected.answer_id}|${draft.action}|${selected.case_revision}|${selected.feedback_revision}|${draft.note}`;
    const operationId =
      operation && operation.signature === signature ? operation.id : newOperationId();
    setOperation({ id: operationId, signature });
    setBusy(true);
    setActionError(null);
    try {
      const result = await operationsApi.feedbackAction(selected.answer_id, {
        operation_id: operationId,
        expected_revision: selected.case_revision,
        expected_feedback_revision: selected.feedback_revision,
        action: draft.action,
        note: draft.note.trim() ? draft.note : null,
      });
      setNotice(
        result.replayed
          ? "该操作已受理，已复用原结果。"
          : `处理状态已更新为 ${CASE_STATE_LABELS[result.state] ?? result.state}，修订 ${result.revision}。`
      );
      setSelected({
        ...selected,
        case_state: result.state,
        case_revision: result.revision,
        reviewed_feedback_revision: result.reviewed_feedback_revision,
        needs_review: result.needs_review,
        handled_at: result.handled_at,
        has_comment: selected.has_comment,
      });
      setDraft({
        // 保持“已处理”选择但不自动切到 reopen，避免下一次点击误重开。
        action: "resolve",
        note: "",
      });
      setOperation(null);
      reload();
      history.reload();
    } catch (err) {
      const message = err instanceof Error ? err.message : "处理失败";
      const code = err instanceof ConsoleApiError ? err.code : undefined;
      setActionError(
        code === "feedback_changed"
          ? `${message}（已保留草稿；请刷新后按最新评价重试）`
          : code === "revision_conflict"
            ? `${message}（已保留草稿）`
            : message
      );
      reload();
    } finally {
      setBusy(false);
    }
  }

  const loadContent = useCallback(
    async (offset: number, signal: AbortSignal): Promise<ContentPage> => {
      if (!selected || !contentKind) throw new Error("未选择正文");
      return operationsApi.feedbackContent(
        selected.answer_id,
        {
          kind: contentKind,
          ref: contentKind === "note" ? noteRef ?? undefined : undefined,
          offset,
          limit: 8000,
        },
        signal
      );
    },
    [selected, contentKind, noteRef]
  );

  const columns: Column<FeedbackItem>[] = [
    {
      key: "answer",
      label: "回答",
      render: (row) => (
        <div className="min-w-40">
          <button className="underline" onClick={() => openRow(row)}>
            {shortId(row.answer_id, 12)}
          </button>
          <div className="text-muted-foreground text-[11px]">
            {GRAPH_LABELS[row.business_code] ?? row.business_code} ·{" "}
            {shortId(row.user_id, 10)}
          </div>
        </div>
      ),
    },
    {
      key: "rating",
      label: "当前评价",
      render: (row) => RATING_LABELS[row.rating] ?? row.rating,
    },
    {
      key: "reason",
      label: "原因",
      render: (row) => (row.reason_code ? REASON_LABELS[row.reason_code] ?? row.reason_code : "—"),
    },
    {
      key: "case",
      label: "处理状态",
      render: (row) => (
        <span className={row.needs_review ? "text-destructive" : undefined}>
          {CASE_STATE_LABELS[row.case_state] ?? row.case_state}
          {row.needs_review ? " · 需复核" : ""}
        </span>
      ),
    },
    {
      key: "comment",
      label: "意见",
      render: (row) => (row.has_comment ? "有" : "—"),
    },
    {
      key: "confirmed",
      label: "回答确认时间",
      render: (row) => formatDateTime(row.confirmed_at),
    },
    {
      key: "handled",
      label: "处理时间",
      render: (row) => formatDateTime(row.handled_at),
    },
    {
      key: "action",
      label: "操作",
      render: (row) => (
        <button className="underline" onClick={() => openRow(row)}>
          处理
        </button>
      ),
    },
  ];

  const selectedHistory = history.data;
  const invalidForState =
    !!selected &&
    ((draft.action === "start" && selected.case_state !== "pending") ||
      (draft.action === "resolve" && selected.case_state === "resolved") ||
      (draft.action === "reopen" && selected.case_state !== "resolved"));

  return (
    <div className="space-y-4">
      <QueryToolbar
        preset={preset}
        onPreset={(value) => {
          setPreset(value);
          setCursor("");
        }}
        startDate={startDate}
        endDate={endDate}
        onStartDate={setStartDate}
        onEndDate={setEndDate}
        businessCode={businessCode}
        onBusinessCode={(value) => {
          setBusinessCode(value);
          setCursor("");
        }}
        businessOptions={businessOptions}
        onRefresh={reload}
        loading={loading}
      >
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">当前评价</span>
          <select
            className="bg-background h-8 rounded border px-2 text-sm"
            value={rating}
            onChange={(event) => {
              setRating(event.target.value);
              setCursor("");
            }}
          >
            <option value="">全部</option>
            <option value="negative">点踩</option>
            <option value="positive">点赞</option>
            <option value="none">已撤销</option>
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">原因</span>
          <select
            className="bg-background h-8 rounded border px-2 text-sm"
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
              setCursor("");
            }}
          >
            <option value="">全部</option>
            {Object.entries(REASON_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground block">处理状态</span>
          <select
            className="bg-background h-8 rounded border px-2 text-sm"
            value={caseState}
            onChange={(event) => {
              setCaseState(event.target.value);
              setCursor("");
            }}
          >
            <option value="">全部</option>
            {Object.entries(CASE_STATE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </QueryToolbar>

      {error && (
        <div className="text-destructive rounded border p-3 text-sm" role="alert">
          {error.message}
          <button className="underline" onClick={reload}>
            重试
          </button>
        </div>
      )}

      <DataTable
        caption="按回答确认时间窗口；满意率只由当前用户评价计算，处理状态独立"
        columns={columns}
        rows={data?.items ?? []}
        rowKey={(row) => row.answer_id}
        loading={loading}
      />

      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-muted-foreground text-xs">
          本页 {data?.items.length ?? 0} 条 · 截止 {formatDateTime(data?.meta.as_of)}
        </span>
        <div className="flex gap-2">
          <button
            className="rounded border px-2 py-1 text-xs disabled:opacity-50"
            disabled={!cursor}
            onClick={() => setCursor("")}
          >
            回到第一页
          </button>
          <button
            className="rounded border px-2 py-1 text-xs disabled:opacity-50"
            disabled={!data?.has_more || !data.next_cursor}
            onClick={() => setCursor(data?.next_cursor ?? "")}
          >
            下一页
          </button>
        </div>
      </div>

      <Dialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSelected(null);
            setOperation(null);
            setActionError(null);
            setNotice(null);
            const element = dialogTrigger.current;
            dialogTrigger.current = null;
            if (element) requestAnimationFrame(() => element.focus());
          }
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>反馈处理</DialogTitle>
            <DialogDescription>
              用户评价修订与运营处理修订独立；处理完成不自动代表问题已解决。
            </DialogDescription>
          </DialogHeader>
          {selected && (
            <div className="space-y-3 text-sm">
              <div className="grid gap-2 sm:grid-cols-2">
                <div>
                  当前评价：{RATING_LABELS[selected.rating] ?? selected.rating}
                  {selected.reason_code
                    ? ` · ${REASON_LABELS[selected.reason_code] ?? selected.reason_code}`
                    : ""}
                  （修订 {selected.feedback_revision}）
                </div>
                <div>
                  处理状态：{CASE_STATE_LABELS[selected.case_state] ?? selected.case_state}
                  （修订 {selected.case_revision}）
                </div>
                <div className="text-muted-foreground text-xs">
                  回答确认：{formatDateTime(selected.confirmed_at)}
                </div>
                <div className="text-muted-foreground text-xs">
                  处理时间：{formatDateTime(selected.handled_at)}
                </div>
              </div>
              {selected.needs_review && (
                <p className="text-destructive text-xs">
                  该回答有尚未复核的负面评价修订，需要复核。
                </p>
              )}
              {!canManage && (
                <p className="text-muted-foreground text-xs">
                  当前账号缺少反馈处理权限，只能查看；写操作会被服务端拒绝。
                </p>
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="xs"
                  variant={contentKind === "comment" ? "default" : "outline"}
                  disabled={!selected.has_comment || !canContent}
                  onClick={() => {
                    setNoteRef(null);
                    setContentKind("comment");
                  }}
                >
                  读取意见
                </Button>
                <Button
                  size="xs"
                  variant={contentKind === "note" ? "default" : "outline"}
                  disabled={!canContent}
                  onClick={() => {
                    setNoteRef(null);
                    setContentKind("note");
                  }}
                >
                  处理说明
                </Button>
                {!canContent && (
                  <span className="text-muted-foreground text-xs">正文读取需受控正文权限</span>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {(["start", "resolve", "reopen"] as const).map((action) => (
                  <Button
                    key={action}
                    size="xs"
                    variant={draft.action === action ? "default" : "outline"}
                    disabled={
                      !canManage ||
                      (action === "start" && selected.case_state !== "pending") ||
                      (action === "resolve" && selected.case_state === "resolved") ||
                      (action === "reopen" && selected.case_state !== "resolved")
                    }
                    onClick={() => {
                      setDraft((value) => ({ ...value, action }));
                      setOperation(null);
                    }}
                  >
                    {action === "start"
                      ? "开始处理"
                      : action === "resolve"
                        ? "标记已处理"
                        : "重新打开"}
                  </Button>
                ))}
              </div>
              <label className="space-y-1 text-xs">
                <span className="text-muted-foreground block">
                  处理说明（{draft.action === "resolve" ? "必填" : "可选"}，最多 1000 字符）
                </span>
                <textarea
                  className="bg-background min-h-24 w-full rounded border p-2 text-sm"
                  maxLength={1000}
                  value={draft.note}
                  onChange={(event) => {
                    setDraft((value) => ({ ...value, note: event.target.value }));
                    setOperation(null);
                  }}
                  placeholder="说明处理结果；不会写入普通日志或事件"
                />
                <span className="text-muted-foreground">{draft.note.length}/1000</span>
              </label>
              {actionError && (
                <p className="text-destructive text-xs" role="alert">
                  {actionError}
                </p>
              )}
              {notice && <p className="text-xs">{notice}</p>}
              <DialogFooter>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSelected(null)}
                  disabled={busy}
                >
                  关闭
                </Button>
                <Button size="sm" onClick={submit} disabled={busy || !canManage || invalidForState}>
                  {busy ? "提交中…" : "提交处理"}
                </Button>
              </DialogFooter>
              <div className="border-t pt-2">
                <h4 className="text-xs font-semibold">修订历史（无正文）</h4>
                {history.loading && (
                  <p className="text-muted-foreground text-xs">正在加载历史…</p>
                )}
                {selectedHistory && (
                  <div className="mt-1 grid gap-2 text-xs sm:grid-cols-2">
                    <div>
                      <div className="text-muted-foreground">用户评价</div>
                      <ul>
                        {selectedHistory.ratingHistory.items.map((item, index) => (
                          <li key={String(item.id ?? index)}>
                            {String(item.rating)}（修订 {String(item.result_revision)}）
                          </li>
                        ))}
                        {selectedHistory.ratingHistory.items.length === 0 && <li>—</li>}
                      </ul>
                    </div>
                    <div>
                      <div className="text-muted-foreground">运营处理</div>
                      <ul>
                        {selectedHistory.caseHistory.items.map((item, index) => (
                          <li key={String(item.id ?? index)}>
                            {String(item.action)} → {String(item.to_state)}（修订{" "}
                            {String(item.result_revision)}）
                          </li>
                        ))}
                        {selectedHistory.caseHistory.items.length === 0 && <li>—</li>}
                      </ul>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <ContentReader
        open={contentKind !== null}
        onOpenChange={(open) => {
          if (!open) {
            setContentKind(null);
            setNoteRef(null);
          }
        }}
        title={contentKind === "comment" ? "用户意见" : "处理说明"}
        description="精确引用分页；未选择备注时只显示引用列表，不预取正文。"
        loadPage={loadContent}
        onSelectRef={(ref) => setNoteRef(ref)}
      />
    </div>
  );
}
