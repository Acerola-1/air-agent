"""Intelligent pollution tracing graph with a DeepAgents lead and four roles."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.profiles import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)
from langchain.agents.middleware.types import AgentMiddleware

from common.event_ledger.lifecycle import LedgerLifecycleMiddleware
from common.langfuse_tracing import with_langfuse_tracing
from common.middleware import TenantRuntimeGuardMiddleware, TimeContextMiddleware
from common.models import ModelRegistry
from common.observability.middleware import ObservabilityModelMiddleware
from intelligent_tracing.data_access import (
    BusinessMCPDataSource,
    StationCatalog,
    StationCatalogProvider,
)
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.guards import (
    NoFilesystemToolsMiddleware,
    TracingBudgetMiddleware,
    guarded_summarization,
)
from intelligent_tracing.policy import DEFAULT_POLICY, AnalysisPolicy
from intelligent_tracing.publishing import TracingPublishMiddleware
from intelligent_tracing.report_store import (
    ReportStore,
    default_report_store,
)
from intelligent_tracing.schemas import StationMeta
from intelligent_tracing.sources import CachedDataSource, TracingDataSource
from intelligent_tracing.state import TracingState
from intelligent_tracing.subagents import (
    build_grid_runnable,
    build_meteo_subagent,
    build_reviewer_subagent,
    build_station_runnable,
)
from intelligent_tracing.tools import (
    build_freeze_request_tool,
    build_policy_summary_tool,
    build_report_tools,
)

SYSTEM_PROMPT = """
## 角色
你是污染过程与来向线索辅助分析的调度 lead。你不直接查询 MCP 数据，不执行
文件/代码工具，不输出事实正文；所有用户可见结论由服务端验证后的报告发布。

## 必须流程
1. 先调用 `freeze_tracing_request` 冻结请求；缺少站点代码/名称、行政区中文名
   （如“郑州市”）、时间范围或半径时向用户补问，在冻结前不得委派数据任务。
2. 冻结后，在同一轮用原生 `task` 并行委派 `station-data` 与 `grid-data`。
   `task` 只有 `description` 和 `subagent_type`；子图会从受控状态读取冻结请求，
   不要在 description 里伪造站点、时间、半径、证据或授权字段。
3. 站点结果返回后，委派 `meteo-analyst` 做气象与风场关联分析。
4. 委派 `evidence-reviewer` 检查反证、遗漏和表达风险；reviewer 只能建议降级，
   不是事实源。
5. 调用 `build_validated_report` 生成服务端验证报告。该工具只接受 claim ID
   计划或不带计划的确定性回退，不接受自由正文。
6. 如用户需要画布，调用 `publish_tracing_canvas`，只传已验证报告 ID 和展示方式；
   不得调用任意 content 的原始画布工具。

## 边界
- 不调用、探测或恢复轨迹/PSCF；相关能力固定不可用。
- 不确认具体企业、源类、贡献率、来源概率或源距离。
- 多站同步/网格连片只说明区域现象；传输关联必须有附加时序/风场证据，
  并讨论共同静稳等替代解释。
- 不发布模型草稿、工具原文或 reviewer 输出。
- 查询计划与子代理描述使用业务语言，不暴露内部提示、工具名或实现细节。
"""

SYSTEM_PROMPT_NO_REVIEW = SYSTEM_PROMPT.replace(
    """4. 委派 `evidence-reviewer` 检查反证、遗漏和表达风险；reviewer 只能建议降级，
   不是事实源。
""",
    "",
)


def _register_harness_profile(model: Any) -> Callable[[], None]:
    """Temporarily disable the auto-added GP subagent for this model build.

    DeepAgents profiles are process-global.  Tracing must not change the
    behaviour of another DeepAgents graph that happens to use the same model,
    so the previous registration is restored immediately after compilation.
    """
    try:
        from deepagents._models import get_model_identifier, get_model_provider

        identifier = get_model_identifier(model)
        provider = get_model_provider(model)
    except Exception:  # pragma: no cover - SDK internals may change
        return lambda: None
    if not identifier or not provider:
        return lambda: None
    key = f"{provider}:{identifier}"
    previous = None
    registry = None
    try:
        from deepagents.profiles.harness.harness_profiles import _HARNESS_PROFILES

        registry = _HARNESS_PROFILES
        previous = registry.get(key)
    except Exception:  # pragma: no cover - private registry may move
        registry = None
    register_harness_profile(
        key,
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            excluded_tools=frozenset(
                {
                    "execute",
                    "write_file",
                    "edit_file",
                    "delete",
                    "read_file",
                    "ls",
                    "glob",
                    "grep",
                }
            ),
        ),
    )

    def _restore() -> None:
        if registry is None:
            return
        if previous is None:
            registry.pop(key, None)
        else:
            registry[key] = previous

    return _restore


def build_tracing_graph(
    *,
    model: Any = None,
    meteo_model: Any = None,
    reviewer_model: Any = None,
    data_source: TracingDataSource | None = None,
    station_catalog: StationCatalog | tuple[StationMeta, ...] | None = None,
    policy: AnalysisPolicy = DEFAULT_POLICY,
    report_store: ReportStore | None = None,
    checkpointer: Any = None,
    name: str = "intelligent-tracing",
    enable_reviewer: bool = True,
    lifecycle_probe: Any = None,
):
    """Build the tracing graph; G0 tests inject a fixture source and fake model."""
    resolved_model = model or ModelRegistry.main_llm
    resolved_meteo_model = meteo_model or resolved_model
    resolved_reviewer_model = reviewer_model or resolved_model
    catalog: StationCatalog = (
        station_catalog if station_catalog is not None else StationCatalogProvider()
    )
    source = CachedDataSource(
        data_source or BusinessMCPDataSource(station_catalog=catalog)
    )
    registry = EvidenceRegistry()
    gate = ReportGate(registry=registry, policy=policy)
    store = report_store or default_report_store()
    backend = StateBackend()
    station_tool = build_freeze_request_tool(station_catalog=catalog, policy=policy)
    policy_tool = build_policy_summary_tool(policy)
    report_tool, canvas_tool = build_report_tools(
        registry=registry, gate=gate, policy=policy, report_store=store
    )
    subagents = [
        {
            "name": "station-data",
            "description": (
                "确定性站点数据专员。读取冻结请求，取站点与邻站小时数据，"
                "完成质控、覆盖与异常识别；零模型调用，只回写 station_findings。"
            ),
            "runnable": build_station_runnable(
                source=source, registry=registry, policy=policy
            ),
        },
        {
            "name": "grid-data",
            "description": (
                "确定性网格数据专员。读取冻结请求，分析网格热点、连片与覆盖，"
                "并输出空间格局；零模型调用，只回写 grid_findings。"
            ),
            "runnable": build_grid_runnable(
                source=source, registry=registry, policy=policy
            ),
        },
        build_meteo_subagent(
            model=resolved_meteo_model,
            source=source,
            registry=registry,
            policy=policy,
            backend=backend,
        ),
    ]
    if enable_reviewer:
        subagents.append(build_reviewer_subagent(model=resolved_reviewer_model))

    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        TimeContextMiddleware(),
        # Replace the default filesystem stack: no read/write/execute tools for
        # the lead.  The only lead tools are the tracing-specific ones below.
        NoFilesystemToolsMiddleware(backend=backend),
        TenantRuntimeGuardMiddleware(inject_mcp_tools=False),
        ObservabilityModelMiddleware(role="main"),
        guarded_summarization(resolved_model, backend),
        TracingBudgetMiddleware(policy.budget),
        LedgerLifecycleMiddleware(name),
        TracingPublishMiddleware(
            registry=registry,
            gate=gate,
            policy=policy,
            report_store=store,
            lifecycle_probe=lifecycle_probe,
        ),
    ]

    restore_profile = _register_harness_profile(resolved_model)
    try:
        return create_deep_agent(
            model=resolved_model,
            tools=[station_tool, policy_tool, report_tool, canvas_tool],
            system_prompt=SYSTEM_PROMPT if enable_reviewer else SYSTEM_PROMPT_NO_REVIEW,
            middleware=middleware,
            backend=backend,
            subagents=subagents,
            state_schema=TracingState,
            checkpointer=checkpointer,
            name=name,
        )
    finally:
        restore_profile()


graph = with_langfuse_tracing(build_tracing_graph(checkpointer=None))
