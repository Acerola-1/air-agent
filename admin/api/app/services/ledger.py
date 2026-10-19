"""企业事件台账服务（可信受理、幂等、尝试/回答/Trace 关联、事件与 outbox）。

设计要点（change add-agent-event-ledger）：
- **受理顺序**：可信授权 → 同事务持久化 request/submission/attempt + 事件 + outbox
  → 由受控入口启动平台运行 → 回填平台 run 关联。台账事务失败时调用方返回 503，
  不启动新运行（spec: 业务请求在台账受理事务失败时不启动新运行）。
- **提交幂等**：同成员+线程+提交键 + 同规范摘要 → 复用已有 request/attempt；
  同键不同摘要 → 409 冲突（不覆盖已有内容）。
- **受理歧义**：平台不提供调用方指定 run_id 与幂等创建（见 task-1.2 实测），因此
  上游响应丢失时置 `admission_unknown`，由校准流程按注入的 metadata 标记匹配，
  **不自动重发**。
- 正文只进 `agent_content_refs`（独立授权）；事件/outbox 禁带正文与票据。
- 明细/状态查询：普通成员仅本人；企业运营需独立权限（服务层在该权限下显式设置
  `air_agent.agent_operations` GUC，与数据库策略共同限定）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import TenantContext, apply_tenant_context
from app.models_ledger import (
    AgentAnswer,
    AgentCapabilitySelection,
    AgentContentRef,
    AgentEvent,
    AgentOutbox,
    AgentProjectionState,
    AgentRequest,
    AgentRunAttempt,
    AgentRunResume,
    AgentSubmission,
    AgentThreadDeletion,
    AgentTraceLink,
)
from app.schemas_ledger import CapabilitySelectionIn
from app.services.identity import Identity

# 与 docs/contracts/agent-events-v1 保持一致的最小子集（避免 admin venv 依赖 src/）
ACTIVE_ATTEMPT_STATES = ("admitting", "admission_unknown", "queued", "running", "waiting_input")
ENDED_STATES = ("succeeded", "failed", "cancelled", "timed_out")
OPERATION_KINDS = ("initial", "retry", "regenerate", "resume")
# F02：人工恢复只允许来自**等待输入**的可恢复状态；其它状态（运行中/已终态/未知）
# 说明"没有什么可恢复的"，如实拒绝而不是受理出一个假的恢复操作。
RESUMABLE_ATTEMPT_STATES = ("waiting_input",)
TERMINAL_STATES = ENDED_STATES
# 证据来源秩：数值越大越权威（states.json.evidence_sources）
EVIDENCE_RANK = {"platform_read": 3, "runtime_callback": 2, "browser_observed": 1}
DEFAULT_CONSUMERS = ("run_projection",)
METRIC_VERSION = "v1"
BUSINESS_TZ = "Asia/Shanghai"


class LedgerError(Exception):
    """台账域错误基类：调用方映射为明确 HTTP 状态。"""

    status_code = 400

    def __init__(self, message: str, *, code: str = "ledger_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ThreadDeletedError(LedgerError):
    """已删除会话（永久语义）：拒绝新的运行事实/事件写入.

    F04：删除后仍可能有**晚到回调**（内部状态/回答/恢复/受理确认端点）到达；一律拒绝，
    不复活已删会话的事实，也不产生新的运行事件。
    """

    status_code = 409


class SubmissionConflict(LedgerError):
    """同提交键不同内容。"""

    status_code = 409


class CapabilityValidationError(LedgerError):
    """能力选择语义校验失败（非法ID、依赖缺失、菜单冲突）."""

    status_code = 422


class CapabilityConflict(LedgerError):
    """能力选择冲突（版本过期、功能停用、恢复时材料/绑定变更）."""

    status_code = 409


class CapabilityUnauthorized(LedgerError):
    """未授权能力选择（未授权或已撤销的 MCP 服务）."""

    status_code = 403


class AdmissionRejected(LedgerError):
    """受理前置条件不满足（线程归属/图/上下文）。"""

    status_code = 403


class BusinessDenied(LedgerError):
    """业务准入拒绝（保留 401/403/409/503 语义，不伪装成其他错误）。"""

    def __init__(
        self, message: str, *, status_code: int = 403, code: str = "business_denied"
    ) -> None:
        super().__init__(message, code=code)
        self.status_code = status_code


def _business_grant(identity: Identity, graph_id: str):
    """服务端按实际业务求交；任何失败转为台账域错误（携带原状态码）."""
    from common.identity.business_access import BusinessAccessError, grant_for_graph

    try:
        return grant_for_graph(graph_id=graph_id, identity=identity)
    except BusinessAccessError as exc:
        raise BusinessDenied(exc.message, status_code=exc.status_code, code=exc.code) from exc


class NotFound(LedgerError):
    status_code = 404


@dataclass(frozen=True)
class AdmissionResult:
    request_id: uuid.UUID
    attempt_id: uuid.UUID
    submission_id: uuid.UUID
    admission_status: str
    operation_kind: str
    reused: bool
    thread_id: str
    graph_id: str
    business_code: str | None = None
    business_policy_version: str | None = None
    capability_selection: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": str(self.request_id),
            "attempt_id": str(self.attempt_id),
            "submission_id": str(self.submission_id),
            "admission_status": self.admission_status,
            "operation_kind": self.operation_kind,
            "reused": self.reused,
            "thread_id": self.thread_id,
            "graph_id": self.graph_id,
            "business_code": self.business_code,
            "business_policy_version": self.business_policy_version,
            "ledger_marker": ledger_marker(self.request_id, self.attempt_id),
            "capability_selection": self.capability_selection,
        }


def ledger_marker(request_id: uuid.UUID | str, attempt_id: uuid.UUID | str) -> str:
    """注入平台 run metadata 的非秘密关联标记（供受理校准匹配）。"""
    return f"lr:{request_id}:{attempt_id}"


def parse_ledger_marker(marker: str) -> tuple[str, str] | None:
    try:
        _, request_id, attempt_id = marker.split(":", 2)
    except ValueError:
        return None
    if not request_id or not attempt_id:
        return None
    return request_id, attempt_id


# --------------------------------------------------------------------- 规范摘要


def canonical_digest(payload: Any) -> str:
    """规范 JSON 摘要：sha256(sort_keys, 紧凑分隔符, 非 ASCII 保留)。

    与 `src/common/event_ledger/contract.py::canonical_digest` 及
    `docs/contracts/agent-events-v1/README.md` 的定义字节一致（跨项目契约测试保证）。
    """
    import hashlib
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_content_digest(*, graph_id: str, operation_kind: str, content: Any) -> str:
    """提交内容摘要：图 + 操作类型 + 规范化文本/消息。"""
    normalized = content.strip() if isinstance(content, str) else content
    return canonical_digest(
        {"graph_id": graph_id, "operation_kind": operation_kind, "content": normalized}
    )


def canonical_selection_digest(
    *,
    graph_id: str,
    skill_mode: str,
    skill_id: str | None,
    mcp_mode: str,
    tool_ids: list[str],
    material_version: str,
) -> str:
    """能力选择快照规范摘要（Change 4 任务 3.4）."""
    return canonical_digest(
        {
            "graph_id": graph_id,
            "material_version": material_version,
            "mcp_mode": mcp_mode,
            "skill_id": skill_id,
            "skill_mode": skill_mode,
            "tool_ids": sorted(list(dict.fromkeys(tool_ids))),
        }
    )


def canonical_content_digest_v2(
    *,
    graph_id: str,
    operation_kind: str,
    content: Any,
    capability_selection_digest: str,
) -> str:
    """v2 提交内容摘要：包含图 + 操作类型 + 规范化文本/消息 + 选择摘要."""
    normalized = content.strip() if isinstance(content, str) else content
    return canonical_digest(
        {
            "capability_selection_digest": capability_selection_digest,
            "content": normalized,
            "graph_id": graph_id,
            "operation_kind": operation_kind,
        }
    )


canonical_content_digest_v1 = canonical_content_digest


def normalize_capability_selection(
    raw: CapabilitySelectionIn | dict[str, Any] | None,
) -> dict[str, Any]:
    """规范化用户能力选择意图，未声明归一化为 auto/auto."""
    if raw is None:
        return {
            "schema_version": "agent.capability-selection.v1",
            "skill": {"mode": "auto", "skill_id": None},
            "mcp": {"mode": "auto", "tool_ids": []},
            "catalog_version": None,
            "selection_source": "automatic",
        }
    if isinstance(raw, dict):
        raw_model = CapabilitySelectionIn.model_validate(raw)
    else:
        raw_model = raw

    skill_mode = raw_model.skill.mode
    skill_id = raw_model.skill.skill_id
    if skill_mode == "auto":
        skill_id = None
    elif skill_mode == "explicit":
        if not skill_id or not isinstance(skill_id, str):
            raise CapabilityValidationError(
                "explicit 模式下 skill_id 不能为空", code="invalid_capability_selection"
            )
        skill_id = skill_id.strip()
        if ".." in skill_id or any(ord(c) < 32 for c in skill_id):
            raise CapabilityValidationError(
                "skill_id 包含非法路径字符或控制字符", code="invalid_capability_selection"
            )

    mcp_mode = raw_model.mcp.mode
    raw_tools = raw_model.mcp.tool_ids or []
    if mcp_mode == "auto":
        tool_ids = []
    elif mcp_mode == "allowlist":
        unique_tools = sorted(list(dict.fromkeys(raw_tools)))
        if len(unique_tools) > 20:
            raise CapabilityValidationError(
                "已选 MCP 工具数量不能超过 20", code="invalid_capability_selection"
            )
        for tid in unique_tools:
            if (
                not isinstance(tid, str)
                or len(tid) > 200
                or ".." in tid
                or any(ord(c) < 32 for c in tid)
            ):
                raise CapabilityValidationError(
                    f"非法 MCP 工具标识: {tid}", code="invalid_capability_selection"
                )
        tool_ids = unique_tools
    else:
        tool_ids = []

    is_explicit = (skill_mode == "explicit") or (mcp_mode == "allowlist")
    selection_source = "user_explicit" if is_explicit else "automatic"

    return {
        "schema_version": "agent.capability-selection.v1",
        "skill": {"mode": skill_mode, "skill_id": skill_id},
        "mcp": {"mode": mcp_mode, "tool_ids": tool_ids},
        "catalog_version": raw_model.catalog_version,
        "selection_source": selection_source,
    }


def _validate_mcp_tool_authorization(
    *,
    business_code: str,
    tenant_id: uuid.UUID,
    tool_ids: list[str],
) -> None:
    """按业务注册表 + 受保护连接白名单校验 allowlist（不查旧绑定表）.

    静态校验足够在受理阶段拒绝越权/未分类工具：连接配置的
    `approved_query_tools` 是服务侧审核过的可执行清单，业务注册表再显式声明
    本业务允许的服务与工具。未登记的新工具不会被自动开放。
    """
    if not tool_ids:
        return
    from common.business_registry import business_for_code
    from common.mcp_authorization import McpAuthorizationError, resolve_connection

    definition = business_for_code(business_code)
    if definition is None:
        raise CapabilityUnauthorized("业务未登记，拒绝能力选择", code="business_unregistered")
    for tid in tool_ids:
        parts = tid.split(":")
        if len(parts) < 3 or parts[0] != "mcp":
            raise CapabilityValidationError(
                f"非法的 MCP 工具标识格式: {tid}", code="invalid_tool_id"
            )
        server_key, tool_name = parts[1], parts[2]
        try:
            resolved = resolve_connection(
                business_code=business_code,
                tenant_id=str(tenant_id),
                server_key=server_key,
            )
        except McpAuthorizationError as exc:
            raise CapabilityUnauthorized(
                f"未授权或不可用的 MCP 服务: {server_key}", code="mcp_unauthorized"
            ) from exc
        if not resolved.business_scope.allows(tool_name) or not resolved.connection.allows_tool(
            tool_name
        ):
            raise CapabilityUnauthorized(
                f"工具 {tid} 不在已审核的业务查询范围内", code="mcp_tool_unauthorized"
            )


_SKILL_ID_PATTERN = re.compile(r"^skill:([a-zA-Z0-9_-]+):([a-zA-Z0-9_-]+)$")
_SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_GRAPH_DIR_MAP: dict[str, str] = {
    "basic-qa": "basic_qa",
    "data-analysis": "data_analysis",
    "intelligent-tracing": "intelligent_tracing",
}


def _calculate_skill_material_version(skill_dir: Path) -> str:
    if not skill_dir.is_dir():
        return "sha256:empty"
    hasher = hashlib.sha256()
    files = sorted(
        [p for p in skill_dir.rglob("*") if p.is_file() and not p.name.startswith(".")],
        key=lambda p: p.relative_to(skill_dir).as_posix(),
    )
    for file_path in files:
        rel_posix = file_path.relative_to(skill_dir).as_posix()
        try:
            content = file_path.read_bytes()
        except OSError:
            content = b""
        hasher.update(f"{rel_posix}:".encode())
        hasher.update(content)
        hasher.update(b"\n")
    return f"sha256:{hasher.hexdigest()}"


def _parse_skill_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---"):
        return {"name": "", "description": "", "required-tools": []}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {"name": "", "description": "", "required-tools": []}
    frontmatter_raw = parts[1]
    res: dict[str, Any] = {}
    current_key: str | None = None
    multiline_buf: list[str] = []

    for line in frontmatter_raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
            if current_key and multiline_buf:
                res[current_key] = "\n".join(multiline_buf).strip()
                multiline_buf = []
            k, v = line.split(":", 1)
            current_key = k.strip()
            v_str = v.strip()
            if v_str.startswith("[") and v_str.endswith("]"):
                items = [
                    item.strip().strip("'\"") for item in v_str[1:-1].split(",") if item.strip()
                ]
                res[current_key] = items
                current_key = None
            elif v_str:
                res[current_key] = v_str.strip("'\"")
                current_key = None
            else:
                multiline_buf = []
        elif current_key and (stripped.startswith("- ") or stripped.startswith("* ")):
            if not isinstance(res.get(current_key), list):
                res[current_key] = []
            val = stripped[2:].strip().strip("'\"")
            res[current_key].append(val)
        elif current_key:
            multiline_buf.append(stripped)

    if current_key and multiline_buf and current_key not in res:
        res[current_key] = "\n".join(multiline_buf).strip()
    return res


def validate_skill_selection(
    graph_id: str, skill_id: str, *, menu_name: str | None = None
) -> dict[str, Any]:
    m = _SKILL_ID_PATTERN.match(skill_id)
    if not m:
        raise CapabilityValidationError(
            f"非法的技能标识格式: {skill_id}", code="invalid_skill_selection"
        )
    g_id, slug = m.group(1), m.group(2)
    if g_id != graph_id:
        raise CapabilityValidationError(
            f"技能 {skill_id} 不属于当前图 {graph_id}", code="invalid_skill_selection"
        )
    if graph_id not in _GRAPH_DIR_MAP:
        raise CapabilityValidationError(f"未知图标识: {graph_id}", code="invalid_skill_selection")

    if graph_id == "data-analysis" and menu_name:
        try:
            from data_analysis.menu_skill_mapping import match_menu_skill

            matched = match_menu_skill(menu_name)
            if matched and matched.skill_name != slug:
                raise CapabilityValidationError(
                    f"所选技能 {skill_id} 与当前菜单 {menu_name} 映射不一致",
                    code="menu_skill_mismatch",
                )
        except (ImportError, CapabilityValidationError):
            raise
        except Exception:
            pass

    repo_root = Path(__file__).resolve().parents[4]
    skill_dir = repo_root / "src" / _GRAPH_DIR_MAP[graph_id] / "skills" / slug
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        raise CapabilityValidationError(
            f"技能 {skill_id} 不存在或不可用", code="invalid_skill_selection"
        )

    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapabilityValidationError(
            f"技能文件读取失败: {exc}", code="invalid_skill_selection"
        ) from exc

    meta = _parse_skill_frontmatter(text)
    mat_version = _calculate_skill_material_version(skill_dir)
    return {
        "id": skill_id,
        "name": slug,
        "material_version": mat_version,
        "required_tool_ids": meta.get("required-tools") or [],
    }


# ---------------------------------------------------------------- 事件与 outbox


def _event_row(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    event_type: str,
    source: str,
    occurred_at: dt.datetime,
    payload: dict[str, Any],
    thread_id: str | None = None,
    request_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    run_id: str | None = None,
    answer_id: uuid.UUID | None = None,
    message_id: str | None = None,
    state_revision: int | None = None,
    event_id: uuid.UUID | None = None,
) -> AgentEvent:
    return AgentEvent(
        event_id=event_id or uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        membership_id=membership_id,
        schema_version="1.0",
        event_type=event_type,
        source=source,
        occurred_at=occurred_at,
        received_at=dt.datetime.now(dt.UTC),
        thread_id=thread_id,
        request_id=request_id,
        attempt_id=attempt_id,
        run_id=run_id,
        answer_id=answer_id,
        message_id=message_id,
        payload=payload,
        payload_digest=canonical_digest(payload),
        state_revision=state_revision,
        created_at=dt.datetime.now(dt.UTC),
    )


async def emit_fact(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    consumers: tuple[str, ...] = DEFAULT_CONSUMERS,
    occurred_at: dt.datetime | None = None,
    thread_id: str | None = None,
    request_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    run_id: str | None = None,
    answer_id: uuid.UUID | None = None,
    message_id: str | None = None,
    state_revision: int | None = None,
    event_id: uuid.UUID | None = None,
) -> AgentEvent:
    """事实 + outbox 同事务写入（调用方事务提交后二者一起可见）。"""
    event = _event_row(
        tenant_id=tenant_id,
        user_id=user_id,
        membership_id=membership_id,
        event_type=event_type,
        source=source,
        occurred_at=occurred_at or dt.datetime.now(dt.UTC),
        payload=payload,
        thread_id=thread_id,
        request_id=request_id,
        attempt_id=attempt_id,
        run_id=run_id,
        answer_id=answer_id,
        message_id=message_id,
        state_revision=state_revision,
        event_id=event_id,
    )
    session.add(event)
    await session.flush()
    now = dt.datetime.now(dt.UTC)
    for consumer in consumers:
        session.add(
            AgentOutbox(
                tenant_id=tenant_id,
                event_id=event.event_id,
                consumer=consumer,
                thread_id=thread_id,
                available_at=now,
                status="pending",
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()
    return event


# --------------------------------------------------------------------- 线程归属


async def _assert_thread_owned(
    session: AsyncSession, *, thread_id: str, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """线程必须已有**已确认**归属（native/migrated），pending 视为未知归属。"""
    row = (
        await session.execute(
            text(
                "SELECT source FROM thread_ownership"
                " WHERE thread_id = :t AND tenant_id = :ten AND user_id = :u"
            ),
            {"t": thread_id, "ten": str(tenant_id), "u": str(user_id)},
        )
    ).first()
    if row is None or str(row[0]) not in ("native", "migrated"):
        raise AdmissionRejected("会话归属未确认或不属于当前企业", code="thread_not_owned")


# ---------------------------------------------------------------------- 受理


def _assert_source_business(attempt: AgentRunAttempt, grant) -> None:
    """重试/恢复不得换业务；旧运行没有新快照时要求重新受理."""
    if attempt.business_code is None or attempt.business_policy_version is None:
        raise CapabilityConflict(
            "原运行缺少业务许可快照，请重新发起请求", code="business_snapshot_missing"
        )
    if (
        attempt.business_code != grant.business_code
        or attempt.business_policy_version != grant.policy_version
    ):
        raise CapabilityConflict(
            "重试或恢复不允许更换业务或业务授权策略",
            code="business_snapshot_changed",
        )


async def admit_request(
    session: AsyncSession,
    identity: Identity,
    *,
    thread_id: str,
    submission_key: str,
    graph_id: str,
    operation_kind: str,
    content: Any,
    user_message_id: str | None = None,
    source_attempt_id: uuid.UUID | None = None,
    content_snapshot: str | None = None,
    retention_days: int | None = None,
    menu_name: str | None = None,
    capability_selection: CapabilitySelectionIn | dict[str, Any] | None = None,
) -> AdmissionResult:
    """可信受理：幂等创建/复用 request + attempt。

    调用方必须在同一事务内提交；失败则整个受理回滚（不启动平台运行）。
    """
    if identity.tenant is None or identity.membership is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    if operation_kind not in OPERATION_KINDS:
        raise AdmissionRejected(f"未知操作类型: {operation_kind}", code="bad_operation_kind")
    tenant_id = identity.tenant.id
    user_id = identity.user.id
    membership_id = identity.membership.id
    grant = _business_grant(identity, graph_id)

    norm_sel = normalize_capability_selection(capability_selection)
    settings = get_settings()
    if (
        norm_sel["selection_source"] == "user_explicit"
        and not settings.agent_capability_selection_enabled
    ):
        raise CapabilityConflict("能力选择功能已停用", code="capability_selection_disabled")

    await _assert_thread_owned(session, thread_id=thread_id, tenant_id=tenant_id, user_id=user_id)
    # R05：删除中的线程**禁止新写**（新提问/新事件都不允许落在已删除会话上）
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=thread_id):
        raise AdmissionRejected("会话正在删除，无法提交新问题", code="thread_deleting")

    inherited_source_selection = None
    source_attempt = None
    if source_attempt_id is not None:
        source_attempt = (
            await session.execute(
                select(AgentRunAttempt).where(
                    AgentRunAttempt.id == source_attempt_id,
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
    elif operation_kind in ("retry", "regenerate", "resume"):
        source_attempt = (
            await session.execute(
                select(AgentRunAttempt)
                .join(
                    AgentRequest,
                    and_(
                        AgentRequest.id == AgentRunAttempt.request_id,
                        AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
                    ),
                )
                .where(
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.user_id == user_id,
                    AgentRequest.thread_id == thread_id,
                )
                .order_by(AgentRunAttempt.admitted_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    if source_attempt is not None:
        _assert_source_business(source_attempt, grant)

    if (
        (capability_selection is None or operation_kind in ("retry", "resume"))
        and operation_kind in ("resume", "retry", "regenerate")
        and source_attempt is not None
        and source_attempt.capability_selection_id
    ):
        inherited_source_selection = await session.get(
            AgentCapabilitySelection, source_attempt.capability_selection_id
        )
        if inherited_source_selection is not None:
            inherited_semantics = {
                "skill": {
                    "mode": inherited_source_selection.skill_mode,
                    "skill_id": inherited_source_selection.skill_id,
                },
                "mcp": {
                    "mode": inherited_source_selection.mcp_mode,
                    "tool_ids": sorted(inherited_source_selection.tool_ids or []),
                },
            }
            if capability_selection is not None and any(
                norm_sel[key] != inherited_semantics[key] for key in ("skill", "mcp")
            ):
                raise CapabilityConflict(
                    "重试或恢复不允许修改能力选择，请使用重新生成",
                    code=f"{operation_kind}_selection_immutable",
                )
            norm_sel["skill"]["mode"] = inherited_source_selection.skill_mode
            norm_sel["skill"]["skill_id"] = inherited_source_selection.skill_id
            norm_sel["mcp"]["mode"] = inherited_source_selection.mcp_mode
            norm_sel["mcp"]["tool_ids"] = list(inherited_source_selection.tool_ids or [])
            norm_sel["selection_source"] = inherited_source_selection.selection_source
            norm_sel["catalog_version"] = inherited_source_selection.catalog_version

    existing = (
        await session.execute(
            select(AgentSubmission).where(
                AgentSubmission.tenant_id == tenant_id,
                AgentSubmission.user_id == user_id,
                AgentSubmission.thread_id == thread_id,
                AgentSubmission.submission_key == submission_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        normalized_fallback = (
            existing.operation_kind == "initial"
            and operation_kind in ("retry", "regenerate")
            and source_attempt_id is None
        )
        effective_op = "initial" if normalized_fallback else operation_kind
        if existing.digest_version == 1:
            if norm_sel["selection_source"] == "user_explicit":
                raise SubmissionConflict("同一提交键已用于不同选择", code="submission_conflict")
            v1_digest = canonical_content_digest_v1(
                graph_id=graph_id, operation_kind=effective_op, content=content
            )
            v1_incoming_digest = canonical_content_digest_v1(
                graph_id=graph_id, operation_kind=operation_kind, content=content
            )
            if (existing.content_digest not in (v1_digest, v1_incoming_digest)) or (
                existing.operation_kind != operation_kind and not normalized_fallback
            ):
                raise SubmissionConflict(
                    "同一提交键已用于不同内容或操作类型", code="submission_conflict"
                )
            sel_out = {"selection_status": "not_collected"}
        else:
            existing_sel = None
            if existing.capability_selection_id:
                existing_sel = await session.get(
                    AgentCapabilitySelection, existing.capability_selection_id
                )
            if existing_sel is None:
                existing_sel_digest = canonical_selection_digest(
                    graph_id=graph_id,
                    skill_mode="auto",
                    skill_id=None,
                    mcp_mode="auto",
                    tool_ids=[],
                    material_version="auto",
                )
            else:
                existing_sel_digest = existing_sel.selection_digest

            req_mat = "auto"
            if norm_sel["skill"]["mode"] == "explicit":
                if (
                    inherited_source_selection is not None
                    and norm_sel["skill"]["skill_id"] == inherited_source_selection.skill_id
                ):
                    req_mat = inherited_source_selection.material_version
                else:
                    item = validate_skill_selection(
                        graph_id, norm_sel["skill"]["skill_id"], menu_name=menu_name
                    )
                    req_mat = item["material_version"]
            elif inherited_source_selection is not None:
                req_mat = inherited_source_selection.material_version

            req_sel_digest = canonical_selection_digest(
                graph_id=graph_id,
                skill_mode=norm_sel["skill"]["mode"],
                skill_id=norm_sel["skill"]["skill_id"],
                mcp_mode=norm_sel["mcp"]["mode"],
                tool_ids=norm_sel["mcp"]["tool_ids"],
                material_version=req_mat,
            )
            if req_sel_digest != existing_sel_digest:
                raise SubmissionConflict("同一提交键已用于不同选择", code="submission_conflict")

            v2_digest = canonical_content_digest_v2(
                graph_id=graph_id,
                operation_kind=effective_op,
                content=content,
                capability_selection_digest=existing_sel_digest,
            )
            v2_incoming_digest = canonical_content_digest_v2(
                graph_id=graph_id,
                operation_kind=operation_kind,
                content=content,
                capability_selection_digest=existing_sel_digest,
            )
            if (existing.content_digest not in (v2_digest, v2_incoming_digest)) or (
                existing.operation_kind != operation_kind and not normalized_fallback
            ):
                raise SubmissionConflict(
                    "同一提交键已用于不同内容或操作类型", code="submission_conflict"
                )
            if existing_sel:
                sel_out = {
                    "selection_id": str(existing_sel.id),
                    "selection_digest": existing_sel.selection_digest,
                    "digest_version": existing_sel.digest_version,
                    "selection_status": "collected",
                    "skill": {"mode": existing_sel.skill_mode, "skill_id": existing_sel.skill_id},
                    "mcp": {"mode": existing_sel.mcp_mode, "tool_ids": existing_sel.tool_ids},
                    "material_version": existing_sel.material_version,
                    "selection_source": existing_sel.selection_source,
                    "validation_time": existing_sel.validation_time.isoformat(),
                }
            else:
                sel_out = {"selection_status": "not_collected"}

        bound_attempt_id = existing.attempt_id
        if bound_attempt_id is None:
            rows = (
                (
                    await session.execute(
                        select(AgentRunAttempt.id).where(
                            AgentRunAttempt.tenant_id == tenant_id,
                            AgentRunAttempt.request_id == existing.request_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) != 1:
                raise LedgerError(
                    "该提交未绑定唯一尝试，无法安全重放", code="submission_attempt_ambiguous"
                )
            bound_attempt_id = rows[0]
            existing.attempt_id = bound_attempt_id
        attempt = await session.get(AgentRunAttempt, bound_attempt_id)
        if attempt is None or attempt.tenant_id != tenant_id:
            raise LedgerError("提交记录缺少关联尝试", code="inconsistent_submission")
        _assert_source_business(attempt, grant)
        if normalized_fallback:
            attempt.content_digest = (
                canonical_content_digest_v1(
                    graph_id=graph_id, operation_kind="initial", content=content
                )
                if existing.digest_version == 1
                else canonical_content_digest_v2(
                    graph_id=graph_id,
                    operation_kind="initial",
                    content=content,
                    capability_selection_digest=existing_sel_digest,
                )
            )
        return AdmissionResult(
            request_id=existing.request_id,
            attempt_id=attempt.id,
            submission_id=existing.id,
            admission_status=existing.admission_status,
            operation_kind=existing.operation_kind,
            reused=True,
            thread_id=thread_id,
            graph_id=graph_id,
            business_code=grant.business_code,
            business_policy_version=grant.policy_version,
            capability_selection=sel_out,
        )

    now = dt.datetime.now(dt.UTC)
    source_attempt: AgentRunAttempt | None = None
    if source_attempt_id is None and operation_kind in ("retry", "regenerate", "resume"):
        source_attempt = (
            await session.execute(
                select(AgentRunAttempt)
                .join(
                    AgentRequest,
                    and_(
                        AgentRequest.id == AgentRunAttempt.request_id,
                        AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
                    ),
                )
                .where(
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.user_id == user_id,
                    AgentRequest.thread_id == thread_id,
                )
                .order_by(AgentRunAttempt.admitted_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if source_attempt_id is not None:
        if operation_kind not in ("retry", "regenerate", "resume"):
            raise AdmissionRejected(
                "source_attempt_id 仅用于 retry/regenerate/resume",
                code="source_attempt_not_allowed",
            )
        source_attempt = (
            await session.execute(
                select(AgentRunAttempt).where(
                    AgentRunAttempt.id == source_attempt_id,
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if source_attempt is None:
            raise NotFound("原尝试不存在或不属于当前用户", code="source_attempt_not_found")

    if source_attempt is not None:
        _assert_source_business(source_attempt, grant)
        source_thread = await session.scalar(
            select(AgentRequest.thread_id).where(
                AgentRequest.id == source_attempt.request_id, AgentRequest.tenant_id == tenant_id
            )
        )
        if str(source_thread or "") != thread_id:
            raise AdmissionRejected("操作来源必须属于同一会话", code="source_thread_mismatch")
    if operation_kind == "resume" and source_attempt is not None:
        source_thread = await session.scalar(
            select(AgentRequest.thread_id).where(
                AgentRequest.id == source_attempt.request_id,
                AgentRequest.tenant_id == tenant_id,
            )
        )
        if str(source_thread or "") != thread_id:
            raise AdmissionRejected(
                "恢复来源必须属于同一会话", code="resume_source_thread_mismatch"
            )
        if str(source_attempt.state) not in RESUMABLE_ATTEMPT_STATES:
            raise AdmissionRejected(
                f"当前没有可恢复的等待状态（{source_attempt.state}）",
                code="resume_source_not_waiting",
            )

    if source_attempt is None and operation_kind == "resume":
        raise AdmissionRejected("当前没有可恢复的来源", code="resume_source_missing")
    if source_attempt is None and operation_kind in ("retry", "regenerate", "resume"):
        operation_kind = "initial"

    mat_version = "auto"
    if operation_kind == "resume" and source_attempt is not None:
        source_selection = None
        if source_attempt.capability_selection_id:
            source_selection = await session.get(
                AgentCapabilitySelection, source_attempt.capability_selection_id
            )
        if source_selection is not None:
            if source_selection.skill_mode == "explicit" and source_selection.skill_id:
                try:
                    curr_skill = validate_skill_selection(graph_id, source_selection.skill_id)
                    if curr_skill["material_version"] != source_selection.material_version:
                        raise CapabilityConflict(
                            "原技能规则材料已更新，无法恢复原运行",
                            code="material_version_changed",
                        )
                except Exception as exc:
                    if isinstance(exc, CapabilityConflict):
                        raise
                    raise CapabilityConflict(
                        "原技能不可用，无法恢复原运行", code="skill_unavailable"
                    ) from exc
            if source_selection.tool_ids:
                try:
                    _validate_mcp_tool_authorization(
                        business_code=grant.business_code,
                        tenant_id=tenant_id,
                        tool_ids=source_selection.tool_ids,
                    )
                except Exception as exc:
                    raise CapabilityConflict(
                        "原 MCP 服务授权已失效或撤销，无法恢复原运行",
                        code="mcp_connection_invalidated",
                    ) from exc
            norm_sel["skill"]["mode"] = source_selection.skill_mode
            norm_sel["skill"]["skill_id"] = source_selection.skill_id
            norm_sel["mcp"]["mode"] = source_selection.mcp_mode
            norm_sel["mcp"]["tool_ids"] = source_selection.tool_ids
            norm_sel["selection_source"] = source_selection.selection_source
            norm_sel["catalog_version"] = source_selection.catalog_version
            mat_version = source_selection.material_version

    elif operation_kind in ("retry", "regenerate") and source_attempt is not None:
        if capability_selection is None and source_attempt.capability_selection_id:
            source_selection = await session.get(
                AgentCapabilitySelection, source_attempt.capability_selection_id
            )
            if source_selection is not None:
                if source_selection.tool_ids and source_selection.mcp_mode == "allowlist":
                    try:
                        _validate_mcp_tool_authorization(
                            business_code=grant.business_code,
                            tenant_id=tenant_id,
                            tool_ids=source_selection.tool_ids,
                        )
                    except Exception as exc:
                        raise CapabilityConflict(
                            "原 MCP 服务授权已失效或撤销，无法重试",
                            code="mcp_connection_invalidated",
                        ) from exc
                norm_sel["skill"]["mode"] = source_selection.skill_mode
                norm_sel["skill"]["skill_id"] = source_selection.skill_id
                norm_sel["mcp"]["mode"] = source_selection.mcp_mode
                norm_sel["mcp"]["tool_ids"] = source_selection.tool_ids
                norm_sel["selection_source"] = source_selection.selection_source
                norm_sel["catalog_version"] = source_selection.catalog_version
                mat_version = source_selection.material_version

    if norm_sel["skill"]["mode"] == "explicit":
        try:
            skill_item = validate_skill_selection(
                graph_id, norm_sel["skill"]["skill_id"], menu_name=menu_name
            )
            mat_version = skill_item["material_version"]
        except Exception as exc:
            detail = getattr(exc, "detail", str(exc))
            raise CapabilityValidationError(detail, code="invalid_skill_selection") from exc

    # 目录版本也约束仅选工具和显式空集合，不能仅在显式 Skill 分支核验。
    if norm_sel["selection_source"] == "user_explicit":
        try:
            from common.capabilities import (
                compute_catalog_version,
                discover_authorized_mcp_catalog,
                list_graph_skills,
            )
            from common.identity.runtime import RuntimeIdentity

            runtime_ident = RuntimeIdentity(
                user_id=str(user_id),
                tenant_id=str(tenant_id),
                membership_id=str(membership_id),
                session_id=str(identity.session.id if identity.session else ""),
                session_ctx_version=identity.context_version,
                user_auth_revision=identity.user.auth_revision,
                tenant_auth_revision=identity.tenant.auth_revision,
                membership_revision=identity.membership.revision,
                verification_ref=getattr(identity, "remote_verification_ref", None),
                permissions=frozenset(identity.permissions),
                business_code=grant.business_code,
                business_policy_version=grant.policy_version,
                business_permission=grant.permission,
                business_graph=grant.graph_id,
            )
            auth_revs = {
                "tenant": int(getattr(identity.tenant, "auth_revision", 1) or 1),
                "user": int(getattr(identity.user, "auth_revision", 1) or 1),
                "membership": int(getattr(identity.membership, "revision", 1) or 1),
            }
            curr_skills = list_graph_skills(graph_id, menu_name=menu_name)
            curr_mcp_services, curr_mcp_tools = await discover_authorized_mcp_catalog(
                runtime_ident, business_code=grant.business_code
            )
            expected_cat_ver = compute_catalog_version(
                graph_id=graph_id,
                identity=runtime_ident,
                auth_revisions=auth_revs,
                skills=curr_skills,
                mcp_services=curr_mcp_services,
                tools=curr_mcp_tools,
            )
            cat_ver = norm_sel.get("catalog_version")
            if cat_ver != expected_cat_ver:
                raise CapabilityConflict(
                    "能力目录版本已过期，请刷新后重试", code="catalog_version_expired"
                )
        except (CapabilityConflict, CapabilityValidationError):
            raise
        except Exception as exc:
            raise CapabilityValidationError(
                f"能力目录核验失败: {type(exc).__name__}",
                code="catalog_verification_failed",
            ) from exc

    if (
        norm_sel["skill"]["mode"] == "explicit"
        and skill_item.get("required_tool_ids")
        and norm_sel["mcp"]["mode"] == "allowlist"
    ):
        chosen = set(norm_sel["mcp"]["tool_ids"])
        for req in skill_item["required_tool_ids"]:
            if req not in chosen:
                msg = (
                    f"所选技能 {norm_sel['skill']['skill_id']} 依赖工具 {req}，但未在已选工具列表中"
                )
                raise CapabilityValidationError(msg, code="required_tool_missing")

    if norm_sel["mcp"]["mode"] == "allowlist" and norm_sel["mcp"]["tool_ids"]:
        _validate_mcp_tool_authorization(
            business_code=grant.business_code,
            tenant_id=tenant_id,
            tool_ids=norm_sel["mcp"]["tool_ids"],
        )

    if norm_sel["selection_source"] == "user_explicit":
        cat_ver = norm_sel.get("catalog_version")
        if not cat_ver:
            raise CapabilityValidationError(
                "显式选择必须提供 catalog_version", code="missing_catalog_version"
            )
        if cat_ver == "expired":
            raise CapabilityConflict(
                "能力目录版本已过期，请刷新后重试", code="catalog_version_expired"
            )
        if not re.match(r"^(cat_[0-9a-f]{16,64}|opaque-[a-zA-Z0-9_-]+|v\d+)$", str(cat_ver)):
            raise CapabilityValidationError(
                f"非法或伪造的 catalog_version: {cat_ver}", code="invalid_catalog_version"
            )

    selection_digest = canonical_selection_digest(
        graph_id=graph_id,
        skill_mode=norm_sel["skill"]["mode"],
        skill_id=norm_sel["skill"]["skill_id"],
        mcp_mode=norm_sel["mcp"]["mode"],
        tool_ids=norm_sel["mcp"]["tool_ids"],
        material_version=mat_version,
    )
    v2_digest = canonical_content_digest_v2(
        graph_id=graph_id,
        operation_kind=operation_kind,
        content=content,
        capability_selection_digest=selection_digest,
    )

    if source_attempt is not None:
        request_id = source_attempt.request_id
        request = (
            await session.execute(
                select(AgentRequest)
                .where(AgentRequest.id == request_id, AgentRequest.tenant_id == tenant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if request is None:
            raise LedgerError("原提问不存在", code="source_request_not_found")
        max_index = (
            await session.execute(
                select(func.max(AgentRunAttempt.attempt_index)).where(
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.request_id == request_id,
                )
            )
        ).scalar() or 0
        attempt_index = int(max_index) + 1
    else:
        request_id = uuid.uuid4()
        attempt_index = 1
        request = AgentRequest(
            id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            thread_id=thread_id,
            user_message_id=user_message_id,
            graph_id=graph_id,
            business_code=grant.business_code,
            business_policy_version=grant.policy_version,
            operation_kind=operation_kind,
            department_id=identity.membership.department_id,
            role_snapshot=list(identity.membership_role_codes),
            identity_revision={
                "session_ctx_version": identity.session.ctx_version,
                "membership_revision": identity.membership.revision,
                "user_auth_revision": identity.user.auth_revision,
                "tenant_auth_revision": identity.tenant.auth_revision,
            },
            admitted_at=now,
            created_at=now,
        )
        session.add(request)
        await session.flush()

    content_ref_id: uuid.UUID | None = None
    if content_snapshot:
        existing_ref = await session.scalar(
            select(AgentContentRef.id)
            .where(AgentContentRef.tenant_id == tenant_id, AgentContentRef.request_id == request_id)
            .limit(1)
        )
        if existing_ref is not None:
            content_ref_id = existing_ref
        else:
            settings = get_settings()
            policy_days = settings.ledger_content_retention_days_by_tenant.get(
                str(tenant_id), settings.ledger_content_retention_days
            )
            effective_days = min(retention_days or policy_days, policy_days)
            content_ref_id = uuid.uuid4()
            session.add(
                AgentContentRef(
                    id=content_ref_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    membership_id=membership_id,
                    thread_id=thread_id,
                    request_id=request_id,
                    message_id=user_message_id,
                    content_version=1,
                    content_snapshot=content_snapshot,
                    retention_until=now + dt.timedelta(days=effective_days),
                    created_at=now,
                )
            )

    selection_id = uuid.uuid4()
    if operation_kind == "resume" and source_attempt is not None:
        attempt_id = source_attempt.id
    else:
        attempt_id = uuid.uuid4()
        session.add(
            AgentRunAttempt(
                id=attempt_id,
                tenant_id=tenant_id,
                user_id=user_id,
                membership_id=membership_id,
                request_id=request_id,
                attempt_index=attempt_index,
                source_attempt_id=source_attempt.id if source_attempt else source_attempt_id,
                graph_id=graph_id,
                business_code=grant.business_code,
                business_policy_version=grant.policy_version,
                state="admitting",
                state_revision=1,
                operation_kind=operation_kind,
                content_digest=v2_digest,
                capability_selection_id=selection_id,
                admitted_at=now,
                last_progress_at=now,
                updated_at=now,
            )
        )
    await session.flush()

    submission_id = uuid.uuid4()
    session.add(
        AgentSubmission(
            id=submission_id,
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            thread_id=thread_id,
            submission_key=submission_key,
            operation_kind=operation_kind,
            content_digest=v2_digest,
            digest_version=2,
            capability_selection_id=selection_id,
            admission_status="admitting",
            request_id=request_id,
            attempt_id=attempt_id,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()

    session.add(
        AgentCapabilitySelection(
            id=selection_id,
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            request_id=request_id,
            attempt_id=attempt_id,
            submission_id=submission_id,
            graph_id=graph_id,
            skill_mode=norm_sel["skill"]["mode"],
            skill_id=norm_sel["skill"]["skill_id"],
            mcp_mode=norm_sel["mcp"]["mode"],
            tool_ids=norm_sel["mcp"]["tool_ids"],
            catalog_version=norm_sel.get("catalog_version"),
            material_version=mat_version,
            selection_digest=selection_digest,
            digest_version=2,
            selection_source=norm_sel["selection_source"],
            validation_time=now,
            created_at=now,
        )
    )
    await session.flush()

    try:
        await emit_fact(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            event_type="request.accepted",
            source="server",
            payload={
                "graph_id": graph_id,
                "business_code": grant.business_code,
                "business_policy_version": grant.policy_version,
                "operation_kind": operation_kind,
                "submission_digest_prefix": v2_digest[:16],
                "user_message_id": user_message_id,
                "admitted_at": now.isoformat(),
            },
            thread_id=thread_id,
            request_id=request_id,
            attempt_id=attempt_id,
            occurred_at=now,
        )
        sel_payload = {
            "selection_id": str(selection_id),
            "selection_digest": selection_digest,
            "graph_id": graph_id,
            "submission_id": str(submission_id),
            "skill_mode": norm_sel["skill"]["mode"],
            "mcp_mode": norm_sel["mcp"]["mode"],
            "selection_source": norm_sel["selection_source"],
        }
        if norm_sel["skill"]["skill_id"]:
            sel_payload["skill_id"] = norm_sel["skill"]["skill_id"]
        if norm_sel["mcp"]["tool_ids"]:
            sel_payload["tool_ids"] = norm_sel["mcp"]["tool_ids"]
        if mat_version:
            sel_payload["material_version"] = mat_version

        await emit_fact(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            event_type="capability.selection_accepted",
            source="server",
            payload=sel_payload,
            thread_id=thread_id,
            request_id=request_id,
            attempt_id=attempt_id,
            occurred_at=now,
        )
        await session.flush()
    except IntegrityError as exc:
        raise SubmissionConflict("并发提交冲突，请重试", code="submission_conflict") from exc

    sel_dict = {
        "id": str(selection_id),
        "selection_id": str(selection_id),
        "selection_digest": selection_digest,
        "digest_version": 2,
        "selection_status": "collected",
        "skill": {"mode": norm_sel["skill"]["mode"], "skill_id": norm_sel["skill"]["skill_id"]},
        "mcp": {"mode": norm_sel["mcp"]["mode"], "tool_ids": norm_sel["mcp"]["tool_ids"]},
        "skill_mode": norm_sel["skill"]["mode"],
        "skill_id": norm_sel["skill"]["skill_id"],
        "mcp_mode": norm_sel["mcp"]["mode"],
        "tool_ids": norm_sel["mcp"]["tool_ids"],
        "catalog_version": norm_sel.get("catalog_version") or "cat_000000000000000000000000",
        "material_version": mat_version,
        "selection_source": norm_sel["selection_source"],
        "validation_time": now.isoformat(),
    }
    return AdmissionResult(
        request_id=request_id,
        attempt_id=attempt_id,
        submission_id=submission_id,
        admission_status="admitting",
        operation_kind=operation_kind,
        reused=False,
        thread_id=thread_id,
        graph_id=graph_id,
        business_code=grant.business_code,
        business_policy_version=grant.policy_version,
        capability_selection=sel_dict,
    )


async def confirm_admission(
    session: AsyncSession,
    identity: Identity,
    *,
    request_id: uuid.UUID,
    attempt_id: uuid.UUID,
    submission_id: uuid.UUID | None = None,
    platform_run_id: str | None,
    outcome: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """回填平台受理结果：confirmed（有 run_id）或 admission_unknown（不确定）.

    F02：确认**只作用于本次提交**。恢复与初次提交共享同一 attempt，若按 attempt
    批量改写，会把其它操作（retry/regenerate/初次）的受理状态一起改掉；因此优先按
    `submission_id` 定位，缺省时按 attempt 下"未被确认的最新一次启动"回退（兼容旧调用）。
    恢复确认同时落**恢复映射**（原 run 关系 + 新 run），不新建提问/尝试。
    """
    if outcome not in ("confirmed", "admission_unknown"):
        raise LedgerError("outcome 必须是 confirmed 或 admission_unknown", code="bad_outcome")
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后到达的"受理确认"属晚到回调 → 拒绝写入
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    submission = None
    if submission_id is not None:
        submission = (
            await session.execute(
                select(AgentSubmission).where(
                    AgentSubmission.id == submission_id,
                    AgentSubmission.tenant_id == tenant_id,
                    AgentSubmission.user_id == identity.user.id,
                )
            )
        ).scalar_one_or_none()
        if submission is None:
            raise NotFound("提交不存在或不属于当前用户", code="submission_not_found")
        if str(submission.attempt_id) != str(attempt_id) or str(submission.request_id) != str(
            request_id
        ):
            raise AdmissionRejected("提交与目标尝试/请求不一致", code="submission_mismatch")
    if str(attempt.request_id) != str(request_id):
        raise AdmissionRejected("尝试不属于该请求", code="attempt_request_mismatch")
    if (
        outcome == "admission_unknown"
        and submission is not None
        and submission.admission_status == "confirmed"
        and submission.platform_run_id
    ):
        # wait 不带 run_id 并不推翻原生入口已确认的本次提交。
        return {
            "outcome": "confirmed",
            "platform_run_id": submission.platform_run_id,
            "reused": True,
        }
    now = dt.datetime.now(dt.UTC)
    prior_run_id = attempt.platform_run_id
    resume_index: int | None = None
    if outcome == "confirmed":
        if not platform_run_id:
            raise LedgerError("confirmed 必须提供 platform_run_id", code="missing_run_id")
        if submission is not None:
            if submission.platform_run_id and submission.platform_run_id != platform_run_id:
                raise AdmissionRejected("提交运行映射冲突", code="submission_run_conflict")
            if (
                submission.platform_run_id == platform_run_id
                and submission.admission_status == "confirmed"
            ):
                return {"outcome": "confirmed", "platform_run_id": platform_run_id, "reused": True}
            submission.platform_run_id = platform_run_id
        is_resume = str(submission.operation_kind) == "resume" if submission is not None else False
        if prior_run_id is not None and str(prior_run_id) != platform_run_id and not is_resume:
            raise AdmissionRejected("旧操作不能覆盖当前运行", code="stale_submission")
        attempt.platform_run_id = platform_run_id
        if attempt.state in ("admitting", "admission_unknown", "waiting_input"):
            attempt.state = "running"
        attempt.evidence_source = "platform_read"
        attempt.state_revision += 1
        attempt.started_at = attempt.started_at or now
        if is_resume:
            # 恢复映射：保留原 run 关系，追加本次 resume 的 run（幂等，不新建尝试）
            prior = await session.scalar(
                text(
                    "SELECT resume_index FROM agent_run_resumes"
                    " WHERE tenant_id = :t AND attempt_id = :a AND platform_run_id = :r"
                ),
                {
                    "t": str(tenant_id),
                    "a": str(attempt_id),
                    "r": str(platform_run_id),
                },
            )
            if prior is not None:
                resume_index = int(prior)
            else:
                max_index = await session.scalar(
                    text(
                        "SELECT COALESCE(max(resume_index), 0) FROM agent_run_resumes"
                        " WHERE tenant_id = :t AND attempt_id = :a"
                    ),
                    {"t": str(tenant_id), "a": str(attempt_id)},
                )
                resume_index = int(max_index or 0) + 1
                await session.execute(
                    text(
                        "INSERT INTO agent_run_resumes (id, tenant_id, user_id, attempt_id,"
                        " resume_index, original_run_id, platform_run_id, created_at)"
                        " VALUES (gen_random_uuid(), CAST(:t AS uuid), CAST(:u AS uuid),"
                        " CAST(:a AS uuid), :idx, :orig, :run, now())"
                    ),
                    {
                        "t": str(tenant_id),
                        "u": str(identity.user.id),
                        "a": str(attempt_id),
                        "idx": resume_index,
                        "orig": prior_run_id,
                        "run": str(platform_run_id),
                    },
                )
    else:
        if attempt.platform_run_id is None:
            attempt.state = "admission_unknown"
            attempt.state_revision += 1
    attempt.last_progress_at = now
    attempt.updated_at = now
    # 只改写目标提交：显式指定 submission_id 时按提交；否则取该尝试下**最近一次已取
    # 启动权且尚未终态判定**的提交（兼容旧调用），绝不再批量改写全部提交。
    target = (
        select(AgentSubmission.id)
        .where(
            AgentSubmission.tenant_id == tenant_id,
            AgentSubmission.attempt_id == attempt_id,
            AgentSubmission.start_claimed_at.is_not(None),
        )
        .order_by(AgentSubmission.start_claimed_at.desc())
        .limit(1)
    )
    if submission is not None:
        await session.execute(
            update(AgentSubmission)
            .where(
                AgentSubmission.tenant_id == tenant_id,
                AgentSubmission.id == submission.id,
            )
            .values(
                admission_status=("confirmed" if outcome == "confirmed" else "admission_unknown"),
                **({"platform_run_id": platform_run_id} if outcome == "confirmed" else {}),
                updated_at=now,
            )
        )
    else:
        await session.execute(
            update(AgentSubmission)
            .where(AgentSubmission.id.in_(target.scalar_subquery()))
            .values(
                admission_status=("confirmed" if outcome == "confirmed" else "admission_unknown"),
                **({"platform_run_id": platform_run_id} if outcome == "confirmed" else {}),
                updated_at=now,
            )
        )
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="run.admission_confirmed",
        source="runtime",
        payload={
            "platform_run_id": platform_run_id or "",
            "admission_outcome": outcome,
            "submission_id": str(submission.id) if submission is not None else "",
            **({"resume_index": resume_index} if resume_index else {}),
            **({"original_platform_run_id": prior_run_id} if resume_index else {}),
            **({"admission_unknown_reason": reason} if reason else {}),
            "admitted_at": now.isoformat(),
        },
        thread_id=None,
        request_id=request_id,
        attempt_id=attempt_id,
        run_id=platform_run_id,
        state_revision=attempt.state_revision,
        occurred_at=now,
    )
    await session.flush()
    return {
        "attempt_id": str(attempt_id),
        "state": attempt.state,
        "outcome": outcome,
        **({"submission_id": str(submission.id)} if submission is not None else {}),
        **({"resume_index": resume_index} if resume_index else {}),
    }


# ------------------------------------------------------------------- 停止意图


async def claim_start(
    session: AsyncSession,
    identity: Identity,
    *,
    submission_id: uuid.UUID,
    attempt_id: uuid.UUID,
    thread_id: str,
    graph_id: str,
    operation_kind: str,
    content: str,
    source: str,
) -> dict[str, Any]:
    """受控入口的启动权原子裁决（R01）：一次业务提交只能启动一次平台运行.

    与 Agent 侧 `store.claim_start` 语义一致（原生与 BFF 共用同一裁决规则）：
    并发只有一个胜出；已有平台映射直接复用；换图/换操作/换正文拒绝。
    """
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    await _load_attempt(session, attempt_id, tenant_id)
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT s.id, s.attempt_id, s.operation_kind AS submission_kind,
                       s.start_claimed_at, s.digest_version, cs.selection_digest,
                       COALESCE(s.platform_run_id,
                       CASE WHEN s.operation_kind <> 'resume' THEN
                         CASE WHEN EXISTS (SELECT 1 FROM agent_run_resumes x
                              WHERE x.attempt_id=a.id AND x.tenant_id=a.tenant_id)
                           THEN (SELECT x.original_run_id FROM agent_run_resumes x
                                 WHERE x.attempt_id=a.id AND x.tenant_id=a.tenant_id
                                 ORDER BY x.resume_index LIMIT 1)
                           ELSE a.platform_run_id END END) AS submission_run_id,
                       a.graph_id, a.state,
                       CASE WHEN s.operation_kind = 'initial' THEN a.content_digest
                            ELSE s.content_digest END AS content_digest,
                       a.platform_run_id, r.thread_id
                FROM agent_submissions s
                JOIN agent_run_attempts a
                  ON a.id = s.attempt_id AND a.tenant_id = s.tenant_id
                JOIN agent_requests r
                  ON r.id = s.request_id AND r.tenant_id = s.tenant_id
                LEFT JOIN agent_capability_selections cs
                  ON cs.id = COALESCE(s.capability_selection_id, a.capability_selection_id)
                  AND cs.tenant_id = s.tenant_id
                WHERE s.id = CAST(:sid AS uuid) AND s.tenant_id = CAST(:t AS uuid)
                  AND s.user_id = CAST(:u AS uuid) FOR UPDATE OF a, s
                """
                ),
                {"sid": str(submission_id), "t": str(tenant_id), "u": str(identity.user.id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("提交不存在或不属于当前用户", code="submission_not_found")
    if str(row["thread_id"]) != thread_id or str(row["attempt_id"]) != str(attempt_id):
        raise NotFound("提交与目标尝试/会话不一致", code="submission_mismatch")
    if str(row["graph_id"]) != graph_id:
        return {"status": "mismatch", "detail": "graph", "reserved_graph_id": str(row["graph_id"])}
    if str(row["submission_kind"]) != operation_kind:
        return {
            "status": "mismatch",
            "detail": "operation_kind",
            "reserved_operation_kind": str(row["submission_kind"]),
        }
    if row["content_digest"]:
        dv = row.get("digest_version")
        sel_digest = row.get("selection_digest")
        if dv == 2 and sel_digest:
            v2_digest = canonical_content_digest_v2(
                graph_id=graph_id,
                operation_kind=operation_kind,
                content=content,
                capability_selection_digest=str(sel_digest),
            )
            if str(row["content_digest"]) != v2_digest:
                v1_digest = canonical_content_digest_v1(
                    graph_id=graph_id, operation_kind=operation_kind, content=content
                )
                if str(row["content_digest"]) != v1_digest:
                    return {"status": "mismatch", "detail": "content"}
        else:
            v1_digest = canonical_content_digest_v1(
                graph_id=graph_id, operation_kind=operation_kind, content=content
            )
            if str(row["content_digest"]) != v1_digest:
                return {"status": "mismatch", "detail": "content"}
    if row["submission_run_id"]:
        return {
            "status": "already_confirmed",
            "platform_run_id": str(row["submission_run_id"]),
            "state": str(row["state"]),
        }
    if str(row["state"]) in TERMINAL_STATES:
        return {"status": "terminal", "state": str(row["state"])}
    if str(row["submission_kind"]) == "resume" and row["start_claimed_at"] is None:
        if str(row["state"]) != "waiting_input":
            return {"status": "mismatch", "detail": "resume_not_waiting"}
    token = uuid.uuid4()
    claimed = (
        await session.execute(
            text(
                "UPDATE agent_submissions SET start_claimed_at = now(),"
                " start_claim_token = CAST(:token AS uuid), start_claim_source = :src,"
                " updated_at = now()"
                " WHERE id = CAST(:sid AS uuid) AND tenant_id = CAST(:t AS uuid)"
                "   AND start_claimed_at IS NULL"
                " RETURNING id"
            ),
            {"token": str(token), "src": source, "sid": str(submission_id), "t": str(tenant_id)},
        )
    ).first()
    if claimed is None:
        return {"status": "already_started"}
    if operation_kind == "resume":
        await session.execute(
            text(
                "UPDATE agent_run_attempts SET state='queued',"
                " state_revision=state_revision+1, last_progress_at=now(), updated_at=now()"
                " WHERE id=CAST(:a AS uuid) AND tenant_id=CAST(:t AS uuid)"
            ),
            {"a": str(attempt_id), "t": str(tenant_id)},
        )
    return {"status": "claimed", "start_claim_token": str(token)}


async def record_stop_intent(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    thread_id: str | None = None,
    origin: str,
    phase: str,
    platform_run_id: str | None = None,
) -> dict[str, Any]:
    """停止意图独立保存：origin/phase 必须显式；未知不得猜测为 user_stop。"""
    if origin not in (
        "user_stop",
        "navigation",
        "tenant_switch",
        "transport_disconnect",
        "permission_revocation",
        "unknown",
    ):
        raise LedgerError(f"未知停止来源: {origin}", code="bad_cancel_origin")
    if phase not in ("queued", "processing", "output", "unknown"):
        raise LedgerError(f"未知停止阶段: {phase}", code="bad_cancel_phase")
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    query = (
        select(AgentRunAttempt)
        .join(
            AgentRequest,
            and_(
                AgentRequest.id == AgentRunAttempt.request_id,
                AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
            ),
        )
        .where(AgentRunAttempt.tenant_id == tenant_id, AgentRunAttempt.user_id == identity.user.id)
    )
    if attempt_id:
        query = query.where(AgentRunAttempt.id == attempt_id)
    if thread_id:
        query = query.where(AgentRequest.thread_id == thread_id)
    if request_id:
        query = query.where(AgentRequest.id == request_id)
    if platform_run_id:
        mapped = select(AgentSubmission.attempt_id).where(
            AgentSubmission.tenant_id == tenant_id,
            AgentSubmission.user_id == identity.user.id,
            AgentSubmission.platform_run_id == platform_run_id,
        )
        query = query.where(
            or_(AgentRunAttempt.platform_run_id == platform_run_id, AgentRunAttempt.id.in_(mapped))
        )
    elif not attempt_id:
        if not thread_id:
            raise NotFound("找不到可关联的运行尝试", code="attempt_not_found")
        query = query.where(AgentRunAttempt.state.in_(ACTIVE_ATTEMPT_STATES))
    # 有run标识时只能指向已有可信映射；迟到停止不能污染同线程最新attempt。
    attempts = list(
        (
            await session.execute(
                query.order_by(AgentRunAttempt.admitted_at.desc())
                .limit(2)
                .with_for_update(of=AgentRunAttempt)
            )
        ).scalars()
    )
    if not attempts or ((attempt_id or platform_run_id) and len(attempts) != 1):
        raise NotFound("找不到可关联的运行尝试", code="attempt_not_found")
    attempt = attempts[0]
    request = await session.get(AgentRequest, attempt.request_id)
    if request is None or await is_thread_deletion_pending(
        session, tenant_id=tenant_id, thread_id=request.thread_id
    ):
        raise NotFound("找不到可关联的运行尝试", code="attempt_not_found")

    now = dt.datetime.now(dt.UTC)
    attempt.cancel_requested = True
    attempt.cancel_origin = origin
    attempt.cancel_phase = phase
    attempt.state_revision += 1
    attempt.last_progress_at = now
    attempt.updated_at = now
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="run.cancel_requested",
        source="runtime",
        payload={
            "origin": origin,
            "phase": phase,
            **({"platform_run_id": platform_run_id} if platform_run_id else {}),
            "requested_at": now.isoformat(),
        },
        thread_id=request.thread_id,
        request_id=attempt.request_id,
        attempt_id=attempt.id,
        run_id=attempt.platform_run_id,
        state_revision=attempt.state_revision,
        occurred_at=now,
    )
    await session.flush()
    return {
        "attempt_id": str(attempt.id),
        "cancel_requested": True,
        "origin": origin,
        "phase": phase,
        "state": attempt.state,
    }


async def observe_state(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID,
    state: str,
    evidence_source: str,
    platform_status: str | None = None,
    state_revision: int | None = None,
) -> dict[str, Any]:
    """带证据/修订的状态转换：低秩证据不得覆盖高秩终态；乱序不回退。"""
    from app.models_ledger import ATTEMPT_STATES  # noqa: PLC0415

    if state not in ATTEMPT_STATES:
        raise LedgerError(f"未知状态: {state}", code="bad_state")
    if evidence_source not in EVIDENCE_RANK:
        raise LedgerError(f"未知证据来源: {evidence_source}", code="bad_evidence_source")
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后的状态回调（含补偿校准）不得写入新事件
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == attempt.request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    now = dt.datetime.now(dt.UTC)
    current_rank = EVIDENCE_RANK.get(attempt.evidence_source or "", 0)
    incoming_rank = EVIDENCE_RANK[evidence_source]
    applied = True
    rejection: str | None = None
    if attempt.state in TERMINAL_STATES:
        if incoming_rank < current_rank:
            applied = False
            rejection = "lower_rank_after_terminal"
        elif state != attempt.state:
            # 同秩冲突：保留冲突，用更强证据修订但不删除原事实
            rejection = (
                "terminal_conflict_repaired"
                if incoming_rank > current_rank
                else ("terminal_conflict_kept")
            )
    if applied and rejection != "terminal_conflict_kept":
        attempt.state = state
        attempt.evidence_source = evidence_source
        attempt.platform_status = platform_status or attempt.platform_status
        attempt.state_revision = max(attempt.state_revision + 1, state_revision or 0)
        if state == "running" and attempt.started_at is None:
            attempt.started_at = now
        if state in TERMINAL_STATES:
            attempt.ended_at = attempt.ended_at or now
    attempt.last_progress_at = now
    attempt.updated_at = now
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="run.state_observed",
        source="runtime",
        payload={
            "state": state,
            "evidence_source": evidence_source,
            "platform_status": platform_status or "",
            "state_revision": attempt.state_revision,
            "cancel_requested": attempt.cancel_requested,
            **({"waiting_kind": platform_status} if state == "waiting_input" else {}),
        },
        request_id=attempt.request_id,
        attempt_id=attempt.id,
        run_id=attempt.platform_run_id,
        state_revision=attempt.state_revision,
        occurred_at=now,
    )
    await session.flush()
    return {
        "attempt_id": str(attempt.id),
        "state": attempt.state,
        "state_revision": attempt.state_revision,
        "applied": applied,
        "note": rejection,
    }


async def record_resume(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID,
    platform_run_id: str,
) -> dict[str, Any]:
    """人工恢复：不新建提问/尝试，追加平台运行映射与恢复记录。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后到达的回答/恢复/关联回调不得复活已删会话的事实
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == attempt.request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    now = dt.datetime.now(dt.UTC)
    last_index = (
        await session.execute(
            select(func.max(AgentRunResume.resume_index)).where(
                AgentRunResume.tenant_id == tenant_id,
                AgentRunResume.attempt_id == attempt_id,
            )
        )
    ).scalar() or 0
    resume_index = int(last_index) + 1
    session.add(
        AgentRunResume(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=attempt.user_id,
            attempt_id=attempt_id,
            resume_index=resume_index,
            original_run_id=attempt.platform_run_id,
            platform_run_id=platform_run_id,
            created_at=now,
        )
    )
    attempt.platform_run_id = platform_run_id
    attempt.state = "running"
    attempt.evidence_source = "platform_read"
    attempt.state_revision += 1
    attempt.last_progress_at = now
    attempt.updated_at = now
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="run.resumed",
        source="runtime",
        payload={
            "resume_index": resume_index,
            "platform_run_id": platform_run_id,
            "resumed_at": now.isoformat(),
        },
        request_id=attempt.request_id,
        attempt_id=attempt_id,
        run_id=platform_run_id,
        state_revision=attempt.state_revision,
        occurred_at=now,
    )
    await session.flush()
    return {"attempt_id": str(attempt_id), "resume_index": resume_index, "state": attempt.state}


async def record_answer(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID,
    message_id: str | None,
    content_version: int = 1,
    confirmed: bool,
) -> dict[str, Any]:
    """可见回答版本：同尝试同消息同版本幂等；草稿/推理不入表（由调用方保证）。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后到达的回答/恢复/关联回调不得复活已删会话的事实
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == attempt.request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    now = dt.datetime.now(dt.UTC)
    existing = None
    if message_id:
        existing = (
            await session.execute(
                select(AgentAnswer).where(
                    AgentAnswer.tenant_id == tenant_id,
                    AgentAnswer.attempt_id == attempt_id,
                    AgentAnswer.message_id == message_id,
                    AgentAnswer.content_version == content_version,
                )
            )
        ).scalar_one_or_none()
    if existing is None:
        existing = AgentAnswer(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=attempt.user_id,
            attempt_id=attempt_id,
            message_id=message_id,
            content_version=content_version,
            partial=not confirmed,
            confirmed=confirmed,
            confirmed_at=now if confirmed else None,
            created_at=now,
        )
        session.add(existing)
        await session.flush()
    else:
        existing.confirmed = existing.confirmed or confirmed
        existing.partial = existing.partial and not confirmed
        if confirmed and existing.confirmed_at is None:
            existing.confirmed_at = now
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="answer.confirmed" if confirmed else "answer.partial",
        source="runtime",
        payload={
            "answer_id": str(existing.id),
            "content_version": content_version,
            **({"message_id": message_id} if message_id else {}),
            **(
                {"confirmed_at": now.isoformat()} if confirmed else {"observed_at": now.isoformat()}
            ),
        },
        request_id=attempt.request_id,
        attempt_id=attempt_id,
        run_id=attempt.platform_run_id,
        answer_id=existing.id,
        message_id=message_id,
        occurred_at=now,
    )
    await session.flush()
    return {
        "answer_id": str(existing.id),
        "confirmed": existing.confirmed,
        "partial": existing.partial,
        "content_version": existing.content_version,
    }


async def link_trace(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID,
    provider: str,
    trace_id: str,
    project_scope: str = "default",
    primary: bool = False,
    link_source: str = "runtime_callback",
) -> dict[str, Any]:
    """Trace 多重映射：幂等补充；已有主 Trace 不被无规则替换。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后到达的回答/恢复/关联回调不得复活已删会话的事实
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == attempt.request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    now = dt.datetime.now(dt.UTC)
    existing = (
        await session.execute(
            select(AgentTraceLink).where(
                AgentTraceLink.tenant_id == tenant_id,
                AgentTraceLink.provider == provider,
                AgentTraceLink.project_scope == project_scope,
                AgentTraceLink.trace_id == trace_id,
                AgentTraceLink.attempt_id == attempt_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {
            "trace_link_id": str(existing.id),
            "created": False,
            "is_primary": existing.is_primary,
        }
    has_primary = (
        await session.execute(
            select(func.count())
            .select_from(AgentTraceLink)
            .where(
                AgentTraceLink.tenant_id == tenant_id,
                AgentTraceLink.attempt_id == attempt_id,
                AgentTraceLink.is_primary.is_(True),
            )
        )
    ).scalar() or 0
    is_primary = primary and not has_primary
    row = AgentTraceLink(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        attempt_id=attempt_id,
        provider=provider,
        project_scope=project_scope,
        trace_id=trace_id,
        is_primary=is_primary,
        link_source=link_source,
        created_at=now,
    )
    session.add(row)
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="trace.linked",
        source="runtime",
        payload={
            "provider": provider,
            "project_scope": project_scope,
            "trace_id": trace_id,
            "primary": is_primary,
            "link_source": link_source,
        },
        request_id=attempt.request_id,
        attempt_id=attempt_id,
        run_id=attempt.platform_run_id,
        occurred_at=now,
    )
    await session.flush()
    return {"trace_link_id": str(row.id), "created": True, "is_primary": is_primary}


async def _load_attempt(
    session: AsyncSession, attempt_id: uuid.UUID, tenant_id: uuid.UUID
) -> AgentRunAttempt:
    attempt = (
        await session.execute(
            select(AgentRunAttempt)
            .where(AgentRunAttempt.id == attempt_id, AgentRunAttempt.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise NotFound("运行尝试不存在或不属于当前企业", code="attempt_not_found")
    return attempt


# --------------------------------------------------------------------- 删除标记


async def request_thread_deletion(
    session: AsyncSession,
    identity: Identity,
    *,
    thread_id: str,
) -> dict[str, Any]:
    """会话删除：立即禁读（标记 requested）+ 事件 + outbox；清理异步可重试。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    now = dt.datetime.now(dt.UTC)
    # R05：删除入口必须**校验线程归属**（本人本企业），或调用方持有独立的
    # 线程删除管理权限。缺失即拒绝，且不泄露其他企业/他人线程是否存在（统一 404）。
    owner = (
        (
            await session.execute(
                text(
                    "SELECT user_id::text AS user_id, source FROM thread_ownership"
                    " WHERE thread_id = :t AND tenant_id = :ten"
                ),
                {"t": thread_id, "ten": str(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    if owner is None or str(owner["source"]) not in ("native", "migrated"):
        raise NotFound("对象不存在", code="not_found")
    owns = str(owner["user_id"]) == str(identity.user.id)
    if not owns and "agent:thread:delete" not in identity.permissions:
        raise NotFound("对象不存在", code="not_found")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"thread:{tenant_id}:{thread_id}"},
    )
    existing = (
        await session.execute(
            select(AgentThreadDeletion).where(
                AgentThreadDeletion.tenant_id == tenant_id,
                AgentThreadDeletion.thread_id == thread_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = AgentThreadDeletion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            thread_id=thread_id,
            user_id=uuid.UUID(str(owner["user_id"])),
            requested_by=identity.user.id,
            state="requested",
            attempts=0,
            requested_at=now,
        )
        session.add(existing)
        await session.flush()
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=identity.user.id,
        membership_id=identity.membership.id if identity.membership else identity.user.id,
        event_type="resource.deleted",
        source="server",
        payload={
            "resource_type": "thread",
            "resource_id": thread_id,
            "deletion_state": existing.state,
            "deleted_at": now.isoformat(),
        },
        thread_id=thread_id,
        consumers=("deletion_cleanup",),
        occurred_at=now,
    )
    await session.flush()
    return {"thread_id": thread_id, "deletion_state": existing.state}


async def assert_thread_live(
    session: AsyncSession, *, tenant_id: uuid.UUID, thread_id: str
) -> None:
    """已删除会话的写入闸门：晚到回调/新事实一律拒绝（F04）."""
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=thread_id):
        raise ThreadDeletedError("该会话已删除，拒绝写入运行事实", code="thread_deleted")


async def is_thread_deletion_pending(
    session: AsyncSession, *, tenant_id: uuid.UUID, thread_id: str
) -> bool:
    """会话**已被删除**（含清理完成）→ 回读与新写一律拒绝.

    F04：删除是**永久**语义，不能因为清理把 state 置为 completed 就重新放行读取与新写
    ——那等于"删完又能读、又能继续写"，用户以为删掉的内容仍在被使用。只要存在删除记录，
    该会话即对外不可用（清理进度由 state/attempts 反映，仅用于运维可见性）。
    """
    row = (
        await session.execute(
            text("SELECT 1 FROM agent_thread_deletions WHERE tenant_id = :ten AND thread_id = :t"),
            {"ten": str(tenant_id), "t": thread_id},
        )
    ).first()
    return row is not None


# ----------------------------------------------------------------------- 查询


async def _enable_operations_read(session: AsyncSession, identity: Identity) -> None:
    """企业运营读：权限校验通过后显式开启 GUC（与数据库策略共同限定）。"""
    await apply_tenant_context(
        session,
        TenantContext(
            user_id=str(identity.user.id),
            tenant_id=str(identity.tenant.id) if identity.tenant else None,
            session_id=str(identity.session.id),
            platform=identity.platform_admin,
        ),
    )
    await session.execute(
        text("SELECT set_config('air_agent.agent_operations', :v, true)"),
        {"v": "on"},
    )


async def get_request_detail(
    session: AsyncSession, identity: Identity, request_id: uuid.UUID
) -> dict[str, Any]:
    """本人请求回读：含尝试、回答版本、Trace 映射；**不含正文**。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    request = (
        await session.execute(
            select(AgentRequest).where(
                AgentRequest.id == request_id,
                AgentRequest.tenant_id == tenant_id,
                AgentRequest.user_id == identity.user.id,
            )
        )
    ).scalar_one_or_none()
    if request is None:
        raise NotFound("对象不存在", code="not_found")
    # R05：删除中的线程立即禁读（本人回读同样受限）
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=request.thread_id):
        raise NotFound("对象不存在", code="not_found")
    attempts = (
        (
            await session.execute(
                select(AgentRunAttempt)
                .where(
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.request_id == request_id,
                )
                .order_by(AgentRunAttempt.attempt_index)
            )
        )
        .scalars()
        .all()
    )
    attempt_ids = [a.id for a in attempts]
    answers = (
        (
            await session.execute(
                select(AgentAnswer)
                .where(
                    AgentAnswer.tenant_id == tenant_id,
                    AgentAnswer.attempt_id.in_(attempt_ids or [uuid.uuid4()]),
                )
                .order_by(AgentAnswer.created_at)
            )
        )
        .scalars()
        .all()
    )
    traces = (
        (
            await session.execute(
                select(AgentTraceLink).where(
                    AgentTraceLink.tenant_id == tenant_id,
                    AgentTraceLink.attempt_id.in_(attempt_ids or [uuid.uuid4()]),
                )
            )
        )
        .scalars()
        .all()
    )
    selection_ids = [a.capability_selection_id for a in attempts if a.capability_selection_id]
    selections_map: dict[uuid.UUID, AgentCapabilitySelection] = {}
    if selection_ids:
        sel_rows = (
            (
                await session.execute(
                    select(AgentCapabilitySelection).where(
                        AgentCapabilitySelection.tenant_id == tenant_id,
                        AgentCapabilitySelection.id.in_(selection_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        selections_map = {s.id: s for s in sel_rows}

    formatted_attempts = []
    for a in attempts:
        att_dict = {
            "attempt_id": str(a.id),
            "attempt_index": a.attempt_index,
            "state": a.state,
            "state_revision": a.state_revision,
            "evidence_source": a.evidence_source,
            "platform_run_id": a.platform_run_id,
            "cancel_requested": a.cancel_requested,
            "cancel_origin": a.cancel_origin,
            "cancel_confirmed": a.cancel_confirmed,
            "admitted_at": a.admitted_at.isoformat(),
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "ended_at": a.ended_at.isoformat() if a.ended_at else None,
        }
        sel = selections_map.get(a.capability_selection_id) if a.capability_selection_id else None
        if sel:
            att_dict["capability_selection"] = {
                "selection_id": str(sel.id),
                "selection_digest": sel.selection_digest,
                "digest_version": sel.digest_version,
                "selection_status": "collected",
                "skill": {"mode": sel.skill_mode, "skill_id": sel.skill_id},
                "mcp": {"mode": sel.mcp_mode, "tool_ids": sel.tool_ids},
                "material_version": sel.material_version,
                "selection_source": sel.selection_source,
                "validation_time": sel.validation_time.isoformat(),
            }
        else:
            att_dict["capability_selection"] = {"selection_status": "not_collected"}
        formatted_attempts.append(att_dict)

    latest_sel = (
        formatted_attempts[-1]["capability_selection"]
        if formatted_attempts
        else {"selection_status": "not_collected"}
    )
    return {
        "request": {
            "request_id": str(request.id),
            "thread_id": request.thread_id,
            "graph_id": request.graph_id,
            "operation_kind": request.operation_kind,
            "admitted_at": request.admitted_at.isoformat(),
            "department_snapshot": request.department_snapshot,
            "role_snapshot": request.role_snapshot,
        },
        "capability_selection": latest_sel,
        "attempts": formatted_attempts,
        "answers": [
            {
                "answer_id": str(x.id),
                "attempt_id": str(x.attempt_id),
                "message_id": x.message_id,
                "content_version": x.content_version,
                "partial": x.partial,
                "confirmed": x.confirmed,
            }
            for x in answers
        ],
        "traces": [
            {
                "attempt_id": str(t.attempt_id),
                "provider": t.provider,
                "project_scope": t.project_scope,
                "trace_id": t.trace_id,
                "primary": t.is_primary,
                "link_source": t.link_source,
            }
            for t in traces
        ],
        "content_included": False,
    }


async def list_runs(
    session: AsyncSession,
    identity: Identity,
    *,
    state: str | None = None,
    graph_id: str | None = None,
    thread_id: str | None = None,
    started_after: dt.datetime | None = None,
    started_before: dt.datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
    own_only: bool = False,
) -> dict[str, Any]:
    """企业运行明细：运营权限下可跨成员；own_only 时仅本人。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    if not own_only:
        await _enable_operations_read(session, identity)
    limit = max(1, min(int(limit), 200))
    stmt = (
        select(AgentRunAttempt, AgentRequest.thread_id, AgentRequest.graph_id)
        .join(
            AgentRequest,
            and_(
                AgentRequest.id == AgentRunAttempt.request_id,
                AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
            ),
        )
        .where(AgentRunAttempt.tenant_id == tenant_id)
    )
    if own_only:
        stmt = stmt.where(AgentRunAttempt.user_id == identity.user.id)
    if state:
        stmt = stmt.where(AgentRunAttempt.state == state)
    if graph_id:
        stmt = stmt.where(AgentRunAttempt.graph_id == graph_id)
    if thread_id:
        stmt = stmt.where(AgentRequest.thread_id == thread_id)
    if started_after:
        stmt = stmt.where(AgentRunAttempt.admitted_at >= started_after)
    if started_before:
        stmt = stmt.where(AgentRunAttempt.admitted_at < started_before)
    if cursor:
        try:
            cursor_ts, cursor_id = cursor.split("|", 1)
            # 游标走查询串：ISO 时间里的 `+00:00` 会被解码成空格，必须先还原
            # （生成侧用 quote，兼容历史未编码形态）。
            cursor_dt = dt.datetime.fromisoformat(unquote(cursor_ts))
            stmt = stmt.where(
                (AgentRunAttempt.admitted_at, AgentRunAttempt.id)
                < (cursor_dt, uuid.UUID(cursor_id))
            )
        except (ValueError, TypeError) as exc:
            raise LedgerError("cursor 非法", code="bad_cursor") from exc
    stmt = stmt.order_by(AgentRunAttempt.admitted_at.desc(), AgentRunAttempt.id.desc()).limit(
        limit + 1
    )
    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "attempt_id": str(a.id),
            "request_id": str(a.request_id),
            "thread_id": thread_id_value,
            "graph_id": graph_id_value,
            "state": a.state,
            "state_revision": a.state_revision,
            "evidence_source": a.evidence_source,
            "platform_run_id": a.platform_run_id,
            "cancel_requested": a.cancel_requested,
            "admitted_at": a.admitted_at.isoformat(),
            "ended_at": a.ended_at.isoformat() if a.ended_at else None,
            "content_included": False,
        }
        for a, thread_id_value, graph_id_value in rows
    ]
    next_cursor = (
        f"{quote(rows[-1][0].admitted_at.isoformat())}|{rows[-1][0].id}"
        if has_more and rows
        else None
    )
    return {"items": items, "next_cursor": next_cursor, "limit": limit, "content_included": False}


async def summary(
    session: AsyncSession,
    identity: Identity,
    *,
    start: dt.datetime,
    end: dt.datetime,
) -> dict[str, Any]:
    """首期指标：提问/活跃/尝试/恢复/状态/完成率/停止意图；缺失不补零。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    await _enable_operations_read(session, identity)
    base = (
        select(func.count(), AgentRunAttempt.state)
        .where(
            AgentRunAttempt.tenant_id == tenant_id,
            AgentRunAttempt.admitted_at >= start,
            AgentRunAttempt.admitted_at < end,
        )
        .group_by(AgentRunAttempt.state)
    )
    state_rows = (await session.execute(base)).all()
    state_counts = {str(state): int(count) for count, state in state_rows}
    attempts_total = sum(state_counts.values())

    # 默认 cohort **排除**历史导入请求（historical 标记）：历史线程不得抬高提问量，
    # 也不得与本次真实采集混算；另列 historical 计数供核对。
    is_historical = text("COALESCE(identity_revision ->> 'historical', 'false') = 'true'")
    not_historical = text("COALESCE(identity_revision ->> 'historical', 'false') <> 'true'")
    questions = (
        await session.execute(
            select(func.count(func.distinct(AgentRequest.id))).where(
                AgentRequest.tenant_id == tenant_id,
                AgentRequest.admitted_at >= start,
                AgentRequest.admitted_at < end,
                not_historical,
            )
        )
    ).scalar() or 0
    historical_requests = (
        await session.execute(
            select(func.count(func.distinct(AgentRequest.id))).where(
                AgentRequest.tenant_id == tenant_id,
                AgentRequest.admitted_at >= start,
                AgentRequest.admitted_at < end,
                is_historical,
            )
        )
    ).scalar() or 0
    active_users = (
        await session.execute(
            select(func.count(func.distinct(AgentRequest.user_id))).where(
                AgentRequest.tenant_id == tenant_id,
                AgentRequest.admitted_at >= start,
                AgentRequest.admitted_at < end,
                not_historical,
            )
        )
    ).scalar() or 0
    resumes = (
        await session.execute(
            select(func.count())
            .select_from(AgentRunResume)
            .where(
                AgentRunResume.tenant_id == tenant_id,
                AgentRunResume.created_at >= start,
                AgentRunResume.created_at < end,
            )
        )
    ).scalar() or 0
    stop_intents = (
        await session.execute(
            select(func.count())
            .select_from(AgentRunAttempt)
            .where(
                AgentRunAttempt.tenant_id == tenant_id,
                AgentRunAttempt.admitted_at >= start,
                AgentRunAttempt.admitted_at < end,
                AgentRunAttempt.cancel_requested.is_(True),
                AgentRunAttempt.cancel_origin == "user_stop",
            )
        )
    ).scalar() or 0
    latency = await _latency_metrics(session, tenant_id, start, end)
    ended = sum(int(state_counts.get(s, 0)) for s in ENDED_STATES)
    succeeded = int(state_counts.get("succeeded", 0))
    waiting = int(state_counts.get("waiting_input", 0))
    unknown = int(state_counts.get("status_unknown", 0))
    admitting = int(state_counts.get("admitting", 0)) + int(
        state_counts.get("admission_unknown", 0)
    )
    projected_at, last_event_at = await _projection_times(session, tenant_id)
    now = dt.datetime.now(dt.UTC)
    return {
        "metric_version": METRIC_VERSION,
        "as_of": (last_event_at or now).isoformat(),
        "projected_at": projected_at.isoformat() if projected_at else None,
        "data_lag_seconds": int((now - last_event_at).total_seconds()) if last_event_at else None,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "tz": BUSINESS_TZ},
        "cohort": "attempts_admitted_in_window",
        "questions": {"value": int(questions), "status": "measured"},
        # 历史导入单列（excluded_from_default_cohort），不补造反馈/Token/耗时
        "historical_requests": {
            "value": int(historical_requests),
            "status": "historical_unattributed" if historical_requests else "zero",
            "excluded_from_default_cohort": True,
        },
        "active_users": {"value": int(active_users), "status": "measured"},
        "attempts": {"value": attempts_total, "status": "measured"},
        "resumes": {"value": int(resumes), "status": "measured"},
        "states": state_counts,
        "technical_completion_rate": {
            "numerator": succeeded,
            "denominator": ended,
            "value": round(succeeded / ended, 4) if ended else None,
            "status": "measured" if ended else "not_collected",
            "not_ended": {
                "waiting_input": waiting,
                "status_unknown": unknown,
                "admitting_or_unknown": admitting,
            },
        },
        "user_stop_intent": {
            "value": int(stop_intents),
            "denominator": attempts_total,
            "status": "measured",
        },
        "actual_cancelled": {
            "value": int(state_counts.get("cancelled", 0)),
            "status": "measured",
        },
        # 未采集项显式 null + 标签，禁止伪零
        "first_content_ms": {"value": None, "status": "not_collected", "reserved_for": "change-3"},
        "model_ttft_ms": {"value": None, "status": "not_collected", "reserved_for": "change-5"},
        "feedback_rate": {"value": None, "status": "not_collected", "reserved_for": "change-3"},
        "copy_rate": {"value": None, "status": "not_collected", "reserved_for": "change-3"},
        "usage_tokens": {"value": None, "status": "not_collected", "reserved_for": "change-5"},
        "cost": {"value": None, "status": "not_collected", "reserved_for": "change-5"},
        "measurement_source": "ledger_facts",
        # 服务端耗时与分位数（口径见 docs/contracts/agent-events-v1/metrics.json）：
        # 只取服务端时钟（admitted_at → ended_at），不与浏览器墙钟混算；人工等待另列；
        # 缺失样本单列，不补零、不平均每日分位数。
        "server_latency_ms": latency,
        "payload_bytes": None,
    }


async def _latency_metrics(
    session: AsyncSession, tenant_id: uuid.UUID, start: dt.datetime, end: dt.datetime
) -> dict[str, Any]:
    """窗口内已结束尝试的服务耗时分位数（服务端时钟；明细计算，不平均每日分位数）."""
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (ended_at - admitted_at)) * 1000) AS p50,
                  percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (ended_at - admitted_at)) * 1000) AS p95,
                  percentile_cont(0.99) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (ended_at - admitted_at)) * 1000) AS p99,
                  count(*) AS sample_count
                FROM agent_run_attempts
                WHERE tenant_id = :t AND admitted_at >= :s AND admitted_at < :e
                  AND ended_at IS NOT NULL
                """
                ),
                {"t": str(tenant_id), "s": start, "e": end},
            )
        )
        .mappings()
        .first()
    )
    missing = (
        await session.execute(
            text(
                "SELECT count(*) FROM agent_run_attempts WHERE tenant_id = :t"
                " AND admitted_at >= :s AND admitted_at < :e AND ended_at IS NULL"
            ),
            {"t": str(tenant_id), "s": start, "e": end},
        )
    ).scalar() or 0
    samples = int(row["sample_count"] or 0) if row else 0
    p50 = float(row["p50"]) if row and row["p50"] is not None else None
    p95 = float(row["p95"]) if row and row["p95"] is not None else None
    p99 = float(row["p99"]) if row and row["p99"] is not None else None
    return {
        "p50_ms": round(p50, 1) if p50 is not None else None,
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "p99_ms": round(p99, 1) if p99 is not None else None,
        "sample_count": samples,
        "missing_count": int(missing),
        "status": "measured" if samples else "not_collected",
        "measurement_source": "server_clock_admitted_to_ended",
        "quantile_method": "percentile_cont_on_window_detail",
    }


async def _projection_times(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[dt.datetime | None, dt.datetime | None]:
    row = (
        await session.execute(
            select(AgentProjectionState).where(
                AgentProjectionState.tenant_id == tenant_id,
                AgentProjectionState.consumer == "run_projection",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        last_event = (
            await session.execute(
                select(func.max(AgentEvent.occurred_at)).where(AgentEvent.tenant_id == tenant_id)
            )
        ).scalar()
        return None, last_event
    return row.projected_at, row.last_event_at


async def get_run_detail(
    session: AsyncSession, identity: Identity, attempt_id: uuid.UUID
) -> dict[str, Any]:
    """企业运行详情（运营权限）：元数据 + 回答版本关联 + Trace 映射；无正文。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    await _enable_operations_read(session, identity)
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    request = (
        await session.execute(
            select(AgentRequest).where(
                AgentRequest.id == attempt.request_id,
                AgentRequest.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    answers = (
        (
            await session.execute(
                select(AgentAnswer).where(
                    AgentAnswer.tenant_id == tenant_id, AgentAnswer.attempt_id == attempt_id
                )
            )
        )
        .scalars()
        .all()
    )
    traces = (
        (
            await session.execute(
                select(AgentTraceLink).where(
                    AgentTraceLink.tenant_id == tenant_id, AgentTraceLink.attempt_id == attempt_id
                )
            )
        )
        .scalars()
        .all()
    )
    resumes = (
        await session.execute(
            select(func.count())
            .select_from(AgentRunResume)
            .where(AgentRunResume.tenant_id == tenant_id, AgentRunResume.attempt_id == attempt_id)
        )
    ).scalar() or 0
    return {
        "attempt": {
            "attempt_id": str(attempt.id),
            "request_id": str(attempt.request_id),
            "thread_id": request.thread_id if request else None,
            "graph_id": attempt.graph_id,
            "state": attempt.state,
            "state_revision": attempt.state_revision,
            "evidence_source": attempt.evidence_source,
            "platform_status": attempt.platform_status,
            "platform_run_id": attempt.platform_run_id,
            "cancel_requested": attempt.cancel_requested,
            "cancel_origin": attempt.cancel_origin,
            "cancel_phase": attempt.cancel_phase,
            "cancel_confirmed": attempt.cancel_confirmed,
            "admitted_at": attempt.admitted_at.isoformat(),
            "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
            "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None,
            "resume_count": int(resumes),
        },
        "answers": [
            {
                "answer_id": str(a.id),
                "message_id": a.message_id,
                "content_version": a.content_version,
                "partial": a.partial,
                "confirmed": a.confirmed,
            }
            for a in answers
        ],
        "traces": [
            {
                "provider": t.provider,
                "project_scope": t.project_scope,
                "trace_id": t.trace_id,
                "primary": t.is_primary,
                "link_source": t.link_source,
            }
            for t in traces
        ],
        "content_included": False,
    }


async def read_request_content(
    session: AsyncSession, identity: Identity, request_id: uuid.UUID
) -> dict[str, Any]:
    """原文读取：需 `agent:content:read`（路由层已校验）；仅本人企业范围内。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    request = (
        await session.execute(
            select(AgentRequest).where(
                AgentRequest.id == request_id, AgentRequest.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if request is None:
        raise NotFound("对象不存在", code="not_found")
    # R05：删除中/已删除一律禁读（立即生效，不等后台清理完成）
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=request.thread_id):
        raise NotFound("对象不存在", code="not_found")
    now = dt.datetime.now(dt.UTC)
    # 交付缺口：按**请求**精确读取不可漂移快照（不按可变 thread 指针取全量）；
    # 保留期到期即拒绝。
    settings = get_settings()
    policy_days = settings.ledger_content_retention_days_by_tenant.get(
        str(tenant_id), settings.ledger_content_retention_days
    )
    refs = (
        (
            await session.execute(
                select(AgentContentRef).where(
                    AgentContentRef.tenant_id == tenant_id,
                    AgentContentRef.request_id == request.id,
                    AgentContentRef.deleted_at.is_(None),
                    AgentContentRef.created_at > now - dt.timedelta(days=policy_days),
                    AgentContentRef.retention_until.is_(None)
                    | (AgentContentRef.retention_until > now),
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "request_id": str(request.id),
        "thread_id": request.thread_id,
        "contents": [
            {
                "content_ref_id": str(r.id),
                "message_id": r.message_id,
                "content_version": r.content_version,
                "content": r.content_snapshot,
                "retention_until": r.retention_until.isoformat() if r.retention_until else None,
            }
            for r in refs
        ],
    }


async def ingest_browser_events(
    session: AsyncSession, identity: Identity, *, events: list[dict[str, Any]]
) -> dict[str, Any]:
    """浏览器事件受理：来源固定为 browser（服务端覆盖），逐项结果。

    change-3 启用 browser.feedback/copy/timing：
    - browser.feedback / browser.copy / browser.timing 走**逐项独立受理**：
      反馈命令委托 feedback 服务（幂等/CAS/同事务），计时/复制直接写入事件
      （同一事务逐项 flush；合法项不因非法邻项回滚——批次级 rollback 由
      调用方在整批异常时回滚，逐项失败仅记录 reason）。
    - 关联复核：answer/attempt 必须属于本人同租户且对象存在（feedback 要求
      已确认回答；timing 的 attempt 由服务端按归属复核），信封重复标识不得
      自相矛盾；事件 ID 重复（tenant 内同 event_id）→ 复用原结果不重复记账。
    - 浏览器不能声明终态/usage/成本（runtime 五类型仍预留拒绝）。
    """
    from app.services.browser_interactions import accept

    return await accept(session, identity, events)


def _opt_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def list_pending_attempts(
    session: AsyncSession, identity: Identity, *, thread_id: str
) -> list[dict[str, Any]]:
    """线程上尚未确认平台映射的尝试（供流式运行的 run_id 确认候选）。

    按提交回读：恢复与初次共享 attempt，不能因原 attempt 已有映射而遗漏恢复提交。
    调用方须再核对平台 metadata 的 request/attempt/submission 三项。
    """
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=thread_id):
        return []
    rows = (
        (
            await session.execute(
                select(AgentSubmission)
                .join(
                    AgentRequest,
                    and_(
                        AgentRequest.id == AgentSubmission.request_id,
                        AgentRequest.tenant_id == AgentSubmission.tenant_id,
                    ),
                )
                .where(
                    AgentSubmission.tenant_id == tenant_id,
                    AgentSubmission.user_id == identity.user.id,
                    AgentRequest.thread_id == thread_id,
                    AgentSubmission.admission_status.in_(("admitting", "admission_unknown")),
                )
                .order_by(AgentSubmission.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "request_id": str(a.request_id),
            "attempt_id": str(a.attempt_id),
            "submission_id": str(a.id),
            "state": a.admission_status,
            "ledger_marker": ledger_marker(a.request_id, a.attempt_id),
        }
        for a in rows
    ]


async def outbox_health(session: AsyncSession, identity: Identity) -> dict[str, Any]:
    """积压可见：pending/oldest_age/retry/dead_letter（平台运维权限）。"""
    now = dt.datetime.now(dt.UTC)
    rows = (
        await session.execute(
            text(
                "SELECT status, count(*) AS n, min(available_at) AS oldest"
                " FROM agent_outbox GROUP BY status"
            )
        )
    ).all()
    by_status = {str(s): int(n) for s, n, _ in rows}
    oldest = min((r[2] for r in rows if r[2] is not None), default=None)
    return {
        "now": now.isoformat(),
        "pending": by_status.get("pending", 0),
        "leased": by_status.get("leased", 0),
        "dead_letter": by_status.get("dead_letter", 0),
        "done": by_status.get("done", 0),
        "oldest_available_at": oldest.isoformat() if oldest else None,
        "oldest_age_seconds": int((now - oldest).total_seconds()) if oldest else None,
    }
