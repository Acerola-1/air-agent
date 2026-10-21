/** API返回类型来自Pydantic/OpenAPI生成副本，避免前后端手工漂移。 */
import type * as Schema from "./contracts.generated";

export type Metric = Schema.ConsoleMetric;
export type MetricStatus = Metric["status"];
export type WindowMeta = Schema.ConsoleWindowOut;
export type Meta = Schema.ConsoleMeta;
export type ListResponse<T> = Omit<Schema.ConsoleList_ConsoleUserUsage_, "items"> & { items: T[] };
export type OverviewResponse = Schema.ConsoleOverview;
export type TrendDay = Schema.ConsoleTrendDay;
export type TrendsResponse = Schema.ConsoleTrends;
export type UserUsage = Schema.ConsoleUserUsage;
export type RunAnswerRef = Schema.ConsoleAnswerRef;
export type RunItem = Schema.ConsoleRun;
export type RunDetail = Schema.ConsoleRunDetail;
export type ContentPage = Schema.ConsoleContentPage;
export type FeedbackItem = Schema.ConsoleFeedback;
export type FeedbackActionResponse = Schema.FeedbackCaseActionOut;
export type CapabilityGroup = Schema.ConsoleCapability;
export type PerformanceResponse = Schema.ConsolePerformance;
export type CostsResponse = Schema.ConsoleCosts;
