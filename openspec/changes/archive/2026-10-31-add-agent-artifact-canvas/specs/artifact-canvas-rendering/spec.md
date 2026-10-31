## ADDED Requirements

### Requirement: Frontend SHALL render artifacts by content_type without pre-registration

前端 MUST 提供 `ContentRenderer` 组件，根据 Artifact 事件的 `content_type` 字段选择原生渲染引擎，无需预注册渲染器。支持的 `content_type` 包括：`html`（iframe sandbox）、`markdown`（react-markdown）、`svg`（dangerouslySetInnerHTML）、`text`（pre 标签）、`iframe_url`（iframe src）、`image_url`（img 标签）。

#### Scenario: HTML Artifact 渲染

- **WHEN** 前端收到 `{type: "artifact", content_type: "html", content: "<!doctype html>..."}`
- **THEN** `ContentRenderer` MUST 使用 `<iframe srcDoc={content} sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads">` 渲染
- **AND** iframe MUST 设置 `referrerPolicy="no-referrer"`

#### Scenario: Markdown Artifact 渲染

- **WHEN** 前端收到 `{type: "artifact", content_type: "markdown", content: "# 标题\n..."}`
- **THEN** `ContentRenderer` MUST 使用 `@assistant-ui/react-markdown` 的 `MarkdownText` 组件渲染
- **AND** MUST 支持 GFM 表格、代码高亮

#### Scenario: SVG Artifact 渲染

- **WHEN** 前端收到 `{type: "artifact", content_type: "svg", content: "<svg>...</svg>"}`
- **THEN** `ContentRenderer` MUST 通过 `dangerouslySetInnerHTML` 内嵌 SVG
- **AND** SVG MUST 自动响应容器宽度

#### Scenario: 未知的 content_type 兜底

- **WHEN** 前端收到 `{content_type: "unknown_type", ...}`
- **THEN** `ContentRenderer` MUST 使用 `<pre>` 标签显示原始内容作为兜底
- **AND** MUST NOT 抛出异常或白屏

### Requirement: Frontend SHALL provide canvas window for open_in=canvas_window

前端 MUST 提供独立画布窗口组件（`ArtifactCanvasWindow`），当 Artifact 事件的 `open_in` 为 `canvas_window` 时在独立 Dialog 中渲染内容。画布窗口 MUST 支持多 Tab 切换、关闭、最大化/还原。每个 Tab 显示 Artifact 标题和 content_type 图标。

#### Scenario: 单个画布打开

- **WHEN** 前端收到第一个 `open_in="canvas_window"` 的 Artifact 事件
- **THEN** MUST 弹出独立 Dialog 窗口
- **AND** Dialog 标题栏显示 Artifact 的 `title` 和对应 `content_type` 的图标
- **AND** Dialog 内容区域通过 `ContentRenderer` 渲染 Artifact 内容

#### Scenario: 多个画布 Tab 切换

- **WHEN** 画布窗口已打开且前端收到第二个 `open_in="canvas_window"` 的 Artifact 事件
- **THEN** MUST 在画布窗口顶部显示 Tab 栏，列出所有已打开的 Artifact
- **AND** 用户点击不同 Tab 可切换当前显示的 Artifact
- **AND** 每个 Tab 有关闭按钮

#### Scenario: 画布最大化

- **WHEN** 用户点击画布窗口的最大化按钮
- **THEN** 画布窗口 MUST 扩展到接近全屏尺寸（`max-w-[96vw] h-[92vh]`）
- **AND** 再次点击可还原为默认尺寸

#### Scenario: 画布关闭

- **WHEN** 用户点击画布 Tab 的关闭按钮或 Dialog 的关闭区域
- **THEN** 该 Artifact MUST 从 `canvasItems` 中移除
- **AND** 如果还有其他画布，自动切换到最后一个；如果没有则关闭窗口

### Requirement: Frontend SHALL provide inline container for open_in=inline_below

前端 MUST 提供内联容器组件，当 Artifact 事件的 `open_in` 为 `inline_below` 时，将渲染内容嵌入到对应工具调用结果的下方（对话流中），而非弹出独立窗口。

#### Scenario: 内联 Artifact 渲染

- **WHEN** 前端收到 `{open_in: "inline_below", tool_call_id: "call_abc"}` 的 Artifact 事件
- **THEN** MUST 在 `tool_call_id` 对应的 Tool UI 组件下方渲染 `ContentRenderer` 输出
- **AND** MUST NOT 弹出独立画布窗口

#### Scenario: 同一工具调用产生多个内联 Artifact

- **WHEN** 同一 `tool_call_id` 产生第二个 `open_in="inline_below"` 的 Artifact 事件
- **THEN** MUST 在前一个内联 Artifact 下方继续追加渲染
- **AND** 多个内联 Artifact 按创建时间顺序排列

### Requirement: Frontend SHALL listen to artifact events via SSE stream

前端 MUST 提供 `ArtifactProvider` 组件，挂载在 `AssistantRuntimeProvider` 内部，监听 `useStreamRuntime` 的 SSE 流中的 `{type: "artifact"}` 自定义事件，并将事件分发到 Zustand store（`useArtifactStore`）。Store MUST 按 `open_in` 字段将 Artifact 分桶存储。

#### Scenario: SSE 事件接收与分发

- **WHEN** SSE 流推送 `{type: "artifact", open_in: "canvas_window", ...}` 事件
- **THEN** `ArtifactProvider` MUST 调用 `useArtifactStore.add(event)` 将事件存入 `canvasItems` 桶
- **AND** 如果是第一个 canvas Artifact，MUST 设置 `activeCanvasId`

#### Scenario: 切换 graph 时清空 Artifact 状态

- **WHEN** 用户切换 graph（`assistantId` 变化导致 `AssistantRuntimeProvider` 以新 key 重新挂载）
- **THEN** `useArtifactStore` MUST 被重置为初始空状态
- **AND** 已打开的画布窗口 MUST 关闭

### Requirement: Frontend SHALL mount canvas window globally

前端 MUST 在全局布局层（`MyRuntimeProvider` 内、`AssistantRuntimeProvider` 外层或同级）挂载 `ArtifactCanvasWindow` 组件，确保画布窗口在任何对话页面都可用。

#### Scenario: 画布窗口全局可用

- **WHEN** 用户在任一 graph 对话页面中触发 Artifact 生成
- **THEN** 画布窗口 MUST 能正常弹出
- **AND** 画布窗口 MUST 不受对话页面路由变化影响
