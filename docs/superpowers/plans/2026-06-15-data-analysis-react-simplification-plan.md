# data_analysis ReAct 流程简化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `src/data_analysis/` 从 ReAct 循环图简化为线性 StateGraph 流程，删除冗余组件，保留核心功能。

**Architecture:** 删除循环节点和条件边，改为 `START → resolve_skill → prepare_model → call_model → [execute_tools] → output → END` 的线性流程。`call_model` 内部保留 API 重试，输出节点直接推送原始回复。

**Tech Stack:** Python, LangGraph, LangChain, pytest, ruff

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/data_analysis/state.py` | **Modify** | 删除 `iteration_count` 和 `available_tools` 字段 |
| `src/data_analysis/nodes.py` | **Modify** | 删除 `route_after_model`、`route_after_tools`、`increment_iteration`、`_stream_cleaned_answer`；重写 `call_model`（简化重试逻辑）；重写 `finalize_output`（删除二次 LLM 清理） |
| `src/data_analysis/graph.py` | **Modify** | 删除循环节点和条件边，改为线性流程 |
| `src/data_analysis/skill_discovery.py` | **Delete** | `resolve_skill` 已完全替代 `find_skill` |
| `tests/unit_tests/test_data_analysis_menu_skill_discovery.py` | **Delete** | 测试 `find_skill` 工具，该工具已删除 |
| `tests/unit_tests/test_business_graph_config.py` | **Modify** | 更新 data-analysis 图的断言 |

---

## Task 1: 简化 State 定义

**Files:**
- Modify: `src/data_analysis/state.py`
- Test: `tests/unit_tests/test_business_graph_config.py` (间接验证)

- [ ] **Step 1: 删除 `iteration_count` 和 `available_tools` 字段**

修改 `src/data_analysis/state.py`，删除两个字段：

```python
class DataAnalysisState(TypedDict):
    """data_analysis LangGraph 节点流状态.

    字段说明:
        messages: 对话消息列表，使用 add_messages reducer 合并
        selected_skill: 当前匹配到的 Skill 名称
        selected_skill_path: Skill 在后端可见的虚拟路径
        selected_skill_description: Skill 业务描述
        selected_skill_allowed_tools: Skill 允许的业务工具名列表
        skill_rules_content: Skill 规则文件全文（references/fast.md 或 expert.md）
        skill_search_attempted: 是否已尝试 Skill 匹配
        skill_search_question: 传入的问题文本
        system_prompt: 组装后的完整系统提示词
    """

    messages: Annotated[list[AnyMessage], add_messages]
    selected_skill: NotRequired[str | None]
    selected_skill_path: NotRequired[str | None]
    selected_skill_description: NotRequired[str | None]
    selected_skill_allowed_tools: NotRequired[list[str]]
    skill_rules_content: NotRequired[str]
    skill_search_attempted: NotRequired[bool]
    skill_search_question: NotRequired[str]
    system_prompt: NotRequired[str]
```

- [ ] **Step 2: 验证编译通过**

Run: `python3 -m compileall src/data_analysis/state.py`
Expected: Compiled OK

- [ ] **Step 3: Commit**

```bash
git add src/data_analysis/state.py
git commit -m "refactor(data_analysis): 删除 iteration_count 和 available_tools 字段"
```

---

## Task 2: 重写 nodes.py（删除循环相关函数，简化 call_model 和 finalize_output）

**Files:**
- Modify: `src/data_analysis/nodes.py`
- Test: `tests/unit_tests/test_business_graph_config.py`

- [ ] **Step 1: 删除导入的循环相关函数**

从 `src/data_analysis/nodes.py` 中删除以下导入和函数：

**删除的导入：**
```python
# 删除这些导入（如果 nodes.py 中没有直接用到）
from data_analysis.state import MAX_ITERATIONS  # 常量也删除
```

**删除的函数：**
- `route_after_model` — 条件路由函数
- `route_after_tools` — 条件路由函数
- `increment_iteration` — 已在 graph.py 中定义，graph.py 也会删除
- `_stream_cleaned_answer` — 二次 LLM 清理函数

**删除的常量：**
- `MAX_ITERATIONS = 3` — 从 `state.py` 移入的常量，现在不需要了

- [ ] **Step 2: 重写 `call_model` 函数**

简化后的 `call_model`：

```python
import asyncio

async def call_model(
    state: DataAnalysisState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """LLM 推理：决定调工具 / 直接输出.

    从 state 取 messages、system_prompt，
    按 selected_skill_allowed_tools 过滤业务工具，
    组装完整消息列表后调用模型。

    API 调用失败时内部重试最多 3 次。
    模型返回空内容且无 tool_calls 时，追加引导消息重试一次。
    """
    model = ModelRegistry.mimo_v2_5_pro
    system_prompt = state.get("system_prompt", "")

    # 按 allowed-tools 过滤业务工具（运行时查找，不存入 state）
    allowed = state.get("selected_skill_allowed_tools") or []
    all_tools = get_business_tools()
    available = filter_business_tools(all_tools, allowed)

    messages = state["messages"]
    full_messages: list[Any] = []
    if system_prompt:
        full_messages.append(SystemMessage(content=system_prompt))
    full_messages.extend(messages)

    # 绑定工具
    if available:
        model_with_tools = model.bind_tools(available)
    else:
        model_with_tools = model

    # API 重试最多 3 次
    response = None
    for attempt in range(3):
        try:
            response = await model_with_tools.ainvoke(full_messages)
            break
        except Exception as exc:
            logger.exception("数据分析模型调用失败 (attempt {}/3): {}", attempt + 1, exc)
            if attempt == 2:
                return {"messages": [AIMessage(content=MODEL_USER_MESSAGE)]}
            await asyncio.sleep(0.5 * (attempt + 1))  # 指数退避

    assert response is not None, "response should be set after successful API call"

    # 空回复检测：content 为空且无 tool_calls -> 追加引导重试一次
    if (
        isinstance(response, AIMessage)
        and not response.tool_calls
        and not get_message_content(response).strip()
    ):
        logger.info("数据分析模型空回复，追加引导消息重试")
        nudge_messages = [
            *full_messages,
            response,
            HumanMessage(content=_EMPTY_REPLY_NUDGE),
        ]
        try:
            nudge_response = await model_with_tools.ainvoke(nudge_messages)
            if (
                isinstance(nudge_response, AIMessage)
                and not nudge_response.tool_calls
                and not get_message_content(nudge_response).strip()
            ):
                logger.warning("数据分析模型空回复重试仍为空，使用兜底消息")
                response = AIMessage(content=_FALLBACK_EMPTY_REPLY)
            else:
                response = nudge_response
        except Exception as exc:
            logger.warning("数据分析模型空回复重试失败: {}，使用兜底消息", exc)
            response = AIMessage(content=_FALLBACK_EMPTY_REPLY)

    return {"messages": [response]}
```

- [ ] **Step 3: 重写 `finalize_output` 函数（删除二次 LLM 清理）**

```python
async def finalize_output(
    state: DataAnalysisState,
    config: RunnableConfig,
    *,
    writer: StreamWriter,
) -> dict[str, Any]:
    """最终输出：提取答案并流式推送.

    直接输出原始模型回复，不调用二次 LLM 清理。
    通过 StreamWriter 推送流式事件，与其他模块保持一致。
    """
    answer = _extract_last_visible_answer(state)

    # 模型空回复兜底
    if not answer:
        logger.warning("数据分析未找到可见业务答案，使用兜底回复")
        answer = _FALLBACK_EMPTY_REPLY

    # 推送进度事件
    writer(
        {
            "node": FINAL_OUTPUT_CLEANUP_NODE,
            "type": "progress",
            "message": FINAL_OUTPUT_CLEANUP_PROGRESS_MESSAGE,
        }
    )

    # 流式推送答案（直接输出原始内容）
    cleaned = answer.strip()
    if cleaned:
        writer(
            {
                "node": FINAL_OUTPUT_CLEANUP_NODE,
                "type": "final_output_delta",
                "message": cleaned,
            }
        )
        writer(
            {
                "node": FINAL_OUTPUT_CLEANUP_NODE,
                "type": "final_output_done",
                "message": cleaned,
            }
        )

    # 将结果写入 messages
    return {"messages": [AIMessage(content=cleaned)]}
```

- [ ] **Step 4: 删除 `graph.py` 中引用的循环相关函数**

从 `src/data_analysis/graph.py` 的导入中删除：

```python
# 删除以下导入
from data_analysis.nodes import (
    # ... 保留的函数 ...
    route_after_model,      # 删除
    route_after_tools,      # 删除
    # increment_iteration 在 graph.py 中定义，也删除
)
```

- [ ] **Step 5: 验证编译通过**

Run: `python3 -m compileall src/data_analysis/nodes.py`
Expected: Compiled OK

- [ ] **Step 6: Commit**

```bash
git add src/data_analysis/nodes.py
git commit -m "refactor(data_analysis): 删除循环路由函数，简化 call_model 和 finalize_output"
```

---

## Task 3: 重写 graph.py（线性流程）

**Files:**
- Modify: `src/data_analysis/graph.py`
- Test: `tests/unit_tests/test_business_graph_config.py`

- [ ] **Step 1: 重写 graph.py 为线性流程**

```python
"""结构化数据分析业务图入口（LangGraph 节点流实现）.

极简线性流程：
    START -> resolve_skill -> prepare_model -> call_model -> [execute_tools] -> finalize_output -> END

无 ReAct 循环，无迭代计数，无二次 LLM 清理。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import AIMessage
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode
from loguru import logger

from common.config.checkpointing import get_checkpointer
from common.runtime_tools import get_business_tools
from data_analysis.nodes import (
    call_model,
    finalize_output,
    prepare_model,
    resolve_skill,
)
from data_analysis.state import DataAnalysisState
from data_analysis.tool_wrappers import composed_tool_wrapper

SKILLS_DIR = Path(__file__).parent / "skills"


def _build_tool_node() -> ToolNode:
    """构建工具执行节点.

    使用 composed_tool_wrapper 组合以下逻辑:
    - 工具进度推送
    - 动态工具实例绑定
    - MCP/通用异常兜底
    - 富输出推送 + ToolMessage 压缩
    """
    return ToolNode(
        tools=get_business_tools(),
        handle_tool_errors=True,
        awrap_tool_call=composed_tool_wrapper,
    )


def _route_after_model(state: DataAnalysisState) -> Literal["execute_tools", "finalize_output"]:
    """条件边：call_model 后路由——有 tool_calls -> execute_tools；无 -> finalize_output."""
    messages = state.get("messages", [])
    if not messages:
        return "finalize_output"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "execute_tools"
    return "finalize_output"


def build_graph() -> StateGraph:
    """构建 data_analysis 节点流图（线性流程）."""
    builder = StateGraph(DataAnalysisState)

    # 添加节点
    builder.add_node("resolve_skill", resolve_skill)
    builder.add_node("prepare_model", prepare_model)
    builder.add_node("call_model", call_model)
    builder.add_node("execute_tools", _build_tool_node())
    builder.add_node("finalize_output", finalize_output)

    # 入口
    builder.add_edge(START, "resolve_skill")

    # 确定性链
    builder.add_edge("resolve_skill", "prepare_model")
    builder.add_edge("prepare_model", "call_model")

    # 条件边：call_model 后路由（仅一次，不循环）
    builder.add_conditional_edges(
        "call_model",
        _route_after_model,
        {
            "execute_tools": "execute_tools",
            "finalize_output": "finalize_output",
        },
    )

    # 工具执行后直接输出
    builder.add_edge("execute_tools", "finalize_output")

    # 出口
    builder.set_finish_point("finalize_output")

    return builder


graph = build_graph().compile(checkpointer=get_checkpointer())
logger.info("data_analysis 节点流图编译完成")
```

- [ ] **Step 2: 验证编译通过**

Run: `python3 -m compileall src/data_analysis/graph.py`
Expected: Compiled OK

- [ ] **Step 3: Commit**

```bash
git add src/data_analysis/graph.py
git commit -m "refactor(data_analysis): 重写 graph.py 为线性流程，删除 ReAct 循环"
```

---

## Task 4: 删除 skill_discovery.py

**Files:**
- Delete: `src/data_analysis/skill_discovery.py`
- Delete: `tests/unit_tests/test_data_analysis_menu_skill_discovery.py`

- [ ] **Step 1: 确认无其他引用**

Run:
```bash
grep -r "from data_analysis.skill_discovery" src/ tests/ --include="*.py"
grep -r "import data_analysis.skill_discovery" src/ tests/ --include="*.py"
```
Expected: 无引用（`resolve_skill` 已完全替代）

- [ ] **Step 2: 删除文件**

```bash
git rm src/data_analysis/skill_discovery.py
git rm tests/unit_tests/test_data_analysis_menu_skill_discovery.py
```

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor(data_analysis): 删除 skill_discovery.py 及其测试（resolve_skill 已完全替代）"
```

---

## Task 5: 更新 test_business_graph_config.py

**Files:**
- Modify: `tests/unit_tests/test_business_graph_config.py`

- [ ] **Step 1: 更新 data-analysis 图的断言**

修改 `test_business_graph_configs_have_graph_level_skills_and_prompts` 函数中关于 data-analysis 的断言：

```python
def test_business_graph_configs_have_graph_level_skills_and_prompts() -> None:
    for graph_name, graph_file in DEEP_AGENT_GRAPHS.items():
        source = graph_file.read_text(encoding="utf-8")
        assert 'SKILLS_DIR = Path(__file__).parent / "skills"' in source
        assert 'skills=["/skills/"]' in source
        assert "tools=[find_skill]" in source
        assert "with_main_agent_tool_use_output_guard" in source
        assert 'unmatched_policy="native"' in source
        assert "你是中科宇图的空气质量数据查询助手" in source
        assert "rendered_text" not in source
        assert "subagent_type" not in source

    # data-analysis 使用 StateGraph 节点流
    da_graph = STATE_GRAPHS["data-analysis"].read_text(encoding="utf-8")
    da_nodes = Path("src/data_analysis/nodes.py").read_text(encoding="utf-8")
    da_prompt_builder = Path("src/data_analysis/prompt_builder.py").read_text(encoding="utf-8")
    assert 'SKILLS_DIR = Path(__file__).parent / "skills"' in da_graph
    # skill 发现逻辑在 nodes.py 中
    assert "resolve_skill" in da_graph
    assert "match_menu_skill" in da_nodes
    assert "with_data_analysis_output_guard" in da_prompt_builder
    assert "with_main_agent_tool_use_output_guard" not in da_graph
    assert 'unmatched_policy="native"' not in da_graph
    # 确认无 ReAct 循环相关代码
    assert "increment_iteration" not in da_graph
    assert "route_after_tools" not in da_graph
```

修改 `test_business_graphs_use_final_output_cleanup_middleware`：

```python
def test_business_graphs_use_final_output_cleanup_middleware() -> None:
    for graph_file in DEEP_AGENT_GRAPHS.values():
        source = graph_file.read_text(encoding="utf-8")

        assert "FinalOutputCleanupMiddleware" in source
        assert "FinalOutputCleanupMiddleware()" in source

    # data-analysis 使用 finalize_output 节点替代（直接输出，无二次 LLM）
    da_graph = STATE_GRAPHS["data-analysis"].read_text(encoding="utf-8")
    da_nodes = Path("src/data_analysis/nodes.py").read_text(encoding="utf-8")
    assert "finalize_output" in da_graph
    assert "FINAL_OUTPUT_CLEANUP_NODE" in da_nodes
    # 确认无二次 LLM 清理
    assert "_stream_cleaned_answer" not in da_nodes
```

- [ ] **Step 2: 运行测试**

Run: `.venv/bin/python -m pytest tests/unit_tests/test_business_graph_config.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit_tests/test_business_graph_config.py
git commit -m "test(data_analysis): 更新 business_graph_config 测试断言"
```

---

## Task 6: 运行完整测试和代码质量检查

**Files:**
- All modified files

- [ ] **Step 1: 运行 ruff 检查**

Run:
```bash
ruff check src/data_analysis/ tests/unit_tests/test_business_graph_config.py
ruff format src/data_analysis/ tests/unit_tests/test_business_graph_config.py
```
Expected: No errors

- [ ] **Step 2: 运行 pyright 检查**

Run:
```bash
pyright src/data_analysis/
```
Expected: No errors

- [ ] **Step 3: 运行相关单元测试**

Run:
```bash
.venv/bin/python -m pytest tests/unit_tests/test_business_graph_config.py tests/unit_tests/test_data_analysis_output_guard.py -v
```
Expected: PASS

- [ ] **Step 4: 验证图编译**

Run:
```bash
.venv/bin/python -c "from data_analysis.graph import graph; print('Graph compiled successfully')"
```
Expected: `Graph compiled successfully`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(data_analysis): 完成 ReAct 流程简化，删除循环，改为线性流程"
```

---

## Self-Review Checklist

- [ ] **Spec coverage**: 每个设计目标都有对应的任务
  - 极简流程 ✅ Task 3
  - 删除冗余 ✅ Task 2, 3, 4
  - 保留重试 ✅ Task 2 (call_model 内部重试)
  - 统一输出 ✅ Task 2 (finalize_output 流式推送)
  - 保留降级 ✅ Task 3 (ToolNode + composed_tool_wrapper)

- [ ] **Placeholder scan**: 无 "TBD", "TODO", "implement later"

- [ ] **Type consistency**: `DataAnalysisState` 字段名在所有文件中一致

- [ ] **Import 一致性**: `graph.py` 中导入的函数与 `nodes.py` 中导出的函数匹配

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-15-data-analysis-react-simplification-plan.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration. **REQUIRED SUB-SKILL: superpowers:subagent-driven-development**

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints. **REQUIRED SUB-SKILL: superpowers:executing-plans**

Which approach?
