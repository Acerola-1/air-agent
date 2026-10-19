"""Lead-facing tracing tools: freeze request, build report, publish canvas."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Literal

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict

from intelligent_tracing.claims import build_claim_allowlist, fallback_plan
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.process_assessment import assess_process
from intelligent_tracing.report_store import ReportStore
from intelligent_tracing.request import (
    RequestValidationError,
    resolve_station,
    validate_and_freeze_request,
)
from intelligent_tracing.runtime import (
    TracingScopeError,
    runtime_scope_from_config,
)
from intelligent_tracing.schemas import (
    ReportPlan,
    StationMeta,
    TimeWindow,
    TracingRequest,
)


class FreezeRequestInput(BaseModel):
    """Candidate fields accepted from natural-language request parsing.

    ``extra="ignore"`` lets the tool node inject its non-model ``runtime``
    argument; domain validation remains strict in ``TracingRequest``.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    station_code: str = ""
    station_name: str = ""
    region_code: str = ""
    pollutant: str = "pm2.5"
    start: datetime
    end: datetime
    timezone: str = "Asia/Shanghai"
    neighbor_radius_km: float | None = None
    baseline_start: datetime | None = None
    baseline_end: datetime | None = None
    analysis_id: str
    revision: int = 1
    capability_snapshot_ref: str = ""


class BuildReportInput(BaseModel):
    """Optional model-proposed claim plan; no free text."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    plan: ReportPlan | None = None


class PublishCanvasInput(BaseModel):
    """Validated-report reference plus display option; no raw content."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    report_id: str
    open_in: Literal["canvas_window", "inline_below", "modal", "sidebar"] = (
        "canvas_window"
    )


def _config_from_runtime(runtime: ToolRuntime) -> dict[str, Any]:
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        return config
    try:
        from langgraph.config import get_config

        return dict(get_config() or {})
    except Exception:  # pragma: no cover - offline direct calls
        return {}


def build_freeze_request_tool(
    *,
    station_catalog: tuple[StationMeta, ...],
    policy: AnalysisPolicy,
    allow_missing_capability: bool = False,
) -> StructuredTool:
    """Build the request-freezing tool; it performs no data query."""

    async def _freeze(
        runtime: ToolRuntime,
        station_code: str = "",
        station_name: str = "",
        region_code: str = "",
        pollutant: str = "pm2.5",
        start: datetime | None = None,
        end: datetime | None = None,
        timezone: str = "Asia/Shanghai",
        neighbor_radius_km: float | None = None,
        baseline_start: datetime | None = None,
        baseline_end: datetime | None = None,
        analysis_id: str = "",
        revision: int = 1,
        capability_snapshot_ref: str = "",
    ) -> Command | dict[str, Any]:
        try:
            candidate = FreezeRequestInput(
                station_code=station_code,
                station_name=station_name,
                region_code=region_code,
                pollutant=pollutant,
                start=start,  # type: ignore[arg-type]
                end=end,  # type: ignore[arg-type]
                timezone=timezone,
                neighbor_radius_km=neighbor_radius_km,
                baseline_start=baseline_start,
                baseline_end=baseline_end,
                analysis_id=analysis_id,
                revision=revision,
                capability_snapshot_ref=capability_snapshot_ref,
            )
            resolution = resolve_station(
                station_code=candidate.station_code,
                station_name=candidate.station_name,
                region_code=candidate.region_code,
                catalog=station_catalog,
            )
        except (RequestValidationError, ValueError) as exc:
            return {"status": "invalid", "message": str(exc)}
        if resolution.ambiguous:
            return {
                "status": "needs_disambiguation",
                "message": "站点名称对应多个候选，请确认站点代码或更具体区域；未发起数据查询。",
                "candidates": [item.__dict__ for item in resolution.candidates],
            }
        if resolution.target is None:
            return {"status": "invalid", "message": "未能解析站点"}
        if candidate.neighbor_radius_km is None:
            return {
                "status": "needs_clarification",
                "missing": ["neighbor_radius_km"],
                "message": "请确认调查半径（1–50 km）；未发起数据查询。",
            }
        baseline = None
        if candidate.baseline_start and candidate.baseline_end:
            try:
                baseline = TimeWindow(
                    start=candidate.baseline_start,
                    end=candidate.baseline_end,
                    timezone=candidate.timezone,
                )
            except ValueError as exc:
                return {"status": "invalid", "message": f"基线窗口无效：{exc}"}
        try:
            request = TracingRequest(
                analysis_id=candidate.analysis_id,
                revision=candidate.revision,
                target=resolution.target,
                pollutant=candidate.pollutant,
                time_window=TimeWindow(
                    start=candidate.start,
                    end=candidate.end,
                    timezone=candidate.timezone,
                ),
                neighbor_radius_km=candidate.neighbor_radius_km,
                baseline_window=baseline,
                analysis_policy_version=policy.version,
                capability_snapshot_ref=candidate.capability_snapshot_ref,
            )
            frozen = validate_and_freeze_request(
                request,
                policy=policy,
                config=_config_from_runtime(runtime),
                allow_missing_capability=allow_missing_capability,
            )
            config = _config_from_runtime(runtime)
            scope = runtime_scope_from_config(
                config,
                analysis_id=frozen.analysis_id,
            )
        except (RequestValidationError, ValueError, TracingScopeError) as exc:
            return {"status": "invalid", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 - tool boundary must be explicit
            return {
                "status": "invalid",
                "message": f"{type(exc).__name__}: {exc}",
            }
        tool_call_id = str(getattr(runtime, "tool_call_id", "") or "freeze")
        return Command(
            update={
                "request": frozen,
                "scope": scope,
                "messages": [
                    ToolMessage(
                        content=json.dumps(
                            {
                                "status": "frozen",
                                "analysis_id": frozen.analysis_id,
                                "revision": frozen.revision,
                                "thread_id": scope.thread_id,
                            },
                            ensure_ascii=False,
                        ),
                        tool_call_id=tool_call_id,
                        name="freeze_tracing_request",
                    )
                ],
            }
        )

    return StructuredTool.from_function(
        coroutine=_freeze,
        name="freeze_tracing_request",
        description=(
            "校验并冻结溯源请求。必须在任何数据委派前调用；缺少站点、时间或"
            "半径时返回需要补问，不发起数据查询。"
        ),
        args_schema=FreezeRequestInput,
        infer_schema=False,
    )


def build_policy_summary_tool(policy: AnalysisPolicy) -> StructuredTool:
    """Expose a compact policy summary; no file or network access."""

    def _summary() -> str:
        return (
            f"策略版本={policy.version}；"
            f"异常robust_z={policy.baseline.robust_z_threshold}，"
            f"绝对变化下限={policy.baseline.min_abs_delta}，"
            f"最短持续={policy.baseline.min_duration_hours}小时；"
            f"调查半径={policy.spatial.default_neighbor_radius_km}km；"
            f"扇区={policy.wind.sector_width_degrees}度；"
            f"静风阈值={policy.wind.calm_speed_threshold}m/s；"
            "固定限制=trajectory_unavailable,emission_inventory_missing,"
            "no_chemical_composition。"
        )

    return StructuredTool.from_function(
        func=_summary,
        name="get_analysis_policy_summary",
        description="返回冻结的 analysis-policy-v1 阈值、口径和固定限制。",
        infer_schema=False,
    )


def build_report_tools(
    *,
    registry: EvidenceRegistry,
    gate: ReportGate,
    policy: AnalysisPolicy,
    report_store: ReportStore,
) -> tuple[StructuredTool, StructuredTool]:
    """Build deterministic report construction and canvas publication tools."""

    async def _build(
        runtime: ToolRuntime, plan: ReportPlan | None = None
    ) -> Command | dict[str, Any]:
        state = runtime.state
        request = state.get("request")
        scope = state.get("scope")
        station = state.get("station_findings")
        spatial = state.get("grid_findings")
        meteo = state.get("meteo_findings")
        if request is None or scope is None:
            return {"status": "not_ready", "missing": ["request_or_scope"]}
        if station is None or spatial is None or meteo is None:
            missing = [
                name
                for name, value in (
                    ("station_findings", station),
                    ("grid_findings", spatial),
                    ("meteo_findings", meteo),
                )
                if value is None
            ]
            return {"status": "not_ready", "missing": missing}
        if any(
            item.analysis_id != request.analysis_id or item.revision != request.revision
            for item in (station, spatial, meteo)
        ):
            return {"status": "stale", "missing": ["findings_revision_mismatch"]}
        process = assess_process(request, {}, station, spatial, meteo, policy=policy)
        allowlist = build_claim_allowlist(
            request,
            station,
            spatial,
            meteo,
            process,
            policy=policy,
            extra_limitations=("budget_exhausted",)
            if state.get("budget_exhausted")
            else (),
        )
        candidate_plan = plan or fallback_plan(allowlist)
        result = gate.validate_and_render(candidate_plan, allowlist, request, scope)
        used_fallback = False
        if not result.ok:
            fallback_result = gate.validate_and_render(
                fallback_plan(allowlist), allowlist, request, scope
            )
            if not fallback_result.ok:
                return {
                    "status": "gate_failed",
                    "errors": list(fallback_result.errors),
                }
            result = fallback_result
            used_fallback = True
        report = result.report
        assert report is not None
        await report_store.save(report)
        summary = {
            "status": "validated",
            "report_id": report.report_id,
            "revision": report.revision,
            "used_fallback": used_fallback,
            "claim_count": len(report.claim_ids),
            "limitations": list(report.limitations),
            "message_id": report.report_id,
        }
        tool_call_id = getattr(runtime, "tool_call_id", None)
        messages = (
            [
                ToolMessage(
                    content=json.dumps(summary, ensure_ascii=False),
                    tool_call_id=tool_call_id,
                    name="build_validated_report",
                )
            ]
            if tool_call_id
            else []
        )
        return Command(
            update={
                "allowlist": allowlist,
                "validated_report": report,
                "messages": messages,
            }
        )

    async def _canvas(
        runtime: ToolRuntime,
        report_id: str,
        open_in: Literal["canvas_window", "inline_below", "modal", "sidebar"] = (
            "canvas_window"
        ),
    ) -> dict[str, Any]:
        state = runtime.state
        report = state.get("validated_report")
        scope = state.get("scope")
        if report is None or scope is None:
            return {"status": "not_ready", "message": "尚无已验证报告"}
        if report.report_id != report_id:
            return {"status": "invalid", "message": "报告引用与当前验证版本不一致"}
        if report.scope != scope:
            return {"status": "invalid", "message": "报告作用域不一致"}
        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        canvas = report.canvas.model_copy(update={"open_in": open_in})
        artifact_ref = {
            "artifact_id": artifact_id,
            "content_sha256": report.content_sha256,
            "title": canvas.title,
            "content_type": canvas.content_type,
            "content": canvas.content,
            "open_in": canvas.open_in,
            "auto_detected": False,
            "validated_report_ref": report.report_id,
        }
        storage_status = "unavailable"
        config = _config_from_runtime(runtime)
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        run_id = str(
            configurable.get("run_id")
            or metadata.get("run_id")
            or configurable.get("langgraph_run_id")
            or ""
        )
        import os

        if os.getenv("RUNTIME_DATABASE_URI") and run_id:
            try:
                from common.storage.artifact_db import save_tool_artifact

                stored_id = await save_tool_artifact(
                    tool_call_id=str(getattr(runtime, "tool_call_id", "") or ""),
                    tool_name="publish_tracing_canvas",
                    raw_payload=json.dumps(artifact_ref, ensure_ascii=False),
                    tenant_id=report.scope.tenant_id,
                    user_id=report.scope.user_id,
                    thread_id=report.scope.thread_id,
                    run_id=run_id,
                    summary=canvas.title,
                    kind="validated_canvas",
                    parent_artifact_id=report.report_id,
                    processing_metadata={
                        "validated_report_ref": report.report_id,
                        "content_sha256": report.content_sha256,
                    },
                )
                artifact_ref["artifact_id"] = stored_id
                storage_status = "stored"
            except Exception:  # noqa: BLE001 - DB outage must not leak raw errors
                storage_status = "unavailable"
        return {
            "status": "published",
            "artifact_ref": artifact_ref,
            "storage_status": storage_status,
        }

    build_tool = StructuredTool.from_function(
        coroutine=_build,
        name="build_validated_report",
        description=(
            "从受信发现生成服务端验证报告。只接受可选的 claim ID 计划；"
            "非法计划回退确定性模板，不重试模型洗白。"
        ),
        args_schema=BuildReportInput,
        infer_schema=False,
    )
    canvas_tool = StructuredTool.from_function(
        coroutine=_canvas,
        name="publish_tracing_canvas",
        description=(
            "按已验证报告 ID 发布服务端渲染画布。只接受报告 ID 和展示方式，"
            "不接受任意正文或 HTML。"
        ),
        args_schema=PublishCanvasInput,
        infer_schema=False,
    )
    return build_tool, canvas_tool
