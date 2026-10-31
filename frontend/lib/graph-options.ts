/** 前端暴露的 2 个业务 — 稳定编码、中文名与唯一 IAM 使用权限.
 *
 *  graph_id 必须和 langgraph.json graphs 的 key 完全一致:
 *    basic-qa / intelligent-tracing
 *  data-analysis 仍注册在后端，但前端不再提供入口。
 *  业务授权以 IAM 实时权限为准；本文件只负责展示与过滤，不构成授权。
 */
export const GRAPH_OPTIONS = [
  {
    id: "basic-qa",
    label: "基础问答",
    desc: "标准空气质量问答",
    permission: "agent:business:basic-qa:use",
  },
  {
    id: "intelligent-tracing",
    label: "溯源追踪",
    desc: "数据来源与推导链路追踪",
    permission: "agent:business:intelligent-tracing:use",
  },
] as const;

export type GraphId = (typeof GRAPH_OPTIONS)[number]["id"];

/** 当前身份实时可用业务：只按 IAM 精确权限过滤，不做前端推断或 root 特判。 */
export function availableGraphOptions(
  permissions: readonly string[] | null | undefined
): Array<(typeof GRAPH_OPTIONS)[number]> {
  const granted = new Set(permissions ?? []);
  return GRAPH_OPTIONS.filter((option) => granted.has(option.permission));
}

/** 给每个 graph 分配一个稳定的小圆点颜色，方便快速区分 */
export const GRAPH_COLORS: Record<GraphId, string> = {
  "basic-qa": "#16a34a", // green-600
  "intelligent-tracing": "#7c3aed", // violet-600
};

export function colorForGraph(id: string): string {
  return GRAPH_COLORS[id as GraphId] ?? "#6b7280";
}
