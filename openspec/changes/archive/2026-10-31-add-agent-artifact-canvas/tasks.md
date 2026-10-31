## 1. 后端：删除旧 RichOutputMiddleware

- [ ] 1.1 删除 `src/common/middleware/rich_output_middleware.py` 文件
- [ ] 1.2 从 `src/common/middleware/__init__.py` 中移除 `RichOutputMiddleware` 的导入和 `__all__` 导出，添加 `ArtifactMiddleware` 的导入和导出
- [ ] 1.3 从 `src/common/business_graph/tool_wrappers.py` 中删除 `_handle_rich_output()` 函数及其对 `RichOutputMiddleware` 的导入，替换为 `ArtifactMiddleware` 的调用
- [ ] 1.4 修改 3 个 DeepAgent graph（`deep_research/graph.py` / `intelligent_report/graph.py` / `intelligent_tracing/graph.py`）：将 `_build_middleware()` 中的 `RichOutputMiddleware()` 替换为 `ArtifactMiddleware()`，更新导入
- [ ] 1.5 全局搜索确认无残留的 `RichOutputMiddleware` / `rich_output` / `_handle_rich_output` 引用

## 2. 后端：新建 ArtifactMiddleware

- [ ] 2.1 创建 `src/common/middleware/artifact_middleware.py`，实现 `ArtifactMiddleware` 类（继承 `AgentMiddleware`）
- [ ] 2.2 实现 fenced code block 正则扫描逻辑（` ```html ` / ` ```markdown ` / ` ```svg `），内容长度 > 50 字符才触发
- [ ] 2.3 实现 `wrap_tool_call` / `awrap_tool_call`：拦截 ToolMessage 中的 fenced code block，封装 Artifact 事件并推送 `stream_writer`，替换原始内容为占位摘要
- [ ] 2.4 实现 `create_artifact` 工具结果透传逻辑：当 `tool_name == "create_artifact"` 时跳过扫描
- [ ] 2.5 实现标题提取逻辑：从 HTML `<title>` / Markdown `# 标题` / SVG `<desc>` / 首行截取
- [ ] 2.6 在 `src/common/middleware/__init__.py` 中导出 `ArtifactMiddleware`

## 3. 后端：新建 create_artifact 工具

- [ ] 3.1 创建 `src/common/tools/create_artifact.py`，定义 `CreateArtifactInput` Pydantic 模型（`title` / `content_type` / `content` / `open_in`）
- [ ] 3.2 实现 `create_artifact_tool_fn` 执行函数：通过 `stream_writer` 推送 Artifact 事件，返回简短摘要
- [ ] 3.3 实现 `build_create_artifact_tool` 工厂函数：包装为 `StructuredTool` 实例
- [ ] 3.4 在 3 个 LangGraph 图（`basic_qa` / `intelligent_analysis` / `data_analysis`）的工具列表中注册 `create_artifact` 工具

## 4. 后端：更新系统提示词

- [ ] 4.1 编写 Artifact 输出引导文本模板（说明何时使用 fenced code block 输出 html/markdown/svg）
- [ ] 4.2 在 `deep_research` graph 的 `SYSTEM_PROMPT` 中追加 Artifact 引导文本
- [ ] 4.3 在 `intelligent_report` graph 的 `SYSTEM_PROMPT` 中追加 Artifact 引导文本
- [ ] 4.4 在 `intelligent_tracing` graph 的 `SYSTEM_PROMPT` 中追加 Artifact 引导文本
- [ ] 4.5 在 `basic_qa` / `intelligent_analysis` / `data_analysis` 三个 LangGraph 图的系统提示词中追加 Artifact 引导文本

## 5. 前端：Zustand Store + 事件监听

- [ ] 5.1 创建 `frontend/store/artifact-store.ts`：定义 `ArtifactEvent` 类型、`ArtifactContentType` / `ArtifactOpenIn` 联合类型、`useArtifactStore` Zustand store（按 `open_in` 分桶）
- [ ] 5.2 创建 `frontend/app/artifact-provider.tsx`：`ArtifactProvider` 组件，挂载在 `AssistantRuntimeProvider` 内部，监听 SSE 流中的 `{type: "artifact"}` 事件并分发到 store
- [ ] 5.3 修改 `frontend/app/MyRuntimeProvider.tsx`：在 `<AssistantRuntimeProvider>` 内部包一层 `<ArtifactProvider>`
- [ ] 5.4 确保 `assistantId` 变化时（切换 graph）store 自动重置

## 6. 前端：通用渲染引擎

- [ ] 6.1 创建 `frontend/components/artifact/content-renderer.tsx`：实现 `ContentRenderer` 组件，按 `content_type` 分派渲染引擎
- [ ] 6.2 实现 `html` 渲染分支：`<iframe srcDoc={content} sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads" referrerPolicy="no-referrer">`
- [ ] 6.3 实现 `markdown` 渲染分支：使用 `@assistant-ui/react-markdown` 的 `MarkdownText` 组件
- [ ] 6.4 实现 `svg` 渲染分支：`dangerouslySetInnerHTML` 内嵌
- [ ] 6.5 实现 `text` / `iframe_url` / `image_url` 渲染分支
- [ ] 6.6 实现未知 `content_type` 的兜底渲染（`<pre>` 显示原始内容）

## 7. 前端：画布窗口容器

- [ ] 7.1 创建 `frontend/components/artifact/canvas-window.tsx`：实现 `ArtifactCanvasWindow` 组件，使用 `Dialog` 组件作为独立画布窗口
- [ ] 7.2 实现多 Tab 切换栏：每个 Tab 显示 content_type 图标 + 标题 + 关闭按钮
- [ ] 7.3 实现最大化/还原按钮切换
- [ ] 7.4 实现画布关闭逻辑：从 `canvasItems` 移除，自动切换到最后一个或关闭窗口
- [ ] 7.5 在 `MyRuntimeProvider` 的渲染树中全局挂载 `<ArtifactCanvasWindow />`

## 8. 前端：内联容器

- [ ] 8.1 创建 `frontend/components/artifact/inline-container.tsx`：实现内联渲染容器，按 `tool_call_id` 索引
- [ ] 8.2 在 `thread.tsx` 或 `tool-fallback.tsx` 中挂载内联容器：Tool UI 下方检查 `useArtifactStore.inlineMap` 是否有对应的 Artifact

## 9. 验证与测试

- [ ] 9.1 后端单元测试：`ArtifactMiddleware` 的 fenced code block 扫描与上下文压缩（`tests/unit_tests/test_artifact_middleware.py`）
- [ ] 9.2 后端单元测试：`create_artifact` 工具参数校验与事件推送（`tests/unit_tests/test_create_artifact.py`）
- [ ] 9.3 端到端验证：在 LangGraph Studio 中测试 Agent 输出 ```html 代码块 → 前端画布弹出 HTML iframe
- [ ] 9.4 端到端验证：测试 `create_artifact` 工具调用 → 前端画布弹出
- [ ] 9.5 端到端验证：测试多 Tab 切换、最大化、关闭
- [ ] 9.6 运行 `ruff check` + `ruff format` 确保后端代码规范通过
- [ ] 9.7 更新 `AGENTS.md` 中关于富输出协议的说明（替换为 Artifact 协议描述）
