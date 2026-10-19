"""公开图运行生命周期适配：从可信运行上下文生成台账事实.

接入点（始终共用入口，覆盖流式与非流式）：.
- 运行开始/受理回填：`run.admission_confirmed`
- 状态观测：`run.state_observed`（带证据来源与修订）
- 停止意图：`run.cancel_requested`（来源/阶段显式）
- 人工恢复：`run.resumed`
- 可见回答版本：`answer.confirmed` / `answer.partial`

约束：
- 身份只取 `configurable.langgraph_auth_user`（平台注入，客户端不可伪造）；
- **无台账受理上下文**（缺 ledger_request_id/ledger_attempt_id）时不得凭空造事实，
  调用方按 `unregistered` 处理（未登记入口应拒绝开放）；
- 不在图导入时做外部 I/O；连接在首次使用时惰性建立。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from loguru import logger

from common.event_ledger.store import (
    LedgerBackendUnavailable,
    LedgerScope,
    attach_platform_run,
    observe_state,
    record_answer,
    record_resume,
    record_stop_intent,
    write_event,
)
from common.identity.runtime import identity_from_config

# 公开图标识（langgraph.json）
PUBLIC_GRAPHS = (
    "basic-qa",
    "data-analysis",
    "intelligent-tracing",
)

# 平台原生 run 状态 → 业务主状态（未知一律 status_unknown，不猜成功/取消）
NATIVE_STATUS_MAP = {
    "success": "succeeded",
    "error": "failed",
    "failed": "failed",
    "timeout": "timed_out",
    "interrupted": "waiting_input",
    "pending": "queued",
    "running": "running",
}


class LedgerContextMissing(Exception):
    """缺少台账受理上下文：该入口未登记，调用方必须拒绝开放."""


def ledger_scope_from_config(config: dict[str, Any] | None) -> LedgerScope | None:
    """从可信 config 提取台账作用域（身份来自平台注入的 langgraph_auth_user."""
    identity = identity_from_config(config)
    if identity is None:
        return None
    return LedgerScope(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        membership_id=identity.membership_id,
    )


def ledger_ids_from_config(config: dict[str, Any] | None) -> tuple[str, str] | None:
    """提取受理关联 ID（受控入口注入；configurable 优先，运行 metadata 兜底）.

    R01 修复：原生入口在放行时会把**服务端裁决过**的 request/attempt 同时写入运行
    config.configurable 与 run metadata；某些路径（平台只持久化 metadata，
    metadata-only 形态）下 configurable 不可见，此时从 `config["metadata"]` 读取，
    保证"平台 success 而台账仍 admitting"不再出现。
    """
    cfg = config or {}
    configurable = cfg.get("configurable") or {}
    metadata = cfg.get("metadata") or {}
    request_id = configurable.get("ledger_request_id") or metadata.get(
        "ledger_request_id"
    )
    attempt_id = configurable.get("ledger_attempt_id") or metadata.get(
        "ledger_attempt_id"
    )
    if not request_id or not attempt_id:
        return None
    return str(request_id), str(attempt_id)


def require_ledger_context(
    config: dict[str, Any] | None,
) -> tuple[LedgerScope, str, str]:
    """强制要求台账受理上下文（未覆盖入口拒绝开放."""
    scope = ledger_scope_from_config(config)
    ids = ledger_ids_from_config(config)
    if scope is None or ids is None:
        raise LedgerContextMissing("缺少台账受理上下文：该运行入口未登记，拒绝执行")
    return scope, ids[0], ids[1]


def business_state_from_native(platform_status: str | None) -> str:
    """平台状态 → 业务状态；未知不猜测."""
    if not platform_status:
        return "status_unknown"
    return NATIVE_STATUS_MAP.get(str(platform_status).lower(), "status_unknown")


async def lifecycle_observe_state(
    config: dict[str, Any] | None,
    *,
    platform_status: str,
    evidence_source: str = "platform_read",
) -> dict[str, Any]:
    """把平台原生状态写为带证据的业务状态（未登记入口拒绝."""
    scope, _request_id, attempt_id = require_ledger_context(config)
    state = business_state_from_native(platform_status)
    try:
        return await observe_state(
            scope,
            attempt_id=attempt_id,
            state=state,
            evidence_source=evidence_source,
            platform_status=platform_status,
            cancel_confirmed=(state == "cancelled"),
        )
    except LedgerBackendUnavailable:
        logger.bind(event_ledger=True).warning("ledger observe_state unavailable")
        return {"applied": False, "reason": "backend_unavailable"}


async def lifecycle_answer(
    config: dict[str, Any] | None,
    *,
    message_id: str | None,
    content_version: int = 1,
    confirmed: bool = True,
) -> dict[str, Any]:
    """只登记**用户可见**的最终/部分正文（调用方保证草稿与推理不进入."""
    scope, _request_id, attempt_id = require_ledger_context(config)
    try:
        return await record_answer(
            scope,
            attempt_id=attempt_id,
            message_id=message_id,
            content_version=content_version,
            confirmed=confirmed,
        )
    except LedgerBackendUnavailable:
        logger.bind(event_ledger=True).warning("ledger record_answer unavailable")
        return {"recorded": False, "reason": "backend_unavailable"}


async def lifecycle_resume(
    config: dict[str, Any] | None, *, platform_run_id: str
) -> dict[str, Any]:
    """人工恢复：平台会新建 run_id；台账保留尝试、追加运行映射，不新建提问."""
    scope, _request_id, attempt_id = require_ledger_context(config)
    try:
        return await record_resume(
            scope, attempt_id=attempt_id, platform_run_id=platform_run_id
        )
    except LedgerBackendUnavailable:
        logger.bind(event_ledger=True).warning("ledger record_resume unavailable")
        return {"recorded": False, "reason": "backend_unavailable"}


async def lifecycle_stop_intent(
    config: dict[str, Any] | None,
    *,
    origin: str,
    phase: str,
    platform_run_id: str | None = None,
) -> dict[str, Any]:
    """停止意图（独立于终态）。origin=unknown 表示未知原因，绝不推断为 user_stop."""
    scope, _request_id, attempt_id = require_ledger_context(config)
    try:
        return await record_stop_intent(
            scope,
            attempt_id=attempt_id,
            origin=origin,
            phase=phase,
            platform_run_id=platform_run_id,
        )
    except LedgerBackendUnavailable:
        logger.bind(event_ledger=True).warning("ledger stop intent unavailable")
        return {"recorded": False, "reason": "backend_unavailable"}


async def lifecycle_admission_confirmed(
    config: dict[str, Any] | None,
    *,
    platform_run_id: str | None,
    outcome: str = "confirmed",
    reason: str | None = None,
) -> dict[str, Any]:
    """受理结果回填（受控入口在平台响应后调用；响应丢失用 admission_unknown."""
    scope, request_id, attempt_id = require_ledger_context(config)
    payload = {
        "platform_run_id": platform_run_id or "",
        "admission_outcome": outcome,
    }
    if reason:
        payload["admission_unknown_reason"] = reason
    try:
        result = await write_event(
            scope,
            event_type="run.admission_confirmed",
            source="runtime",
            payload=payload,
            request_id=request_id,
            attempt_id=attempt_id,
            run_id=platform_run_id,
        )
    except LedgerBackendUnavailable:
        return {"written": False, "reason": "backend_unavailable"}
    if outcome == "confirmed" and platform_run_id:
        await observe_state(
            scope,
            attempt_id=attempt_id,
            state="running",
            evidence_source="platform_read",
            platform_status="running",
        )
    return result


# ------------------------------------------------------------------ 图包装接入


def _message_field(message: Any, name: str, default: Any = None) -> Any:
    """同时支持 dict 与 LangChain 消息对象（图 state 里是对象，测试里常是 dict）."""
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def _visible_final_message(messages: Any) -> Any:
    """从终态消息里取**最后一条用户可见 assistant 正文**.

    与前端同一约定：`additional_kwargs.ui_visible === false` 的内部/草稿消息
    （推理、工具、未确认草稿）一律排除；只认 type=ai 且有非空文本的最后一条。
    返回原始消息（dict 或 LangChain 消息对象），调用方只读 id。
    """
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if str(_message_field(message, "type") or "") != "ai":
            continue
        extra = _message_field(message, "additional_kwargs") or {}
        if isinstance(extra, dict) and extra.get("ui_visible") is False:
            continue
        content = _message_field(message, "content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        if isinstance(content, str) and content.strip():
            return message
    return None


def _resolve_config(config: Any) -> dict[str, Any] | None:
    """解析运行配置：优先节点入参，缺失时取 `langgraph.config.get_config()`.

    框架只保证 `get_config()` 在 Pregel 任务上下文中可用（`Runtime` 不含 config，
    轮次01 实测）；节点入参在部分路径可能不含 configurable，故逐级回退。
    """
    candidates = [config]
    try:
        from langgraph.config import get_config

        candidates.append(dict(get_config() or {}))
    except Exception:  # 无 runnable 上下文（离线直调）时忽略
        pass
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("configurable"):
            return candidate
    return None


def declared_graph_id(config: dict[str, Any] | None) -> str:
    """受控入口声明的图名（`configurable.ledger_graph_id`，metadata 兜底）."""
    cfg = config or {}
    configurable = cfg.get("configurable") or {}
    metadata = cfg.get("metadata") or {}
    return str(
        configurable.get("ledger_graph_id") or metadata.get("ledger_graph_id") or ""
    )


def trusted_run_id(config: dict[str, Any] | None) -> str:
    """平台在运行期注入的**可信** platform run id（configurable/metadata 皆可）.

    F05：图内可见 `configurable.run_id`（实测镜像 0.15.1）与 `metadata.run_id`，
    都是平台注入、客户端无法写入的字段；据此可在终态前补齐平台映射，
    而不是让"平台 success 但台账没有 platform_run_id"的记录只能等外部补偿。
    """
    resolved = _resolve_config(config) or {}
    configurable = resolved.get("configurable") or {}
    metadata = resolved.get("metadata") or {}
    for candidate in (
        configurable.get("run_id"),
        configurable.get("langgraph_run_id"),
        metadata.get("run_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


async def _record_platform_mapping(
    scope: LedgerScope,
    *,
    attempt_id: str,
    run_id: str,
    submission_id: str | None = None,
    operation_kind: str = "initial",
) -> bool:
    """把图内可信 run 写回台账：无映射→收敛；已有映射但不同→追加恢复映射.

    恢复（人工 resume）会在同一 attempt 上产生**新** run，因此"不同即冲突"的
    attach 语义不适用：此时改为记录恢复映射（保留原 run 关系，幂等）。
    """
    result = await attach_platform_run(
        scope,
        attempt_id=attempt_id,
        platform_run_id=run_id,
        evidence="runtime_run_id",
        submission_id=submission_id,
    )
    if result.get("reason") == "mapping_conflict" and operation_kind == "resume":
        await record_resume(
            scope,
            attempt_id=attempt_id,
            platform_run_id=run_id,
            submission_id=submission_id,
        )
        return True
    return bool(result.get("attached") or result.get("reason") == "already_attached")


_AUTHORIZATION_STOP_MARKERS = (
    "本次运行已停止",
    "工具调用被拒绝",
    "本次工具调用被拒绝",
)


def authorization_stop_from_messages(messages: Any) -> bool:
    """判断图是否因业务/身份撤权守卫而停止（不得记成成功回答）."""
    if not isinstance(messages, list):
        return False
    contents: list[str] = []
    for message in messages:
        content = _message_field(message, "content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        if isinstance(content, str):
            contents.append(content)
    joined = "\n".join(contents)
    return "授权状态已变化" in joined and any(
        marker in joined for marker in _AUTHORIZATION_STOP_MARKERS
    )


async def _on_graph_end(
    config: dict[str, Any] | None, state: Any, *, graph_name: str = ""
) -> None:
    """图正常结束：落终态证据与可见回答版本（无受理上下文时保持未登记，不造假）.

    **换图检测闭环**：本函数由每张图**自己的**收尾节点/中间件调用，`graph_name` 是
    实际执行的图名（可信来源：图定义本身，不来自客户端）。若与受控入口声明的
    `ledger_graph_id` 不一致，只落一条冲突证据（`platform_status=graph_mismatch`），
    且**不登记可见回答**——不得把别的图的输出记到本次提问名下。

    失败关闭：台账不可用只记录日志，绝不把台账故障变成业务失败，也不回滚答案。
    """
    resolved = _resolve_config(config)
    scope = ledger_scope_from_config(resolved)
    ids = ledger_ids_from_config(resolved)
    if scope is None or ids is None:
        return
    _request_id, attempt_id = ids
    declared = declared_graph_id(resolved)
    if graph_name and declared and graph_name != declared:
        try:
            await observe_state(
                scope,
                attempt_id=attempt_id,
                state="status_unknown",
                evidence_source="runtime_callback",
                platform_status="graph_mismatch",
            )
        except LedgerBackendUnavailable:
            logger.bind(event_ledger=True).warning(
                "ledger graph mismatch record unavailable (attempt={})", attempt_id
            )
        logger.bind(event_ledger=True).warning(
            "graph mismatch: executed={} declared={} attempt={}",
            graph_name,
            declared,
            attempt_id,
        )
        return
    try:
        # F05：先补**可信平台运行映射**（终态/答案关联都依赖它），再记终态与答案。
        run_id = trusted_run_id(resolved)
        if run_id:
            cfg = (resolved or {}).get("configurable") or {}
            meta = (resolved or {}).get("metadata") or {}
            mapped = await _record_platform_mapping(
                scope,
                attempt_id=attempt_id,
                run_id=run_id,
                submission_id=cfg.get("ledger_submission_id")
                or meta.get("ledger_submission_id"),
                operation_kind=cfg.get("ledger_operation_kind")
                or meta.get("ledger_operation_kind")
                or "initial",
            )
            if not mapped:
                return  # 晚到旧操作只保留自身映射，不覆盖当前尝试的答案/状态。

        messages = state.get("messages") if isinstance(state, dict) else None
        if authorization_stop_from_messages(messages):
            # 撤权/身份失效后的可读停止不是业务成功；不登记确认回答，只落失败证据。
            await observe_state(
                scope,
                attempt_id=attempt_id,
                state="failed",
                evidence_source="runtime_callback",
                platform_status="authorization_revoked",
            )
            return
        visible = _visible_final_message(messages)
        if visible is not None:
            await record_answer(
                scope,
                attempt_id=attempt_id,
                message_id=str(_message_field(visible, "id") or "") or None,
                content_version=1,
                confirmed=True,
            )
        await observe_state(
            scope,
            attempt_id=attempt_id,
            state="succeeded",
            evidence_source="runtime_callback",
            platform_status="success",
        )
    except Exception as exc:  # noqa: BLE001 - 回调失败开放：绝不把台账故障变成业务失败
        # 受理路径失败关闭（不启动运行），但**终态回调**必须失败开放：答案已经
        # 产生并展示，台账写入失败只记录类别与缺失，便于事后校准补齐。
        logger.bind(event_ledger=True).warning(
            "ledger graph-end callback failed: class={} attempt={}",
            type(exc).__name__,
            attempt_id,
        )


def make_ledger_finalize_node(graph_name: str) -> Any:
    """构造公开图共用的台账收尾节点（保持编译结果为 Graph 类型）.

    平台加载器按 "Graph or Graph factory" 校验导出对象；`with_listeners` 会
    把编译结果变成 RunnableBinding 而被拒绝，因此改用**图内节点**落账：
    终态与可见回答版本在 END 之前写入台账。

    行为约束：
    - 无台账受理上下文 / 台账不可用 → 直接返回 `{}`，不改变原图状态与调用方行为；
    - 只读 state 取可见最终正文，不注入任何内容到回答。
    """

    async def _ledger_finalize(
        state: Any, config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # graph_name 来自图定义本身（可信），用于与受控入口声明的图名比对
        await _on_graph_end(config, state, graph_name=graph_name)
        return {}

    _ledger_finalize.__name__ = f"ledger_finalize_{graph_name.replace('-', '_')}"
    return _ledger_finalize


class LedgerLifecycleMiddleware(AgentMiddleware):  # type: ignore[misc]
    """deepagents 图（intelligent-tracing）的台账收尾.

    该图用 `create_deep_agent` 构建，没有可插节点的 StateGraph 拓扑；中间件是
    它支持的扩展点。`aafter_agent` 在代理终止后触发，落终态证据与可见回答版本。

    与节点流图共用的 `make_ledger_finalize_node` 共用同一个 `_on_graph_end` 实现，
    避免两套语义漂移；同样只读 state、不注入内容、失败开放。
    """

    def __init__(self, graph_name: str) -> None:
        """记录图名（仅供日志与去重标识）."""
        super().__init__()
        self.graph_name = graph_name

    async def aafter_agent(self, state: Any, runtime: Any = None) -> None:  # noqa: ANN401
        """代理终止后落账（失败开放，绝不改变模型输出）."""
        config = None
        runtime_config = getattr(runtime, "config", None)
        if isinstance(runtime_config, dict):
            config = runtime_config
        await _on_graph_end(config, state, graph_name=self.graph_name)
        return None
