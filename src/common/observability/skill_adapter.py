"""Skill阶段事实：只在实际加载和执行位置记录，不以Graph名称代替Skill."""

from __future__ import annotations

import hashlib
import threading
import uuid
from typing import Any

from common.observability.collector import WorkItem, get_collector
from common.observability.context import observation_context_from_config
from common.observability.store import observability_enabled, record_span

_LOADED: dict[tuple[str, str, str], dict[str, Any]] = {}
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
    selection_id: str | None = None,
    selection_digest: str | None = None,
    submission_id: str | None = None,
) -> None:
    """采集白名单名称和内容摘要，有界非阻塞入队，正文不离开调用点."""
    if not observability_enabled():
        return
    ctx = observation_context_from_config(config)
    if ctx is None:
        return
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    sel_id = (
        selection_id
        or configurable.get("capability_selection_id")
        or getattr(ctx, "selection_id", None)
    )
    sel_digest = (
        selection_digest
        or configurable.get("capability_selection_digest")
        or getattr(ctx, "selection_digest", None)
    )
    sub_id = (
        submission_id
        or getattr(ctx, "submission_id", None)
        or configurable.get("submission_id")
    )
    version = version or (hashlib.sha256(body.encode()).hexdigest() if body else None)
    key = (ctx.tenant_id, ctx.attempt_id, ctx.submission_id or "")
    if stage == "loaded" and status == "succeeded":
        with _LOCK:
            _LOADED.setdefault(key, {})[skill_id] = {
                "version": version,
                "source": source,
                "selection_id": sel_id,
                "selection_digest": sel_digest,
                "submission_id": sub_id,
            }
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
            selection_id=sel_id,
            selection_digest=sel_digest,
            submission_id=sub_id,
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
        loaded = _LOADED.pop(key, None)
        loaded = loaded or {}
    for skill, info in loaded.items():
        if isinstance(info, dict):
            version = info.get("version")
            source = info.get("source") or "manual"
            sel_id = info.get("selection_id")
            sel_digest = info.get("selection_digest")
            sub_id = info.get("submission_id")
        else:
            version = info
            source = "manual"
            sel_id = None
            sel_digest = None
            sub_id = None
        emit_skill_stage(
            config,
            skill,
            "executed",
            status="succeeded" if succeeded else "failed",
            version=version,
            source=source,
            selection_id=sel_id,
            selection_digest=sel_digest,
            submission_id=sub_id,
        )
