"""固定三图的可信策略装配；导入本模块不会加载业务图或外呼."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from common.capability_policy import register_policy_provider
from common.tool_execution.policy import (
    admitted_business_policy,
    register_execution_policy,
)


def _unchanged_input(value: Any) -> None:
    return None


def _unchanged_selection(slug: str, menu_name: str | None) -> None:
    return None


def resolve_policy(graph_id: str) -> Any:
    """只解析服务端已经确认的图标识，模块路径不来自请求."""
    if graph_id == "basic-qa":
        from basic_qa import capability_policy as module
        from basic_qa.input_policy import validate_input
        from basic_qa.tool_policy import authorize_send

        register_execution_policy(graph_id, authorize_send)
    elif graph_id == "data-analysis":
        from data_analysis import capability_policy as module
        from data_analysis.tool_policy import authorize_send

        validate_input = _unchanged_input
        register_execution_policy(graph_id, authorize_send, requires_request=False)
    elif graph_id == "intelligent-tracing":
        register_execution_policy(
            graph_id, admitted_business_policy, requires_request=False
        )
        return SimpleNamespace(
            titles=lambda: {},
            menu_skill=lambda _: None,
            required_tool_ids=list,
            validate_selection=_unchanged_selection,
            validate_input=_unchanged_input,
        )
    else:
        raise ValueError("unknown business graph")
    return SimpleNamespace(
        titles=module.titles,
        menu_skill=module.menu_skill,
        required_tool_ids=module.required_tool_ids,
        validate_selection=getattr(module, "validate_selection", _unchanged_selection),
        validate_input=validate_input,
    )


def configure() -> None:
    """各宿主只登记解析器，实际请求才选中对应图."""
    register_policy_provider(resolve_policy)
