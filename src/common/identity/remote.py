"""显式身份后端选择与 IAM 远程客户端（I2，G1.1/G1.2）.

部署模式（`AGENT_IDENTITY_BACKEND`，启动时固定，缺省 `local` 保留既有部署）：
- `local`：既有行为——admin API 签发 rt-v1 运行票据 + 运行时直连身份 PG 复核，
  不发任何 IAM HTTP 请求（A01：local 对 IAM 网络请求 0）。
- `remote`：唯一权威是共享 IAM——人类身份、会话、成员与有效权限全部由 IAM
  内省裁决；失败关闭，绝不回退本地身份表、旧 HMAC 或匿名（A01 负例）。
  非法取值直接配置错误拒绝启动，不会"猜一个模式"。

HTTP 客户端（G1.1）：
- 严格 DTO：`iam.identity.v1`（人类）与 `iam.introspection.v1`（inactive/机器）按
  G0 冻结探针（见 evidence g0-iam-probes.md）逐字段校验——缺字段、bool 当整数、
  字符串修订、未知 schema、错 issuer/audience、非法 JSON 一律视为协议失败关闭；
  HTTP 200 从不等于授权（A02）。
- 总 deadline：单次 IAM 核验预算默认 500ms（排队+连接+读取，`IAM_INTROSPECT_DEADLINE_SECONDS`）；
  有界失败，无自动授权重试。
- 异常映射：网络/协议/超时/内部客户端认证故障 → `IdentityBackendUnavailable`
  （调用方 503 关闭）；主体无效（active=false）→ `RemoteIdentityRejected`
  （401）；修订/上下文不匹配 → `RemoteContextMismatch`（409）。不靠 message 猜授权，
  不把服务故障伪装成登出。
- 连接生命周期：进程级 `httpx.Client` 复用连接，不缓存跨请求的正向授权；
  导入本模块不建立连接（无网络预热）。
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

import httpx

from common.identity.db import IdentityBackendUnavailable
from common.identity.runtime import RuntimeIdentity

# ---------------------------------------------------------------- 部署模式选择

_LOCAL = "local"
_REMOTE = "remote"
VALID_BACKENDS = (_LOCAL, _REMOTE)


class IdentityBackendConfigError(RuntimeError):
    """身份后端配置非法：remote 缺配置或模式取值错误。启动/请求期失败关闭."""


def identity_backend_mode() -> str:
    """读取并校验部署模式（每次调用读取，测试可注入；语义在启动时已固定）.

    非法值抛 `IdentityBackendConfigError`——调用方一律失败关闭，不存在隐式 local 回退.
    """
    raw = os.environ.get("AGENT_IDENTITY_BACKEND", "").strip().lower()
    if not raw:
        return _LOCAL
    if raw not in VALID_BACKENDS:
        raise IdentityBackendConfigError(
            f"AGENT_IDENTITY_BACKEND 非法取值：{raw!r}（仅支持 local|remote）"
        )
    return raw


def is_remote() -> bool:
    """当前部署是否 remote（调用方保证已先通过 mode 校验，非法值同样失败关闭）."""
    return identity_backend_mode() == _REMOTE


def require_remote_config() -> dict[str, str]:
    """Remote 必需配置校验：缺任一项即配置错误（拒绝启动/503），不得降级 local.

    - `IAM_BASE_URL`：IAM 根地址（http，回环或内网）
    - `IAM_ISSUER`：精确 issuer（内省响应逐字比较）
    - `IAM_INTERNAL_CLIENT_ID` / `IAM_INTERNAL_CLIENT_SECRET`：登记的 Agent 后端
      internal client（Basic 凭据；BFF 与 native 进程可共享同一受控凭据）
    - `IAM_RUNTIME_AUDIENCE`：运行受众（缺省 langgraph-runtime）
    """
    import os

    identity_backend_mode()
    keys = (
        "IAM_BASE_URL",
        "IAM_ISSUER",
        "IAM_INTERNAL_CLIENT_ID",
        "IAM_INTERNAL_CLIENT_SECRET",
    )
    missing = [key for key in keys if not (os.environ.get(key) or "").strip()]
    if missing:
        raise IdentityBackendConfigError(
            "remote 身份后端缺少必需配置: " + ", ".join(missing)
        )
    config = {key: os.environ[key].strip() for key in keys}
    config.setdefault("IAM_RUNTIME_AUDIENCE", "")  # dict[str,str] 形状保持
    config["IAM_RUNTIME_AUDIENCE"] = (
        os.environ.get("IAM_RUNTIME_AUDIENCE") or "langgraph-runtime"
    )
    return config


# ---------------------------------------------------------------- 严格 DTO

IDENTITY_SCHEMA_HUMAN = "iam.identity.v1"
IDENTITY_SCHEMA_SERVICE = "iam.introspection.v1"
REQUIRED_REVISIONS = (
    "user_auth_revision",
    "tenant_auth_revision",
    "membership_revision",
    "session_ctx_version",
)


class RemoteIdentityRejected(Exception):
    """主体无效（active=false 或协议判定无效）：调用方映射 401，不回显身份细节."""


class RemoteContextMismatch(Exception):
    """修订/上下文不匹配（复查 context_matches=false）：调用方映射 409."""


class RemoteProtocolError(Exception):
    """上游响应不符合冻结协议（HTTP 200 但形状非法）：失败关闭，映射 503."""


def _fail_protocol(reason: str) -> Any:
    """协议失败的统一出口：抛 RemoteProtocolError（调用方 503）。测试可 monkeypatch."""
    raise RemoteProtocolError(reason)


def _require_int_strict(payload: dict[str, Any], key: str, where: str) -> int:
    """严格整数：bool 不是整数，字符串/浮点不接受（A02 负例）."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        _fail_protocol(f"{where}.{key} 必须是整数（拒绝 bool/字符串/浮点）")
    return value


def _require_str(payload: dict[str, Any], key: str, where: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        _fail_protocol(f"{where}.{key} 必须是非空字符串")
    return value


def _require_uuid(payload: dict[str, Any], key: str, where: str) -> str:
    import uuid

    value = _require_str(payload, key, where)
    try:
        if str(uuid.UUID(value)) != value:
            _fail_protocol(f"{where}.{key} 不是规范 UUID")
    except ValueError as exc:
        raise RemoteProtocolError(f"{where}.{key} UUID 非法") from exc
    return value


@dataclass(frozen=True)
class RemoteIdentity:
    """已通过严格验证的 IAM 人类身份快照（仅非秘密标识 + 修订/权限）.

    字段与 RuntimeIdentity 对齐（四修订），另带 IAM 权限码集合——remote 权限
    完全来自 IAM，不沿用本地角色 join。
    """

    user_id: str
    account: str
    tenant_id: str | None
    membership_id: str | None
    session_id: str
    context_version: int
    user_auth_revision: int
    tenant_auth_revision: int | None
    membership_revision: int | None
    session_ctx_version: int
    context_usable: bool
    needs_tenant_selection: bool
    permissions: frozenset[str]

    def to_runtime_identity(self) -> RuntimeIdentity:
        """转运行身份（不含任何凭据；逐发送复查用四修订）."""
        return RuntimeIdentity(
            user_id=self.user_id,
            tenant_id=self.tenant_id or "",
            membership_id=self.membership_id or "",
            session_id=self.session_id,
            account=self.account,
            session_ctx_version=self.session_ctx_version,
            user_auth_revision=self.user_auth_revision,
            tenant_auth_revision=self.tenant_auth_revision,
            membership_revision=self.membership_revision,
        )


def parse_human_identity(
    payload: Any,
    *,
    expected_issuer: str,
    expected_audience: str,
) -> RemoteIdentity:
    """严格解析 iam.identity.v1 人类身份（G0 探针 P2 形状 + identity.schema.json）.

    任何形状/取值不符抛 `RemoteProtocolError`（HTTP 200 ≠ 授权，A02）。
    """
    if not isinstance(payload, dict):
        _fail_protocol("identity 响应必须是 JSON 对象")
    if payload.get("schema_version") != IDENTITY_SCHEMA_HUMAN:
        _fail_protocol(f"schema_version 必须 = {IDENTITY_SCHEMA_HUMAN}")
    if payload.get("active") is not True:
        # active=false 走 inactive 语义（机器/inactive 形状），不由本函数处理
        _fail_protocol("human identity 响应 active 必须为 true")
    if _require_str(payload, "issuer", "identity") != expected_issuer:
        _fail_protocol("issuer 不匹配")
    if _require_str(payload, "audience", "identity") != expected_audience:
        _fail_protocol("audience 不匹配")

    principal = payload.get("principal")
    if not isinstance(principal, dict):
        _fail_protocol("principal 缺失")
    if principal.get("type") != "human" or principal.get("realm") != "workforce":
        _fail_protocol("principal 必须是 workforce 人类主体")
    user_id = _require_uuid(principal, "id", "principal")

    user = payload.get("user")
    if not isinstance(user, dict):
        _fail_protocol("user 缺失")
    account = _require_str(user, "account", "user")
    if _require_str(user, "status", "user") != "active":
        _fail_protocol("user.status 必须 active（否则上游应返回 inactive 信封）")
    if _require_str(user, "id", "user") != user_id:
        _fail_protocol("user.id 与 principal.id 不一致")

    session = payload.get("session")
    if not isinstance(session, dict):
        _fail_protocol("session 缺失（人类主体必有会话）")
    session_id = _require_uuid(session, "id", "session")
    session_ctx_version = _require_int_strict(session, "ctx_version", "session")

    current_tenant = payload.get("current_tenant")
    tenant_id: str | None = None
    if current_tenant is not None:
        if not isinstance(current_tenant, dict):
            _fail_protocol("current_tenant 形状非法")
        tenant_id = _require_uuid(current_tenant, "id", "current_tenant")
        if _require_str(current_tenant, "status", "current_tenant") != "active":
            _fail_protocol("current_tenant.status 必须 active")

    membership = payload.get("membership")
    membership_id: str | None = None
    if membership is not None:
        if not isinstance(membership, dict):
            _fail_protocol("membership 形状非法")
        membership_id = _require_uuid(membership, "id", "membership")
        if _require_str(membership, "status", "membership") != "active":
            _fail_protocol("membership.status 必须 active")

    revisions = payload.get("revisions")
    if not isinstance(revisions, dict):
        _fail_protocol("revisions 缺失")
    parsed_revisions = {}
    for key in REQUIRED_REVISIONS:
        if tenant_id is None and key in ("tenant_auth_revision", "membership_revision"):
            if type(revisions.get(key)) is not int or revisions[key] != 0:
                _fail_protocol("无企业上下文的租户/成员修订必须为整数零")
            parsed_revisions[key] = 0
        else:
            value = _require_int_strict(revisions, key, "revisions")
            if value < (0 if key == "user_auth_revision" else 1):
                _fail_protocol("修订超出合法范围")
            parsed_revisions[key] = value
    if bool(tenant_id) != bool(membership_id):
        _fail_protocol("企业与成员上下文必须同时存在")
    if membership_id:
        if membership.get("tenant_id") != tenant_id:
            _fail_protocol("成员所属企业不匹配")
        if (
            _require_int_strict(membership, "revision", "membership")
            != parsed_revisions["membership_revision"]
        ):
            _fail_protocol("成员修订不一致")
    if session_ctx_version < 1:
        _fail_protocol("会话修订必须为正整数")

    context_version = _require_int_strict(payload, "context_version", "identity")
    if context_version != parsed_revisions["session_ctx_version"]:
        _fail_protocol("context_version 必须与 revisions.session_ctx_version 一致")
    if context_version != session_ctx_version:
        _fail_protocol("context_version 必须与 session.ctx_version 一致")

    permissions_raw = payload.get("permissions")
    if not isinstance(permissions_raw, list) or not all(
        isinstance(code, str) and code for code in permissions_raw
    ):
        _fail_protocol("permissions 必须是非空字符串数组")
    # uniqueItems 语义
    if len(set(permissions_raw)) != len(permissions_raw):
        _fail_protocol("permissions 含重复项")

    for key in ("context_usable", "needs_tenant_selection"):
        if not isinstance(payload.get(key), bool):
            _fail_protocol(f"{key} 必须是布尔")
    if not isinstance(payload.get("platform_admin"), bool):
        _fail_protocol("platform_admin 必须是布尔")
    if not isinstance(payload.get("evaluated_at"), str):
        _fail_protocol("evaluated_at 缺失")

    if payload["context_usable"] != bool(tenant_id and membership_id):
        _fail_protocol("上下文可用标志与企业/成员不一致")
    if payload["context_usable"] and payload["needs_tenant_selection"]:
        _fail_protocol("可用上下文不能同时要求选择企业")
    if not tenant_id and permissions_raw and not payload["platform_admin"]:
        _fail_protocol("无企业且非平台身份不能携带业务权限")
    for key in ("evaluated_at",):
        _require_timestamp(payload.get(key), key)
    for key in ("created_at", "expires_at", "absolute_expires_at"):
        _require_timestamp(session.get(key), f"session.{key}")

    return RemoteIdentity(
        user_id=user_id,
        account=account,
        tenant_id=tenant_id,
        membership_id=membership_id,
        session_id=session_id,
        context_version=context_version,
        user_auth_revision=parsed_revisions["user_auth_revision"],
        tenant_auth_revision=parsed_revisions["tenant_auth_revision"],
        membership_revision=parsed_revisions["membership_revision"],
        session_ctx_version=parsed_revisions["session_ctx_version"],
        context_usable=bool(payload["context_usable"]),
        needs_tenant_selection=bool(payload["needs_tenant_selection"]),
        permissions=frozenset(permissions_raw),
    )


def _require_timestamp(raw: Any, where: str) -> None:
    import datetime as dt

    try:
        value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("timezone missing")
    except (AttributeError, TypeError, ValueError) as exc:
        raise RemoteProtocolError(f"{where} 时间格式非法") from exc


def parse_recheck(payload: Any) -> tuple[bool, bool, dict[str, int]]:
    """严格解析 execution_context 复查响应（G0 探针 P4 形状）.

    返回 (active, context_matches, revisions)；形状不符抛 `RemoteProtocolError`.
    """
    if not isinstance(payload, dict):
        _fail_protocol("recheck 响应必须是 JSON 对象")
    if not isinstance(payload.get("active"), bool):
        _fail_protocol("recheck.active 必须是布尔")
    if not isinstance(payload.get("context_matches"), bool):
        _fail_protocol("recheck.context_matches 必须是布尔")
    revisions_raw = payload.get("revisions")
    if revisions_raw is None:
        # inactive 信封（RecheckInactiveVO）不带 revisions：主体已失效，无需基线
        if payload.get("active") is not True:
            return (False, bool(payload["context_matches"]), {})
        _fail_protocol("recheck.revisions 缺失")
    if not isinstance(revisions_raw, dict):
        _fail_protocol("recheck.revisions 形状非法")
    revisions = {
        key: _require_int_strict(revisions_raw, key, "revisions")
        for key in REQUIRED_REVISIONS
    }
    return (
        bool(payload["active"]),
        bool(payload["context_matches"]),
        revisions,
    )


# ---------------------------------------------------------------- HTTP 客户端

_DEFAULT_DEADLINE = 0.5  # 秒；总预算 = 排队 + 连接 + 读取
_CLIENT_LOCK = threading.Lock()
_CLIENT: httpx.Client | None = None
_CLIENTS: dict[str, httpx.Client] = {}


def _deadline() -> float:
    import os

    raw = os.environ.get("IAM_INTROSPECT_DEADLINE_SECONDS", "")
    if not raw:
        return _DEFAULT_DEADLINE
    try:
        value = float(raw)
    except ValueError as exc:
        raise IdentityBackendConfigError(
            "IAM_INTROSPECT_DEADLINE_SECONDS 非法"
        ) from exc
    if not __import__("math").isfinite(value) or value <= 0 or value > 10:
        raise IdentityBackendConfigError("IAM_INTROSPECT_DEADLINE_SECONDS 超出 (0,10]")
    return value


def _config_key(config: dict[str, str]) -> str:
    """连接绑定配置指纹：base_url + client id（secret 不进指纹/日志）."""
    return (
        __import__("hashlib")
        .sha256(
            "|".join(
                config.get(key, "")
                for key in (
                    "IAM_BASE_URL",
                    "IAM_INTERNAL_CLIENT_ID",
                    "IAM_INTERNAL_CLIENT_SECRET",
                )
            ).encode()
        )
        .hexdigest()
    )


def _shared_client(config: dict[str, str]) -> httpx.Client:
    """进程级连接复用客户端（Basic 凭据由 IAM_INTERNAL_* 构成）.

    导入本模块不建立连接；首次核验时惰性创建。trust_env=False 避免测试环境
    代理变量劫持回环请求.
    """
    global _CLIENT
    with _CLIENT_LOCK:
        if (
            _CLIENT is not None
            and getattr(_CLIENT, "_iam_config_key", None) in (None, _config_key(config))
            and not _CLIENT.is_closed
        ):
            return _CLIENT
        key = _config_key(config)
        client = _CLIENTS.get(key)
        if client is None or client.is_closed:
            client = httpx.Client(
                base_url=config["IAM_BASE_URL"].rstrip("/"),
                auth=(
                    config["IAM_INTERNAL_CLIENT_ID"],
                    config["IAM_INTERNAL_CLIENT_SECRET"],
                ),
                timeout=httpx.Timeout(_deadline()),
                trust_env=False,
                headers={"accept": "application/json"},
            )
            client._iam_config_key = key
            _CLIENTS[key] = client
        _CLIENT = client
        return client


def close_shared_client() -> None:
    """关闭并复位共享客户端（测试/进程收尾用）."""
    global _CLIENT
    with _CLIENT_LOCK:
        clients = set(_CLIENTS.values())
        if _CLIENT is not None:
            clients.add(_CLIENT)
        for client in clients:
            client.close()
        _CLIENTS.clear()
        _CLIENT = None


_REQUEST_WORKERS = ThreadPoolExecutor(max_workers=16, thread_name_prefix="iam-http")
_REQUEST_SLOTS = threading.BoundedSemaphore(16)


def request_iam(
    method: str,
    path: str,
    *,
    config: dict[str, str],
    body: dict | None = None,
    bearer: str | None = None,
    internal: bool = True,
) -> httpx.Response:
    """有界连接复用；500ms 总预算包括队列、连接与读取，超时永不授权 ."""
    deadline = _deadline()
    started = time.monotonic()
    if not _REQUEST_SLOTS.acquire(timeout=deadline):
        raise IdentityBackendUnavailable("IAM 核验队列超时")

    def send():
        try:
            client = _shared_client(config)
            kwargs: dict[str, Any] = {"timeout": deadline}
            if body is not None:
                kwargs["json"] = body
            if bearer is not None:
                kwargs["auth"] = None
                kwargs["headers"] = {"authorization": f"Bearer {bearer}"}
            elif not internal:
                kwargs["auth"] = None
            return client.request(method, path, **kwargs)
        finally:
            _REQUEST_SLOTS.release()

    future = _REQUEST_WORKERS.submit(send)
    try:
        return future.result(timeout=max(0, deadline - (time.monotonic() - started)))
    except (FutureTimeout, httpx.HTTPError) as exc:
        # In-flight transport is bounded too; its late result is discarded.
        raise IdentityBackendUnavailable("IAM 请求不可用或超过总时限") from exc


def response_json(response: httpx.Response) -> Any:
    """只接受成功 JSON；内部认证失败按服务故障处理 ."""
    if response.status_code != 200:
        raise IdentityBackendUnavailable(f"IAM 意外状态码 {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise RemoteProtocolError("IAM 响应不是合法 JSON") from exc


def subject_payload(
    subject_token: str,
    *,
    expected_audience: str,
    config: dict[str, str] | None = None,
    include_execution_context: bool = False,
) -> dict:
    """取得并完整验证主体；可选返回 IAM 登记的执行引用 ."""
    cfg = config or require_remote_config()
    body = {"subject_token": subject_token, "audience": expected_audience}
    if include_execution_context:
        body["include_execution_context"] = True
    payload = response_json(
        request_iam("POST", "/v1/internal/identity/introspect", config=cfg, body=body)
    )
    if (
        isinstance(payload, dict)
        and payload.get("schema_version") == IDENTITY_SCHEMA_SERVICE
    ):
        if payload.get("active") is False:
            raise RemoteIdentityRejected("主体无效或已撤销")
        raise RemoteProtocolError("人类身份端点收到非人类主体")
    parse_human_identity(
        payload, expected_issuer=cfg["IAM_ISSUER"], expected_audience=expected_audience
    )
    return payload


def introspect_subject_token(
    subject_token: str,
    *,
    expected_audience: str,
    config: dict[str, str] | None = None,
) -> RemoteIdentity:
    """用主体 token 换取严格验证过的人类身份（同步；BFF 服务端进程内）.

    - 网络/超时/内部客户端认证故障 → `IdentityBackendUnavailable`（503 关闭）。
    - active=false / invalid 信封 → `RemoteIdentityRejected`（401）。
    - 协议形状非法 → `RemoteProtocolError`（503）。
    不缓存正向结论：每次调用都是一次实时 IAM 裁决。
    """
    cfg = config or require_remote_config()
    payload = subject_payload(
        subject_token, expected_audience=expected_audience, config=cfg
    )
    return parse_human_identity(
        payload, expected_issuer=cfg["IAM_ISSUER"], expected_audience=expected_audience
    )


def recheck_execution_context(
    verification_ref: str,
    expected_revisions: dict[str, int],
    *,
    expected_audience: str,
    config: dict[str, str] | None = None,
) -> tuple[bool, bool]:
    """执行引用实时复查（同步；运行中逐发送边界用，G2 接入 async 包装）.

    返回 (active, context_matches)。active=false → `RemoteIdentityRejected`；
    context_matches=false → `RemoteContextMismatch`；网络/协议故障 → 503 语义。
    """
    cfg = config or require_remote_config()
    payload = response_json(
        request_iam(
            "POST",
            "/v1/internal/identity/introspect",
            config=cfg,
            body={
                "execution_context": {
                    "verification_ref": verification_ref,
                    "expected_revisions": expected_revisions,
                },
                "audience": expected_audience,
            },
        )
    )
    try:
        active, context_matches, _revisions = parse_recheck(payload)
        if active and context_matches and _revisions != expected_revisions:
            raise RemoteProtocolError("IAM 复查返回修订与请求基线不一致")
    except RemoteProtocolError:
        raise
    if not active and payload.get("reason") == "revision_mismatch":
        raise RemoteContextMismatch("授权上下文已变更")
    if not active:
        raise RemoteIdentityRejected("主体无效或已撤销（引用复查）")
    if not context_matches:
        raise RemoteContextMismatch("修订/上下文不匹配（引用复查）")
    return True, True


def monotonic_now() -> float:
    """测试可替换的时钟（本模块不做时间运算断言，仅供调用方测量）."""
    return time.monotonic()
