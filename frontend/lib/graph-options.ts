/** 当前 air_agent 支持的 6 个 graph — 硬编码中文展示名, 下拉切换用
 *
 *  graph_id 必须和 langgraph.json graphs 的 key 完全一致:
 *    basic-qa / intelligent-analysis / data-analysis /
 *    intelligent-report / deep-research / intelligent-tracing
 */
export const GRAPH_OPTIONS = [
  { id: "basic-qa", label: "基础问答", desc: "标准 Q&A 对话流" },
  { id: "intelligent-analysis", label: "智能分析", desc: "多步推理 + 图表分析" },
  { id: "intelligent-tracing", label: "溯源追踪", desc: "数据来源与推导链路追踪" },
  { id: "intelligent-report", label: "报告生成", desc: "生成结构化报告" },
  { id: "deep-research", label: "深度研究", desc: "多轮检索 + 长文研究" },
  { id: "data-analysis", label: "数据分析助手", desc: "表格 / SQL / 可视化" },
] as const;

export type GraphId = (typeof GRAPH_OPTIONS)[number]["id"];

/** 给每个 graph 分配一个稳定的小圆点颜色，方便快速区分 */
export const GRAPH_COLORS: Record<GraphId, string> = {
  "basic-qa": "#16a34a", // green-600
  "intelligent-analysis": "#2563eb", // blue-600
  "intelligent-tracing": "#7c3aed", // violet-600
  "intelligent-report": "#db2777", // pink-600
  "deep-research": "#ea580c", // orange-600
  "data-analysis": "#0891b2", // cyan-600
};

export function colorForGraph(id: string): string {
  return GRAPH_COLORS[id as GraphId] ?? "#6b7280";
}
