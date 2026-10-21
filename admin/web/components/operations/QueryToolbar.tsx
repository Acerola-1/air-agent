"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { WINDOW_OPTIONS, type WindowPreset } from "@/lib/operations/query";

type Props = {
  preset: WindowPreset;
  onPreset: (value: WindowPreset) => void;
  startDate: string;
  endDate: string;
  onStartDate: (value: string) => void;
  onEndDate: (value: string) => void;
  businessCode: string;
  onBusinessCode: (value: string) => void;
  businessOptions: Array<{ value: string; label: string }>;
  onRefresh: () => void;
  loading?: boolean;
  children?: React.ReactNode;
};

/** 共享查询条：业务日窗口、业务过滤、刷新；不把正文/搜索词放 URL。 */
export function QueryToolbar({
  preset,
  onPreset,
  startDate,
  endDate,
  onStartDate,
  onEndDate,
  businessCode,
  onBusinessCode,
  businessOptions,
  onRefresh,
  loading,
  children,
}: Props) {
  return (
    <div className="bg-muted/40 flex flex-wrap items-end gap-3 rounded-lg border p-3">
      <label className="space-y-1 text-xs">
        <span className="text-muted-foreground block">业务日窗口</span>
        <select
          className="bg-background h-8 rounded border px-2 text-sm"
          value={preset}
          onChange={(event) => onPreset(event.target.value as WindowPreset)}
        >
          {WINDOW_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      {preset === "custom" && (
        <>
          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground block">开始日期（含）</span>
            <input
              type="date"
              className="bg-background h-8 rounded border px-2 text-sm"
              value={startDate}
              onChange={(event) => onStartDate(event.target.value)}
              aria-label="开始日期，包含当天"
            />
          </label>
          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground block">结束日期（含）</span>
            <input
              type="date"
              className="bg-background h-8 rounded border px-2 text-sm"
              value={endDate}
              onChange={(event) => onEndDate(event.target.value)}
              aria-label="结束日期，包含当天"
            />
          </label>
        </>
      )}
      <label className="space-y-1 text-xs">
        <span className="text-muted-foreground block">业务</span>
        <select
          className="bg-background h-8 max-w-48 rounded border px-2 text-sm"
          value={businessCode}
          onChange={(event) => onBusinessCode(event.target.value)}
        >
          <option value="">全部业务</option>
          {businessOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      {children}
      <Button variant="outline" size="sm" onClick={onRefresh} disabled={loading}>
        {loading ? "查询中…" : "刷新"}
      </Button>
    </div>
  );
}

/** 精确标识搜索：中文输入法组合期间不提交，300ms 后生效，清除立即生效。 */
export function DebouncedInput({
  label,
  value,
  onValue,
  placeholder,
  delay = 300,
}: {
  label: string;
  value: string;
  onValue: (value: string) => void;
  placeholder?: string;
  delay?: number;
}) {
  const [draft, setDraft] = useState(value);
  const composing = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => setDraft(value), [value]);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    []
  );

  function schedule(next: string) {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onValue(next), delay);
  }

  return (
    <label className="space-y-1 text-xs">
      <span className="text-muted-foreground block">{label}</span>
      <div className="flex items-center gap-1">
        <input
          className="bg-background h-8 w-56 rounded border px-2 text-sm"
          value={draft}
          placeholder={placeholder}
          onChange={(event) => {
            const next = event.target.value;
            setDraft(next);
            if (!composing.current) schedule(next);
          }}
          onCompositionStart={() => {
            composing.current = true;
          }}
          onCompositionEnd={(event) => {
            composing.current = false;
            schedule((event.target as HTMLInputElement).value);
          }}
          aria-label={label}
        />
        {draft && (
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground rounded px-1 text-sm"
            aria-label={`清除${label}`}
            onClick={() => {
              if (timer.current) clearTimeout(timer.current);
              setDraft("");
              onValue("");
            }}
          >
            ×
          </button>
        )}
      </div>
    </label>
  );
}
