# data_analysis 节点流重构 — 实现计划

> 日期：2026-06-14
> 分支：`dev_data_analysis_upgrade`
> 依赖设计文档：`docs/superpowers/specs/2026-06-14-data-analysis-node-flow-design.md`

## 实现步骤

### Phase 1：基础设施（state schema + 节点骨架）

**Step 1.1 — 创建 state schema**

- 新建 `src/data_analysis/state.py`
- 定义 `DataAnalysisState` TypedDict（见设计文档 §5）
- 包含 `messages: Annotated[list[AnyMessage], add_messages]`
- 包含 Skill 字段、模型字段、循环控制字段
- 验证：`python3 -m compileall src/data_analysis/state.py`

**Step 1.2 — 创建图骨架**

- 修改 `src/data_analysis/graph.py`
- 用 `StateGraph(DataAnalysisState)` 构建空图
- 添加所有节点（先用 placeholder 函数）
- 添加边和条件边
- 编译图，确保可运行（空节点返回 `{"messages": []}`)
- 验证：`python3 -m compileall src/data_analysis/graph.py` + `langgraph dev` 启动测试

**Step 1.3 — 确定图的拓扑**

```python
builder = StateGraph(DataAnalysisState)

builder.add_node("resolve_skill", resolve_skill_node)
builder.add_node("prepare_model", prepare_model_node)
builder.add_node("call_model", call_model_node)
builder.add_node("execute_tools", tool_node)
builder.add_node("finalize_output", finalize_output_node)

builder.add_edge(START, "resolve_skill")
builder.add_edge("resolve_skill", "prepare_model")
builder.add_edge("prepare_model", "call_model")
builder.add_conditional_edges("call_model", route_after_model)
builder.add_conditional_edges("execute_tools", route_after_tools)

builder.set_finish_point("finalize_output")

graph = builder.compile(checkpointer=get_checkpointer())
```

验证条件边路径映射：
- `route_after_model` → `"execute_tools"` 或 `"finalize_output"`
- `route_after_tools` → `"prepare_model"` 或 `"finalize_output"`

### Phase 2：确定性节点实现

**Step 2.1 — 实现 resolve_skill**

- 从 `config["configurable"]` 取 `menu_name` 和 `mode`
- 用 `match_menu_skill(menu_name)` 匹配 Skill
- 复用 `_load_allowed_tools()` 解析 SKILL.md frontmatter
- 读取 `references/fast.md` 或 `references/expert.md`
- 写入 state 字段
- 处理失配（menu_name 为空、无匹配 Skill）
- 验证：单元测试覆盖正常匹配、失配、非法 mode

**Step 2.2 — 实现 prepare_model**

- 构建 system_prompt：
  - `SYSTEM_PROMPT` + `with_data_analysis_output_guard()`
  - 时间上下文注入（复用 `TimeContextMiddleware._get_time_context()` 逻辑）
  - mode 上下文注入（复用 `ModeRoutingMiddleware._get_runtime_context()` 逻辑）
  - Skill 规则注入（`skill_rules_content` 注入为额外 HumanMessage 或追加到 system_prompt）
- 刷新 MCP 工具：调用 `await mcp_client.ensure_mcp_tools()`
- 过滤业务工具：只保留 `selected_skill_allowed_tools` 中列出的工具
- 写入 `system_prompt` 和 `available_tools` 到 state
- 验证：单元测试覆盖正常注入、无 Skill 降级

**Step 2.3 — 组装系统提示词的 helper 函数**

- 新建 `src/data_analysis/prompt_builder.py`
- 提取 `build_system_prompt()` 函数
- 提取 `filter_business_tools()` 函数
- 提取 `_build_time_context()` 函数（从 TimeContextMiddleware 逻辑提取）
- 提取 `_build_mode_context()` 函数（从 ModeRoutingMiddleware 逻辑提取）
- 验证：`ruff check` + `python3 -m compileall`

### Phase 3：LLM 节点实现

**Step 3.1 — 实现 call_model**

- 从 state 取 `messages`、`system_prompt`、`available_tools`
- 构造完整消息列表：`[SystemMessage(content=system_prompt)] + messages`
- 用 `model.bind_tools(available_tools)` 绑定工具
- 调用 `await model_with_tools.ainvoke(messages)`
- 返回 `{"messages": [response]}`
- 模型异常兜底：try/except 返回 AIMessage("抱歉，当前智能分析服务暂时不可用，请稍后再试。")
- 验证：单元测试 + 手动测试（langgraph dev）

**Step 3.2 — 实现 route_after_model**

- 检查最后一条 AIMessage 是否有 tool_calls
- 有 → `"execute_tools"`（除非 iteration_count >= MAX_ITERATIONS）
- 无 → `"finalize_output"`
- 返回 `Literal["execute_tools", "finalize_output"]`
- 验证：单元测试

**Step 3.3 — 实现 route_after_tools**

- 递增 iteration_count
- iteration_count < MAX_ITERATIONS → `"prepare_model"`
- iteration_count >= MAX_ITERATIONS → `"finalize_output"`
- 返回 `Literal["prepare_model", "finalize_output"]`
- 验证：单元测试

### Phase 4：工具执行节点

**Step 4.1 — 构建 composed_tool_wrapper**

- 新建 `src/data_analysis/tool_wrappers.py`
- 组合以下逻辑为一个 `async def awrap_tool_call(request, handler)` 函数：
  1. **工具进度推送**：提取 `ToolProgressMiddleware._push_progress()` 逻辑
  2. **工具实例绑定**：提取 `SkillToolRegistryMiddleware.awrap_tool_call()` 逻辑
  3. **异常兜底**：提取 `GlobalExceptionMiddleware` + `MCPResilienceMiddleware` 逻辑
  4. **富输出处理**：提取 `RichOutputMiddleware._handle_tool_result()` 逻辑
- 各函数独立可测试
- 验证：`ruff check` + 单元测试每个 wrapper 逻辑

**Step 4.2 — 构建 ToolNode**

- 获取 MCP 业务工具列表
- 过滤为 `selected_skill_allowed_tools` 中的工具
- 创建 `ToolNode(filtered_tools, handle_tool_errors=True, awrap_tool_call=composed_tool_wrapper)`
- 验证：`python3 -m compileall`

**Step 4.3 — StreamWriter 在 ToolNode 中的传递**

- ToolNode 内的 `ToolRuntime` 对象包含 `stream_writer`
- 在 `composed_tool_wrapper` 中通过 `request.runtime.stream_writer` 获取 writer
- 验证：手动测试（langgraph dev）确认前端收到 progress 事件

### Phase 5：最终输出节点

**Step 5.1 — 实现 finalize_output**

- 从 state 的 messages 中提取最后一条可见 AIMessage（无 tool_calls）
- 通过 `StreamWriter` 推送 `progress` 事件
- 调用 finalizer 模型流式清理答案
- 逐 chunk 推送 `final_output_delta` 事件
- 推送 `final_output_done` 事件
- 退化处理：finalizer 失败时不修改 messages
- 复用 `FinalOutputCleanupMiddleware` 中的核心逻辑：
  - `_extract_last_visible_answer()` → 提取为独立函数
  - `build_final_output_cleanup_prompt()` → 直接复用
  - `_stream_cleaned_answer()` → 提取为独立函数

**Step 5.2 — 提取 finalizer 辅助函数**

- 新建 `src/data_analysis/finalizer.py`
- 从 `FinalOutputCleanupMiddleware` 提取：
  - `extract_last_visible_answer(state: dict) -> str`
  - `stream_cleaned_answer(answer: str, writer: Callable, model, timeout: int) -> str`
  - `build_final_output_cleanup_prompt()` 已在 `common.middleware` 中，直接复用
- 验证：单元测试

### Phase 6：集成与测试

**Step 6.1 — 替换 graph.py 入口**

- 删除 `create_deep_agent` 调用
- 用新的 `StateGraph` 编译图替代
- 保持 `langgraph.json` 入口不变（`src/data_analysis/graph.py:graph`）
- 验证：`python3 -m compileall src/data_analysis/graph.py`

**Step 6.2 — 清理不再需要的代码**

- 删除 `data_analysis/skill_discovery.py` 中的 `create_data_analysis_find_skill_tool` 函数（不再需要 `find_skill` 工具）
- 但保留 `_load_allowed_tools`、`_exposed_skill_path` 等辅助函数（`resolve_skill` 复用）
- 验证：`ruff check src/data_analysis/`

**Step 6.3 — 端到端手动测试**

- `langgraph dev` 启动
- 前端发送请求：不同 menu_name（broadcast-hour、integrated-index-ratio 等）
- 验证：
  - Skill 匹配正确
  - 工具调用正确（进度推送、富输出推送）
  - 最终输出清理正确（progress → final_output_delta → final_output_done）
  - 失配降级正确
  - 工具失败降级正确
  - REACT 循环最多 3 次

**Step 6.4 — 性能对比测试**

- 记录典型请求的：
  - 首 token 延迟
  - 总完成延迟
  - 模型调用次数
- 与当前 DeepAgents 版本对比
- 验证：延迟减少 >= 40%

### Phase 7：代码质量检查

**Step 7.1 — 全量 lint**

```bash
ruff check src/data_analysis/
python3 -m compileall src/data_analysis/
```

**Step 7.2 — 单元测试编写**

- `tests/unit_tests/test_data_analysis_resolve_skill.py`
- `tests/unit_tests/test_data_analysis_prepare_model.py`
- `tests/unit_tests/test_data_analysis_route_conditions.py`
- `tests/unit_tests/test_data_analysis_finalizer.py`
- `tests/unit_tests/test_data_analysis_tool_wrappers.py`

**Step 7.3 — 提交**

```bash
git add -A
git commit -m "[重构] data_analysis: LangGraph 节点流替代 DeepAgents agent loop"
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/data_analysis/state.py` | 新建 | DataAnalysisState TypedDict |
| `src/data_analysis/graph.py` | 重写 | StateGraph 替代 create_deep_agent |
| `src/data_analysis/nodes.py` | 新建 | 所有节点函数实现 |
| `src/data_analysis/prompt_builder.py` | 新建 | 系统提示词组装 |
| `src/data_analysis/tool_wrappers.py` | 新建 | 组合工具调用包装逻辑 |
| `src/data_analysis/finalizer.py` | 新建 | 最终输出清理逻辑 |
| `src/data_analysis/skill_discovery.py` | 简化 | 删除 `create_data_analysis_find_skill_tool`，保留辅助函数 |
| `src/data_analysis/menu_skill_mapping.py` | 不变 | 完全复用 |
| `src/common/middleware/*.py` | 不变 | 其他图继续使用 |
| `langgraph.json` | 不变 | 入口路径不变 |
| `tests/unit_tests/test_data_analysis_*.py` | 新建 | 单元测试 |

## 依赖关系

```
Phase 1 (骨架) ──→ Phase 2 (确定性节点) ──→ Phase 3 (LLM 节点)
                                                     │
                                                     ├──→ Phase 4 (工具执行)
                                                     │
                                                     ├──→ Phase 5 (最终输出)
                                                     │
                                                     └─→ Phase 6 (集成)
                                                            │
                                                            └─→ Phase 7 (质量检查)
```

Phase 2-5 可并行开发，但需要 Phase 1 的骨架先完成。

## 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| ToolNode 的 `awrap_tool_call` 中获取 `stream_writer` 可能方式不同 | 提前验证 ToolNode + awrap_tool_call + StreamWriter 的集成，Phase 4 优先手动测试 |
| finalizer 模型（mimo_v2_5_pro）在 finalize_output 节点中调用可能需特殊配置 | 复用 `FinalOutputCleanupMiddleware` 的 `astream()` 调用方式，提前验证 |
| MCP 工具刷新（`ensure_mcp_tools`）在 `prepare_model` 中调用可能阻塞 | 已改为异步，与 find_skill 的异步优化一致 |
| Skill 规则内容过长导致 system_prompt 超限 | 监控 token 量，必要时截断规则中的"全局总则"部分（Skill 规则文件已很长） |
| `iteration_count` reducer 需确认 LangGraph 的默认行为 | 无 reducer 注解 → `LastValue`（覆盖），确认测试通过 |
