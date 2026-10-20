"""Four tracing specialists: two deterministic, two isolated LLM roles."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, NotRequired, TypedDict

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from common.middleware.tenant_runtime_guard_middleware import (
    TenantRuntimeGuardMiddleware,
)
from common.observability.middleware import ObservabilityModelMiddleware
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.meteo_analysis import analyze_meteo
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.schemas import (
    ChildExecution,
    EvidenceReview,
    MeteoFindings,
    MeteoNarrative,
    RuntimeScope,
    SpatialFindings,
    StationFindings,
    TracingRequest,
)
from intelligent_tracing.sources import TracingDataSource
from intelligent_tracing.spatial_analysis import analyze_spatial
from intelligent_tracing.station_analysis import analyze_station


class TracingSubgraphState(TypedDict):
    """Shared state keys passed to compiled tracing specialists."""

    messages: Annotated[list[AnyMessage], add_messages]
    request: NotRequired[TracingRequest]
    scope: NotRequired[RuntimeScope]
    station_findings: NotRequired[StationFindings]
    grid_findings: NotRequired[SpatialFindings]
    meteo_findings: NotRequired[MeteoFindings]
    evidence_review: NotRequired[EvidenceReview]
    child_executions: NotRequired[tuple[ChildExecution, ...]]
    structured_response: NotRequired[Any]


def build_child_execution(
    *,
    role: str,
    request: TracingRequest,
    config: dict[str, Any] | None,
    started_at: datetime,
    status: str,
    error_class: str = "",
    tool_call_count: int = 0,
) -> ChildExecution:
    """Link a real subtask to the existing run/attempt; never mint a request."""
    cfg = dict(config or {})
    configurable = cfg.get("configurable") or {}
    metadata = cfg.get("metadata") or {}
    return ChildExecution(
        child_execution_id=f"child_{role}_{uuid.uuid4().hex[:12]}",
        role=role,  # type: ignore[arg-type]
        analysis_id=request.analysis_id,
        revision=request.revision,
        run_id=str(
            configurable.get("run_id")
            or metadata.get("run_id")
            or configurable.get("langgraph_run_id")
            or ""
        ),
        attempt_id=str(
            configurable.get("ledger_attempt_id")
            or metadata.get("ledger_attempt_id")
            or ""
        ),
        started_at=started_at,
        finished_at=datetime.now(UTC),
        status=status,  # type: ignore[arg-type]
        tool_call_count=tool_call_count,
        error_class=error_class,
    )


def current_run_config(runtime: Any = None) -> dict[str, Any]:
    """Read ambient run config; declarative middleware runtime may be sparse."""
    try:
        from langgraph.config import get_config

        config = dict(get_config() or {})
        if config:
            return config
    except Exception:  # pragma: no cover - offline direct calls
        pass
    config = getattr(runtime, "config", None)
    return dict(config) if isinstance(config, dict) else {}


def unavailable_station_findings(request: TracingRequest) -> StationFindings:
    """Return an explicit insufficient result for a failed station fetch."""
    from intelligent_tracing.schemas import Coverage

    return StationFindings(
        analysis_id=request.analysis_id,
        revision=request.revision,
        station_code=request.target.station_code,
        status="insufficient",
        coverage=Coverage(
            expected_hours=0,
            valid_hours=0,
            ratio=0.0,
            max_gap_hours=0,
            duplicates=0,
            invalid_rows=0,
            sufficient=False,
        ),
        limitations=("station_data_unavailable", "coverage_insufficient"),
    )


def unavailable_spatial_findings(request: TracingRequest) -> SpatialFindings:
    """Return an explicit unknown pattern for a failed grid fetch."""
    return SpatialFindings(
        analysis_id=request.analysis_id,
        revision=request.revision,
        pattern="unknown",
        target_anomalous=False,
        valid_neighbor_count=0,
        limitations=("grid_data_unavailable",),
    )


def unavailable_meteo_findings(request: TracingRequest) -> MeteoFindings:
    """Return an explicit meteo-unavailable result; never invent weather."""
    return MeteoFindings(
        analysis_id=request.analysis_id,
        revision=request.revision,
        pblh_available_ratio=0.0,
        low_pblh_ratio=0.0,
        calm_ratio=1.0,
        limitations=("meteo_data_unavailable", "pblh_unavailable"),
    )


def build_station_runnable(
    *,
    source: TracingDataSource,
    registry: EvidenceRegistry,
    policy: AnalysisPolicy,
):
    """Build the zero-model station-data CompiledSubAgent graph."""

    async def _run(
        state: TracingSubgraphState, config: RunnableConfig
    ) -> dict[str, Any]:
        request = state.get("request")
        scope = state.get("scope")
        if request is None or scope is None:
            raise ValueError("缺少已冻结请求或可信作用域，拒绝取数")
        started = datetime.now(UTC)
        failure = ""
        try:
            bundle = await source.fetch(request, scope=scope, policy=policy)
            findings = analyze_station(
                request,
                bundle,
                registry=registry,
                scope=scope,
                policy=policy,
            )
        except Exception as exc:  # noqa: BLE001 - partial failure must remain explicit
            findings = unavailable_station_findings(request)
            failure = type(exc).__name__
        summary = (
            f"站点分析完成：{findings.status}；"
            f"覆盖 {findings.coverage.valid_hours}/{findings.coverage.expected_hours} 小时；"
            f"证据 {len(findings.evidence_ids)} 条。"
        )
        return {
            "messages": [AIMessage(content=summary)],
            "station_findings": findings,
            "child_executions": (
                build_child_execution(
                    role="station-data",
                    request=request,
                    config=dict(config or {}),
                    started_at=started,
                    status="failed" if failure else "succeeded",
                    error_class=failure,
                ),
            ),
            "structured_response": findings,
        }

    builder = StateGraph(TracingSubgraphState)
    builder.add_node("run", _run)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile()


def build_grid_runnable(
    *,
    source: TracingDataSource,
    registry: EvidenceRegistry,
    policy: AnalysisPolicy,
):
    """Build the zero-model grid-data CompiledSubAgent graph."""

    async def _run(
        state: TracingSubgraphState, config: RunnableConfig
    ) -> dict[str, Any]:
        request = state.get("request")
        scope = state.get("scope")
        if request is None or scope is None:
            raise ValueError("缺少已冻结请求或可信作用域，拒绝取数")
        started = datetime.now(UTC)
        failure = ""
        try:
            bundle = await source.fetch(request, scope=scope, policy=policy)
            station = analyze_station(
                request,
                bundle,
                registry=registry,
                scope=scope,
                policy=policy,
            )
            spatial = analyze_spatial(
                request,
                bundle,
                station,
                registry=registry,
                scope=scope,
                policy=policy,
            )
        except Exception as exc:  # noqa: BLE001 - partial failure must remain explicit
            spatial = unavailable_spatial_findings(request)
            failure = type(exc).__name__
        summary = (
            f"空间分析完成：{spatial.pattern}；"
            f"有效邻站 {spatial.valid_neighbor_count} 个；"
            f"网格连片 {spatial.grid.contiguous_cell_count if spatial.grid else 0} 个。"
        )
        return {
            "messages": [AIMessage(content=summary)],
            "grid_findings": spatial,
            "child_executions": (
                build_child_execution(
                    role="grid-data",
                    request=request,
                    config=dict(config or {}),
                    started_at=started,
                    status="failed" if failure else "succeeded",
                    error_class=failure,
                ),
            ),
            "structured_response": spatial,
        }

    builder = StateGraph(TracingSubgraphState)
    builder.add_node("run", _run)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    return builder.compile()


class MeteoDeterministicMiddleware(AgentMiddleware):
    """Compute authoritative meteo findings and inject a verified summary.

    The subagent has no data tools.  The middleware fetches the frozen snapshot,
    computes wind/lag findings deterministically, injects a compact summary into
    the model's system message, and publishes the same findings on completion.
    """

    def __init__(
        self,
        *,
        source: TracingDataSource,
        registry: EvidenceRegistry,
        policy: AnalysisPolicy,
    ) -> None:
        """Bind the execution-layer dependencies."""
        super().__init__()
        self._source = source
        self._registry = registry
        self._policy = policy
        self._runs: dict[str, tuple[datetime, str, MeteoFindings]] = {}

    @staticmethod
    def _run_key(config: Any) -> str:
        cfg = dict(config or {}) if isinstance(config, dict) else {}
        configurable = cfg.get("configurable") or {}
        metadata = cfg.get("metadata") or {}
        return str(
            configurable.get("run_id")
            or metadata.get("run_id")
            or configurable.get("thread_id")
            or "default"
        )

    @staticmethod
    def _summary(findings: MeteoFindings) -> str:
        wind = findings.wind
        if wind is None:
            return "确定性气象摘要：风场证据不足或不可用，只能说明限制。"
        return (
            "确定性气象摘要（不得改写数值）："
            f"配对样本={wind.paired_samples}，静风比例={wind.calm_ratio:.2f}，"
            f"方向扇区覆盖={wind.sector_coverage:.2f}，"
            f"方向级结论={'可用' if wind.direction_claim_allowed else '不可用'}。"
        )

    async def _analyze(
        self, state: Any, config: dict[str, Any] | None
    ) -> tuple[datetime, str, MeteoFindings] | None:
        request = state.get("request") if hasattr(state, "get") else None
        scope = state.get("scope") if hasattr(state, "get") else None
        station = state.get("station_findings") if hasattr(state, "get") else None
        if request is None or scope is None or station is None:
            return None
        started = datetime.now(UTC)
        failure = ""
        try:
            bundle = await self._source.fetch(request, scope=scope, policy=self._policy)
            findings = analyze_meteo(
                request,
                bundle,
                station,
                registry=self._registry,
                scope=scope,
                policy=self._policy,
            )
        except Exception as exc:  # noqa: BLE001 - partial failure must remain explicit
            findings = unavailable_meteo_findings(request)
            failure = type(exc).__name__
        result = (started, failure, findings)
        self._runs[self._run_key(config)] = result
        return result

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Inject the deterministic summary before the analyst narrates."""
        config = current_run_config(getattr(request, "runtime", None))
        key = self._run_key(config)
        result = self._runs.get(key)
        if result is None:
            result = await self._analyze(getattr(request, "state", {}), config)
        if result is not None:
            summary = SystemMessage(content=self._summary(result[2]))
            existing = getattr(request, "system_message", None)
            content = existing.content if existing is not None else ""
            request = request.override(
                system_message=SystemMessage(
                    content=f"{content}\n\n{summary.content}".strip()
                )
            )
        return await handler(request)

    async def aafter_agent(
        self, state: TracingSubgraphState, runtime: Any = None
    ) -> dict[str, Any] | None:
        """Publish deterministic findings and the linked child execution."""
        request = state.get("request")
        if request is None:
            return None
        config = current_run_config(runtime)
        key = self._run_key(config)
        result = self._runs.pop(key, None)
        if result is None:
            result = await self._analyze(state, config)
        if result is None:
            return None
        started, failure, findings = result
        return {
            "meteo_findings": findings,
            "child_executions": (
                build_child_execution(
                    role="meteo-analyst",
                    request=request,
                    config=config,
                    started_at=started,
                    status="failed" if failure else "succeeded",
                    error_class=failure,
                ),
            ),
        }


class ReviewerChildExecutionMiddleware(AgentMiddleware):
    """Record the reviewer subtask without turning advice into a fact source."""

    async def aafter_agent(
        self, state: TracingSubgraphState, runtime: Any = None
    ) -> dict[str, Any] | None:
        """Append one reviewer child-execution record."""
        request = state.get("request")
        if request is None:
            return None
        return {
            "child_executions": (
                build_child_execution(
                    role="evidence-reviewer",
                    request=request,
                    config=current_run_config(runtime),
                    started_at=datetime.now(UTC),
                    status="succeeded",
                ),
            )
        }


def build_meteo_subagent(
    *,
    model: Any,
    source: TracingDataSource,
    registry: EvidenceRegistry,
    policy: AnalysisPolicy,
    backend: Any,
) -> dict[str, Any]:
    """Return the isolated declarative meteo-analyst spec."""
    from intelligent_tracing.guards import (
        NoFilesystemToolsMiddleware,
        guarded_summarization,
    )

    return {
        "name": "meteo-analyst",
        "description": (
            "隔离的气象分析专员。只使用受限气象工具和确定性风场/滞后分析；"
            "不得自行计算数值、扩大数据选择或提升结论等级。"
        ),
        "model": model,
        "tools": [],
        "system_prompt": (
            "你是污染溯源的气象分析专员。系统已给出确定性气象摘要，所有数值以"
            "摘要为准，不得自行推算、补齐缺测、推断源距离或确认污染来源。只返回"
            "对分析有用的气象条件、风场一致性、滞后关联、反证与限制。"
        ),
        "response_format": MeteoNarrative,
        "middleware": [
            NoFilesystemToolsMiddleware(),
            TenantRuntimeGuardMiddleware(inject_mcp_tools=False),
            ObservabilityModelMiddleware(role="meteo"),
            guarded_summarization(model, backend),
            MeteoDeterministicMiddleware(
                source=source, registry=registry, policy=policy
            ),
        ],
    }


def build_reviewer_subagent(*, model: Any) -> dict[str, Any]:
    """Return the isolated evidence-reviewer spec with no data/file tools."""
    from deepagents.backends import StateBackend

    from intelligent_tracing.guards import (
        NoFilesystemToolsMiddleware,
        guarded_summarization,
    )

    return {
        "name": "evidence-reviewer",
        "description": (
            "隔离的证据审查专员。查找反证、冲突、遗漏和表达风险，只能建议降级；"
            "不执行数据、文件或代码工具，不决定最高证据权限，不提供新事实。"
        ),
        "model": model,
        "tools": [],
        "system_prompt": (
            "你是污染溯源报告的独立审查员。只根据任务描述中给出的结构化摘要，"
            "指出反证、冲突、遗漏、替代解释和可能过度的表达。不要新增事实、数值、"
            "证据 ID 或来源结论；不要建议提升证据等级；不得确认企业、源类或贡献率。"
            "输出结构化审查结果。"
        ),
        "response_format": EvidenceReview,
        "middleware": [
            NoFilesystemToolsMiddleware(),
            TenantRuntimeGuardMiddleware(inject_mcp_tools=False),
            ObservabilityModelMiddleware(role="reviewer"),
            guarded_summarization(model, StateBackend()),
            ReviewerChildExecutionMiddleware(),
        ],
    }
