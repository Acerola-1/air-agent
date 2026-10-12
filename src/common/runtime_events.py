"""显式图的有身份状态事件."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langgraph.config import get_stream_writer


def event_payload(
    config: dict[str, Any], event_type: str, **values: Any
) -> dict[str, Any]:
    """构建带线程运行身份的事件."""
    configurable = config.get("configurable", {}) or {}
    metadata = config.get("metadata", {}) or {}
    return {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "processing_id": configurable.get("processing_run_id", ""),
        "type": event_type,
        "thread_id": configurable.get("thread_id", ""),
        "run_id": metadata.get("run_id")
        or configurable.get("run_id")
        or configurable.get("langgraph_run_id", ""),
        **values,
    }


def emit_event(
    config: dict[str, Any], event_type: str, **values: Any
) -> dict[str, Any]:
    """发布事件，独立调用时返回同一载荷."""
    payload = event_payload(config, event_type, **values)
    try:
        get_stream_writer()(payload)
    except RuntimeError:
        # 独立单元测试与非流式调用没有流 writer。
        pass
    return payload


def merge_processing_status(
    previous: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """合并并行工具状态，避免同一步多个 Command 冲突或提前宣称全体完成."""
    if not current:
        return previous
    states = (
        dict(previous.get("artifacts", {}))
        if previous.get("processing_id") == current.get("processing_id")
        else {}
    )
    key = str(current.get("artifact_id") or "phase")
    states[key] = {
        "status": current.get("status"),
        "message": current.get("message", ""),
    }
    statuses = [value["status"] for value in states.values()]
    status = (
        "failed"
        if "failed" in statuses
        else "started"
        if "started" in statuses
        else "completed"
    )
    return {**current, "status": status, "artifacts": states}
