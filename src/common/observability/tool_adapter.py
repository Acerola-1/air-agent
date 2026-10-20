"""Skill 与工具观测适配：阶段分离 + 逻辑/尝试分离 + 结果分类.

设计约束（spec agent-capability-observability）：

- Skill：候选(matched)/选中(selected)/加载(loaded)/执行(executed) 各一条
  span；候选未执行/加载失败不得计为成功执行。
- 工具：逻辑调用一次（logical_call_id），每次物理尝试一条 attempt
  （重试/降级独立）；工具同名不同服务不合并（service_key 参与标识）。
- 结果分类：succeeded / empty_result / validation_rejected / permission_denied /
  technical_error / timed_out / cancelled / unknown；HTTP200 业务无结果不算
  succeeded；外呼前的授权拒绝不产生网络尝试（attempt 也要记录——状态
  permission_denied，但它不是网络调用，error/attempt 语义由分类表达）。
- 非秘密：只登记名称/类别/时长/稳定标识，不记录参数、结果正文、异常文本。

双层去重约定：物理尝试的**所有者**是 tool_wrappers / mcp 执行层（本模块的
`record_tool_result`）；LangChain 回调层不重复写 tool attempt。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from common.observability.collector import WorkItem, get_collector
from common.observability.context import ObservationContext, new_id
from common.observability.store import (
    observability_enabled,
    record_span,
    record_tool_attempt,
)

# HTTP200 业务失败载荷的识别模式（规范业务返回：success=false / code!=200）
_BUSINESS_FAILURE_CODE_RE = re.compile(
    r"\"code\"\s*:\s*(40[0-9]|4[1-9][0-9]|5[0-9][0-9])"
)
_BUSINESS_FAILURE_SUCCESS_RE = re.compile(r"\"success\"\s*:\s*false")


def classify_tool_output(content: Any) -> str:
    """按规范业务返回分类工具输出（不读正文，只看结构性标记）.

    内容不是 dict/str 或无结构标记时返回 succeeded 有数据的保守判定；
    空字符串/空 JSON 是 empty_result；code 4xx/5xx 或 success:false 按
    permission_denied（401/403）/ technical_error（5xx）/ empty_result（无数据码）
    细分。匹配失败未知返回 succeeded——分类错误宁可保守，不臆造失败。
    """
    if content is None or content == "" or content == [] or content == {}:
        return "empty_result"
    if isinstance(content, str):
        if not content.strip():
            return "empty_result"
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            return "succeeded"
    if isinstance(content, list):
        if not content:
            return "empty_result"
        if all(
            isinstance(item, dict) and item.get("type") == "text" for item in content
        ):
            statuses = [classify_tool_output(item.get("text")) for item in content]
            return next(
                (status for status in statuses if status != "succeeded"), "succeeded"
            )
        return "unknown"
    if content == {} or content == []:
        return "empty_result"
    if not isinstance(content, dict):
        return "unknown"
    if content.get("schema_version") == "agent.knowledge-search.v1":
        outcome = content.get("outcome")
        if outcome == "ok":
            references = content.get("references")
            return (
                "succeeded"
                if content.get("retrieval_status") in {"retrieved", "complete"}
                and isinstance(references, list)
                and bool(references)
                and all(
                    isinstance(ref, dict) and ref.get("verified") is True
                    for ref in references
                )
                else "unknown"
            )
        return {
            "no_match": "empty_result",
            "permission_denied": "permission_denied",
            "context_changed": "permission_denied",
            "timeout": "timed_out",
            "cancelled": "cancelled",
            "unavailable": "technical_error",
            "evidence_unusable": "technical_error",
        }.get(outcome, "unknown")
    code = str(content.get("code", content.get("status_code", "")))
    if code in {"401", "403"}:
        return "permission_denied"
    if code in {"400", "422"}:
        return "validation_rejected"
    if (
        code.startswith("5")
        or content.get("success") is False
        or content.get("isError") is True
    ):
        return "technical_error"
    if "data" in content and content["data"] in (None, [], {}, ""):
        return "empty_result"
    if (
        content.get("success") is True
        or code in {"200", "0"}
        or isinstance(content.get("rows"), list)
    ):
        return "succeeded"
    return "unknown"


def classify_exception(exc: BaseException) -> tuple[str, str | None]:
    """异常 → (结果分类, 规范错误类别)；不传播异常正文."""
    name = type(exc).__name__
    tool_content = getattr(exc, "tool_content", None)
    if tool_content is not None:
        status = classify_tool_output(tool_content)
        return (
            status
            if status
            in {
                "permission_denied",
                "validation_rejected",
                "technical_error",
                "empty_result",
            }
            else "technical_error",
            "mcp_execution_error",
        )
    lowered = name.lower()
    text = str(exc).lower()[:200] if exc else ""
    if "timeout" in lowered or "timeout" in text:
        return "timed_out", "timeout"
    if "cancel" in lowered:
        return "cancelled", "cancelled"
    if "authorization" in text or "mcpauthorizationerror" in lowered:
        return "permission_denied", "authorization"
    if "validation" in lowered or "validation" in text:
        return "validation_rejected", "validation"
    return "technical_error", name


@dataclass
class SkillSpanScope:
    """一次 Skill 生命周期的进程内句柄（候选→选中→加载→执行各写一条）."""

    ctx: ObservationContext
    skill_id: str
    skill_version: str | None
    selection_source: str
    model_observation_id: str | None = None

    async def record_matched(self, *, candidate_count: int) -> None:
        """候选阶段（可多条候选，只记总数与 top 候选）."""
        await self._emit(stage="matched", status=f"candidates={candidate_count}")

    async def record_selected(self) -> None:
        """选中阶段."""
        await self._emit(stage="selected", status=None)

    async def record_loaded(self, *, ok: bool) -> None:
        """加载阶段（失败不计成功）."""
        await self._emit(stage="loaded", status="succeeded" if ok else "failed")

    async def record_executed(
        self, *, ok: bool, duration_ms: int | None = None
    ) -> None:
        """执行阶段（ok=False 记 not_executed，不计成功执行）."""
        await self._emit(
            stage="executed",
            status="succeeded" if ok else "not_executed",
            duration_ms=duration_ms,
        )

    async def _emit(
        self, *, stage: str, status: str | None, duration_ms: int | None = None
    ) -> None:
        """写一条 Skill 阶段 span（观测关闭时零开销）."""
        if not observability_enabled():
            return
        await record_span(
            self.ctx,
            observation_id=new_id(),
            parent_observation_id=None,
            span_kind="skill",
            span_name=self.skill_id,
            stage=stage,
            status=status,
            duration_ms=duration_ms,
            skill_id=self.skill_id,
            skill_version=self.skill_version,
            selection_source=self.selection_source,
            model_observation_id=self.model_observation_id,
        )


async def skill_span(
    ctx: ObservationContext,
    *,
    skill_id: str,
    skill_version: str | None,
    selection_source: str,
) -> SkillSpanScope | None:
    """开始一个 Skill 观测作用域；观测关闭时返回 None（调用方零开销）."""
    if not observability_enabled():
        return None
    return SkillSpanScope(
        ctx=ctx,
        skill_id=skill_id,
        skill_version=skill_version,
        selection_source=selection_source,
    )


async def record_skill_candidates(
    ctx: ObservationContext,
    *,
    candidates: list[tuple[str, str | None]],
    selection_source: str,
) -> None:
    """记录语义路由候选（每候选一条 matched span；未选中即 not_executed）."""
    if not observability_enabled():
        return
    for name, version in candidates:
        await record_span(
            ctx,
            observation_id=new_id(),
            parent_observation_id=None,
            span_kind="skill",
            span_name=name,
            stage="matched",
            status=None,
            skill_id=name,
            skill_version=version,
            selection_source=selection_source,
        )


async def _record_tool_result(
    ctx: ObservationContext,
    *,
    logical_call_id: str | None,
    tool_name: str,
    service_key: str | None,
    retry_index: int = 0,
    status: str | None = None,
    duration_ms: int | None = None,
    tool_call_id: str | None = None,
    skill_id: str | None = None,
    fallback_kind: str | None = None,
    error_category: str | None = None,
    output: Any = None,
    network_attempted: bool | None = False,
    started_at: dt.datetime | None = None,
) -> str:
    """记录一次物理工具尝试并返回 observation_id.

    status 缺省时从 output 结构化分类；调用方（权限拒绝处）可显式传
    permission_denied 表示**未发生网络外呼**的拒绝（retry_index=0、无耗时）。
    """
    if not observability_enabled():
        return ""
    resolved = status or classify_tool_output(output)
    observation_id = new_id()
    logical = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{ctx.tenant_id}:{ctx.attempt_id}:{ctx.submission_id}:{service_key or 'local'}:{tool_name}:{logical_call_id or new_id()}",
        )
    )
    collector = get_collector()
    called_at = started_at or dt.datetime.now(dt.UTC) - dt.timedelta(
        milliseconds=duration_ms or 0
    )

    async def _write() -> None:
        await record_tool_attempt(
            ctx,
            observation_id=observation_id,
            logical_call_id=logical,
            tool_name=tool_name,
            service_key=service_key,
            retry_index=retry_index,
            status=resolved,
            duration_ms=duration_ms,
            tool_call_id=tool_call_id,
            skill_id=skill_id,
            fallback_kind=fallback_kind,
            error_category=error_category,
            network_attempted=network_attempted,
            started_at=called_at,
        )

    collector.enqueue(WorkItem(kind="tool_attempt", ctx=ctx, fn=_write))
    return observation_id


async def record_tool_result(*args, **kwargs):
    """尽力采集；监测故障不改变已授权工具的结果或异常."""
    try:
        return await _record_tool_result(*args, **kwargs)
    except Exception:
        return ""


async def record_tool_exception(
    ctx: ObservationContext,
    *,
    logical_call_id: str | None,
    tool_name: str,
    service_key: str | None,
    retry_index: int = 0,
    exc: BaseException,
    network_attempted: bool | None = False,
    started_at: dt.datetime | None = None,
    duration_ms: int | None = None,
    tool_call_id: str | None = None,
    skill_id: str | None = None,
) -> str:
    """异常路径的分类记录（不传播异常正文）."""
    if not observability_enabled():
        return ""
    status, category = classify_exception(exc)
    return await record_tool_result(
        ctx,
        logical_call_id=logical_call_id,
        tool_name=tool_name,
        service_key=service_key,
        retry_index=retry_index,
        status=status,
        duration_ms=duration_ms,
        tool_call_id=tool_call_id,
        skill_id=skill_id,
        error_category=category,
        network_attempted=network_attempted,
        started_at=started_at,
    )


# Only the authorized physical MCP boundary owns network attempts. The scoped key
# includes tenant/attempt/service, so identical provider call IDs never join users.
_COUNTER_LOCK = __import__("threading").Lock()
_COUNTERS: dict[str, int] = {}


async def observed_tool_call(
    config,
    *,
    tool_name,
    service_key,
    invoke,
    logical_call_id=None,
    network_attempted=True,
):
    """在已授权物理调用边界记录一次尝试，采集失败不改变业务结果."""
    from common.observability.context import observation_context_from_config

    ctx = observation_context_from_config(config)
    if ctx is None or not observability_enabled():
        return await invoke()
    logical = str(
        logical_call_id
        or (config.get("metadata") or {}).get("obs_tool_call_id")
        or new_id()
    )
    key = (
        f"{ctx.tenant_id}:{ctx.attempt_id}:{ctx.submission_id}:{service_key}:{logical}"
    )
    with _COUNTER_LOCK:
        retry = _COUNTERS.get(key, 0)
        _COUNTERS[key] = retry + 1
        if len(_COUNTERS) > 1000:
            _COUNTERS.pop(next(iter(_COUNTERS)))
    started = __import__("time").monotonic()
    started_at = dt.datetime.now(dt.UTC)
    try:
        value = await invoke()
    except BaseException as exc:
        await record_tool_exception(
            ctx,
            logical_call_id=logical,
            tool_name=tool_name,
            service_key=service_key,
            retry_index=retry,
            exc=exc,
            network_attempted=network_attempted,
            started_at=started_at,
            duration_ms=int((__import__("time").monotonic() - started) * 1000),
            tool_call_id=logical,
        )
        raise
    await record_tool_result(
        ctx,
        logical_call_id=logical,
        tool_name=tool_name,
        service_key=service_key,
        retry_index=retry,
        output=getattr(value, "content", value),
        network_attempted=network_attempted,
        started_at=started_at,
        duration_ms=int((__import__("time").monotonic() - started) * 1000),
        tool_call_id=logical,
    )
    return value
