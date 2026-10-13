"""LangGraph 原生 Auth：票据认证 + 资源级授权（任务 5.1/5.2/5.5）.

安全模型：
- `@auth.authenticate` 校验 admin API 签发的运行票据（签名/受众/时效），随后
  **每次请求实时查身份库**核对会话/账号/企业/成员状态与修订号；任何一项失效
  即 401/403。身份后端不可用 → 503 失败关闭，绝不匿名放行.
- 全局 `@auth.on` default-deny：所有未显式授权的 (resource, action) 一律拒绝
  （探针证实未注册路径会 fail-open，必须显式兜底）.
- 线程创建强制注入可信 metadata（tenant_id/user_id/membership_id/session_id），
  并登记 thread_ownership；读取/更新/删除/run 创建都按归属校验，归属未知即拒
  （“未知历史归属不可访问”）.
- assistants 读/搜开放（图定义元数据）；store/crons/assistants 写入全部默认拒绝.
- 客户端在 metadata/config/input 里提交的同名身份字段一律被覆盖：
  configurable 保留键由平台校验层禁止客户端传入（RESERVED_CONFIGURABLE_KEYS）.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import uuid
from typing import Any
from uuid import uuid5

from langgraph_sdk import Auth

from common.event_ledger.store import (
    LedgerBackendUnavailable,
    LedgerScope,
    attempt_reservation,
    claim_start,
    record_thread_deletion,
    thread_deletion_exists,
)
from common.identity.db import (
    IdentityBackendUnavailable,
    claim_thread_ownership,
    fetch_identity_state,
    fetch_thread_in_tenant,
    fetch_thread_owner,
)
from common.identity.ticket import (
    TicketError,
    parse_keys,
    verify_migration_ticket,
    verify_reconcile_ticket,
    verify_ticket,
)

auth = Auth()

logger = logging.getLogger(__name__)

_TRUSTED_METADATA_KEYS = ("tenant_id", "user_id", "membership_id", "session_id")


# ------------------------------------------------------- 平台可信执行图解析（F03）

_GRAPH_NAMES_CACHE: tuple[str, ...] | None = None


def trusted_graph_names() -> tuple[str, ...]:
    """平台自身的图注册表：运行期 `GRAPHS` 优先，回退平台声明的图清单环境变量.

    两者都由**平台/部署**提供，客户端无法写入，因此可作为"实际会执行哪个图"的可信来源。
    """
    global _GRAPH_NAMES_CACHE  # noqa: PLW0603 - 进程内注册表缓存
    if _GRAPH_NAMES_CACHE:
        return _GRAPH_NAMES_CACHE
    names: list[str] = []
    try:
        from langgraph_api.graph import GRAPHS  # noqa: PLC0415 - 仅镜像内可用

        names = [str(name) for name in GRAPHS]
    except Exception:  # noqa: BLE001 - 非平台进程内导入失败时回退
        names = []
    if not names:
        for key in ("LANGSERVE_GRAPHS", "LANGGRAPH_GRAPHS"):
            raw = os.environ.get(key) or ""
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                continue
            if isinstance(data, dict) and data:
                names = [str(name) for name in data]
                break
    _GRAPH_NAMES_CACHE = tuple(names)
    return _GRAPH_NAMES_CACHE


def resolve_executing_graph(assistant_id: str) -> str:
    """由平台可信映射解析**实际将执行**的图名；无法解析返回空串（调用方必须拒绝）.

    平台按 `assistant_id` 决定执行哪个图：图名直接命中；助手 UUID 由平台用
    `uuid5(NAMESPACE_GRAPH, 图名)` 生成（`langgraph_api.graph.get_assistant_id` 实测），
    故可反查。客户端自报的图名/UUID 只作为**待校验声明**，不作为裁决依据。
    """
    text = str(assistant_id or "").strip()
    if not text:
        return ""
    names = trusted_graph_names()
    if text in names:
        return text
    if _looks_like_uuid(text):
        try:
            from langgraph_api.graph import NAMESPACE_GRAPH  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return ""
        lowered = text.lower()
        for name in names:
            if str(uuid5(NAMESPACE_GRAPH, name)) == lowered:
                return name
    return ""


def _migration_ctx(claims) -> dict[str, Any]:  # noqa: ANN001 - mt-v1 claims
    """R06 受限迁移上下文：只允许对票据绑定的单个线程更新可信 metadata 键."""
    return {
        "identity": claims.user_id,
        "display_name": "thread-metadata-migration",
        "is_authenticated": True,
        "permissions": [],
        "tenant_id": claims.tenant_id,
        "membership_id": "",
        "session_id": "",
        "is_migration": True,
        "migration_thread_id": claims.thread_id,
    }


class _Deny(Exception):
    pass


def _user_attr(ctx, name: str, default=None):  # noqa: ANN001, ANN202
    user = ctx.user
    if isinstance(user, dict):
        return user.get(name, default)
    return getattr(user, name, default)


def _is_migration_ctx(ctx) -> bool:  # noqa: ANN001
    return bool(_user_attr(ctx, "is_migration", False))


def _deny_migration(ctx) -> None:  # noqa: ANN001
    """迁移票据仅允许线程 metadata 窄更新，其余操作一律 403."""
    if _is_migration_ctx(ctx):
        raise Auth.exceptions.HTTPException(
            status_code=403, detail="迁移票据不允许此操作"
        )


_UUID_RE_HOOK = __import__("re").compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_like_uuid(text: str) -> bool:
    """判断是否是 UUID 形态（assistant_id 是 UUID，图名不是）."""
    return bool(_UUID_RE_HOOK.match(text or ""))


def _ledger_ids(value: Any) -> tuple[str, str]:
    """从运行创建载荷提取 (attempt_id, submission_id)；缺任一即视为无预留."""
    return _payload_value(value, "ledger_attempt_id"), _payload_value(
        value, "ledger_submission_id"
    )


def _payload_value(value: Any, key: str) -> str:  # noqa: ANN401
    """按 metadata → kwargs.config.configurable → 顶层 config 顺序取载荷字段."""
    if not isinstance(value, dict):
        return ""
    metadata = value.get("metadata")
    if isinstance(metadata, dict) and metadata.get(key):
        return str(metadata[key])
    candidates: list[Any] = []
    kwargs = value.get("kwargs")
    if isinstance(kwargs, dict):
        candidates.append(kwargs.get("config"))
    candidates.append(value.get("config"))
    for config in candidates:
        configurable = config.get("configurable") if isinstance(config, dict) else None
        if isinstance(configurable, dict) and configurable.get(key):
            return str(configurable[key])
    return ""


def _payload_values(value: Any, key: str) -> list[str]:  # noqa: ANN401
    """按 metadata / kwargs.config.configurable / 顶层 config 取**全部**声明值.

    F03 需要检出"metadata 与 config 自报不一致"的形态，因此不能只取首个命中值。
    去重并保持出现顺序；空值忽略。
    """
    if not isinstance(value, dict):
        return []
    found: list[str] = []

    def _add(candidate: Any) -> None:  # noqa: ANN401
        text = str(candidate).strip() if candidate not in (None, "") else ""
        if text and text not in found:
            found.append(text)

    metadata = value.get("metadata")
    if isinstance(metadata, dict):
        _add(metadata.get(key))
    candidates: list[Any] = []
    kwargs = value.get("kwargs")
    if isinstance(kwargs, dict):
        candidates.append(kwargs.get("config"))
    candidates.append(value.get("config"))
    for config in candidates:
        configurable = config.get("configurable") if isinstance(config, dict) else None
        if isinstance(configurable, dict):
            _add(configurable.get(key))
    return found


def _ledger_attempt_id(value: Any) -> str:
    """从运行创建载荷提取台账尝试 ID.

    实测（镜像 0.15.1）create_run 钩子收到的载荷形状：
    `{thread_id, assistant_id, metadata, kwargs{config{configurable}, input, ...}, ...}`
    —— 即 `config` 位于 **kwargs 内**而非顶层。逐级尝试，兼容形态变化：

    1. `metadata.ledger_attempt_id`
    2. `kwargs.config.configurable.ledger_attempt_id`
    3. 顶层 `config.configurable.ledger_attempt_id`（防御性）

    该 ID 非秘密但不可伪造：调用方仍必须在台账中真实拥有一个属于本人 + 本线程、
    且未终态的尝试，否则被拒绝（见 `attempt_reservation`）。
    """
    return _payload_value(value, "ledger_attempt_id")


def _ledger_operation_kind(value: Any) -> str:
    """客户端声明的操作类型（服务端裁决时作为比对依据；缺省表示沿用预留记录）."""
    if not isinstance(value, dict):
        return ""
    metadata = value.get("metadata")
    if isinstance(metadata, dict) and metadata.get("ledger_operation_kind"):
        return str(metadata["ledger_operation_kind"])
    kwargs = value.get("kwargs")
    candidates = []
    if isinstance(kwargs, dict):
        candidates.append(kwargs.get("config"))
    candidates.append(value.get("config"))
    for config in candidates:
        configurable = config.get("configurable") if isinstance(config, dict) else None
        if isinstance(configurable, dict) and configurable.get("ledger_operation_kind"):
            return str(configurable["ledger_operation_kind"])
    return ""


def _run_input_content(value: Any) -> str:
    """从运行创建载荷取"最后一条用户消息正文"（与 admin 侧规范摘要口径一致）.

    admin 受理时用首条用户文本；BFF 与原生入口转发同一 input，故此处同样取**最后一条**
    用户/人类消息并 strip。取不到返回空串（此时内容校验按"预留未记录摘要"处理）。
    """
    kwargs = value.get("kwargs") if isinstance(value, dict) else None
    payload = kwargs.get("input") if isinstance(kwargs, dict) else None
    if payload is None and isinstance(value, dict):
        payload = value.get("input")
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return ""
    text = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("type") or message.get("role") or "")
        if role not in ("human", "user"):
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
    return text.strip()


def _pin_ledger_ids(
    value: Any,  # noqa: ANN401 - 平台注入的运行创建载荷
    *,
    request_id: str,
    attempt_id: str,
    submission_id: str,
    operation_kind: str,
    graph_id: str,
) -> None:
    """把服务端裁决过的关联写入 metadata 与 config.configurable（覆盖客户端自报值）.

    `ledger_graph_id` 固定为**平台可信映射解析出的实际执行图**，而不是客户端自报值：
    图内收尾节点的换图比对因此以事实为基准（F03）。
    """
    pinned = {
        "ledger_request_id": request_id,
        "ledger_attempt_id": attempt_id,
        "ledger_submission_id": submission_id,
        "ledger_operation_kind": operation_kind,
        "ledger_graph_id": graph_id,
    }
    if not isinstance(value, dict):
        return
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(pinned)
    value["metadata"] = metadata
    configs = []
    kwargs = value.get("kwargs")
    if isinstance(kwargs, dict):
        if not isinstance(kwargs.get("config"), dict):
            kwargs["config"] = {}
        configs.append(kwargs["config"])
        value["kwargs"] = kwargs
    if isinstance(value.get("config"), dict):
        configs.append(value["config"])
    for config in configs:
        configurable = config.get("configurable")
        if not isinstance(configurable, dict):
            configurable = {}
        configurable.update(pinned)
        config["configurable"] = configurable


def _reconcile_ctx(claims) -> dict[str, Any]:  # noqa: ANN001 - rs-v1 claims
    """服务级只读校准主体：**没有** user/membership/permissions，只有窄读范围."""
    return {
        "identity": claims.actor,
        "display_name": claims.actor,
        "is_authenticated": True,
        "permissions": [],
        "service_scope": claims.scope,
        "tenant_id": claims.tenant_id,
        "reconcile_threads": list(claims.thread_ids),
    }


def _is_reconcile_ctx(ctx: Auth.types.AuthContext) -> bool:
    """当前请求是否服务级只读校准主体（scope 必须精确匹配）."""
    return str(_user_attr(ctx, "service_scope", "") or "") == "ledger:reconcile"


def _ledger_scope(ids: dict[str, str]) -> LedgerScope | None:
    """可信身份 → 台账作用域（缺失任一标识即无法校验，返回 None）."""
    tenant_id = ids.get("tenant_id") or ""
    user_id = ids.get("user_id") or ""
    membership_id = ids.get("membership_id") or ""
    if not tenant_id or not user_id or not membership_id:
        return None
    return LedgerScope(
        tenant_id=tenant_id, user_id=user_id, membership_id=membership_id
    )


def _claims_from_ctx(ctx: Auth.types.AuthContext) -> dict[str, str]:
    # 平台 normalize 后的用户对象支持属性访问；测试/本地替身允许传 dict。
    return {
        "user_id": str(_user_attr(ctx, "identity", "") or ""),
        "tenant_id": str(_user_attr(ctx, "tenant_id", "") or ""),
        "membership_id": str(_user_attr(ctx, "membership_id", "") or ""),
        "session_id": str(_user_attr(ctx, "session_id", "") or ""),
    }


@auth.authenticate
async def authenticate(request):  # noqa: ANN001 - 平台注入 starlette Request
    """校验运行票据并核对实时身份状态；返回可信用户上下文."""
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    audience = os.environ.get("RUNTIME_TICKET_AUDIENCE", "langgraph-runtime")
    try:
        keys = parse_keys(os.environ.get("RUNTIME_TICKET_KEYS", ""))
        claims = verify_ticket(token, keys, expected_audience=audience)
    except TicketError:
        # R06：受限迁移票据（mt-v1）——只授权单线程 metadata 窄更新，其余资源
        # 在资源钩子处一律拒绝；伪造/过期/错受众同样 401。
        migration_audience = os.environ.get(
            "RUNTIME_TICKET_MIGRATION_AUDIENCE", "langgraph-migration"
        )
        try:
            mclaims = verify_migration_ticket(
                token, keys, expected_audience=migration_audience
            )
        except TicketError:
            # 最后尝试**服务级只读校准票据**（rs-v1）：不绑定用户会话，原成员被撤销后
            # 校准仍可用，但只按票据租户（可再按线程清单）只读运行状态。放在用户票据
            # 之后尝试，绝不影响正常用户路径的判定。
            reconcile_keys = parse_keys(os.environ.get("LEDGER_RECONCILE_KEYS", ""))
            if not reconcile_keys:
                raise Auth.exceptions.HTTPException(
                    status_code=401, detail="运行凭据无效"
                ) from None
            reconcile_audience = os.environ.get(
                "LEDGER_RECONCILE_AUDIENCE", "ledger-reconcile"
            )
            try:
                rclaims = verify_reconcile_ticket(
                    token, reconcile_keys, expected_audience=reconcile_audience
                )
            except TicketError:
                raise Auth.exceptions.HTTPException(
                    status_code=401, detail="运行凭据无效"
                ) from None
            # 服务票据不可直接读取原生 kwargs/state/history；只有投影网关能
            # 签发绑定此票据和读路径的内部委托。路径限制先于任何资源钩子。
            from fastapi import HTTPException as GatewayError

            from common.event_ledger.service_read import (
                PROJECTION_HEADER,
                authorized_owner,
                native_read_thread,
                projection_signature,
            )

            try:
                path = request.url.path
                thread_id = native_read_thread(request.method, path)
                expected = projection_signature(token, path)
                supplied = request.headers.get(PROJECTION_HEADER, "")
                if not hmac.compare_digest(expected, supplied):
                    raise GatewayError(403, detail="校准凭据必须经过状态投影网关")
                owner = await authorized_owner(rclaims, thread_id)
            except GatewayError as exc:
                raise Auth.exceptions.HTTPException(
                    status_code=exc.status_code, detail=exc.detail
                ) from exc
            actor = _reconcile_ctx(rclaims)
            # 平台按 identity 过滤运行列表；窄读委托使用已验证线程归属，
            # 不依赖或恢复原用户会话权限，所有其它路径仍在上面被拒绝。
            actor["identity"] = str(owner["user_id"])
            actor["service_actor"] = rclaims.actor
            return actor
        return _migration_ctx(mclaims)

    try:
        state = await fetch_identity_state(
            claims.session_id,
            user_id=claims.user_id,
            tenant_id=claims.tenant_id,
        )
    except IdentityBackendUnavailable as exc:
        # 身份服务故障：失败关闭（spec：不得继续匿名/旧身份）
        raise Auth.exceptions.HTTPException(
            status_code=503, detail="身份服务暂不可用"
        ) from exc
    if state is None:
        raise Auth.exceptions.HTTPException(status_code=401, detail="会话无效")
    if not state.session_active or not state.session_unexpired:
        raise Auth.exceptions.HTTPException(status_code=401, detail="会话已撤销或过期")
    if state.session_ctx_version != claims.session_ctx_version:
        # 企业已被切换（含并发切换）：旧票据的上下文作废
        raise Auth.exceptions.HTTPException(status_code=409, detail="企业上下文已变更")
    if not state.user_active or state.user_auth_revision != claims.user_auth_revision:
        raise Auth.exceptions.HTTPException(status_code=401, detail="账号状态已变更")
    if state.tenant_status != "active":
        raise Auth.exceptions.HTTPException(status_code=403, detail="企业不可用")
    if state.tenant_auth_revision != claims.tenant_auth_revision:
        raise Auth.exceptions.HTTPException(status_code=403, detail="企业授权已变更")
    if state.membership_status != "active":
        raise Auth.exceptions.HTTPException(status_code=403, detail="成员身份无效")
    if state.membership_revision != claims.membership_revision:
        raise Auth.exceptions.HTTPException(status_code=403, detail="成员授权已变更")

    return {
        "identity": claims.user_id,
        "display_name": state.account,
        "is_authenticated": True,
        "permissions": [],
        # 非秘密标识字段：平台注入 configurable.langgraph_auth_user，客户端不可覆写
        "tenant_id": claims.tenant_id,
        "membership_id": claims.membership_id,
        "session_id": claims.session_id,
        # R04：入站核验时的修订快照，运行中每次外呼与实时状态比较
        "session_ctx_version": state.session_ctx_version,
        "user_auth_revision": state.user_auth_revision,
        "tenant_auth_revision": state.tenant_auth_revision,
        "membership_revision": state.membership_revision,
    }


@auth.on
async def deny_by_default(ctx: Auth.types.AuthContext, value: Any) -> bool:
    """全局 default-deny：未显式登记的路径一律 403."""
    return False


# --------------------------------------------------------------------- threads


@auth.on.threads.create
async def on_thread_create(
    ctx: Auth.types.AuthContext, value: Auth.types.threads.create.Value
):
    """线程创建：可信归属校验 + thread_ownership 登记."""
    _deny_migration(ctx)
    ids = _claims_from_ctx(ctx)
    # 本版本平台不采用 create handler 对 metadata 的改写（实测），因此改为**强校验**：
    # 可信键要么不出现，要么必须与已验证身份完全一致——杜绝“写入他人 tenant/user 污染
    # 企业搜索”。合法代理总是显式携带本人 user_id + 当前 tenant_id.
    metadata = dict(value.get("metadata") or {})
    for key in _TRUSTED_METADATA_KEYS:
        submitted = metadata.get(key)
        if submitted is not None and str(submitted) != ids[key]:
            raise Auth.exceptions.HTTPException(
                status_code=403, detail="线程归属字段与认证身份不一致"
            )

    thread_id = str(value.get("thread_id") or "")
    if not thread_id:
        # 平台生成的线程 id 在 handler 里不可见；无显式 id 的创建被拒绝，
        # 保证“创建即认领归属”的不变式（聊天代理总是显式携带 thread_id）.
        raise Auth.exceptions.HTTPException(
            status_code=403, detail="线程标识必须由客户端显式提供"
        )
    try:
        uuid.UUID(thread_id)
    except ValueError as exc:
        raise Auth.exceptions.HTTPException(
            status_code=400, detail="线程标识非法"
        ) from exc
    # R01：只允许严格创建。do_nothing/update 会在"线程已存在"时静默吸收平台
    # 既有对象，使未知历史归属可被认领；平台层 409 与本侧认领协议共同裁决。
    if_exists = value.get("if_exists")
    if if_exists is not None and str(if_exists) != "raise":
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail="线程创建必须使用严格语义（if_exists=raise），已存在线程不可通过创建吸收",
        )
    try:
        status = await claim_thread_ownership(
            thread_id,
            tenant_id=ids["tenant_id"],
            user_id=ids["user_id"],
            session_id=ids["session_id"],
        )
    except IdentityBackendUnavailable as exc:
        raise Auth.exceptions.HTTPException(
            status_code=503, detail="归属登记不可用"
        ) from exc
    if status == "conflict":
        # 已有归属（他人/他企业/历史）：绝不把既存未知资源登记为本人。
        raise Auth.exceptions.HTTPException(
            status_code=409, detail="该会话标识已存在归属记录，创建被拒绝"
        )
    # claimed：待确认归属；仅当代理确认平台真实创建成功后才可读（见
    # artifact_http 的 /internal/threads/claim-confirmation）。
    # same：本人既有归属的严格创建重试，由平台 409 裁决，不改变归属。
    return


@auth.on.threads.read
async def on_thread_read(
    ctx: Auth.types.AuthContext, value: Auth.types.threads.read.Value
):
    """线程读取：按归属表放行本人本企业的线程.

    F05：服务级只读校准主体（rs-v1）按**票据绑定的租户**放行（可再以线程清单收窄）——
    只读运行状态，不恢复任何业务权限；其它一切动作仍由各自钩子拒绝。
    """
    _deny_migration(ctx)
    if _is_reconcile_ctx(ctx):
        tenant_id = str(_user_attr(ctx, "tenant_id", "") or "")
        thread_id = str(value.get("thread_id") or "")
        allowed = _user_attr(ctx, "reconcile_threads", []) or []
        if allowed and thread_id not in allowed:
            raise Auth.exceptions.HTTPException(
                status_code=403, detail="该校准凭据未授权该会话"
            )
        try:
            owner = await fetch_thread_in_tenant(thread_id, tenant_id=tenant_id)
        except IdentityBackendUnavailable as exc:
            raise Auth.exceptions.HTTPException(
                status_code=503, detail="归属校验不可用"
            ) from exc
        if owner is None or str(owner.get("tenant_id") or "") != tenant_id:
            raise Auth.exceptions.HTTPException(status_code=404, detail="会话不可访问")
        if str(owner.get("source") or "") == "native_pending":
            raise Auth.exceptions.HTTPException(status_code=404, detail="会话不可访问")
        return
    ids = _claims_from_ctx(ctx)
    thread_id = str(value.get("thread_id") or "")
    owner = await _owner_or_deny(thread_id, ids)
    # 归属匹配才放行；普通成员只读本人线程（user 条件）
    if owner["user_id"] != ids["user_id"] or owner["tenant_id"] != ids["tenant_id"]:
        raise Auth.exceptions.HTTPException(status_code=403, detail="无权访问此会话")
    return


@auth.on.threads.update
async def on_thread_update(
    ctx: Auth.types.AuthContext, value: Auth.types.threads.update.Value
):
    """线程更新：归属校验并强制覆盖可信 metadata 键（所有者不可转移）."""
    ids = _claims_from_ctx(ctx)
    thread_id = str(value.get("thread_id") or "")
    if _is_migration_ctx(ctx):
        # R06：受限迁移路径——票据绑定单线程，且该线程必须已有迁移台账登记的
        # 归属行；平台按受支持的 PATCH 语义写 metadata，脚本不再触碰私有表。
        mig_thread = str(_user_attr(ctx, "migration_thread_id", "") or "")
        if not mig_thread or mig_thread != thread_id:
            raise Auth.exceptions.HTTPException(
                status_code=403, detail="迁移票据与目标线程不一致"
            )
        try:
            owner = await fetch_thread_owner(
                thread_id, tenant_id=ids["tenant_id"], user_id=ids["user_id"]
            )
        except IdentityBackendUnavailable as exc:
            raise Auth.exceptions.HTTPException(
                status_code=503, detail="归属校验不可用"
            ) from exc
        if owner is None or str(owner.get("source")) != "migrated":
            raise Auth.exceptions.HTTPException(
                status_code=403, detail="迁移目标必须是台账登记归属的线程"
            )
        metadata = value.get("metadata")
        if isinstance(metadata, dict):
            for key in _TRUSTED_METADATA_KEYS:
                if key in metadata:
                    metadata[key] = ids[key]
            value["metadata"] = metadata
        return
    owner = await _owner_or_deny(thread_id, ids)
    if owner["user_id"] != ids["user_id"] or owner["tenant_id"] != ids["tenant_id"]:
        raise Auth.exceptions.HTTPException(status_code=403, detail="无权修改此会话")
    # 所有者/企业不可转移：剥离或覆盖任何可信键
    metadata = value.get("metadata")
    if isinstance(metadata, dict):
        for key in _TRUSTED_METADATA_KEYS:
            if key in metadata:
                metadata[key] = ids[key]
        value["metadata"] = metadata
    return


@auth.on.threads.delete
async def on_thread_delete(
    ctx: Auth.types.AuthContext, value: Auth.types.threads.delete.Value
):
    """线程删除：归属校验并联动清理归属登记."""
    _deny_migration(ctx)
    ids = _claims_from_ctx(ctx)
    thread_id = str(value.get("thread_id") or "")
    owner = await _owner_or_deny(thread_id, ids)
    if owner["user_id"] != ids["user_id"] or owner["tenant_id"] != ids["tenant_id"]:
        raise Auth.exceptions.HTTPException(status_code=403, detail="无权删除此会话")
    from common.identity.db import delete_thread_ownership

    # F04：直连原生删除同样必须先落**持久删除标记**（立即禁读/禁新写、排入受限清理），
    # 否则"删了却仍能读/继续写"。标记失败则拒绝删除（避免删了但没登记）。
    scope = _ledger_scope(ids)
    if scope is not None:
        try:
            await record_thread_deletion(scope, thread_id=thread_id)
        except LedgerBackendUnavailable as exc:
            raise Auth.exceptions.HTTPException(
                status_code=503, detail="删除登记暂不可用，未执行删除"
            ) from exc
    await delete_thread_ownership(
        thread_id, tenant_id=ids["tenant_id"], user_id=ids["user_id"]
    )
    return


@auth.on.threads.search
async def on_thread_search(
    ctx: Auth.types.AuthContext, value: Auth.types.threads.search.Value
):
    """线程搜索：强制叠加可信归属过滤（客户端不可覆盖）."""
    _deny_migration(ctx)
    if _is_reconcile_ctx(ctx):
        # 认证层只允许带内部委托的固定线程运行GET，租户/归属已验证。
        # 这里不能再按客户端可缺省的身份metadata过滤，否则旧的合法运行被隐藏。
        return {}
    ids = _claims_from_ctx(ctx)
    incoming = dict(value.get("metadata") or {})
    for key in _TRUSTED_METADATA_KEYS:
        incoming.pop(key, None)
    # 普通成员：当前企业 + 本人；可信过滤无条件并入（无法被输入覆盖）.
    # FilterType 即 metadata 过滤器本体（键为 metadata 字段，值为 $eq/$contains）.
    trusted = {"tenant_id": ids["tenant_id"], "user_id": ids["user_id"]}
    return {**incoming, **_eq_filter(trusted)}


@auth.on.threads.create_run
async def on_thread_create_run(
    ctx: Auth.types.AuthContext, value: Auth.types.threads.create_run.Value
):
    """运行创建：线程归属校验；无绑定线程的运行拒绝."""
    _deny_migration(ctx)
    ids = _claims_from_ctx(ctx)
    thread_id = str(value.get("thread_id") or "")
    if not thread_id:
        raise Auth.exceptions.HTTPException(
            status_code=403, detail="运行必须绑定已授权的会话"
        )
    owner = await _owner_or_deny(thread_id, ids)
    if owner["tenant_id"] != ids["tenant_id"] or owner["user_id"] != ids["user_id"]:
        raise Auth.exceptions.HTTPException(status_code=403, detail="无权在此会话运行")

    # F04：**已删除会话不得再产生运行**。删除是永久语义（存在标记即已删除，清理完成
    # 也不放行），新受理在管理端就被拒；这里封住"旧预留 + 直连原生"的旁路。
    deletion_scope = _ledger_scope(ids)
    if deletion_scope is not None:
        try:
            deleted = await thread_deletion_exists(deletion_scope, thread_id=thread_id)
        except LedgerBackendUnavailable as exc:
            raise Auth.exceptions.HTTPException(
                status_code=503, detail="台账服务暂不可用，拒绝创建运行"
            ) from exc
        if deleted:
            raise Auth.exceptions.HTTPException(
                status_code=403, detail="该会话已删除，不得再创建运行"
            )

    # 台账受理预留校验 + 启动权原子裁决（change add-agent-event-ledger，R01 修复）。
    # 覆盖流式/非流式与恢复路径——任何获准的业务运行都必须先经可信台账受理，
    # 否则会被记成"未统计的获准业务运行"。除"预留真实存在且归属本人本线程"外，
    # 还要求：
    #   1) 真实 graph（assistant_id）与预留一致——防换图；
    #   2) 规范内容摘要与预留一致——防换正文；
    #   3) 操作类型一致（客户端声明时）——防把 resume 当新提问；
    #   4) **同一预留只能消费一次**（单条带条件 UPDATE 原子裁决，并发只有一个胜出）。
    # 已启动/已有平台映射/受理未知一律不再放行第二次创建（不盲重发）。
    # attempt_id 非秘密但不可伪造：必须真实存在且归属可信身份。
    attempt_id, submission_id = _ledger_ids(value)
    if not attempt_id or not submission_id:
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail="运行创建缺少台账受理预留，请经授权入口发起",
        )
    scope = _ledger_scope(ids)
    if scope is None:
        raise Auth.exceptions.HTTPException(
            status_code=403, detail="运行创建缺少可信身份"
        )
    # F03：实际执行图必须由**平台可信映射**解析，且在任何业务节点执行前判定。
    # 早先实现只信任客户端自报的 `ledger_graph_id`，导致"预留 basic-qa + 自报
    # basic-qa + assistant_id 实为 intelligent-analysis"被 200 受理并创建运行，
    # 结束时的图名比对无法阻止已经发生的模型/工具副作用。现在：
    #   1) `actual` 由 assistant_id 经平台图注册表解析（客户端不可写入）；
    #   2) 预留图必须是 `actual`，否则 409；
    #   3) 客户端所有自报图（metadata 与 config 分别检查）不得与 `actual` 冲突，
    #      冲突即 409——避免"信声明不信事实"。
    assistant = str(value.get("assistant_id") or "")
    actual_graph = resolve_executing_graph(assistant)
    if not actual_graph:
        logger.warning(
            "ledger create_run: unresolvable assistant graph assistant_id=%r", assistant
        )
        raise Auth.exceptions.HTTPException(
            status_code=403,
            detail="无法从平台助手映射确定实际执行图，拒绝创建运行",
        )
    for declared in _payload_values(value, "ledger_graph_id"):
        if (
            declared != actual_graph
            and resolve_executing_graph(declared) != actual_graph
        ):
            raise Auth.exceptions.HTTPException(
                status_code=409,
                detail=(
                    f"运行请求声明的图（{declared}）与实际执行图（{actual_graph}）"
                    "不一致，拒绝创建"
                ),
            )
    graph_id = actual_graph
    declared_kind = _ledger_operation_kind(value)
    content = _run_input_content(value)
    try:
        reservation = await attempt_reservation(
            scope, attempt_id=attempt_id, thread_id=thread_id, allow_terminal=True
        )
        if reservation is None:
            raise Auth.exceptions.HTTPException(
                status_code=409,
                detail={
                    "code": "run_reservation_missing",
                    "error": "台账受理预留无效或不属于当前会话（可能已终态）",
                },
            )
        decision = await claim_start(
            scope,
            submission_id=submission_id,
            attempt_id=attempt_id,
            thread_id=thread_id,
            graph_id=graph_id,
            operation_kind=declared_kind or "initial",
            content=content,
            source="native",
        )
    except LedgerBackendUnavailable as exc:
        raise Auth.exceptions.HTTPException(
            status_code=503,
            detail={
                "code": "ledger_unavailable",
                "error": "台账服务暂不可用，拒绝创建运行",
            },
        ) from exc

    # 结构化 detail（code 字段）供受控入口（BFF）**可信区分**"已有运行可复用 /
    # 已启动待确认 / 不一致"三种拒绝：文本比对不可靠，也不允许把任意 409 当作成功。
    status = str(decision.get("status") or "")
    if status == "already_confirmed":
        # 已有平台运行映射：绝不重复创建；把既有运行 ID 交给调用方复用
        raise Auth.exceptions.HTTPException(
            status_code=409,
            detail={
                "code": "run_already_confirmed",
                "platform_run_id": str(decision.get("platform_run_id") or ""),
                "error": "该台账预留已有关联运行，不得重复创建",
            },
        )
    if status == "already_started":
        raise Auth.exceptions.HTTPException(
            status_code=409,
            detail={
                "code": "run_already_started",
                "error": "启动权已被消费（可能并发或响应丢失），不得重复创建",
            },
        )
    if status == "mismatch":
        raise Auth.exceptions.HTTPException(
            status_code=409,
            detail={
                "code": "run_request_mismatch",
                "field": str(decision.get("detail") or ""),
                "error": "运行请求与台账预留不一致，拒绝创建",
            },
        )
    if status in ("not_found", "not_owned"):
        raise Auth.exceptions.HTTPException(
            status_code=404,
            detail={"code": "run_reservation_missing", "error": "台账受理预留不可用"},
        )
    if status == "terminal":
        raise Auth.exceptions.HTTPException(
            status_code=409,
            detail={
                "code": "run_attempt_terminal",
                "error": "该台账预留对应的尝试已结束，拒绝创建新运行",
            },
        )
    if status != "claimed":
        raise Auth.exceptions.HTTPException(
            status_code=503,
            detail={
                "code": "run_claim_failed",
                "error": "台账启动权裁决失败，拒绝创建运行",
            },
        )

    # 权威关联注入：把**经校验**的 request/attempt 与操作类型写进运行 config/metadata，
    # 使 lifecycle 从 configurable（或 metadata 兜底）读到的是服务端裁决过的值，
    # 而不是客户端自报值——metadata-only 形态因此也能完整闭环。
    _pin_ledger_ids(
        value,
        request_id=str(reservation["request_id"]),
        attempt_id=attempt_id,
        submission_id=submission_id,
        operation_kind=declared_kind or "initial",
        graph_id=actual_graph,
    )
    # 元数据缺失也必须注入可信身份；否则平台运行列表过滤会隐藏合法的
    # metadata-only 运行，补偿无法定位。客户端同名声明在这里无条件覆盖。
    value["metadata"].update({key: ids[key] for key in _TRUSTED_METADATA_KEYS})
    # 运行中不再信任任何客户端身份字段；平台随后注入 auth user
    return


# --------------------------------------------------------------------- assistants


@auth.on.assistants.read
async def allow_assistant_read(ctx: Auth.types.AuthContext, value: Any):
    """助手定义（图清单）可读；第一版全部企业可用六个公开图.

    写入路径（create/update/delete）落入全局 default-deny.
    """
    _deny_migration(ctx)
    return True


@auth.on.assistants.search
async def allow_assistant_search(ctx: Auth.types.AuthContext, value: Any):
    """助手发现只读放行（图定义）."""
    _deny_migration(ctx)
    return True


# --------------------------------------------------------------------- 内部辅助


def _eq_filter(mapping: dict[str, str]) -> dict[str, dict[str, str]]:
    return {key: {"$eq": value} for key, value in mapping.items()}


async def _owner_or_deny(thread_id: str, ids: dict[str, str]) -> dict[str, str]:
    try:
        owner = await fetch_thread_owner(
            thread_id, tenant_id=ids["tenant_id"], user_id=ids["user_id"]
        )
    except IdentityBackendUnavailable as exc:
        raise Auth.exceptions.HTTPException(
            status_code=503, detail="归属校验不可用"
        ) from exc
    if owner is None:
        # 未知/跨企业归属一律按不可访问处理（404 语义由资源层体现为拒绝）
        raise Auth.exceptions.HTTPException(status_code=404, detail="会话不可访问")
    if str(owner.get("source") or "") == "native_pending":
        # R01：认领未确认（平台从未确认创建成功，或试图吸收未知历史线程后
        # 创建失败留下的残留行）——该资源仍按未知归属处理，不可读写。
        raise Auth.exceptions.HTTPException(status_code=404, detail="会话不可访问")
    return {"tenant_id": str(owner["tenant_id"]), "user_id": str(owner["user_id"])}
