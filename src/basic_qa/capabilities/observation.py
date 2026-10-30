"""Capability lifecycle facts without prompts, tool arguments or result bodies."""

from __future__ import annotations

import json
import uuid

from langchain_core.messages import ToolMessage
from langgraph.types import Command


def emit_fact(
    config,
    *,
    kind,
    action,
    ref,
    status,
    capability_id=None,
    version=None,
    sources=(),
    candidates=(),
    catalog_version=None,
    embedding_counts=None,
):
    """Queue one typed fact; collection failure never changes business results."""
    from common.observability.collector import WorkItem, get_collector
    from common.observability.context import observation_context_from_config
    from common.observability.store import observability_enabled, record_capability_fact

    if not observability_enabled():
        return
    ctx = observation_context_from_config(config)
    if ctx is None:
        return
    observation = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{ctx.tenant_id}:{ctx.attempt_id}:{kind}:{action}:{capability_id}:{ref}",
        )
    )
    payload = {
        "attempt_id": ctx.attempt_id,
        "observation_id": observation,
        "capability_kind": kind,
        "action": action,
        "status": status,
    }
    for key, value in (
        ("capability_id", capability_id),
        ("capability_version", version),
        ("catalog_version", catalog_version),
    ):
        if value:
            payload[key] = value
    if sources:
        payload["source_ids"] = sorted(set(sources))
    if action == "searched":
        payload.update(candidate_ids=list(candidates), candidate_count=len(candidates))
    if embedding_counts:
        for name in ("documents", "queries"):
            if name in embedding_counts:
                payload[
                    f"embedding_{'document' if name == 'documents' else 'query'}_count"
                ] = embedding_counts[name]

    async def write():
        await record_capability_fact(ctx, payload)

    try:
        get_collector().enqueue(WorkItem("capability_fact", ctx, write))
    except Exception:  # noqa: BLE001 - best-effort observability
        pass


def observe_control(config, request, result):
    """Inspect only server control receipts, never arbitrary model arguments."""
    name = request.tool_call.get("name")
    if name not in {"search_skills", "search_mcp", "load_skill", "load_mcp_tool"}:
        return
    messages = (
        result.update.get("messages", []) if isinstance(result, Command) else [result]
    )
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(message.content)
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        ref = message.tool_call_id
        kind = "skill" if name in {"search_skills", "load_skill"} else "mcp_tool"
        if name.startswith("search_"):
            candidates = [
                c.get("skill_id" if kind == "skill" else "tool_id")
                for c in payload.get("candidates", [])
            ]
            candidates = [c for c in candidates if c]
            emit_fact(
                config,
                kind=kind,
                action="searched",
                ref=ref,
                status="succeeded" if payload.get("status") == "ok" else "failed",
                candidates=candidates,
                catalog_version=payload.get("catalog_version"),
                embedding_counts=payload.get("embedding_counts"),
            )
            if kind == "skill":
                from common.observability.skill_adapter import emit_skill_stage

                for candidate in payload.get("candidates", []):
                    emit_skill_stage(
                        config,
                        candidate["skill_id"],
                        "matched",
                        version=candidate.get("material_version"),
                        source="model-search",
                    )
            continue
        status = payload.get("status")
        if message.status == "error" or status not in {
            "loaded",
            "activated",
            "already_active",
        }:
            emit_fact(config, kind=kind, action="load_failed", ref=ref, status="failed")
            continue
        target = payload.get("skill_id" if kind == "skill" else "tool_id")
        version = payload.get(
            "material_version" if kind == "skill" else "schema_version"
        )
        emit_fact(
            config,
            kind=kind,
            action="selected",
            ref=ref,
            status="succeeded",
            capability_id=target,
            version=version,
        )
        emit_fact(
            config,
            kind=kind,
            action="reused" if status == "already_active" else status,
            ref=ref,
            status="succeeded",
            capability_id=target,
            version=version,
        )
        if kind == "skill" and status == "loaded":
            from common.observability.skill_adapter import emit_skill_stage

            emit_skill_stage(
                config, target, "selected", version=version, source="model-load"
            )
            emit_skill_stage(
                config,
                target,
                "loaded",
                version=version,
                source="model-load",
                status="succeeded",
            )


def observe_execution(config, request, result):
    """Record skill sources only when their actual business tool executed."""
    if isinstance(result, Command):
        for message in result.update.get("messages", []):
            observe_execution(config, request, message)
        return
    from basic_qa.capabilities.projection import mcp_stable_id
    from basic_qa.capabilities.runtime import trusted_turn_key
    from common.observability.skill_adapter import emit_skill_stage
    from common.observability.tool_adapter import classify_tool_output

    if not isinstance(result, ToolMessage):
        return
    if result.additional_kwargs.get("network_attempted") is not True:
        return
    tool_id = mcp_stable_id(request.tool_call.get("name", ""))
    state = getattr(request, "state", None) or {}
    record = state.get("bqa_disclosure") or {}
    sources = record.get("sources", {}).get(tool_id, [])
    if not tool_id or not sources:
        return
    # A scope denial is a paired tool result, but never an execution fact.
    try:
        payload = json.loads(result.content)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict) and str(payload.get("error_code", "")).startswith(
        "skill_"
    ):
        return
    status = (
        "succeeded"
        if result.status != "error"
        and classify_tool_output(result.content) == "succeeded"
        else "failed"
    )
    caps = state.get("bqa_capabilities") or {}
    attempt = trusted_turn_key(config)
    emit_fact(
        config,
        kind="mcp_tool",
        action="executed",
        ref=result.tool_call_id,
        status=status,
        capability_id=tool_id,
        version=record.get("schema_versions", {}).get(tool_id),
        sources=sources,
    )
    for source in sources:
        independent = source == "independent"
        ref = (
            caps.get("loaded_tools", {}).get(tool_id, {})
            if independent
            else caps.get("loaded_skills", {}).get(source, {})
        )
        kind = "mcp_tool" if independent else "skill"
        target = tool_id if independent else source
        version = ref.get("schema_version" if independent else "material_version")
        if ref.get("protected_attempt") != attempt:
            emit_fact(
                config,
                kind=kind,
                action="reused",
                ref=attempt,
                status="succeeded",
                capability_id=target,
                version=version,
            )
        if not independent:
            emit_fact(
                config,
                kind="skill",
                action="executed",
                ref=result.tool_call_id,
                status=status,
                capability_id=source,
                version=version,
            )
            emit_skill_stage(
                config,
                source,
                "executed",
                version=version,
                source="session-tool",
                status=status,
            )
