"""可信绑定、依赖分类和已受理目录共同限制 Skill 的工具范围。"""

import pytest

from common.skill_tool_scope import (
    ScopedTool,
    ToolKind,
    ToolScopeError,
    registered_required_tool_ids,
    resolve_skill_tools,
    resolve_tool,
)


def test_current_required_ids_match_admitted_mcp_selection_identity():
    assert registered_required_tool_ids("basic-qa", ["helper_get_latest_time"]) == [
        "mcp:ipp:helper_get_latest_time"
    ]
    with pytest.raises(ToolScopeError):
        registered_required_tool_ids("basic-qa", ["chart_unregistered"])


def test_audited_unregistered_chart_has_no_fabricated_id():
    scope = resolve_skill_tools(
        allowed=["chart_absent"],
        required=[],
        optional=["chart_absent"],
        catalog=[],
        unavailable_chart_names=["chart_absent"],
    )
    assert scope.allowed_ids == frozenset()
    assert scope.unavailable_optional == ("chart_absent",)
    with pytest.raises(ToolScopeError):
        resolve_skill_tools(
            allowed=["query"],
            required=[],
            optional=["query"],
            catalog=[],
            unavailable_chart_names=["query"],
        )


def test_remote_general_name_is_still_domain():
    assert (
        ScopedTool("local:get_beijing_time", "get_beijing_time").kind
        == ToolKind.GENERAL
    )
    assert (
        ScopedTool("local:compute_artifact", "compute_artifact").kind
        == ToolKind.EVIDENCE
    )
    assert (
        ScopedTool("mcp:ipp:get_beijing_time", "get_beijing_time").kind
        == ToolKind.DOMAIN
    )
    assert ScopedTool("local:unknown", "unknown").kind == ToolKind.DOMAIN


def test_service_binding_does_not_guess_from_filtered_catalog():
    a = ScopedTool("mcp:a:query", "query", ("old_query",))
    b = ScopedTool("mcp:b:query", "query", ("old_query",))
    bindings = {"query": a.id, "old_query": a.id}
    with pytest.raises(ToolScopeError, match="missing trusted"):
        resolve_tool("query", [b])
    assert resolve_tool("query", [b], bindings=bindings) is None
    assert resolve_tool("old_query", [a, b], bindings=bindings) == a
    assert resolve_tool(a.id, [a, b]) == a
    assert resolve_tool("mcp:other:query", [a, b]) is None
    with pytest.raises(ToolScopeError, match="required dependency unavailable"):
        resolve_skill_tools(
            allowed=["query"],
            required=["query"],
            optional=[],
            catalog=[b],
            bindings=bindings,
        )
    assert resolve_skill_tools(
        allowed=["query"],
        required=["query"],
        optional=[],
        catalog=[a, b],
        bindings=bindings,
    ).allowed_ids == {a.id}


def test_scope_is_subset_and_missing_registered_chart_is_optional():
    query = ScopedTool("mcp:ipp:query", "query", ("legacy_query",))
    other = ScopedTool("mcp:ipp:other", "other")
    chart_id = "mcp:charts:chart_city"
    bindings = {"legacy_query": query.id, "chart_city": chart_id}
    scope = resolve_skill_tools(
        allowed=["legacy_query", "chart_city"],
        required=["legacy_query"],
        optional=["chart_city"],
        catalog=[query, other],
        bindings=bindings,
        chart_tool_ids=[chart_id],
    )
    assert scope.allowed_ids == {query.id}
    assert scope.unavailable_optional == ("chart_city",)
    chart = ScopedTool(chart_id, "chart_city")
    scope = resolve_skill_tools(
        allowed=["legacy_query", "chart_city"],
        required=["legacy_query"],
        optional=["chart_city"],
        catalog=[query, chart, other],
        bindings=bindings,
        chart_tool_ids=[chart_id],
    )
    assert scope.allowed_ids == {query.id, chart.id}
    assert not scope.unavailable_optional


def test_non_chart_optional_rejected_even_if_alias_looks_like_chart():
    weather_id = "mcp:weather:forecast"
    for catalog in ([], [ScopedTool(weather_id, "forecast", ("chart_weather",))]):
        with pytest.raises(ToolScopeError, match="registered chart"):
            resolve_skill_tools(
                allowed=["chart_weather"],
                required=[],
                optional=["chart_weather"],
                catalog=catalog,
                bindings={"chart_weather": weather_id},
                chart_tool_ids=["mcp:charts:chart_city"],
            )
    with pytest.raises(ToolScopeError, match="registered chart"):
        resolve_skill_tools(
            allowed=["mcp:weather:chart_impostor"],
            required=[],
            optional=["mcp:weather:chart_impostor"],
            catalog=[],
        )


def test_same_canonical_tool_cannot_be_required_and_optional_via_aliases():
    chart_id = "mcp:charts:chart_city"
    with pytest.raises(ToolScopeError, match="canonical dependency"):
        resolve_skill_tools(
            allowed=["chart_legacy", chart_id],
            required=["chart_legacy"],
            optional=[chart_id],
            catalog=[],
            bindings={"chart_legacy": chart_id},
            chart_tool_ids=[chart_id],
        )
    with pytest.raises(ToolScopeError, match="chart dependency must be optional"):
        resolve_skill_tools(
            allowed=[chart_id],
            required=[chart_id],
            optional=[],
            catalog=[],
            chart_tool_ids=[chart_id],
        )


@pytest.mark.parametrize(
    ("allowed", "required", "optional"),
    [(["query"], [], []), (["query"], ["query"], ["query"]), ([], ["query"], [])],
)
def test_unclassified_or_conflicting_dependencies_rejected(allowed, required, optional):
    with pytest.raises(ToolScopeError):
        resolve_skill_tools(
            allowed=allowed, required=required, optional=optional, catalog=[]
        )


def test_control_tool_cannot_be_added_as_dependency():
    with pytest.raises(ToolScopeError, match="control"):
        resolve_skill_tools(
            allowed=["local:load_skill"],
            required=["local:load_skill"],
            optional=[],
            catalog=[ScopedTool("local:load_skill", "load_skill")],
        )


def test_untrusted_or_invalid_binding_rejected():
    with pytest.raises(ToolScopeError, match="missing trusted"):
        resolve_tool("legacy", [ScopedTool("mcp:a:query", "query", ("legacy",))])
    with pytest.raises(ToolScopeError, match="invalid bound"):
        resolve_tool("legacy", [], bindings={"legacy": "mcp:a:../query"})
    with pytest.raises(ToolScopeError, match="missing trusted"):
        resolve_skill_tools(
            allowed=["chart_mystery"],
            required=[],
            optional=["chart_mystery"],
            catalog=[],
        )


def test_inconsistent_catalog_identity_rejected():
    with pytest.raises(ValueError):
        ScopedTool("mcp:a:query", "different")
    with pytest.raises(ValueError):
        ScopedTool("mcp:a:../query", "../query")
    with pytest.raises(ToolScopeError, match="conflicting"):
        resolve_tool(
            "mcp:a:query",
            [
                ScopedTool("mcp:a:query", "query"),
                ScopedTool("mcp:a:query", "query", ("x",)),
            ],
        )
