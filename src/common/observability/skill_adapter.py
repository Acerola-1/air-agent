"""Skill阶段事实：只在实际加载和执行位置记录，不以Graph名称代替Skill."""

from __future__ import annotations

import hashlib
import threading
import uuid
from typing import Any

from common.observability.collector import WorkItem, get_collector
from common.observability.context import observation_context_from_config
from common.observability.store import observability_enabled, record_span

_LOADED: dict[tuple[str, str, str], dict[str, str | None]] = {}
_LOCK = threading.Lock()


def emit_skill_stage(
    config: dict[str, Any],
    skill_id: str,
    stage: str,
    *,
    status: str | None = None,
    body: str | None = None,
    version: str | None = None,
    source: str = "semantic-router",
) -> None:
    """采集白名单名称和内容摘要，有界非阻塞入队，正文不离开调用点."""
    if not observability_enabled():
        return
    ctx = observation_context_from_config(config)
    if ctx is None:
        return
    version = version or (hashlib.sha256(body.encode()).hexdigest() if body else None)
    key = (ctx.tenant_id, ctx.attempt_id, ctx.submission_id or "")
    if stage == "loaded" and status == "succeeded":
        with _LOCK:
            _LOADED.setdefault(key, {})[skill_id] = version
            if len(_LOADED) > 1000:
                _LOADED.pop(next(iter(_LOADED)))
    observation_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{key}:{skill_id}:{stage}:{status}:{version}")
    )

    async def write():
        await record_span(
            ctx,
            observation_id=observation_id,
            parent_observation_id=None,
            span_kind="skill",
            span_name=skill_id,
            stage=stage,
            status=status,
            skill_id=skill_id,
            skill_version=version,
            selection_source=source,
        )

    try:
        get_collector().enqueue(WorkItem("skill_span", ctx, write))
    except Exception:
        pass


def record_loaded_execution(config: dict[str, Any], *, succeeded: bool) -> None:
    """只对本次已加载能力标记执行结果，失败保持失败而非成功."""
    ctx = observation_context_from_config(config)
    if ctx is None:
        return
    key = (ctx.tenant_id, ctx.attempt_id, ctx.submission_id or "")
    with _LOCK:
        loaded = _LOADED.pop(key, {})
    for skill, version in loaded.items():
        emit_skill_stage(
            config,
            skill,
            "executed",
            status="succeeded" if succeeded else "failed",
            version=version,
            source="loaded-rules",
        )
