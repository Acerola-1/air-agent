## Why

当前项目的富输出能力（`RichOutputMiddleware`）采用"约定数据格式 + 前端预注册渲染器"的死代码对接模式：工具必须返回 `{output_type, chart_subtype, display_mode, data}` 结构化 JSON，前端必须为每种 `output_type` 编写对应的 TSX 渲染组件。这意味着 Agent 无法自主决定输出内容形态——想输出一份自定义 HTML 报表或 Markdown 文档，就必须先在前端新增渲染器代码。这种模式扩展性差、维护成本高，且与 Claude Artifacts / ChatGPT Canvas 这类"Agent 自主生成内容、前端原生加载"的现代交互范式背道而驰。

## What Changes

- **删除** `RichOutputMiddleware` 及其死代码协议（`output_type` / `chart_subtype` / `display_mode` / `rich_outputs` 结构化字段），删除 `tool_wrappers.py` 中的 `_handle_rich_output()` 适配函数
- **新增** `create_artifact` 工具：Agent 可显式调用，接收 `title` / `content_type` / `content` / `open_in` 参数，生成结构化 Artifact 事件
- **新增** `ArtifactMiddleware` 中间件：全局横切层，自动扫描 AIMessage 和 ToolMessage 中的 fenced code block（` ```html ` / ` ```markdown ` / ` ```svg `），封装为统一 Artifact 事件并流式推送前端；同时将源码替换为占位摘要以压缩 LLM 上下文
- **新增** 前端通用画布渲染层：基于 `content_type`（html / markdown / svg / text / iframe_url / image_url）选择原生渲染引擎（iframe sandbox / react-markdown / dangerouslySetInnerHTML 等），支持 `canvas_window` / `inline_below` / `modal` / `sidebar` 四种展示模式，无需预注册渲染器
- **更新** 6 个 graph（3×LangGraph + 3×DeepAgent）的中间件注册：将 `RichOutputMiddleware()` 替换为 `ArtifactMiddleware()`，在 LangGraph 图的工具列表中注册 `create_artifact` 工具

## Capabilities

### New Capabilities

- `agent-artifact-generation`: Agent 自主生成 Artifact 内容（HTML/Markdown/SVG/URL 等）并通过 `create_artifact` 工具或 fenced code block 输出，系统自动封装为统一事件推送给前端
- `artifact-canvas-rendering`: 前端通用画布渲染层，按 `content_type` 选择原生渲染引擎，按 `open_in` 选择展示模式，无需前端预注册渲染器即可加载任意 Agent 生成的内容

### Modified Capabilities

（无现有 spec 需要修改——这是全新能力，旧 RichOutputMiddleware 未被 spec 覆盖）

## Impact

- **后端 Python**：
  - 删除 `src/common/middleware/rich_output_middleware.py`（234 行），新建 `src/common/middleware/artifact_middleware.py`
  - 新建 `src/common/tools/create_artifact.py` 工具
  - 修改 `src/common/business_graph/tool_wrappers.py`：删除 `_handle_rich_output()`，改为调用 `ArtifactMiddleware`
  - 修改 `src/common/middleware/__init__.py`：导出变更
  - 修改 3 个 DeepAgent graph（`deep_research` / `intelligent_report` / `intelligent_tracing`）：替换中间件
  - 修改 3 个 LangGraph graph（`basic_qa` / `intelligent_analysis` / `data_analysis`）：注册 `create_artifact` 工具 + 适配中间件
  - 更新系统提示词：引导 Agent 使用 fenced code block 或 `create_artifact` 工具输出画布内容

- **前端 React/Next.js**：
  - 新建 `frontend/store/artifact-store.ts`：Zustand 状态分桶
  - 新建 `frontend/components/artifact/content-renderer.tsx`：6 合 1 通用渲染引擎
  - 新建 `frontend/components/artifact/canvas-window.tsx`：独立画布窗口容器
  - 新建 `frontend/components/artifact/inline-container.tsx`：内联渲染容器
  - 新建 `frontend/app/artifact-provider.tsx`：SSE 事件监听 Provider
  - 修改 `frontend/app/MyRuntimeProvider.tsx`：挂载 ArtifactProvider
  - 修改 `frontend/app/page.tsx` 或 layout：挂载 CanvasWindow 全局容器

- **协议变更**：`type: "rich_output"` → `type: "artifact"`，字段从 `output_type/chart_subtype/display_mode/message` 简化为 `content_type/content/open_in/title`

- **无外部依赖新增**：前端 iframe + react-markdown 均已存在于项目中
