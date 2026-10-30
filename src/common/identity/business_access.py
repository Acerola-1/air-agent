"""公共业务准入守卫.

唯一规则：当前有效 IAM 身份必须持有
`agent:business:<稳定业务码>:use`，且账号、企业、成员与修订有效。
普通 Skill/MCP 不再要求人员独立授权；平台管理员与 root 也走同一规则。

本模块不缓存正向结论：
- 入口（目录、台账受理、原生 create_run）每次用实时身份求交；
- 运行中由 `common.identity.runtime.guard_*` 逐发送复查执行引用与修订，
  并校验受理时冻结的业务码/策略版本/精确权限未被替换。

错误语义：401 身份缺失/无效、403 业务未授权、409 上下文冲突、503 后端不可用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.business_registry import (
    BUSINESS_POLICY_VERSION,
    BusinessDefinition,
    business_for_code,
    business_for_graph,
)


class BusinessAccessError(RuntimeError):
    """业务准入失败：调用方按 `status_code` 映射 HTTP，且不泄露无关信息."""

    def __init__(
        self, message: str, *, status_code: int = 403, code: str = "business_denied"
    ) -> None:
        """创建带 HTTP 语义与稳定错误码的业务拒绝."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class BusinessGrant:
    """一次请求/运行已核实并冻结的业务许可（仅非秘密字段）."""

    business_code: str
    business_name: str
    graph_id: str
    permission: str
    policy_version: str
    user_id: str
    tenant_id: str
    membership_id: str
    session_ctx_version: int | None
    user_auth_revision: int | None
    tenant_auth_revision: int | None
    membership_revision: int | None
    verification_ref: str | None = None

    def ledger_fields(self) -> dict[str, Any]:
        """写入台账/受理记录的固定字段（不含权限全集与秘密）."""
        return {
            "business_code": self.business_code,
            "business_policy_version": self.policy_version,
            "business_graph_id": self.graph_id,
        }

    def pinned_config(self) -> dict[str, Any]:
        """注入原生运行 config/metadata 的服务端裁决字段.

        这些字段由 create_run 钩子覆盖写入，客户端同名声明不生效；运行层只
        消费这里的值，不从浏览器草稿拼造。
        """
        return {
            "ledger_business_code": self.business_code,
            "ledger_business_policy_version": self.policy_version,
            "ledger_business_permission": self.permission,
            "ledger_graph_id": self.graph_id,
        }


def permissions_of(identity: Any) -> frozenset[str]:
    """从管理端 Identity / 原生 auth 用户对象提取 IAM 权限集合."""
    raw = getattr(identity, "permissions", None)
    if raw is None and isinstance(identity, dict):
        raw = identity.get("permissions")
    if raw is None:
        return frozenset()
    try:
        return frozenset(str(code) for code in raw if code)
    except TypeError:
        return frozenset()


def _field(identity: Any, name: str, default: Any = None) -> Any:
    if isinstance(identity, dict):
        return identity.get(name, default)
    return getattr(identity, name, default)


def _required(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise BusinessAccessError(
            f"缺少有效的{label}", status_code=401, code="identity_missing"
        )
    return text


def _admin_identity_fields(identity: Any) -> dict[str, Any]:
    user = getattr(identity, "user", None)
    tenant = getattr(identity, "tenant", None)
    membership = getattr(identity, "membership", None)
    session = getattr(identity, "session", None)
    return {
        "user_id": getattr(user, "id", None),
        "tenant_id": getattr(tenant, "id", None),
        "membership_id": getattr(membership, "id", None),
        "session_ctx_version": getattr(session, "ctx_version", None),
        "user_auth_revision": getattr(user, "auth_revision", None),
        "tenant_auth_revision": getattr(tenant, "auth_revision", None),
        "membership_revision": getattr(membership, "revision", None),
        "verification_ref": getattr(identity, "remote_verification_ref", None),
    }


def _actor_fields(actor: Any) -> dict[str, Any]:
    return {
        "user_id": _field(actor, "identity"),
        "tenant_id": _field(actor, "tenant_id"),
        "membership_id": _field(actor, "membership_id"),
        "session_ctx_version": _field(actor, "session_ctx_version"),
        "user_auth_revision": _field(actor, "user_auth_revision"),
        "tenant_auth_revision": _field(actor, "tenant_auth_revision"),
        "membership_revision": _field(actor, "membership_revision"),
        "verification_ref": _field(actor, "verification_ref"),
    }


def _grant_fields(identity: Any) -> dict[str, Any]:
    if hasattr(identity, "user") and hasattr(identity, "session"):
        fields = _admin_identity_fields(identity)
    elif hasattr(identity, "user_id") and hasattr(identity, "session_ctx_version"):
        # RuntimeIdentity：运行/目录路径
        fields = {
            "user_id": getattr(identity, "user_id", None),
            "tenant_id": getattr(identity, "tenant_id", None),
            "membership_id": getattr(identity, "membership_id", None),
            "session_ctx_version": getattr(identity, "session_ctx_version", None),
            "user_auth_revision": getattr(identity, "user_auth_revision", None),
            "tenant_auth_revision": getattr(identity, "tenant_auth_revision", None),
            "membership_revision": getattr(identity, "membership_revision", None),
            "verification_ref": getattr(identity, "verification_ref", None),
        }
    else:
        fields = _actor_fields(identity)
    return {
        "user_id": _required(fields["user_id"], "用户身份"),
        "tenant_id": _required(fields["tenant_id"], "企业上下文"),
        "membership_id": _required(fields["membership_id"], "企业成员身份"),
        "session_ctx_version": fields["session_ctx_version"],
        "user_auth_revision": fields["user_auth_revision"],
        "tenant_auth_revision": fields["tenant_auth_revision"],
        "membership_revision": fields["membership_revision"],
        "verification_ref": fields["verification_ref"],
    }


def grant_for_graph(*, graph_id: str, identity: Any) -> BusinessGrant:
    """入口业务准入：实际 Graph → 稳定业务 → 精确权限求交.

    未知图、未登记业务、无企业上下文、权限缺失分别返回 403/401，
    不因客户端声明、平台管理员或 root 身份放行。
    """
    definition = business_for_graph(graph_id)
    if definition is None:
        raise BusinessAccessError(
            "该能力未登记为可运行的业务，已拒绝",
            status_code=403,
            code="business_unregistered",
        )
    fields = _grant_fields(identity)
    if definition.permission not in permissions_of(identity):
        raise BusinessAccessError(
            f"当前企业未开通或未授予「{definition.name_zh}」业务使用权限",
            status_code=403,
            code="business_not_permitted",
        )
    return _grant(definition, fields)


def grant_for_business(*, business_code: str, identity: Any) -> BusinessGrant:
    """按稳定业务码准入（台账已有业务码时使用）."""
    definition = business_for_code(business_code)
    if definition is None:
        raise BusinessAccessError(
            "该业务未登记，已拒绝", status_code=403, code="business_unregistered"
        )
    fields = _grant_fields(identity)
    if definition.permission not in permissions_of(identity):
        raise BusinessAccessError(
            f"当前企业未开通或未授予「{definition.name_zh}」业务使用权限",
            status_code=403,
            code="business_not_permitted",
        )
    return _grant(definition, fields)


def _grant(definition: BusinessDefinition, fields: dict[str, Any]) -> BusinessGrant:
    return BusinessGrant(
        business_code=definition.code,
        business_name=definition.name_zh,
        graph_id=definition.graph,
        permission=definition.permission,
        policy_version=BUSINESS_POLICY_VERSION,
        **fields,
    )


def require_same_business(grant: BusinessGrant, *, graph_id: str) -> None:
    """受理/恢复必须沿用同一业务，不能借已获准业务启动另一图."""
    definition = business_for_graph(graph_id)
    if definition is None or definition.code != grant.business_code:
        raise BusinessAccessError(
            "请求业务与实际执行图不一致，已拒绝",
            status_code=409,
            code="business_mismatch",
        )
