"""Trusted runtime scope extraction for tracing."""

from __future__ import annotations

from typing import Any

from common.identity.runtime import RuntimeIdentity, identity_from_config
from intelligent_tracing.schemas import GRAPH_ID, RuntimeScope


class TracingScopeError(RuntimeError):
    """Trusted run scope is missing or inconsistent."""


def runtime_scope_from_config(
    config: dict[str, Any] | None,
    *,
    analysis_id: str,
    epoch: str | None = None,
) -> RuntimeScope:
    """Build a scope from platform-injected identity and run metadata.

    No request/model/browser field is accepted as identity.  Tests may pass a
    controlled config with a trusted ``langgraph_auth_user`` mapping.
    """
    cfg = config or {}
    identity: RuntimeIdentity | None = identity_from_config(cfg)
    if identity is None:
        raise TracingScopeError("缺少已认证运行身份，无法冻结或发布溯源结果")
    configurable = cfg.get("configurable") or {}
    metadata = cfg.get("metadata") or {}
    thread_id = str(configurable.get("thread_id") or "")
    if not thread_id:
        raise TracingScopeError("缺少可信线程标识")
    context_version = str(
        identity.session_ctx_version
        or configurable.get("context_version")
        or metadata.get("context_version")
        or ""
    )
    if not context_version:
        raise TracingScopeError("缺少可信上下文版本")
    resolved_epoch = str(
        epoch
        or configurable.get("execution_epoch")
        or metadata.get("execution_epoch")
        or configurable.get("run_id")
        or metadata.get("run_id")
        or f"{analysis_id}:pending"
    )
    return RuntimeScope(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        thread_id=thread_id,
        graph_id=str(configurable.get("ledger_graph_id") or GRAPH_ID),
        context_version=context_version,
        epoch=resolved_epoch,
        membership_id=identity.membership_id,
    )
