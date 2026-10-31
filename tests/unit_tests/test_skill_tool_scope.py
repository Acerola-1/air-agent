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


def test_optional_skill_tools_are_retired():
    with pytest.raises(ToolScopeError, match="optional skill tools are retired"):
        resolve_skill_tools(
            allowed=["query"],
            required=["query"],
            optional=["query"],
            catalog=[ScopedTool("mcp:ipp:query", "query")],
        )
    with pytest.raises(ToolScopeError, match="optional skill tools are retired"):
        resolve_skill_tools(
            allowed=["chart_absent"],
            required=[],
            optional=["chart_absent"],
            catalog=[],
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


def test_scope_is_subset_of_admitted_catalog():
    query = ScopedTool("mcp:ipp:query", "query", ("legacy_query",))
    other = ScopedTool("mcp:ipp:other", "other")
    bindings = {"legacy_query": query.id}
    scope = resolve_skill_tools(
        allowed=["legacy_query"],
        required=["legacy_query"],
        optional=[],
        catalog=[query, other],
        bindings=bindings,
    )
    assert scope.allowed_ids == {query.id}


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
