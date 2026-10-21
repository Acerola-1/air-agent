/** Change7编译期扩展示例：只定义契约，不挂载页面或生成示例榜单。 */
import type { ComponentProps } from "react";
import type { QueryToolbar } from "@/components/operations/QueryToolbar";
import type { ListResponse, Meta, Metric } from "./types";

export type InsightsToolbarProps = ComponentProps<typeof QueryToolbar>;
export type InsightsMeta = Meta & {
  analysis_version: string;
  assignment_revision: number;
};
export type InsightsQuestion = {
  cluster_id: string;
  canonical_question: string;
  requests: Metric;
};
export type InsightsPage = Omit<ListResponse<InsightsQuestion>, "meta"> & {
  meta: InsightsMeta;
};
export type InsightsLoader = (signal: AbortSignal) => Promise<InsightsPage>;
// 页面复用useOperationQuery(load, deps)；load使用7自己的/agent/insights/**客户端。
// 企业身份仅来自现有SessionProvider；不得从此示例另建Session或导入console SQL。
