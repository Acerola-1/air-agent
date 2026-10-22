/** 会话展示与搜索必须使用同一个标题，包括无标题会话的可见回退。 */
export function threadTitle(thread: { metadata?: { title?: unknown } | null }): string {
  const title = thread.metadata?.title;
  return typeof title === "string" && title.trim() ? title : "新会话";
}
