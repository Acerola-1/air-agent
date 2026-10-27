"""可信观测上下文：身份只取平台注入 RuntimeIdentity，观测 ID 服务端生成.

观测关联（attempt/submission/parent）必须与身份同租户；客户端 metadata 中的
观测标识一律不采信——它们只能用于日志展示，不进入任何授权判定或写入关联。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from common.identity.runtime import RuntimeIdentity, identity_from_config


def new_id() -> str:
    """生成新的观测 UUID（服务端生成，客户端不可指定）."""
    return str(uuid.uuid4())


@dataclass(frozen=True)
class ObservationContext:
    """一次业务运行内的可信观测上下文（同租户、同 attempt）.

    `run_key` 是进程内的运行绑定键：LangChain 回调按 run_id 反查所属上下文。
    """

    tenant_id: str
    user_id: str
    membership_id: str
    request_id: str
    attempt_id: str
    submission_id: str | None
    graph_id: str
    operation_kind: str
    thread_id: str | None
    platform_run_id: str | None
    account: str = ""
    run_key: str = field(default_factory=new_id)

    def as_guc(self) -> dict[str, str]:
        """返回观测写入所需的事务本地 GUC（tenant + user + operations）."""
        return {
            "air_agent.tenant_id": self.tenant_id,
            "air_agent.user_id": self.user_id,
            # 观测写入按运行事实（受控入口），与台账 lifecycle 同语义：
            # 本运行产生的明细对运行者可见；聚合查询走独立授权服务。
            "air_agent.agent_operations": "off",
        }

    def as_log_dict(self) -> dict[str, str]:
        """脱敏日志形状（仅非秘密标识）."""
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "attempt_id": self.attempt_id,
            "graph_id": self.graph_id,
        }


def observation_context_from_config(
    config: dict[str, Any] | None,
) -> ObservationContext | None:
    """从运行 config 提取可信观测上下文；缺身份或缺受理 ID 返回 None.

    身份来源只有 `configurable.langgraph_auth_user`（平台注入）；受理关联只有
    受控入口注入的 `ledger_request_id` / `ledger_attempt_id`（configurable 优先、
    metadata 兜底，与台账 lifecycle 同一来源约定）。
    """
    identity: RuntimeIdentity | None = identity_from_config(config)
    cfg = config or {}
    configurable = cfg.get("configurable") or {}
    metadata = cfg.get("metadata") or {}
    request_id = configurable.get("ledger_request_id") or metadata.get(
        "ledger_request_id"
    )
    attempt_id = configurable.get("ledger_attempt_id") or metadata.get(
        "ledger_attempt_id"
    )
    if identity is None or not request_id or not attempt_id:
        return None
    return ObservationContext(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        membership_id=identity.membership_id,
        request_id=str(request_id),
        attempt_id=str(attempt_id),
        submission_id=str(
            configurable.get("ledger_submission_id")
            or metadata.get("ledger_submission_id")
            or ""
        )
        or None,
        graph_id=str(
            configurable.get("ledger_graph_id") or metadata.get("ledger_graph_id") or ""
        ),
        operation_kind=str(
            configurable.get("ledger_operation_kind")
            or metadata.get("ledger_operation_kind")
            or "initial"
        ),
        thread_id=str(configurable.get("thread_id") or "") or None,
        platform_run_id=str(
            configurable.get("run_id")
            or configurable.get("langgraph_run_id")
            or metadata.get("run_id")
            or ""
        )
        or None,
        # Metadata is client-controlled; only the authenticated identity owns labels.
        account=identity.account,
    )


def current_observation_config(runtime: Any = None) -> dict[str, Any]:
    """优先使用框架支持的任务配置；Runtime不保证具有config属性."""
    try:
        from langgraph.config import get_config

        config = dict(get_config() or {})
        return config
    except RuntimeError:
        pass
    config = getattr(runtime, "config", None)
    return dict(config) if isinstance(config, dict) else {}
