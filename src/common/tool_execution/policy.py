"""可信宿主登记的逐发送策略；公共层不加载任何图实现."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from common.mcp_authorization import McpAuthorizationError

DispatchValidator = Callable[[], None]
ToolExecutionPolicy = Callable[
    [Any, dict[str, Any], str, str, Any, dict[str, Any]],
    Awaitable[DispatchValidator | None],
]


@dataclass(frozen=True)
class _Binding:
    callback: ToolExecutionPolicy
    requires_request: bool


_POLICIES: dict[str, _Binding] = {}
_REQUEST: ContextVar[Any] = ContextVar("trusted_tool_execution_request", default=None)


def register_execution_policy(
    graph_id: str, policy: ToolExecutionPolicy, *, requires_request: bool = True
) -> None:
    """仅供服务端装配；注册不能由运行配置或客户端metadata提供."""
    previous = _POLICIES.get(graph_id)
    if previous is not None and previous != _Binding(policy, requires_request):
        raise RuntimeError(f"execution policy already registered: {graph_id}")
    _POLICIES[graph_id] = _Binding(policy, requires_request)


@contextmanager
def bind_tool_request(request: Any) -> Iterator[None]:
    """在每次调用内绑定真实ToolRuntime，退出时恢复，不跨任务积累."""
    token = _REQUEST.set(request)
    try:
        yield
    finally:
        _REQUEST.reset(token)


def require_execution_policy(identity: Any) -> ToolExecutionPolicy:
    """按服务端业务登记和可信运行身份取策略，缺失在发现前拒绝."""
    from common.business_registry import business_for_code

    business = business_for_code(identity.business_code or "")
    graph = getattr(business, "graph", None)
    if not graph or identity.business_graph != graph:
        raise McpAuthorizationError("实际图与受理业务不一致")
    policy = _POLICIES.get(graph)
    if policy is None:
        raise McpAuthorizationError("执行策略未装配，拒绝工具发送")
    if policy.requires_request and _REQUEST.get() is None:
        raise McpAuthorizationError("缺少可信工具执行上下文")
    return policy.callback


async def authorize_selection(
    identity: Any, config: dict[str, Any], server_key: str, name: str
) -> None:
    """从受理台账重新读取不可变选择；运行配置只能声明，不能扩大范围."""
    from common.event_ledger.store import (
        LedgerScope,
        fetch_capability_selection_snapshot,
    )

    configurable = config.get("configurable")
    metadata = config.get("metadata") or {}
    if not isinstance(configurable, dict) or not isinstance(metadata, dict):
        raise McpAuthorizationError("缺少可信受理配置")
    keys = (
        "thread_id",
        "ledger_attempt_id",
        "ledger_submission_id",
        "ledger_graph_id",
        "capability_selection_id",
        "capability_selection_digest",
    )
    values = {key: configurable.get(key) for key in keys}
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise McpAuthorizationError("缺少可信受理选择关联")
    if values["ledger_graph_id"] != identity.business_graph:
        raise McpAuthorizationError("受理图与可信身份不一致")
    for key, value in values.items():
        if key in metadata and metadata[key] != value:
            raise McpAuthorizationError("受理关联声明冲突")
    try:
        snapshot = await fetch_capability_selection_snapshot(
            LedgerScope(identity.tenant_id, identity.user_id, identity.membership_id),
            attempt_id=values["ledger_attempt_id"],
            submission_id=values["ledger_submission_id"],
        )
    except Exception as exc:
        raise McpAuthorizationError("无法回读受理选择") from exc
    if not isinstance(snapshot, dict) or any(
        str(snapshot.get(field) or "") != values[key]
        for field, key in (
            ("thread_id", "thread_id"),
            ("attempt_id", "ledger_attempt_id"),
            ("submission_id", "ledger_submission_id"),
            ("graph_id", "ledger_graph_id"),
            ("id", "capability_selection_id"),
            ("selection_digest", "capability_selection_digest"),
        )
    ):
        raise McpAuthorizationError("受理选择与可信运行不一致")
    if str(snapshot.get("membership_id") or "") != identity.membership_id:
        raise McpAuthorizationError("受理选择与当前成员身份不一致")
    mode, tool_ids = snapshot.get("mcp_mode"), snapshot.get("tool_ids")
    if (
        mode not in {"auto", "allowlist"}
        or not isinstance(tool_ids, list)
        or any(not isinstance(item, str) or not item for item in tool_ids)
    ):
        raise McpAuthorizationError("受理工具选择无效")
    for source in (configurable, metadata):
        if "capability_mcp_mode" in source and source["capability_mcp_mode"] != mode:
            raise McpAuthorizationError("工具选择声明冲突")
        if (
            "capability_tool_ids" in source
            and source["capability_tool_ids"] != tool_ids
        ):
            raise McpAuthorizationError("工具选择声明冲突")
    if mode == "allowlist" and f"mcp:{server_key}:{name}" not in tool_ids:
        raise McpAuthorizationError("工具不在已受理选择中")


async def authorize_send(
    identity: Any,
    server_key: str,
    name: str,
    tool: Any,
    config: dict[str, Any],
    actual_args: dict[str, Any],
) -> DispatchValidator | None:
    """最后一次异步准备之后、每次真实invoke之前重新校验."""
    from common.identity.runtime import identity_from_request, revalidate

    policy = require_execution_policy(identity)
    request = _REQUEST.get()
    if request is not None:
        actual = identity_from_request(request)
        keys = (
            "tenant_id",
            "user_id",
            "membership_id",
            "session_id",
            "business_code",
            "business_graph",
        )
        if actual is None or any(
            getattr(actual, k) != getattr(identity, k) for k in keys
        ):
            raise McpAuthorizationError("执行上下文与可信身份不一致")
    try:
        await revalidate(identity)
        validator = await policy(request, config, server_key, name, tool, actual_args)
        binding = _POLICIES.get(identity.business_graph)
        if binding is None or binding.callback is not policy:
            raise McpAuthorizationError("执行策略已变化")
        if binding.requires_request and not callable(validator):
            raise McpAuthorizationError("缺少最终发送校验")
        if validator is not None and not callable(validator):
            raise McpAuthorizationError("最终发送校验无效")

        def validate_dispatch() -> None:
            current = _POLICIES.get(identity.business_graph)
            if current != binding:
                raise McpAuthorizationError("执行策略已变化")
            if request is not None:
                latest = identity_from_request(request)
                if latest is None or any(
                    getattr(latest, key) != getattr(identity, key) for key in keys
                ):
                    raise McpAuthorizationError("执行上下文在发送前变化")
            if validator is not None:
                validator()

        return validate_dispatch
    except McpAuthorizationError:
        raise
    except Exception as exc:
        raise McpAuthorizationError("无法确认执行策略，拒绝工具发送") from exc


async def admitted_business_policy(
    request: Any,
    config: dict[str, Any],
    server_key: str,
    name: str,
    tool: Any,
    actual_args: dict[str, Any],
) -> None:
    """无额外会话激活策略的业务仍由公共业务／连接／受理边界保护."""
    return None
