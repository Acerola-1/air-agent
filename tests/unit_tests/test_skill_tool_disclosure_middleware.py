"""Skill 工具披露中间件测试."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.tools import tool

from common.middleware.skill_tool_disclosure_middleware import (
    SkillToolFilterMiddleware,
    SkillToolRegistryMiddleware,
)


@tool
def find_skill(question: str) -> str:
    """查找匹配的 Skill."""
    return question


@tool
def allowed_business_tool() -> str:
    """允许披露的业务工具."""
    return "allowed"


@tool
def hidden_business_tool() -> str:
    """应被隐藏的业务工具."""
    return "hidden"


@tool
def read_file(path: str) -> str:
    """模拟框架内置文件工具."""
    return path


def _request(state: dict):
    tools = [find_skill, allowed_business_tool, hidden_business_tool, read_file]
    return SimpleNamespace(
        tools=tools,
        state=state,
        override=lambda **kwargs: SimpleNamespace(
            tools=kwargs.get("tools", tools),
            state=state,
        ),
    )


def _tool_names(tools) -> list[str]:
    return [tool.name for tool in tools]


def test_registry_middleware_pre_registers_business_tools() -> None:
    middleware = SkillToolRegistryMiddleware(
        [allowed_business_tool, hidden_business_tool]
    )

    assert _tool_names(middleware.tools) == [
        "allowed_business_tool",
        "hidden_business_tool",
    ]


def test_registry_middleware_refreshes_dynamic_tools_before_model_call() -> None:
    calls = 0
    exposed = [allowed_business_tool]

    def refresh() -> None:
        nonlocal calls, exposed
        calls += 1
        exposed = [allowed_business_tool, hidden_business_tool]

    middleware = SkillToolRegistryMiddleware(lambda: exposed, sync_refresh=refresh)
    request = _request({})

    result = middleware.wrap_model_call(request, lambda modified: modified)

    assert calls == 1
    assert "hidden_business_tool" in _tool_names(result.tools)


def test_registry_middleware_binds_dynamic_tool_for_tool_call() -> None:
    middleware = SkillToolRegistryMiddleware(lambda: [allowed_business_tool])
    request = SimpleNamespace(
        tool=None,
        tool_call={"name": "allowed_business_tool", "args": {}, "id": "call-1"},
        override=lambda **kwargs: SimpleNamespace(
            tool=kwargs.get("tool"),
            tool_call={"name": "allowed_business_tool", "args": {}, "id": "call-1"},
        ),
    )

    result = middleware.wrap_tool_call(request, lambda modified: modified.tool)

    assert result is allowed_business_tool


def test_filter_hides_business_tools_before_skill_selection() -> None:
    middleware = SkillToolFilterMiddleware(
        business_tool_names=["allowed_business_tool", "hidden_business_tool"]
    )
    request = _request({})

    result = middleware.wrap_model_call(request, lambda modified: modified)

    assert _tool_names(result.tools) == ["find_skill", "read_file"]


def test_filter_native_policy_hides_business_tools_before_skill_search() -> None:
    middleware = SkillToolFilterMiddleware(
        business_tool_names=["allowed_business_tool", "hidden_business_tool"],
        unmatched_policy="native",
    )
    request = _request({})

    result = middleware.wrap_model_call(request, lambda modified: modified)

    assert _tool_names(result.tools) == ["find_skill", "read_file"]


def test_filter_native_policy_keeps_business_tools_after_no_match() -> None:
    middleware = SkillToolFilterMiddleware(
        business_tool_names=["allowed_business_tool", "hidden_business_tool"],
        unmatched_policy="native",
    )
    request = _request({"skill_search_attempted": True})

    result = middleware.wrap_model_call(request, lambda modified: modified)

    assert _tool_names(result.tools) == [
        "find_skill",
        "allowed_business_tool",
        "hidden_business_tool",
        "read_file",
    ]


def test_filter_exposes_only_selected_skill_allowed_business_tools() -> None:
    middleware = SkillToolFilterMiddleware(
        business_tool_names=["allowed_business_tool", "hidden_business_tool"]
    )
    request = _request(
        {
            "skill_search_attempted": True,
            "selected_skill_allowed_tools": ["allowed_business_tool"],
        }
    )

    result = middleware.wrap_model_call(request, lambda modified: modified)

    assert _tool_names(result.tools) == [
        "find_skill",
        "allowed_business_tool",
        "read_file",
    ]


def test_filter_keeps_selected_skill_tools_across_followup_questions() -> None:
    middleware = SkillToolFilterMiddleware(
        business_tool_names=["allowed_business_tool", "hidden_business_tool"],
        unmatched_policy="native",
    )
    request = _request(
        {
            "messages": [
                {"role": "user", "content": "上一轮问题"},
                {"role": "assistant", "content": "上一轮回答"},
                {"role": "user", "content": "新一轮问题"},
            ],
            "skill_search_attempted": True,
            "skill_search_question": "上一轮问题",
            "selected_skill_allowed_tools": ["allowed_business_tool"],
        }
    )

    result = middleware.wrap_model_call(request, lambda modified: modified)

    assert _tool_names(result.tools) == [
        "find_skill",
        "allowed_business_tool",
        "read_file",
    ]


def test_filter_uses_selected_skill_tools_for_current_question() -> None:
    middleware = SkillToolFilterMiddleware(
        business_tool_names=["allowed_business_tool", "hidden_business_tool"],
        unmatched_policy="native",
    )
    request = _request(
        {
            "messages": [{"role": "user", "content": "当前问题"}],
            "skill_search_attempted": True,
            "skill_search_question": "当前问题",
            "selected_skill_allowed_tools": ["allowed_business_tool"],
        }
    )

    result = middleware.wrap_model_call(request, lambda modified: modified)

    assert _tool_names(result.tools) == [
        "find_skill",
        "allowed_business_tool",
        "read_file",
    ]
