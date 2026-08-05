# data_analysis ReAct 流程简化设计文档

**日期**: 2026-06-15
**主题**: data_analysis 图结构简化——删除 ReAct 循环，实现极简 StateGraph 流程
**作者**: Claude Code (brainstorming skill)

---

## 1. 背景与动机

当前 `src/data_analysis/` 实现了一个完整的 ReAct 循环（`call_model → execute_tools → increment_iteration → call_model`，最多 3 轮），但存在以下问题：

- **循环逻辑有 bug**：`route_after_model` 和 `route_after_tools` 中的迭代计数检查不一致
- **过度设计**：数据分析场景下，Skill 规则已足够详细，模型通常能一次决策正确
- **维护负担重**：`find_skill` 工具（DeepAgents 渐进式披露）与 `resolve_skill` 节点功能重叠
- **finalize_output 二次 LLM 清理**：增加延迟和成本，且与其他模块输出方式不一致

用户明确要求：**大道至简，删除 ReAct 逻辑，最快产出**。

---

## 2. 设计目标

| 目标 | 说明 |
|------|------|
| 极简流程 | 删除循环，改为一次性线性流程 |
| 删除冗余 | 移除 `find_skill` 工具、`increment_iteration` 节点、二次 LLM 清理 |
| 保留重试 | 模型 API 调用失败时内部重试最多 3 次 |
| 统一输出 | 通过 `StreamWriter` 推送流式事件 + 完整结果，与其他模块一致 |
| 保留降级 | 工具执行异常时返回降级消息，不中断流程 |

---

## 3. 架构设计

### 3.1 图结构（简化后）

```
┌───────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌───────┐
│ START │────▶│ resolve_skill│────▶│ prepare_model│────▶│ call_model   │────▶│ execute_tools│────▶│ output │────▶│  END  │
└───────┘     └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘     └───────┘     └───────┘
                                                              │
                                                              ▼ (无 tool_calls 时直接跳过)
                                                        ┌─────────────┐
                                                        │ finalize    │
                                                        │ _output     │
                                                        └─────────────┘
```

### 3.2 节点说明

| 节点 | 职责 | 变更 |
|------|------|------|
| `resolve_skill` | 确定性匹配 Skill，读取规则文件 | **保留**（当前实现已正确） |
| `prepare_model` | 组装 system_prompt，刷新 MCP 工具 | **保留**（当前实现已正确） |
| `call_model` | 调用 LLM，内部重试最多 3 次 | **修改**：删除循环路由，简化空回复处理 |
| `execute_tools` | 执行工具调用（ToolNode） | **保留**，但不再循环回模型 |
| `output` | 流式推送最终答案 | **新增**：替代 `finalize_output` 的二次 LLM 清理 |

### 3.3 删除的组件

| 组件 | 原因 |
|------|------|
| `increment_iteration` 节点 | 无循环，不需要迭代计数 |
| `route_after_model` 条件边 | 无循环，不需要条件路由 |
| `route_after_tools` 条件边 | 无循环，工具执行后直接输出 |
| `find_skill` 工具（`skill_discovery.py`） | `resolve_skill` 已完全替代 |
| `finalize_output` 的二次 LLM 清理 | 用户要求直接输出 |
| `MAX_ITERATIONS` 常量 | 无循环，不需要 |

---

## 4. 数据流

### 4.1 正常流程

```
1. resolve_skill: menu_name → Skill 路径 + allowed_tools + 规则内容
2. prepare_model: 组装 system_prompt（含 Skill 规则 + 时间上下文 + mode 上下文）
3. call_model: 调用 LLM（带工具绑定）
   - 有 tool_calls → 进入 execute_tools
   - 无 tool_calls → 直接进入 output
4. execute_tools: ToolNode 执行工具（含异常降级）
5. output: 提取最终答案，流式推送
```

### 4.2 异常流程

| 场景 | 处理 |
|------|------|
| 模型 API 调用失败 | `call_model` 内部重试最多 3 次，仍失败则返回降级消息 |
| 模型空回复 | 追加引导消息重试一次，仍空则返回兜底消息 |
| 工具执行失败 | `composed_tool_wrapper` 返回降级 ToolMessage，流程继续 |
| Skill 匹配失败 | `resolve_skill` 返回空规则，模型使用默认提示词回答 |

---

## 5. 状态定义（DataAnalysisState）

```python
class DataAnalysisState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    selected_skill: NotRequired[str | None]
    selected_skill_path: NotRequired[str | None]
    selected_skill_description: NotRequired[str | None]
    selected_skill_allowed_tools: NotRequired[list[str]]
    skill_rules_content: NotRequired[str]
    skill_search_attempted: NotRequired[bool]
    skill_search_question: NotRequired[str]
    system_prompt: NotRequired[str]
    # 删除: iteration_count, available_tools
```

---

## 6. 输出节点设计

### 6.1 `output` 节点职责

1. 从消息历史中提取最后一条可见答案（复用 `_extract_last_visible_answer`）
2. 通过 `StreamWriter` 推送：
   - `"type": "progress"` — 进度事件（可选）
   - `"type": "final_output_delta"` — 流式输出（直接输出原始内容）
   - `"type": "final_output_done"` — 输出完成
3. 将最终答案写入 messages（作为 AIMessage）

### 6.2 与其他模块的一致性

- 使用相同的 `FINAL_OUTPUT_CLEANUP_NODE` 和 `FINAL_OUTPUT_CLEANUP_PROGRESS_MESSAGE` 常量
- 使用相同的 `StreamWriter` 推送格式
- 不调用二次 LLM，直接输出原始模型回复

---

## 7. 重试机制

### 7.1 模型调用重试（`call_model` 内部）

```python
async def call_model(state, config):
    for attempt in range(3):
        try:
            response = await model_with_tools.ainvoke(full_messages)
            break
        except Exception:
            if attempt == 2:
                return {"messages": [AIMessage(content="服务暂不可用，请稍后重试。")]}
            await asyncio.sleep(0.5 * (attempt + 1))  # 指数退避
    # ...处理空回复...
```

### 7.2 空回复重试

- 模型返回空内容且无 tool_calls → 追加引导消息重试一次
- 仍空 → 返回兜底消息 `_FALLBACK_EMPTY_REPLY`

---

## 8. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `src/data_analysis/graph.py` | **重写** | 删除循环节点和条件边，改为线性流程 |
| `src/data_analysis/nodes.py` | **修改** | 删除 `route_after_model`、`route_after_tools`、`increment_iteration`；重写 `call_model`（简化重试）；重写 `finalize_output`（删除二次 LLM） |
| `src/data_analysis/state.py` | **修改** | 删除 `iteration_count` 和 `available_tools` 字段 |
| `src/data_analysis/skill_discovery.py` | **删除** | `resolve_skill` 已完全替代 `find_skill` |
| `src/data_analysis/tool_wrappers.py` | **保留** | `composed_tool_wrapper` 仍然有效 |
| `src/data_analysis/prompt_builder.py` | **保留** | `build_system_prompt` 和 `filter_business_tools` 仍然有效 |
| `src/data_analysis/menu_skill_mapping.py` | **保留** | 菜单映射逻辑不变 |
| `tests/unit_tests/test_data_analysis_menu_skill_discovery.py` | **删除** | 测试 `find_skill` 工具，该工具已删除 |
| `tests/unit_tests/test_data_analysis_output_guard.py` | **保留** | 输出护栏测试不受影响 |
| `tests/unit_tests/test_business_graph_config.py` | **修改** | 更新 data-analysis 图的断言 |

---

## 9. 测试策略

1. **编译测试**：确保 `graph.py` 能正确编译
2. **图结构测试**：验证节点和边的连接正确
3. **节点单元测试**：
   - `resolve_skill`：菜单匹配成功/失败场景
   - `call_model`：正常调用、API 失败重试、空回复处理
   - `output`：正确提取答案并推送流式事件
4. **集成测试**：端到端验证完整流程

---

## 10. 风险评估

| 风险 | 缓解措施 |
|------|----------|
| 删除 ReAct 后模型无法基于工具结果修正答案 | 数据分析场景下 Skill 规则足够详细，一次决策通常正确；工具失败时返回降级消息 |
| 删除 `find_skill` 工具影响其他模块 | `find_skill` 仅在 `data_analysis` 中使用，其他模块使用 `common/skill_discovery.py` |
| 直接输出原始内容质量下降 | 前端已有数据展示，AI 分析结论不需要过度格式化 |

---

## 11. 决策记录

- **采用方案 A（极简 StateGraph）**：用户明确要求"大道至简，删除 ReAct 逻辑"
- **删除 `find_skill` 工具**：`resolve_skill` 已完全替代，无其他依赖
- **删除二次 LLM 清理**：用户要求直接输出，保持与其他模块一致的流式输出方式
- **保留 `composed_tool_wrapper`**：工具降级和富输出逻辑仍然需要

---

*设计完成，等待实现计划。*
