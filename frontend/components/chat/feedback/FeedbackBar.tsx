"use client";

/** 回答评价组件（点赞/点踩/原因/意见/撤销；中文状态；键盘与 aria-pressed）。
 *
 * 状态语义（spec agent-answer-feedback「中文交互状态与无障碍恢复」）：
 * - 只有服务端成功才显示「已保存」；等待显示「保存中」；
 * - 失败保留用户选择与意见，说明未保存并允许重试（复用原 operation_id）；
 * - 冲突（其他页面已修改）提示回读后重选；
 * - 无映射（服务端未确认回答）→ 不可评价。
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import {
  type AnswerMapping,
  type FeedbackRating,
  type FeedbackReasonCode,
  newOperationId,
  loadAnswerMappings,
  cachedAnswerByMessage,
  currentFeedbackEpoch,
  submitFeedback,
  feedbackEnabled,
  subscribeFeedbackPolicy,
} from "@/lib/feedback/client";
import { cn } from "@/lib/utils";

const REASON_LABELS: { code: FeedbackReasonCode; label: string }[] = [
  { code: "answer_incorrect", label: "回答有误" },
  { code: "irrelevant", label: "答非所问" },
  { code: "incomplete", label: "内容不完整" },
  { code: "slow", label: "响应太慢" },
  { code: "other", label: "其他原因" },
];

export type FeedbackBarProps = {
  mapping: AnswerMapping | null;
  /** 消息文本是否非空（空正文不可复制也不可评价） */
  hasContent: boolean;
  /** 企业/用户代次：变化时忽略迟到的异步结果 */
  epoch: number;
};

export function FeedbackBar({ mapping, hasContent, epoch }: FeedbackBarProps) {
  const enabled = useSyncExternalStore(subscribeFeedbackPolicy, feedbackEnabled, () => false);
  const [rating, setRating] = useState<FeedbackRating | null>(null);
  const [revision, setRevision] = useState(0);
  const [reasonCode, setReasonCode] = useState<FeedbackReasonCode | null>(null);
  const [comment, setComment] = useState("");
  const [showDetail, setShowDetail] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<"idle" | "saved" | "conflict" | "failed">("idle");
  const [feedbackable, setFeedbackable] = useState<boolean>(false);
  const operationRef = useRef<string>("");
  const intentRef = useRef<string>("");
  const answerRef = useRef(mapping?.answer_id);
  answerRef.current = mapping?.answer_id;
  const epochRef = useRef(epoch);

  useEffect(() => {
    epochRef.current = epoch;
  }, [epoch]);

  // 权威回读：mapping 变化时同步服务端当前状态
  useEffect(() => {
    setFeedbackable(Boolean(mapping?.feedbackable && mapping?.answer_id));
    setRating(mapping?.rating ?? null);
    setRevision(mapping?.revision ?? 0);
    setReasonCode(mapping?.reason_code ?? null);
    setStatus("idle");
    setShowDetail(false);
    setSaving(false);
    setComment("");
    operationRef.current = "";
  }, [mapping, epoch]);

  if (!hasContent || !enabled) return null;

  const disabled = !feedbackable || mapping === null;

  const submit = async (nextRating: FeedbackRating, withReason: FeedbackReasonCode | null, withComment: string) => {
    if (disabled || saving || !mapping) return;
    const intent = JSON.stringify([mapping.answer_id,nextRating,withReason,withComment,revision]);
    if (intentRef.current !== intent) { intentRef.current=intent; operationRef.current=""; }
    if (!operationRef.current) operationRef.current = newOperationId();
    const capturedEpoch = currentFeedbackEpoch();
    const answerId = mapping.answer_id;
    setSaving(true);
    const outcome = await submitFeedback({
      answerId: mapping.answer_id,
      rating: nextRating,
      reasonCode: withReason,
      comment: withComment.trim() ? withComment : null,
      expectedRevision: revision,
      operationId: operationRef.current,
    });
    if (epochRef.current !== epoch || capturedEpoch !== currentFeedbackEpoch() || answerRef.current !== answerId) return; // 迟到回调：不修改当前页面状态
    setSaving(false);
    if (outcome.ok) {
      setRating(outcome.rating);
      setRevision(outcome.revision);
      setStatus("saved");
      operationRef.current = ""; // 成功后下次是新操作
      if (nextRating === "negative") setShowDetail(false);

    } else if (outcome.reason === "conflict") {
      operationRef.current = "";
      setStatus("conflict");
      if (mapping.message_id) {
        await loadAnswerMappings([mapping.message_id], true, mapping.thread_id);
        if (capturedEpoch !== currentFeedbackEpoch() || answerRef.current !== answerId) return;
        const fresh = cachedAnswerByMessage(mapping.message_id, mapping.thread_id);
        if (fresh) { setRevision(fresh.revision); setRating(fresh.rating); }
      }
    } else {
      setStatus("failed"); // 保留选择与意见，允许重试
    }
  };

  const toggle = (target: "positive" | "negative") => {
    if (disabled) return;
    // 同评级再次点击 = 撤销（提交 none）
    const next: FeedbackRating = rating === target ? "none" : target;
    if (next === "negative" && rating !== "negative") {
      setShowDetail(true);
      setStatus("idle");
      return;
    }
    void submit(next, next === "negative" ? reasonCode : null, next === "negative" ? comment : "");
  };

  const saveDetail = () => {
    void submit("negative", reasonCode, comment);
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1 text-[11px]">
        <button
          type="button"
          onClick={() => toggle("positive")}
          disabled={disabled || saving}
          aria-pressed={rating === "positive"}
          aria-label="点赞"
          className={cn(
            "inline-flex items-center gap-1 rounded px-1.5 py-0.5 cursor-pointer focus-visible:outline-2 focus-visible:outline-ring transition-colors hover:bg-muted disabled:opacity-40",
            rating === "positive" ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground hover:text-foreground"
          )}
        >
          <ThumbsUpIcon className="size-3" />
          <span>{rating === "positive" ? "已赞" : "赞"}</span>
        </button>
        <button
          type="button"
          onClick={() => toggle("negative")}
          disabled={disabled || saving}
          aria-pressed={rating === "negative"}
          aria-label="点踩"
          className={cn(
            "inline-flex items-center gap-1 rounded px-1.5 py-0.5 cursor-pointer focus-visible:outline-2 focus-visible:outline-ring transition-colors hover:bg-muted disabled:opacity-40",
            rating === "negative" ? "text-red-500" : "text-muted-foreground hover:text-foreground"
          )}
        >
          <ThumbsDownIcon className="size-3" />
          <span>{rating === "negative" ? "已踩" : "踩"}</span>
        </button>
        {disabled ? (
          <span className="text-muted-foreground/70">该回答暂不可评价</span>
        ) : saving ? (
          <span className="text-muted-foreground" role="status">保存中…</span>
        ) : status === "saved" ? (
          <span className="text-emerald-600 dark:text-emerald-400" role="status">已保存</span>
        ) : null}
      </div>

      {status === "failed" && <span className="text-destructive text-xs" role="alert">尚未确认保存，已保留内容，请重试。</span>}
      {status === "conflict" && <span className="text-amber-600 text-xs" role="alert">已读取最新评价，请重新选择后保存。</span>}
            {showDetail && (
        <div className="rounded-lg border border-border/60 bg-muted/20 p-2.5 text-xs">
          <p className="mb-1.5 font-medium text-foreground">选择原因（可选）</p>
          <div className="mb-2 flex flex-wrap gap-1.5">
            {REASON_LABELS.map((item) => (
              <button
                key={item.code}
                type="button"
                onClick={() => setReasonCode(reasonCode === item.code ? null : item.code)}
                aria-pressed={reasonCode === item.code}
                className={cn(
                  "rounded-full border px-2 py-0.5 transition-colors",
                  reasonCode === item.code
                    ? "border-red-400/60 bg-red-500/10 text-red-500"
                    : "border-border/60 text-muted-foreground hover:text-foreground"
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={1000}
            rows={2}
            aria-label="意见（可选）"
            placeholder="补充意见（最多 1000 字，可选）"
            className="w-full resize-none rounded-md border border-border/60 bg-background px-2 py-1.5 text-xs outline-none focus:border-emerald-500/50"
          />
          <div className="mt-1.5 flex items-center gap-2">
            <button
              type="button"
              onClick={saveDetail}
              disabled={saving}
              className="rounded-md bg-foreground px-2.5 py-1 font-medium text-background disabled:opacity-50"
            >
              保存
            </button>
            <button
              type="button"
              onClick={() => setShowDetail(false)}
              className="text-muted-foreground hover:text-foreground"
            >
              收起
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
