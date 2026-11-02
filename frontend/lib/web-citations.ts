/** 联网来源角标的纯函数：正文 `[web:n]` ↔ 内部占位链接。
 *
 * 服务端在发布前已过滤未知编号并追加“互联网来源”；这里只负责把正文里的
 * `[web:n]` 变成 React 可渲染的上标节点，不使用 HTML 注入。
 */

export const WEB_CITATION_RE = /\[web:(\d+)\]/g;
export const WEB_CITATION_HREF = /^https:\/\/webcitation\.invalid\/(\d+)$/;
const KNOWLEDGE_CITATION_RE = /\[\[cite:([0-9a-fA-F-]{36})\]\]/g;

/** 模型只能输出 [[cite:<id>]]；渲染前换成可点击占位链接，来源由服务端解析。 */
export function withKnowledgeCitationLinks(content: string): string {
  return content.replace(KNOWLEDGE_CITATION_RE, (_match, id: string) => {
    return `[📄 查看来源](https://citation.invalid/${id})`;
  });
}

/** 把 `[web:n]` 转成内部占位链接；已知编号集合之外的内容直接移除。 */
export function withWebCitationLinks(
  content: string,
  known?: ReadonlySet<number>,
): string {
  return content.replace(WEB_CITATION_RE, (_match, raw: string) => {
    const number = Number(raw);
    if (!Number.isSafeInteger(number) || number <= 0) return "";
    if (known && !known.has(number)) return "";
    return `[${number}](https://webcitation.invalid/${number})`;
  });
}

/** 从内部占位链接取出来源编号；不是联网角标时返回 null。 */
export function webCitationNumber(href: string | undefined): number | null {
  const match = WEB_CITATION_HREF.exec(href ?? "");
  if (!match) return null;
  const number = Number(match[1]);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

/** 从完整回答末尾的“互联网来源”列表提取已知编号（历史消息无状态快照时用）。 */
export function referencedWebNumbers(content: string): Set<number> {
  const numbers = new Set<number>();
  for (const match of content.matchAll(WEB_CITATION_RE)) {
    numbers.add(Number(match[1]));
  }
  return numbers;
}

/** 先转换知识引用，再转换联网角标；两套编号空间互不影响。 */
export function renderCitationMarkdown(
  content: string,
  known?: ReadonlySet<number>,
): string {
  return withWebCitationLinks(withKnowledgeCitationLinks(content), known);
}
