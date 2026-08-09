## Context

当前项目通过 `RichOutputMiddleware`（[rich_output_middleware.py](file:///Users/acerola/Dev/Python/air_agent/src/common/middleware/rich_output_middleware.py)）实现富输出能力。该中间件要求工具返回特定的 `{output_type, chart_subtype, display_mode, data}` 结构化 JSON，前端必须为每种 `output_type` 预先编写对应的 TSX 渲染组件。这导致：

1. Agent 无法自主决定输出内容形态——想输出一份自定义 HTML 报表，必须先在前端新增 `HtmlRenderer` 组件
2. 扩展新展示类型需要同时改后端协议 + 前端渲染器，维护成本高
3. 与 Claude Artifacts / ChatGPT Canvas 这类"Agent 自主生成内容、前端原生加载"的现代交互范式不兼容

项目中 6 个 graph 通过两种方式复用该中间件：
- 3 个 DeepAgent 图（`deep_research` / `intelligent_report` / `intelligent_tracing`）：在 `_build_middleware()` 中直接挂载 `RichOutputMiddleware()`
- 3 个 LangGraph 图（`basic_qa` / `intelligent_analysis` / `data_analysis`）：通过 `tool_wrappers.py` 的 `_handle_rich_output()` 函数等效调用

前端基于 Next.js + `@assistant-ui/react` + `@assistant-ui/react-langchain`，使用 `useStreamRuntime` 连接 LangGraph SSE 流。已有 `toolkit.tsx` 的 Tool → TSX 映射机制（用于 `price_snapshot` / `purchase_stock` 等工具），但该机制与 Artifact 画布是互补关系，不冲突。

## Goals / Non-Goals

**Goals:**

- Agent 能自主判断何时该输出画布内容，并自行生成 HTML / Markdown / SVG 源码
- 前端画布能原生加载任意 Agent 生成的内容，无需预注册渲染器
- 提供两种触发路径：`create_artifact` 工具（显式、结构化）+ fenced code block 自动识别（隐式、零成本）
- 大段源码不进入 LLM 上下文，通过中间件压缩为占位摘要
- 6 个 graph 统一接入，零差异

**Non-Goals:**

- 不做 Artifact 版本历史 / diff 对比（后续迭代）
- 不做 Artifact 分享 / 导出 URL（后续迭代）
- 不做 Agent 生成 React/TSX 组件并热加载（安全风险过高，iframe sandbox 已足够覆盖 HTML 场景）
- 不替换现有 `@assistant-ui/react` runtime（保留现有 Toolkit 机制，Artifact 与之并存）
- 不修改 `toolkit.tsx` 中已有的 `price_snapshot` / `purchase_stock` Tool UI 映射

## Decisions

### 决策 1：删除旧 RichOutputMiddleware，不保留兼容层

**选择**：完全删除 `rich_output_middleware.py` 和 `tool_wrappers.py` 中的 `_handle_rich_output()`，替换为新的 `ArtifactMiddleware`。

**理由**：旧协议（`output_type` / `chart_subtype` / `display_mode` / `message`）与新协议（`content_type` / `content` / `open_in`）字段完全不同，保留兼容层会增加维护复杂度且没有实际用户在用旧协议（前端从未实现 rich_output 的渲染逻辑——Grep 搜索确认前端无 `rich_output` 引用）。

**替代方案**：保留旧中间件、新增并行中间件 → 否决，因为两套协议共存会让 Agent 困惑、前端也要维护两套事件处理。

### 决策 2：工具 + 中间件并存，而非只留一个

**选择**：同时实现 `create_artifact` 工具和 `ArtifactMiddleware` 中间件。

**理由**：
- 工具提供结构化入口（参数校验、open_in 精控），但只能覆盖"Agent 主动调用"这一路径
- 中间件提供全局兜底（扫描 AIMessage / 所有 ToolMessage 中的 fenced code block），并负责上下文压缩（把源码替换为占位摘要）
- 单独使用工具：Agent 直接写 ```html 时不触发画布，且源码污染上下文
- 单独使用中间件：失去 open_in 参数精控和参数校验

### 决策 3：前端用 iframe sandbox 渲染 HTML，不用 dangerouslySetInnerHTML

**选择**：`content_type=html` 时使用 `<iframe srcDoc={content} sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads">` 渲染。

**理由**：
- `dangerouslySetInnerHTML` 会把 Agent 生成的脚本注入到主页面上下文，有 XSS 风险
- iframe sandbox 提供同源隔离，Agent 生成的 HTML 可以自由加载 CDN（如 ECharts、Chart.js），但无法访问父页面 DOM / Cookie
- `srcDoc` 属性直接内嵌 HTML 源码，无需额外 URL

**替代方案**：
- `dangerouslySetInnerHTML` → 否决，安全风险过高
- Web Worker / Shadow DOM → 否决，无法执行完整 HTML（含 `<script>` 标签）

### 决策 4：协议字段极简，content_type 用 MIME 风格而非枚举扩展

**选择**：统一事件结构只有 `type` / `title` / `content_type` / `content` / `open_in` 五个核心字段。`content_type` 取值固定为 `html` / `markdown` / `svg` / `text` / `iframe_url` / `image_url`，不再扩展。

**理由**：所有"结构型"输出都走 html（Agent 自己写 HTML + CDN），所有"文档型"输出都走 markdown，所有"矢量图型"输出都走 svg。要新形态（Mermaid、ECharts、G6 关系图、PPT 嵌入）Agent 只要写 HTML + CDN import 即可，不需要新增 `content_type` 值，也不需要前端加新的 renderer。

### 决策 5：open_in 默认 canvas_window，由工具参数或启发式规则决定

**选择**：
- `create_artifact` 工具调用时：由 Agent 通过 `open_in` 参数指定
- fenced code block 自动识别时：默认 `canvas_window`；但 `image_url` 类型默认 `inline_below`

**理由**：独立画布窗口是用户最期望的"画布"体验（类似 Claude Artifacts），作为默认值最合理。内联模式适合小图/短文本，由启发式规则自动判断即可。

### 决策 6：前端事件监听挂在 useStreamRuntime 的自定义事件上

**选择**：在 `MyRuntimeProvider` 内部包一层 `ArtifactProvider`，通过 `useStreamRuntime` 返回的 runtime 对象监听 SSE 流中的 `{type: "artifact"}` 自定义事件，分发到 Zustand store。

**理由**：`useStreamRuntime` 内部已经订阅了 LangGraph 的 `/threads/{id}/runs/stream` 端点，自定义事件会通过 `stream_writer` 推送到 SSE 流中。不需要额外建立 WebSocket / SSE 连接。

## Risks / Trade-offs

- **[iframe 安全风险]** Agent 生成的 HTML 可能包含恶意脚本 → `sandbox` 属性限制权限（禁止 top 导航 / cookie 访问），`referrerPolicy="no-referrer"` 阻止泄露来源。后续可加 CSP header 进一步限制。

- **[fenced code block 误识别]** Agent 在回答中写普通代码示例（非 Artifact 意图）也会被中间件扫描到 → 仅对 `html` / `markdown` / `svg` 三种 fence 语言触发，且要求内容长度 > 50 字符（过滤掉短代码片段）。`create_artifact` 工具调用不受此限制。

- **[SSE 事件丢失]** 网络抖动可能导致 Artifact 事件丢失 → 前端 store 记录 `createdAt` 时间戳，后续可加重试 / 重连补偿机制。当前版本接受偶发丢失（与 text token 丢失概率相同）。

- **[旧代码删除影响]** 删除 `RichOutputMiddleware` 后，如果有工具仍在返回 `{rich_outputs: [...]}` 格式 → 这些工具的富输出将不再被推送前端，但工具本身仍正常返回数据（只是不会弹出画布）。需要在迁移时检查所有工具的返回值格式。

- **[中间件执行顺序]** `ArtifactMiddleware` 需要在 `FinalOutputCleanupMiddleware` 之前执行，否则 cleanup 可能破坏 fenced code block 结构 → 在 `_build_middleware()` 中确保注册顺序。
