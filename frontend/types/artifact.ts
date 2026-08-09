/** 单个 Artifact 条目的状态定义 —— 与后端推送事件结构严格对应.
 *
 *  后端通过 stream_writer 发送 { type: "artifact", title, content_type, content, open_in }；
 *  前端 Store 负责接收后按 open_in 分桶到不同的显示容器.
 */
export type ArtifactContentType =
  | "html"
  | "markdown"
  | "md"
  | "svg"
  | "text"
  | "iframe_url"
  | "image_url";

export type ArtifactOpenIn =
  | "canvas_window"
  | "inline_below"
  | "modal"
  | "sidebar";

export interface Artifact {
  id: string;
  title: string;
  content_type: ArtifactContentType;
  content: string;
  open_in: ArtifactOpenIn;
  /** true 表示该 Artifact 来自中间件自动扫描 fenced code block，而非显式工具调用. */
  auto_detected?: boolean;
  createdAt: number;
  /** 对 inline_below / modal 模式：当前用户是否已关闭；未被显式关闭时默认 null. */
  closed?: boolean;
}
