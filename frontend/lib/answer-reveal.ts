/** 把已经稳定的正文和刚刚到达的最后一句分开，供页面只淡入新句。 */

const MARKS = ["\n\n", "\n", "。", "！", "？", "；"] as const;

export function splitLatestAnswerSegment(text: string): {
  before: string;
  latest: string;
} {
  if (!text) return { before: "", latest: "" };
  const bodyEnd = text.search(/\s+$/);
  const body = bodyEnd >= 0 ? text.slice(0, bodyEnd) : text;
  let searchEnd = body.length;
  for (const mark of MARKS) {
    if (body.endsWith(mark)) {
      searchEnd = body.length - mark.length;
      break;
    }
  }
  const window = body.slice(0, searchEnd);
  let cut = 0;
  for (const mark of MARKS) {
    const index = window.lastIndexOf(mark);
    if (index >= 0) cut = Math.max(cut, index + mark.length);
  }
  if (cut <= 0) return { before: "", latest: text };
  return { before: text.slice(0, cut), latest: text.slice(cut) };
}
