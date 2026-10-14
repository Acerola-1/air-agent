"""图/工具执行侧的可信运行身份与撤销复查（任务 5.4）.

- 身份来源**只允许**平台注入的 `configurable.langgraph_auth_user`
  （客户端被平台校验层禁止传入该保留键），不读 metadata/input 自报字段.
- 每次模型/工具外呼前 `revalidate`：查身份库核对会话/账号/企业/成员状态与
  修订号；撤销后下一调用抛 `RuntimeIdentityRevoked`.
- 本模块只持有非秘密标识；票据原文绝不进入 state、config 或 Trace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from common.identity.db import IdentityBackendUnavailable, fetch_identity_state
from common.identity.ticket import TicketError


class RuntimeIdentityError(RuntimeError):
    """运行身份缺失或非法：调用失败关闭，绝不匿名继续."""


class RuntimeIdentityRevoked(RuntimeIdentityError):
    """授权在运行中被撤销/停用."""


@dataclass(frozen=True)
class RuntimeIdentity:
    """已认证运行身份（仅非秘密标识 + 入站核验时的修订快照）.

    修订快照（R04）：authenticate 入站时已把实时状态里的会话/账号/企业/成员
    修订写入可信上下文；运行中的每次外呼与快照比较，"状态仍 active 但修订已
    前进"（角色撤销、A→B→A 切换等）同样拒绝，而不只依赖 active 标志。
    """

    user_id: str
    tenant_id: str
    membership_id: str
    session_id: str
    account: str = ""
    session_ctx_version: int | None = None
    user_auth_revision: int | None = None
    tenant_auth_revision: int | None = None
    membership_revision: int | None = None

    def as_log_dict(self) -> dict[str, str]:
        """脱敏日志形状（仅标识，无凭据）."""
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "membership_id": self.membership_id,
        }


def identity_from_config(config: dict[str, Any] | None) -> RuntimeIdentity | None:
    """从 runnable config 提取平台注入的可信身份；非可信来源一律忽略."""
    configurable = (config or {}).get("configurable") or {}
    user = configurable.get("langgraph_auth_user")
    if user is None:
        return None
    get = (
        user.get
        if isinstance(user, dict)
        else (lambda k, default=None: getattr(user, k, default))
    )
    identity = str(get("identity") or "")
    tenant_id = str(get("tenant_id") or "")
    membership_id = str(get("membership_id") or "")
    session_id = str(get("session_id") or "")
    if not identity or not tenant_id or not membership_id or not session_id:
        return None

    def _rev(key: str) -> int | None:
        raw = get(key)
        if raw is None:
            return None
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    return RuntimeIdentity(
        user_id=identity,
        tenant_id=tenant_id,
        membership_id=membership_id,
        session_id=session_id,
        account=str(get("display_name") or ""),
        session_ctx_version=_rev("session_ctx_version"),
        user_auth_revision=_rev("user_auth_revision"),
        tenant_auth_revision=_rev("tenant_auth_revision"),
        membership_revision=_rev("membership_revision"),
    )


def identity_from_auth_user(user: Any) -> RuntimeIdentity | None:
    """从平台认证用户对象（BaseUser，支持属性与 dict 访问）提取可信身份.

    langgraph 1.2.x 的 `Runtime` 不含 `config`；`server_info.user`（若由服务
    注入）与 `configurable.langgraph_auth_user` 是框架支持的同一来源形状。
    """
    if user is None:
        return None

    def get(key: str, default: Any = None) -> Any:
        if isinstance(user, dict):
            return user.get(key, default)
        return getattr(user, key, default)

    identity = str(get("identity") or "")
    tenant_id = str(get("tenant_id") or "")
    membership_id = str(get("membership_id") or "")
    session_id = str(get("session_id") or "")
    if not identity or not tenant_id or not membership_id or not session_id:
        return None

    def _rev(key: str) -> int | None:
        raw = get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    return RuntimeIdentity(
        user_id=identity,
        tenant_id=tenant_id,
        membership_id=membership_id,
        session_id=session_id,
        account=str(get("display_name") or ""),
        session_ctx_version=_rev("session_ctx_version"),
        user_auth_revision=_rev("user_auth_revision"),
        tenant_auth_revision=_rev("tenant_auth_revision"),
        membership_revision=_rev("membership_revision"),
    )


def require_identity(config: dict[str, Any] | None) -> RuntimeIdentity:
    """提取可信身份；缺失即抛错（fail closed）."""
    identity = identity_from_config(config)
    if identity is None:
        raise RuntimeIdentityError("缺少已认证运行身份（custom auth 未生效）")
    return identity


async def revalidate(identity: RuntimeIdentity) -> None:
    """撤销复查：状态失效**或修订快照前进**即抛错（R04）.

    修订比较覆盖"状态仍 active"的授权变化：成员角色/权限撤销递增
    membership_revision（角色权限替换/删除同事务推进受影响成员）、企业授权
    调整递增 tenant_auth_revision、改密/平台授权变更递增 user_auth_revision、
    企业切换（含 A→B→A）递增 session ctx_version。

    F02：强制模式下**修订快照缺失或非法一律拒绝**（升级前旧运行上下文必须
    安全停止并由新请求重建可信身份），不得降级为仅查 active。
    """
    _validate_identity_snapshot(identity)
    try:
        state = await fetch_identity_state(
            identity.session_id,
            user_id=identity.user_id,
            tenant_id=identity.tenant_id,
        )
    except (IdentityBackendUnavailable, TicketError) as exc:
        raise RuntimeIdentityRevoked("身份服务不可用，停止后续外部调用") from exc
    _validate_identity_state(identity, state)


def _validate_identity_snapshot(identity):
    if _enforce_enabled():
        # F02：企业部署（强制模式）下缺/非法快照一律拒绝——旧运行上下文必须
        # 安全停止并由新请求重建可信身份；非强制的本地测试模式仍按状态检查，
        # 避免把守卫缺席的旧 fixture 混入生产语义。
        snapshot = (
            identity.session_ctx_version,
            identity.user_auth_revision,
            identity.tenant_auth_revision,
            identity.membership_revision,
        )
        if any(value is None for value in snapshot):
            raise RuntimeIdentityRevoked(
                "可信运行身份缺少修订快照：停止后续调用，请重新发起请求"
            )


def _validate_identity_state(identity, state):
    if state is None or not state.session_active or not state.session_unexpired:
        raise RuntimeIdentityRevoked("会话已撤销或过期")
    if not state.user_active:
        raise RuntimeIdentityRevoked("账号已停用")
    if state.tenant_status != "active":
        raise RuntimeIdentityRevoked("企业已停用")
    if state.membership_status != "active":
        raise RuntimeIdentityRevoked("成员身份已停用")
    if (
        identity.session_ctx_version is not None
        and identity.session_ctx_version != state.session_ctx_version
    ):
        raise RuntimeIdentityRevoked("企业上下文已切换（会话版本前进）")
    if (
        identity.user_auth_revision is not None
        and identity.user_auth_revision != state.user_auth_revision
    ):
        raise RuntimeIdentityRevoked("账号授权已变更（修订前进）")
    if (
        identity.tenant_auth_revision is not None
        and identity.tenant_auth_revision != state.tenant_auth_revision
    ):
        raise RuntimeIdentityRevoked("企业授权已变更（修订前进）")
    if (
        identity.membership_revision is not None
        and identity.membership_revision != state.membership_revision
    ):
        raise RuntimeIdentityRevoked("成员授权已变更（修订前进）")


def _enforce_enabled() -> bool:
    """企业部署默认强制（fail closed）.

    单元测试/离线开发用 `TENANT_ENFORCE_RUNTIME_IDENTITY=false` 显式关闭；
    生产 compose/Dockerfile 必须保持 true（缺省即 true）.
    """
    return os.environ.get("TENANT_ENFORCE_RUNTIME_IDENTITY", "true").lower() != "false"


def identity_from_request(request: Any) -> RuntimeIdentity | None:
    """从中间件请求对象提取可信身份（R04，按框架支持的来源逐级尝试）.

    1. `langgraph.config.get_config()`——节点/中间件运行在 Pregel 任务的
       RunnableConfig 上下文中，平台把已认证的 `langgraph_auth_user` 注入其
       configurable；这是 Runtime 官方文档给出的受支持访问方式（Runtime 本身
       不含 config，轮次01 实测确认）。
    2. `request.runtime.config`——`ToolRuntime`（prebuilt 工具中间件）携带
       config 的形态。
    3. `request.runtime.server_info.user`——服务端注入的认证用户对象。

    客户端自报字段不在任何一条路径上：来源全部是平台注入的可信上下文。
    """
    try:
        from langgraph.config import get_config

        cfg = dict(get_config() or {})
        identity = identity_from_config(cfg)
        if identity is not None:
            return identity
    except Exception:  # 无 runnable 上下文（本地直调）时继续下一来源
        pass
    runtime = getattr(request, "runtime", None)
    config = getattr(runtime, "config", None) if runtime is not None else None
    if config:
        identity = identity_from_config(dict(config))
        if identity is not None:
            return identity
    server_info = getattr(runtime, "server_info", None) if runtime is not None else None
    return identity_from_auth_user(getattr(server_info, "user", None))


async def guard_request_call(request: Any) -> RuntimeIdentity | None:
    """中间件版守卫：提取失败按强制模式失败关闭，提取成功执行实时复查."""
    identity = identity_from_request(request)
    if identity is None:
        if _enforce_enabled():
            raise RuntimeIdentityError("缺少已认证运行身份，拒绝发起外部调用")
        return None
    await revalidate(identity)
    return identity


async def guard_runtime_call(config: dict[str, Any] | None) -> RuntimeIdentity | None:
    """每次模型/工具外呼前的守卫.

    - 强制模式：无可信身份 → 拒绝；有身份 → 实时撤销复查.
    - 非强制（本地测试）：无身份跳过；有身份仍复查（可被 mock DB 驱动）.
    """
    identity = identity_from_config(config)
    if identity is None:
        if _enforce_enabled():
            raise RuntimeIdentityError("缺少已认证运行身份，拒绝发起外部调用")
        return None
    await revalidate(identity)
    return identity


async def guard_model_call(config: Any) -> RuntimeIdentity | None:
    """RunnableConfig 是 dict-like，直接透传."""
    return await guard_runtime_call(dict(config) if config else None)


def guard_runtime_call_sync(config: dict[str, Any] | None) -> RuntimeIdentity | None:
    """同步模型发送边界与异步路径共用授权判断，数据库故障失败关闭."""
    identity = identity_from_config(config)
    if identity is None:
        if _enforce_enabled():
            raise RuntimeIdentityError("缺少已认证运行身份，拒绝发起外部调用")
        return None
    _validate_identity_snapshot(identity)
    from common.identity.db import fetch_identity_state_sync

    try:
        state = fetch_identity_state_sync(
            identity.session_id, user_id=identity.user_id, tenant_id=identity.tenant_id
        )
    except (IdentityBackendUnavailable, TicketError) as exc:
        raise RuntimeIdentityRevoked("身份服务不可用，停止后续外部调用") from exc
    _validate_identity_state(identity, state)
    return identity
