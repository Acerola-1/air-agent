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
from pydantic import BaseModel, ConfigDict, Field

from common.identity.runtime import identity_from_config
from intelligent_tracing.claims import build_claim_allowlist, fallback_plan
from intelligent_tracing.data_access import (
    StaticStationCatalog,
    StationCatalog,
)
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
    business_binding_from_config,
    runtime_scope_from_config,
)
from intelligent_tracing.schemas import (
    KnowledgeBasisItem,
    PublicationSession,
    ReportPlan,
    StationMeta,
    TimeWindow,
    TracingRequest,
)
from intelligent_tracing.section_publishing import (
    SectionPublicationError,
    publication_execution_key,
    publish_frozen_plan,
)
from intelligent_tracing.sources import DataSourceUnavailable


class _EmptyArgs(BaseModel):
    """Tool with no model-provided arguments."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class FreezeRequestInput(BaseModel):
    """Candidate fields accepted from natural-language request parsing.

    ``extra="ignore"`` lets the tool node inject its non-model ``runtime``
    argument; domain validation remains strict in ``TracingRequest``.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    station_code: str = Field(default="", description="站点代码；与站点名称二选一")
    station_name: str = Field(
        default="", description="站点名称，例如“北区建设指挥部”，不要包含城市名"
    )
    region_code: str = Field(default="", description="行政区代码（可选）")
    region_name: str = Field(
        default="", description="行政区中文名，例如“郑州市”；不要填站点名称"
    )
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


def run_id_from_config(config: dict[str, Any]) -> str:
    """Return the platform run id from trusted config (never from tool input)."""
    configurable = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    return str(
        configurable.get("run_id")
        or metadata.get("run_id")
        or configurable.get("langgraph_run_id")
        or ""
    )


def _normalize_region_and_station(
    region_name: str, station_name: str
) -> tuple[str, str]:
    """Split a combined "郑州市某站" input into region and station parts."""
    region = (region_name or "").strip()
    station = (station_name or "").strip()
    for separator in ("市", "区", "县"):
        index = region.find(separator)
        if index > 0 and index + 1 < len(region):
            prefix = region[: index + 1]
            if station.startswith(prefix) and len(station) > len(prefix):
                station = station[len(prefix) :].strip()
            return prefix, station
    return region, station


def build_freeze_request_tool(
    *,
    station_catalog: StationCatalog | tuple[StationMeta, ...],
    policy: AnalysisPolicy,
) -> StructuredTool:
    """Build the request-freezing tool; it performs no data query.

    ``station_catalog`` 可以是受控静态目录（G0）或真实授权目录提供者；
    真实提供者在冻结时才经统一执行层读取目录，构造工具不会发起网络请求。
    """
    provider: StationCatalog = (
        StaticStationCatalog(station_catalog)
        if isinstance(station_catalog, tuple)
        else station_catalog
    )

    async def _freeze(
        runtime: ToolRuntime,
        station_code: str = "",
        station_name: str = "",
        region_code: str = "",
        region_name: str = "",
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
                region_name=region_name,
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
            config = _config_from_runtime(runtime)
            if identity_from_config(config) is None:
                raise TracingScopeError("缺少已认证运行身份，无法冻结溯源请求")
            binding = business_binding_from_config(config)
            region_name, station_name = _normalize_region_and_station(
                candidate.region_name, candidate.station_name
            )
            catalog = await provider.list_stations(
                config=config, region_name=region_name
            )
            resolution = resolve_station(
                station_code=candidate.station_code,
                station_name=station_name,
                region_code=candidate.region_code,
                region_name=region_name,
                catalog=catalog,
            )
        except TracingScopeError as exc:
            return {"status": "invalid", "message": str(exc)}
        except DataSourceUnavailable:
            return {"status": "catalog_unavailable", "message": "站点目录暂时不可用"}
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
                business_code=binding.business_code,
                business_policy_version=binding.business_policy_version,
                business_permission=binding.business_permission,
                graph_id=binding.graph_id,
            )
            config = _config_from_runtime(runtime)
            frozen = validate_and_freeze_request(
                request,
                policy=policy,
                binding=binding,
                config=config,
            )
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
            "校验并冻结溯源请求。必须在任何数据委派前调用；缺少站点、行政区"
            "中文名、时间或半径时返回需要补问，不发起数据查询。"
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
        description=f"返回冻结的 {policy.version} 阈值、口径和固定限制。",
        args_schema=_EmptyArgs,
        infer_schema=False,
    )


async def _resolve_knowledge_basis(
    plan: ReportPlan, *, scope: Any, config: dict[str, Any]
) -> tuple[KnowledgeBasisItem, ...] | dict[str, Any]:
    """Resolve model-selected citation ids against the protected store.

    The model never supplies document names, page numbers, URLs or excerpts.
    Any unknown/foreign/expired citation rejects the report plan instead of
    being silently dropped.
    """
    ids = tuple(dict.fromkeys(plan.knowledge_reference_ids))
    if not ids:
        return ()
    if len(ids) > 8:
        return {"status": "knowledge_reference_invalid", "errors": ["too_many_refs"]}
    identity = identity_from_config(config)
    if identity is None:
        return {
            "status": "knowledge_reference_invalid",
            "errors": ["identity_missing"],
        }
    from common.knowledge.store import (
        CitationBackendUnavailable,
        CitationNotFound,
        get_citation,
    )
    from common.knowledge.validation import KnowledgeValidationError, validate_citations

    items: list[KnowledgeBasisItem] = []
    from common.event_ledger.lifecycle import trusted_run_id

    run_id = trusted_run_id(config)
    for citation_id in ids:
        try:
            row = await get_citation(identity, citation_id)
        except (CitationNotFound, CitationBackendUnavailable):
            return {
                "status": "knowledge_reference_invalid",
                "errors": ["citation_not_found"],
            }
        thread_id = str(row.get("thread_id") or "")
        if (
            thread_id != scope.thread_id
            or not run_id
            or str(row.get("run_id") or "") != run_id
        ):
            return {
                "status": "knowledge_reference_invalid",
                "errors": ["citation_scope_mismatch"],
            }
        digest = str(row.get("source_sha256") or "")
        if len(digest) != 64:
            return {
                "status": "knowledge_reference_invalid",
                "errors": ["citation_digest_missing"],
            }
        try:
            await validate_citations(identity, [row])
        except KnowledgeValidationError:
            return {
                "status": "knowledge_reference_invalid",
                "errors": ["citation_source_invalid"],
            }
        items.append(
            KnowledgeBasisItem(
                citation_id=str(row["citation_id"]),
                doc_name=str(row.get("doc_name") or "未命名文档")[:500],
                excerpt=str(row.get("excerpt") or "")[:500],
                location=row.get("location"),
                location_status="provided" if row.get("location") else "not_provided",
                source_version=row.get("source_version"),
                source_sha256=digest,
            )
        )
    return tuple(items)


def build_report_tools(
    *,
    registry: EvidenceRegistry,
    gate: ReportGate,
    policy: AnalysisPolicy,
    report_store: ReportStore,
    section_barrier: Any = None,
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
        config = _config_from_runtime(runtime)
        run_id = run_id_from_config(config)
        knowledge_basis = await _resolve_knowledge_basis(
            candidate_plan, scope=scope, config=config
        )
        if isinstance(knowledge_basis, dict):
            return knowledge_basis

        report_id = f"rep_{uuid.uuid4().hex}"
        used_fallback = False

        async def prepare_publication():
            nonlocal used_fallback
            frozen, errors = gate.precheck(
                candidate_plan,
                allowlist,
                request,
                scope,
                report_id=report_id,
                run_id=run_id,
                knowledge_basis=knowledge_basis,
            )
            if frozen is None:
                # fallback 只替换 claim 计划，不悄悄丢弃已核验的知识引用.
                fallback = fallback_plan(allowlist).model_copy(
                    update={
                        "knowledge_reference_ids": candidate_plan.knowledge_reference_ids
                    }
                )
                frozen, fallback_errors = gate.precheck(
                    fallback,
                    allowlist,
                    request,
                    scope,
                    report_id=report_id,
                    run_id=run_id,
                    knowledge_basis=knowledge_basis,
                )
                if frozen is None:
                    raise SectionPublicationError(
                        "precheck_failed", errors=tuple(fallback_errors or errors)
                    )
                used_fallback = True
            return frozen

        writer = getattr(runtime, "stream_writer", None)
        try:
            session, report = await publish_frozen_plan(
                gate=gate,
                report_store=report_store,
                prepare=prepare_publication,
                expected_scope=scope,
                config=config,
                writer=writer,
                session=state.get("publication_session"),
                section_barrier=section_barrier,
            )
        except SectionPublicationError as exc:
            summary = {
                "status": "publication_failed",
                "reason": exc.reason,
                "errors": list(exc.errors),
                "report_id": report_id,
            }
            failed_session = exc.session or PublicationSession(
                report_id=report_id,
                plan_digest="precheck_failed",
                analysis_id=request.analysis_id,
                revision=request.revision,
                scope=scope,
                section_ids=(),
            )
            failed_session = failed_session.model_copy(
                update={
                    "failed_reason": exc.reason,
                    "execution_key": publication_execution_key(config, scope),
                }
            )
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
                update={"publication_session": failed_session, "messages": messages}
            )
        summary = {
            "status": "validated",
            "report_id": report.report_id,
            "revision": report.revision,
            "used_fallback": used_fallback,
            "claim_count": len(report.claim_ids),
            "limitations": list(report.limitations),
            "message_id": report.report_id,
            "plan_digest": session.plan_digest,
            "sections_published": len(session.published_section_ids),
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
                "publication_session": session,
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
        run_id = run_id_from_config(config)
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
