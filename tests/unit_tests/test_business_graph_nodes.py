"""business_graph 节点流单元测试.

覆盖：
- resolve_skill 单命中/多命中/未命中
- 工具过滤语义（native：allowed 为空时全量披露）
- check_permission 硬拒绝路由
- finalize_output 确定性清洗与事件契约（Java AgentStreamNormalizer 消费形状）
- Skill 规则内容缓存
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import common.business_graph.nodes as nodes_module
from common.business_graph.builder import (
    _route_after_intent,
    _route_after_model,
    _route_after_permission,
    _route_after_tools,
)
from common.business_graph.intent import (
    INTENT_CHITCHAT,
    INTENT_DATA_QUERY,
    INTENT_KNOWLEDGE,
    classify_intent_rule,
)
from common.business_graph.nodes import BusinessGraphNodes
from common.business_graph.output_cleanup import (
    deterministic_cleanup,
    has_process_residue,
)
from common.business_graph.skill_content import (
    clear_skill_rules_cache,
    load_skill_rules,
)
from common.skill_router import SkillRouteCandidate

pytestmark = pytest.mark.anyio


# Java AgentStreamNormalizer 识别的 custom 事件 type 全集
_JAVA_CUSTOM_EVENT_TYPES = {
    "progress",
    "permission_status",
    "final_output_delta",
    "final_output_done",
    "rich_output",
    "expanded_questions",
}


class _CollectingWriter:
    """收集 stream 事件的假 writer."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [event.get("type", "") for event in self.events]


def _make_nodes(router: Any = None) -> BusinessGraphNodes:
    return BusinessGraphNodes(
        router=router or SimpleNamespace(),
        base_system_prompt="测试系统提示",
        load_skill_tool=SimpleNamespace(name="load_skill"),
        graph_name="test-graph",
    )


def _make_skill_dir(tmp_path: Path, name: str, *, allowed_tools: str = "") -> Path:
    """构造最小 Skill 目录：SKILL.md + references/fast.md."""
    skill_dir = tmp_path / name
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 测试技能\nallowed-tools: {allowed_tools}\n---\n"
        f"{name} 的正文规则",
        encoding="utf-8",
    )
    (skill_dir / "references" / "fast.md").write_text(
        f"{name} 的 fast 规则", encoding="utf-8"
    )
    return skill_dir / "SKILL.md"


class _StubRouter:
    """语义路由 stub：固定候选 + 真实文件解析."""

    def __init__(self, candidates: list[SkillRouteCandidate], files: dict[str, Path]):
        self._candidates = candidates
        self._files = files

    async def amatch(self, question: str) -> list[SkillRouteCandidate]:
        return list(self._candidates)

    def resolve_skill_file(self, name: str) -> Path | None:
        return self._files.get(name)


def _candidate(name: str, tools: tuple[str, ...] = ()) -> SkillRouteCandidate:
    return SkillRouteCandidate(
        name=name,
        description=f"{name} 描述",
        path=f"/skills/{name}/SKILL.md",
        allowed_tools=tools,
        similarity_score=0.9,
    )


# ── 意图车道 ─────────────────────────────────────────────────


def test_classify_intent_rule_lanes() -> None:
    """意图分类：高置信信号进快车道，不确定一律 fail-open 到 data_query."""
    # 纯问候/寒暄 → chitchat
    for question in ("你好", "你是谁", "谢谢", "你能做什么", ""):
        intent, _ = classify_intent_rule(question)
        assert intent == INTENT_CHITCHAT, question

    # 知识问答（无时间无行政区）→ knowledge
    for question in ("什么是AQI", "怎么计算综合指数", "臭氧的危害有哪些"):
        intent, _ = classify_intent_rule(question)
        assert intent == INTENT_KNOWLEDGE, question

    # 数据查询 / 带时间或行政区的知识问题 / 问候前缀真实查询 → data_query
    for question in (
        "查询保定市今天的空气质量情况",
        "在吗，帮我查保定空气质量",
        "保定市的综合指数怎么计算",
        "昨天为什么污染重",
    ):
        intent, _ = classify_intent_rule(question)
        assert intent == INTENT_DATA_QUERY, question


def test_route_after_intent() -> None:
    assert _route_after_intent({"intent": INTENT_CHITCHAT}) == "prepare_model"
    assert _route_after_intent({"intent": INTENT_KNOWLEDGE}) == "prepare_model"
    assert _route_after_intent({"intent": INTENT_DATA_QUERY}) == "check_permission"
    # intent 缺失（如旧 checkpoint 重放）→ fail-open 全流程
    assert _route_after_intent({}) == "check_permission"


async def test_classify_intent_node_writes_state() -> None:
    nodes = _make_nodes()
    update = await nodes.classify_intent(
        {"messages": [HumanMessage(content="你好")]},
        {"configurable": {}},
        writer=_CollectingWriter(),
    )
    assert update == {"intent": INTENT_CHITCHAT}


async def test_prepare_model_lane_prompts() -> None:
    """闲聊/知识车道使用小提示词，不含权限/技能上下文，且不刷新 MCP."""
    nodes = _make_nodes()

    with patch.object(nodes_module, "ensure_mcp_tools") as mock_ensure:
        chitchat = await nodes.prepare_model(
            {"messages": [], "intent": INTENT_CHITCHAT},
            {"configurable": {}},
            writer=_CollectingWriter(),
        )
        knowledge = await nodes.prepare_model(
            {"messages": [], "intent": INTENT_KNOWLEDGE},
            {"configurable": {}},
            writer=_CollectingWriter(),
        )

    mock_ensure.assert_not_called()
    assert "问候/寒暄" in chitchat["system_prompt"]
    assert "permission_context" not in chitchat["system_prompt"]
    assert "knowledge_retriever_tool" in knowledge["system_prompt"]


def test_available_tools_lanes(monkeypatch) -> None:
    """车道化工具可见性：闲聊零工具；知识仅本地工具."""
    business = [
        _fake_tool("get_beijing_time"),
        _fake_tool("knowledge_retriever_tool"),
        _fake_tool("mcp_tool_a"),
    ]
    monkeypatch.setattr(nodes_module, "get_business_tools", lambda: business)
    nodes = _make_nodes()

    assert nodes._available_tools({"messages": [], "intent": INTENT_CHITCHAT}) == []
    names = [
        t.name
        for t in nodes._available_tools({"messages": [], "intent": INTENT_KNOWLEDGE})
    ]
    assert names == ["get_beijing_time", "knowledge_retriever_tool"]


# ── resolve_skill ───────────────────────────────────────────


async def test_resolve_skill_single_hit_inlines_rules(tmp_path: Path) -> None:
    clear_skill_rules_cache()
    skill_file = _make_skill_dir(tmp_path, "skill-a", allowed_tools="tool_x tool_y")
    router = _StubRouter(
        [_candidate("skill-a", ("tool_x", "tool_y"))], {"skill-a": skill_file}
    )
    nodes = _make_nodes(router)
    writer = _CollectingWriter()

    update = await nodes.resolve_skill(
        {"messages": [HumanMessage(content="平顶山今天的空气质量如何")]},
        {"configurable": {"chat_mode": "fast"}},
        writer=writer,
    )

    assert update["selected_skill"] == "skill-a"
    assert update["selected_skill_allowed_tools"] == ["tool_x", "tool_y"]
    assert update["skill_multi_candidates"] is False
    assert "skill-a 的正文规则" in update["skill_rules_content"]
    assert "skill-a 的 fast 规则" in update["skill_rules_content"]
    assert writer.types() == ["progress"]


async def test_resolve_skill_multi_hit_inlines_top1_and_summaries(
    tmp_path: Path,
) -> None:
    clear_skill_rules_cache()
    file_a = _make_skill_dir(tmp_path, "skill-a", allowed_tools="tool_x")
    file_b = _make_skill_dir(tmp_path, "skill-b", allowed_tools="tool_z")
    router = _StubRouter(
        [_candidate("skill-a", ("tool_x",)), _candidate("skill-b", ("tool_z",))],
        {"skill-a": file_a, "skill-b": file_b},
    )
    nodes = _make_nodes(router)

    update = await nodes.resolve_skill(
        {"messages": [HumanMessage(content="对比分析")]},
        {"configurable": {}},
        writer=_CollectingWriter(),
    )

    assert update["selected_skill"] == "skill-a"
    assert update["skill_multi_candidates"] is True
    # allowed_tools 为候选并集
    assert update["selected_skill_allowed_tools"] == ["tool_x", "tool_z"]
    # top1 完整规则内联 + 其他候选摘要
    assert "skill-a 的正文规则" in update["skill_rules_content"]
    assert "skill-b" in update["skill_rules_content"]
    assert "load_skill" in update["skill_rules_content"]


async def test_resolve_skill_no_match_degrades(tmp_path: Path) -> None:
    router = _StubRouter([], {})
    nodes = _make_nodes(router)

    update = await nodes.resolve_skill(
        {"messages": [HumanMessage(content="你好")]},
        {"configurable": {}},
        writer=_CollectingWriter(),
    )

    assert update["selected_skill"] is None
    assert update["skill_rules_content"] == ""
    assert update["selected_skill_allowed_tools"] == []
    assert update["skill_search_attempted"] is True


async def test_resolve_skill_skips_semantic_match_for_greeting(tmp_path: Path) -> None:
    """纯问候轮次不得触发语义匹配（回归：'你好' 曾被误命中业务 Skill）."""
    file_a = _make_skill_dir(tmp_path, "comparison-composition", allowed_tools="tool_x")

    class _RecordingRouter(_StubRouter):
        def __init__(self, files):
            super().__init__([_candidate("comparison-composition", ("tool_x",))], files)
            self.amatch_calls = 0

        async def amatch(self, question: str):
            self.amatch_calls += 1
            return list(self._candidates)

    router = _RecordingRouter({"comparison-composition": file_a})
    nodes = _make_nodes(router)

    for greeting in ("你好", "你是谁", "谢谢", "你能做什么"):
        update = await nodes.resolve_skill(
            {"messages": [HumanMessage(content=greeting)]},
            {"configurable": {}},
            writer=_CollectingWriter(),
        )
        assert update["selected_skill"] is None, greeting
        assert update["skill_rules_content"] == "", greeting

    # 门控生效：问候轮次完全不调用语义路由（省 embedding）
    assert router.amatch_calls == 0

    # 带问候前缀的真实查询不受影响，仍进入匹配并命中
    update = await nodes.resolve_skill(
        {"messages": [HumanMessage(content="在吗，帮我查保定市空气质量同比")]},
        {"configurable": {}},
        writer=_CollectingWriter(),
    )
    assert router.amatch_calls == 1
    assert update["selected_skill"] == "comparison-composition"


# ── 工具过滤语义 ────────────────────────────────────────────


def _fake_tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def test_available_tools_native_semantics(monkeypatch) -> None:
    """allowed 为空 → 全量业务工具（native 语义）；非空 → 交集 + 始终可见."""
    business = [
        _fake_tool("get_beijing_time"),
        _fake_tool("knowledge_retriever_tool"),
        _fake_tool("mcp_tool_a"),
        _fake_tool("mcp_tool_b"),
    ]
    monkeypatch.setattr(nodes_module, "get_business_tools", lambda: business)
    nodes = _make_nodes()

    # 未限制：全量披露
    names = [t.name for t in nodes._available_tools({"messages": []})]
    assert names == [
        "get_beijing_time",
        "knowledge_retriever_tool",
        "mcp_tool_a",
        "mcp_tool_b",
    ]

    # 限制 mcp_tool_a：交集 + 始终可见工具
    names = [
        t.name
        for t in nodes._available_tools(
            {"messages": [], "selected_skill_allowed_tools": ["mcp_tool_a"]}
        )
    ]
    assert names == ["get_beijing_time", "knowledge_retriever_tool", "mcp_tool_a"]

    # 多候选场景额外披露 load_skill
    names = [
        t.name
        for t in nodes._available_tools(
            {
                "messages": [],
                "selected_skill_allowed_tools": ["mcp_tool_a"],
                "skill_multi_candidates": True,
            }
        )
    ]
    assert names[-1] == "load_skill"


# ── check_permission 硬拒绝路由 ─────────────────────────────


async def test_check_permission_rejection_routes_to_finalize() -> None:
    nodes = _make_nodes()
    reject_update = {
        "permission_need": "need_check",
        "permission_result": {
            "permitted": False,
            "fix_strategy": "reject",
            "correction_text": "因数据权限限制，您无权访问该数据。",
        },
    }

    with patch.object(
        nodes_module._permission_reviewer,
        "abefore_agent",
        new=AsyncMock(return_value=dict(reject_update)),
    ):
        update = await nodes.check_permission(
            {"messages": [HumanMessage(content="查询全国数据")]},
            {"configurable": {}},
            writer=_CollectingWriter(),
        )

    assert update["permission_rejection"] == "因数据权限限制，您无权访问该数据。"
    assert _route_after_permission(update) == "finalize_output"


async def test_check_permission_pass_routes_to_resolve_skill() -> None:
    nodes = _make_nodes()
    with patch.object(
        nodes_module._permission_reviewer,
        "abefore_agent",
        new=AsyncMock(return_value={"permission_need": "no_check"}),
    ):
        update = await nodes.check_permission(
            {"messages": [HumanMessage(content="你好")]},
            {"configurable": {}},
            writer=_CollectingWriter(),
        )

    assert "permission_rejection" not in update
    assert _route_after_permission(update) == "resolve_skill"


# ── finalize_output 事件契约 ────────────────────────────────


async def test_finalize_output_deterministic_cleanup_and_contract() -> None:
    nodes = _make_nodes()
    writer = _CollectingWriter()
    raw_answer = (
        "<think>内部推理过程</think>今天平顶山空气质量为优。\n\n\n\n建议正常户外活动。"
    )

    result = await nodes.finalize_output(
        {
            "messages": [
                HumanMessage(content="平顶山今天空气质量"),
                AIMessage(content=raw_answer),
            ]
        },
        {"configurable": {"expand_question_enabled": False}},
        writer=writer,
    )

    types = writer.types()
    # 事件类型必须是 Java 端可识别集合的子集
    assert set(types) <= _JAVA_CUSTOM_EVENT_TYPES
    # 事件顺序：progress -> N 个 delta -> done
    assert types[0] == "progress"
    assert types[-1] == "final_output_done"
    deltas = [e["message"] for e in writer.events if e["type"] == "final_output_delta"]
    done = next(e for e in writer.events if e["type"] == "final_output_done")
    # delta 拼接与 done 全文一致（Java record 依赖该契约）
    assert "".join(deltas) == done["message"]
    # 思考标签已被规则清洗，业务内容保留
    assert "<think>" not in done["message"]
    assert "内部推理过程" not in done["message"]
    assert "今天平顶山空气质量为优。" in done["message"]
    assert "建议正常户外活动。" in done["message"]
    # 输出消息追加为 AIMessage
    assert result["messages"][0].content == done["message"]


async def test_finalize_output_rejection_short_circuit() -> None:
    nodes = _make_nodes()
    writer = _CollectingWriter()

    result = await nodes.finalize_output(
        {
            "messages": [HumanMessage(content="查询全国数据")],
            "permission_rejection": "因数据权限限制，您无权访问该数据。",
        },
        {"configurable": {"expand_question_enabled": False}},
        writer=writer,
    )

    done = next(e for e in writer.events if e["type"] == "final_output_done")
    assert done["message"] == "因数据权限限制，您无权访问该数据。"
    assert result["messages"][0].content == done["message"]


async def test_finalize_output_pushes_expanded_questions_after_done() -> None:
    nodes = _make_nodes()
    writer = _CollectingWriter()

    with patch.object(
        nodes_module,
        "_generate_expanded_questions",
        new=AsyncMock(return_value=["追问1", "追问2"]),
    ):
        await nodes.finalize_output(
            {
                "messages": [
                    HumanMessage(content="平顶山今天空气质量"),
                    AIMessage(content="今天空气质量为优。"),
                ]
            },
            {"configurable": {"expand_question_enabled": True}},
            writer=writer,
        )

    types = writer.types()
    assert "expanded_questions" in types
    # 追问事件在 final_output_done 之后推送，不阻塞正文
    assert types.index("expanded_questions") > types.index("final_output_done")
    expanded = next(e for e in writer.events if e["type"] == "expanded_questions")
    assert expanded["message"] == ["追问1", "追问2"]
    assert expanded["node"] == "expand_question"


# ── 确定性清洗与残留检测 ────────────────────────────────────


def test_deterministic_cleanup_strips_internal_blocks() -> None:
    text = (
        "<permission_context>{...}</permission_context>\n"
        "<tool_call>{...}</tool_call>\n结论：空气质量为良。"
    )
    assert deterministic_cleanup(text) == "结论：空气质量为良。"


def test_has_process_residue_detection() -> None:
    assert has_process_residue("我需要调用 find_skill 工具查询") is True
    assert has_process_residue("今天空气质量为优，建议户外活动。") is False


# ── 条件边 ──────────────────────────────────────────────────


def test_route_after_model_and_tools() -> None:
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[{"name": "mcp_tool_a", "args": {}, "id": "call-1"}],
    )
    assert _route_after_model({"messages": [tool_call_msg]}) == "execute_tools"
    assert (
        _route_after_model({"messages": [AIMessage(content="答案")]})
        == "finalize_output"
    )
    assert _route_after_tools({"messages": []}) == "finalize_output"


# ── Skill 规则缓存 ──────────────────────────────────────────


def test_load_skill_rules_uses_mtime_cache(tmp_path: Path) -> None:
    clear_skill_rules_cache()
    skill_file = _make_skill_dir(tmp_path, "skill-a")
    router = _StubRouter([], {"skill-a": skill_file})

    first = load_skill_rules(router, "skill-a", "fast")
    assert "skill-a 的正文规则" in first

    # 文件内容变化 + mtime 前移 → 缓存失效并重新读取
    skill_file.write_text(
        "---\nname: skill-a\ndescription: 测试技能\n---\n更新后的规则",
        encoding="utf-8",
    )
    stat = skill_file.stat()
    os.utime(skill_file, (stat.st_atime + 10, stat.st_mtime + 10))
    second = load_skill_rules(router, "skill-a", "fast")
    assert "更新后的规则" in second

    # 未知 Skill 返回空
    assert load_skill_rules(router, "unknown", "fast") == ""
