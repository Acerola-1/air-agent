"""业务注册表回归：稳定映射、精确权限、默认拒绝与工具范围."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from common.business_registry import (  # noqa: E402
    BUSINESS_POLICY_VERSION,
    BUSINESSES,
    TRACING_EXCLUDED_TOOLS,
    available_businesses,
    business_allows_tool,
    business_for_graph,
    has_business_permission,
    registry_summary,
    validate_registry,
)


def test_registry_matches_current_three_graphs() -> None:
    validate_registry({"basic-qa", "data-analysis", "intelligent-tracing"})
    assert set(BUSINESSES) == {"basic-qa", "data-analysis", "intelligent-tracing"}
    assert business_for_graph("basic-qa").code == "basic-qa"
    assert business_for_graph("data-analysis").code == "data-analysis"
    assert business_for_graph("intelligent-tracing").code == "intelligent-tracing"


def test_unknown_and_stale_graphs_are_denied() -> None:
    assert business_for_graph("deep-research") is None
    assert business_for_graph("") is None
    with pytest.raises(ValueError):
        validate_registry({"basic-qa", "data-analysis"})  # 登记了不存在的图


def test_permissions_are_exact_and_not_prefix_or_admin_wildcard() -> None:
    assert has_business_permission({"agent:business:basic-qa:use"}, "basic-qa")
    assert not has_business_permission(
        {"agent:business:basic-qa:use:extra"}, "basic-qa"
    )
    assert not has_business_permission({"agent:business:basic-qa"}, "basic-qa")
    assert not has_business_permission({"agent:business:*"}, "basic-qa")
    # 平台管理员/root 身份不在本函数里特判；没有精确码一律不可用。
    assert not has_business_permission({"*", "super_admin"}, "basic-qa")


def test_available_businesses_stable_order_and_intersection() -> None:
    available = available_businesses(
        {
            "agent:business:data-analysis:use",
            "agent:business:basic-qa:use",
        }
    )
    assert [item.code for item in available] == ["basic-qa", "data-analysis"]


def test_tool_scope_is_business_specific() -> None:
    assert business_allows_tool(
        "basic-qa", "ipp", "mcp_city_common_get_air_quality_realtime_stat"
    )
    # Basic QA可按需独立加载完整已审核查询目录；仍非全量披露。
    assert business_allows_tool("basic-qa", "ipp", "mcp_station_analysis_trajectory")
    assert not business_allows_tool("basic-qa", "ipp", "unregistered_new_operation")
    # 溯源使用更窄的子集：轨迹/PSCF 在业务注册表就被排除。
    for tool in TRACING_EXCLUDED_TOOLS:
        assert not business_allows_tool("intelligent-tracing", "ipp", tool)
    # 其他业务仍按各自子集工作（data-analysis 保留完整已审核清单）。
    assert business_allows_tool(
        "data-analysis", "ipp", "mcp_station_analysis_trajectory"
    )
    # 溯源仍可使用站点/网格/气象等已审核查询工具。
    assert business_allows_tool(
        "intelligent-tracing", "ipp", "mcp_station_common_air_data"
    )
    assert business_allows_tool(
        "intelligent-tracing", "ipp", "mcp_grid_common_air_data"
    )
    assert not business_allows_tool("basic-qa", "unknown", "query")
    assert not business_allows_tool("unknown", "ipp", "query")


def test_registry_summary_has_no_secrets() -> None:
    summary = registry_summary()
    text = str(summary)
    assert "env:" not in text
    assert "credential" not in text
    assert BUSINESS_POLICY_VERSION in text


def test_basic_qa_full_reviewed_catalog_matches_frozen_v2_scope():
    import hashlib

    from common.business_registry import IPP_APPROVED_QUERY_TOOLS

    tools = business_for_graph("basic-qa").connection("ipp").allowed_tools
    assert tools == frozenset(IPP_APPROVED_QUERY_TOOLS)
    assert len(tools) == 109
    assert (
        hashlib.sha256("\n".join(sorted(tools)).encode()).hexdigest()
        == "41c411b97375e4318c477ca37ca82b1b0fe4d4ee31fbd4e874091b8b34cc6c42"
    )


def test_duplicate_graph_mapping_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from common import business_registry as registry

    duplicated = dict(registry.BUSINESSES)
    item = duplicated["data-analysis"]
    duplicated["data-analysis"] = type(item)(
        code=item.code,
        name_zh=item.name_zh,
        permission=item.permission,
        graph="basic-qa",  # 与原基础问答冲突
        connections=item.connections,
    )
    monkeypatch.setattr(registry, "BUSINESSES", duplicated)
    with pytest.raises(ValueError):
        validate_registry({"basic-qa", "data-analysis", "intelligent-tracing"})
