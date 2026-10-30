"""真实模型评测发现的确定性缺陷回归；不依赖外部模型或正式数据。"""

import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from basic_qa.tool_recovery import (
    exhausted_tools,
    planning_exhausted,
    recoverable_tools,
)
from common.capabilities import filter_capability_mcp_tools
from common.mcp_scoped import ScopedMCPToolFactory, set_factory


def test_business_disclosure_without_prepare_node_mutations():
    """模拟节点间 config 不保留临时授权目录，仍不能披露其它业务工具。"""
    tools = [
        SimpleNamespace(
            name=name,
            metadata={
                "is_mcp": True,
                "server_key": "ipp",
                "stable_id": f"mcp:ipp:{name}",
            },
        )
        for name in [
            "helper_get_latest_time",
            "mcp_city_analysis_monitor",
            "synthetic_unregistered_write",
        ]
    ]
    local = SimpleNamespace(name="get_beijing_time", metadata={})
    config = {"configurable": {"capability_authorized_servers": ["ipp"]}}
    set_factory(ScopedMCPToolFactory())
    try:
        result = filter_capability_mcp_tools(
            [*tools, local], config, business_code="basic-qa"
        )
        assert [t.name for t in result] == [
            "helper_get_latest_time",
            "mcp_city_analysis_monitor",
            "get_beijing_time",
        ]
        allowed = filter_capability_mcp_tools(
            tools, config, business_code="data-analysis"
        )
        assert len(allowed) == 2
        config["configurable"].update(
            capability_mcp_mode="allowlist", capability_tool_ids=[]
        )
        assert (
            filter_capability_mcp_tools(tools, config, business_code="data-analysis")
            == []
        )
    finally:
        set_factory(None)


def rejected(name, code):
    return ToolMessage(
        name=name,
        content=json.dumps({"code": code, "success": False}),
        tool_call_id="call",
    )


def test_tool_budget_allows_one_parameter_correction_and_resets_next_turn():
    messages = [
        HumanMessage(content="查询"),
        rejected("denied", 403),
        rejected("bad", 422),
    ]
    assert exhausted_tools(messages) == {"denied"}
    messages.append(rejected("bad", 400))
    assert exhausted_tools(messages) == {"denied", "bad"}
    tools = [SimpleNamespace(name=n) for n in ["denied", "bad", "good"]]
    assert [t.name for t in recoverable_tools(tools, messages)] == ["good"]
    messages.append(HumanMessage(content="下一问"))
    assert exhausted_tools(messages) == set()


def test_successful_correction_resets_consecutive_validation_failures():
    messages = [
        HumanMessage(content="查询"),
        rejected("q", 422),
        ToolMessage(name="q", tool_call_id="ok", content='{"code":200,"data":[1]}'),
        rejected("q", 422),
    ]
    assert exhausted_tools(messages) == set()


def test_planning_rounds_have_a_per_turn_limit():
    plan = AIMessage(content="", tool_calls=[{"name": "q", "id": "c", "args": {}}])
    messages = [HumanMessage(content="查询"), *([plan] * 7)]
    assert not planning_exhausted(messages)
    assert planning_exhausted([*messages, plan])
    assert not planning_exhausted([*messages, plan, HumanMessage(content="下一问")])


def test_analysis_menu_contract_matches_backend_mapping():
    from data_analysis.menu_skill_mapping import DATA_ANALYSIS_SKILL_MAPPINGS

    root = Path(__file__).resolve().parents[2]
    menus = json.loads((root / "frontend/lib/data-analysis-menus.json").read_text())
    expected = {
        (name, f"skill:data-analysis:{m.skill_name}")
        for m in DATA_ANALYSIS_SKILL_MAPPINGS
        for name in m.menu_names
    }
    assert {(m["value"], m["skillId"]) for m in menus} == expected
    assert len(menus) == len(expected)
