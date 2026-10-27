"""Trusted runtime scope extraction for tracing.

业务授权只取平台受理时冻结并注入的 `ledger_business_*` 字段；`capability_*`
选择引用只用于回读技能/工具选择，不构成业务许可证明。任何缺失、伪造或
跨图/跨业务字段都在数据查询前失败关闭。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.business_registry import (
    BUSINESS_POLICY_VERSION,
    business_for_code,
)
from common.identity.runtime import RuntimeIdentity, identity_from_config
from intelligent_tracing.schemas import GRAPH_ID, RuntimeScope


class TracingScopeError(RuntimeError):
    """Trusted run scope is missing or inconsistent."""


@dataclass(frozen=True)
class BusinessBinding:
    """平台冻结的业务许可：只承载非秘密标识与精确权限码."""

    business_code: str
    business_policy_version: str
    business_permission: str
    graph_id: str


def _config_text(config: dict[str, Any], key: str) -> str:
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    return str(configurable.get(key) or metadata.get(key) or "").strip()


def business_binding_from_config(config: dict[str, Any] | None) -> BusinessBinding:
    """从可信 config 解析业务许可，任何缺口或冲突都失败关闭."""
    cfg = dict(config or {})
    business_code = _config_text(cfg, "ledger_business_code")
    policy_version = _config_text(cfg, "ledger_business_policy_version")
    permission = _config_text(cfg, "ledger_business_permission")
    graph_id = _config_text(cfg, "ledger_graph_id")
    if not business_code:
        raise TracingScopeError("缺少可信业务快照，拒绝冻结或发布溯源结果")
    definition = business_for_code(business_code)
    if definition is None:
        raise TracingScopeError("可信业务未在业务目录登记")
    if graph_id != definition.graph or graph_id != GRAPH_ID:
        raise TracingScopeError("可信业务图与实际执行图不一致")
    if permission != definition.permission:
        raise TracingScopeError("可信业务权限码与业务目录不一致")
    if policy_version != BUSINESS_POLICY_VERSION:
        raise TracingScopeError("业务策略版本已失效，拒绝继续使用旧许可")
    return BusinessBinding(
        business_code=business_code,
        business_policy_version=policy_version,
        business_permission=permission,
        graph_id=graph_id,
    )


def _identity_binding(identity: RuntimeIdentity) -> BusinessBinding | None:
    """入口身份若已冻结业务字段，则必须与 config 快照一致."""
    if not identity.business_code:
        return None
    return BusinessBinding(
        business_code=identity.business_code,
        business_policy_version=str(identity.business_policy_version or ""),
        business_permission=str(identity.business_permission or ""),
        graph_id=str(identity.business_graph or ""),
    )


def runtime_scope_from_config(
    config: dict[str, Any] | None,
    *,
    analysis_id: str = "",
    epoch: str | None = None,
) -> RuntimeScope:
    """Build a scope from platform-injected identity and business snapshot.

    No request/model/browser field is accepted as identity or business proof.
    Tests may pass a controlled config with a trusted ``langgraph_auth_user``
    mapping and the same ``ledger_business_*`` keys the platform injects.
    """
    cfg = dict(config or {})
    identity: RuntimeIdentity | None = identity_from_config(cfg)
    if identity is None:
        raise TracingScopeError("缺少已认证运行身份，无法冻结或发布溯源结果")
    binding = business_binding_from_config(cfg)
    identity_binding = _identity_binding(identity)
    if identity_binding is not None and identity_binding != binding:
        raise TracingScopeError("身份冻结的业务许可与运行配置不一致")
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
        or (f"{analysis_id}:pending" if analysis_id else "")
    )
    if not resolved_epoch:
        raise TracingScopeError("缺少可信执行标识")
    return RuntimeScope(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        thread_id=thread_id,
        graph_id=binding.graph_id,
        context_version=context_version,
        epoch=resolved_epoch,
        membership_id=identity.membership_id,
        business_code=binding.business_code,
        business_policy_version=binding.business_policy_version,
        business_permission=binding.business_permission,
    )
