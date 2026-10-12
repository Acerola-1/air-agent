"use client";
import { useEffect, useRef, useState } from "react";
import { useChatStore } from "@/lib/chat-store";
import { readArchive, type ArchivePage } from "@/lib/langgraph-api";
import { Button } from "@/components/ui/button";

/** 显式打开、分页回读原始或派生资料，不预取大载荷。 */
export function ArchiveReader({ artifactId }: { artifactId: string }) {
  const threadId = useChatStore((state) => state.activeThreadId);
  const [page, setPage] = useState<ArchivePage | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [opened, setOpened] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    setPage(null); setOpened(false); setError(null); setPending(false);
    return () => { generation.current++; };
  }, [threadId, artifactId]);
  async function load(offset = 0) {
    if (!threadId || pending) return;
    const current = generation.current;
    setPending(true); setError(null); setOpened(true);
    try {
      const response = await readArchive(threadId, artifactId, offset);
      if (generation.current === current) setPage(response);
    }
    catch (failure) {
      if (generation.current === current) setError(failure instanceof Error ? failure.message : "读取失败，请重试。");
    }
    finally { if (generation.current === current) setPending(false); }
  }
  return <div className="space-y-2 font-sans">
    <Button type="button" variant="outline" size="sm" disabled={pending || !threadId} onClick={() => opened ? setOpened(false) : void load(page?.offset ?? 0)} aria-expanded={opened}>
      {pending ? "正在读取…" : opened ? "收起归档资料" : "查看归档资料"}
    </Button>
    {opened && <div className="space-y-2 rounded-lg border border-border p-2">
      {error && <p role="alert">{error}<button type="button" className="ml-2 cursor-pointer underline focus-visible:outline-2" onClick={() => void load(page?.offset ?? 0)}>重试</button></p>}
      {page && <><p className="text-muted-foreground tabular-nums">第 {page.offset + 1}–{page.offset + [...page.content].length} 字符，共 {page.char_length.toLocaleString()} 字符</p>
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]">{page.content}</pre>
        <div className="flex gap-2"><Button type="button" size="sm" variant="outline" disabled={pending || page.offset === 0} onClick={() => void load(Math.max(0, page.offset - 8000))}>上一段</Button>
          <Button type="button" size="sm" variant="outline" disabled={pending || page.next_offset == null} onClick={() => void load(page.next_offset!)}>下一段</Button></div></>}
    </div>}
  </div>;
}
