"""业务图（basic_qa / intelligent_analysis）节点流的所有节点函数.

用显式节点替代 create_deep_agent 的中间件链：
- classify_intent: 入口意图分类（chitchat / knowledge / data_query）
- resolve_skill: 语义路由前置执行，命中规则直接内联，消除 find_skill 工具往返
- prepare_model: MCP 刷新 + 系统提示词一次性组装
- call_model: LLM 推理，决定调工具 / 直接输出（主答案直推 SSE）
- execute_tools: ToolNode，ReAct 循环

无 check_permission / finalize_output 节点: 权限审查已废弃, 主答案由 call_model 直推 SSE。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from loguru import logger

from common.business_graph.intent import (
    INTENT_CHITCHAT,
    INTENT_DATA_QUERY,
    INTENT_KNOWLEDGE,
    classify_intent_rule,
)
from common.business_graph.prompting import (
    build_chitchat_system_prompt,
    build_knowledge_system_prompt,
    build_system_prompt,
)
from common.business_graph.skill_content import load_skill_rules
from common.business_graph.state import BusinessGraphState
from common.config import config as app_config
from common.context import get_message_content, get_routing_context
from common.context_guard import prepare_model_context
from common.conversation_summarizer import update_conversation_summary
from common.data_governance import DataProcessingFailure, invoke_with_data_deadline
from common.mcp_client import ensure_mcp_tools
from common.model_budget import (
    ContextBudgetExceeded,
    enforce_request_budget,
    fit_tool_schemas,
    request_metrics,
)
from common.models import ModelRegistry
from common.output_contract import visible_result
from common.permission.rules import is_conversational
from common.runtime_events import emit_event
from common.runtime_tools import get_business_tools, tool_name
from common.skill_router import SkillRouteCandidate, SkillSemanticRouter

# 始终可见的本地工具名集合 (不被 skill allowed-tools 过滤掉).
# 知识检索工具已移除, 当前仅保留时间工具.
# 始终可见的工具：时间工具 + create_artifact 画布工具.
# load_skill 始终可见：多候选时模型可自主加载更匹配的 skill 规则，
# 单候选/零候选时模型无理由调用，不会产生副作用。
# create_artifact 是跨技能的通用画布输出能力，不受技能 allowed_tools 限制，
# 保证 Agent 在任何技能命中场景下都能自主产出 HTML/Markdown/SVG 画布内容.
_ALWAYS_VISIBLE_TOOL_NAMES: frozenset[str] = frozenset(
    {"get_beijing_time", "create_artifact", "load_skill", "compute_artifact"}
)

# 兜底消息常量 (模型调用失败 / 空回复时使用, 与 data_analysis/nodes.py 保持一致).
_SERVICE_UNAVAILABLE_MESSAGE = "抱歉，当前智能问答服务暂时不可用，请稍后再试。"
_EMPTY_REPLY_NUDGE = "请基于以上工具返回的数据，直接输出分析结论。"
_FALLBACK_EMPTY_REPLY = "服务暂不可用，请稍后重试。"


def _latest_human_content(messages: Sequence[Any]) -> str:
    """获取最近一条用户消息文本."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return get_message_content(message)
    return ""


def _candidate_summaries(
    candidates: Sequence[SkillRouteCandidate],
) -> list[dict[str, Any]]:
    """构造多候选摘要（除 top1 外的其他候选）."""
    return [
        {
            "skill_name": candidate.name,
            "description": candidate.description,
            "similarity_score": candidate.similarity_score,
            "allowed_tools": list(candidate.allowed_tools),
        }
        for candidate in candidates[1:]
    ]


class BusinessGraphNodes:
    """绑定路由器与系统提示词的业务图节点集合."""

    def __init__(
        self,
        *,
        router: SkillSemanticRouter,
        base_system_prompt: str,
        load_skill_tool: Any,
        graph_name: str,
    ) -> None:
        """初始化节点集合."""
        self._router = router
        self._base_system_prompt = base_system_prompt
        self._load_skill_tool = load_skill_tool
        self._graph_name = graph_name

    # ──── 意图车道节点 ────

    async def classify_intent(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """入口意图分类：决定 chitchat/knowledge/data_query 车道（纯规则，零 LLM）.

        不确定时 fail-open 到 data_query 全流程，快车道只对高置信信号开放。
        """
        question = _latest_human_content(state.get("messages", []))
        max_input_chars = getattr(app_config, "MAX_INPUT_CHARS", 8000)
        if len(question) > max_input_chars:
            overflow_msg = (
                f"您的提问内容过长（共 {len(question)} 字符，系统单次提问上限为 {max_input_chars} 字符），"
                "请精简或分段提问。"
            )
            logger.warning(
                "用户提问超出长度限制，直接拦截: graph={}, length={}, max={}",
                self._graph_name,
                len(question),
                max_input_chars,
            )
            return {
                "intent": "input_overflow",
                "messages": [
                    AIMessage(
                        content=overflow_msg, additional_kwargs={"ui_visible": True}
                    )
                ],
            }

        intent, reason = classify_intent_rule(question)
        logger.info(
            "业务图意图车道: graph={}，intent={}，reason={}",
            self._graph_name,
            intent,
            reason,
        )
        return {"intent": intent}

    # ──── 技能路由节点 ────

    async def resolve_skill(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """语义路由前置节点：替代 find_skill 工具往返.

        单命中：完整规则直接内联到 skill_rules_content；
        多命中：内联 top1 完整规则 + 其他候选摘要，模型可按需调用 load_skill。
        """
        configurable = config.get("configurable", {}) or {}
        mode = get_routing_context(configurable).mode
        question = _latest_human_content(state.get("messages", []))

        update: dict[str, Any] = {"skill_rules_content": ""}
        if not question:
            return update

        # 纯问候/寒暄轮次不进入 Skill 语义匹配，直接降级通用能力，
        # 避免 "你好" 之类被 embedding 相似度误命中业务 Skill（同时省一次 embedding 调用）。
        if is_conversational(question):
            logger.info(
                "业务图 Skill 跳过：对话/问候轮次，graph={}",
                self._graph_name,
            )
            return update

        # F02：运行期技能路由同样是外部 embedding 外呼——发请求前复查
        from common.identity.runtime import guard_runtime_call

        await guard_runtime_call(dict(config or {}))
        candidates = await self._router.amatch(question)
        await self._observe_skill_routing(config, question, candidates)
        if not candidates:
            logger.info(
                "业务图 Skill 未命中，降级通用能力: graph={}",
                self._graph_name,
            )
            return update

        top = candidates[0]
        skill_rules = load_skill_rules(self._router, top.name, mode)

        if len(candidates) > 1:
            summaries = json.dumps(
                _candidate_summaries(candidates),
                ensure_ascii=False,
            )
            skill_rules = "\n\n=====\n\n".join(
                part
                for part in (
                    skill_rules,
                    "以下为其他候选 Skill 摘要（仅当其明显更匹配用户问题时，"
                    f"调用 load_skill 工具加载完整规则后再执行）：\n{summaries}",
                )
                if part
            )

        logger.info(
            "业务图 Skill 命中: graph={}，skill={}，候选数={}，score={}",
            self._graph_name,
            top.name,
            len(candidates),
            top.similarity_score,
        )
        update["skill_rules_content"] = skill_rules
        await self._observe_skill_loaded(config, top.name, skill_rules)
        return update

    async def _observe_skill_routing(self, config, question, candidates):
        from common.observability.store import observability_enabled

        if not observability_enabled():
            return
        from common.observability.skill_adapter import emit_skill_stage

        for candidate in candidates:
            skill_file = self._router.resolve_skill_file(candidate.name)
            try:
                body = (
                    skill_file.read_text()
                    if skill_file and skill_file.exists()
                    else None
                )
            except OSError:
                body = None
            emit_skill_stage(config, candidate.name, "matched", body=body)
        if not candidates:
            emit_skill_stage(config, "unmatched", "other", status="not_executed")

    async def _observe_skill_loaded(self, config, skill_name, body):
        from common.observability.store import observability_enabled

        if not observability_enabled():
            return
        from common.observability.skill_adapter import emit_skill_stage

        emit_skill_stage(config, skill_name, "selected", body=body)
        emit_skill_stage(
            config,
            skill_name,
            "loaded",
            body=body,
            status="succeeded" if body else "failed",
        )

    async def observe_skill_executed(self, config, skill_rules_content, duration_ms):
        """最终模型正文生成后记录本次实际加载能力的执行证据."""
        from common.observability.skill_adapter import record_loaded_execution

        record_loaded_execution(config, succeeded=bool(skill_rules_content.strip()))

    # ──── 模型准备与调用节点 ────

    async def prepare_model(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """模型准备（按意图车道差异化）.

        - chitchat: 小提示词、零工具，跳过 MCP 刷新；
        - knowledge: 仅知识检索指引，本车道工具均为本地工具，无需刷新 MCP；
        - data_query: 全量组装（基础 + 时间 + mode + Skill 规则）。
        """
        intent = state.get("intent") or INTENT_DATA_QUERY
        configurable = config.get("configurable", {}) or {}

        if intent == INTENT_CHITCHAT:
            system_prompt = build_chitchat_system_prompt()
        elif intent == INTENT_KNOWLEDGE:
            system_prompt = build_knowledge_system_prompt(configurable)
        else:
            # F02：请求期公共工具目录刷新是实际外呼，先复查身份
            # （与启动 prewarm 的构建期批量 embedding 分道处理）。
            from common.identity.runtime import guard_runtime_call

            await guard_runtime_call(dict(config or {}))
            await ensure_mcp_tools()
            system_prompt = build_system_prompt(
                base_prompt=self._base_system_prompt,
                graph_name=self._graph_name,
                skill_rules_content=state.get("skill_rules_content", ""),
                configurable=configurable,
            )
        updates: dict[str, Any] = {"system_prompt": system_prompt}
        updates.update(
            await update_conversation_summary(
                {**state, "system_prompt": system_prompt},
                config,
                tools=self._available_tools(state),
            )
        )
        return updates

    def _available_tools(self, state: BusinessGraphState) -> list[Any]:
        """按意图车道过滤业务工具.

        - chitchat 车道：零工具（纯对话，不暴露任何工具 schema）；
        - knowledge 车道：仅始终可见的本地工具（时间 + create_artifact）；
        - data_query 车道：全量业务工具 + load_skill（始终可见），
          不再按 skill allowed_tools 做交集过滤——模型能力已足够，
          skill 规则中的工具使用指引足以引导模型选择正确工具。
        """
        intent = state.get("intent") or INTENT_DATA_QUERY
        if intent == INTENT_CHITCHAT:
            return []

        business = get_business_tools(include_computation=True)
        if intent == INTENT_KNOWLEDGE:
            return [
                t
                for t in business
                if (name := tool_name(t)) is not None
                and name in _ALWAYS_VISIBLE_TOOL_NAMES
            ]

        # data_query 车道：全量业务工具 + load_skill
        # 不再按 skill allowed_tools 做交集过滤——模型能力已足够，
        # skill 规则中的工具使用指引足以引导模型选择正确工具。
        available = list(business)
        available.append(self._load_skill_tool)
        return available

    async def call_model(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """LLM 推理：决定调工具 / 直接输出.

        最多重试 3 次；空回复时追加引导消息重试一次。
        """
        # F02：企业运行守卫在**每次实际外呼前**执行——首次、代码级重试、
        # 空回复重试各自复查；授权拒绝转为可读状态，不进入重试、不降级。
        from common.identity.runtime import RuntimeIdentityError, guard_runtime_call

        async def _guard_or_message() -> Any:
            try:
                await guard_runtime_call(dict(config or {}))
            except RuntimeIdentityError as exc:
                return AIMessage(content=f"授权状态已变化，本次运行已停止：{exc}")
            return None

        blocked = await _guard_or_message()
        if blocked is not None:
            return visible_result(blocked, config)

        recent = state.get("messages", [])
        last_human = max(
            (
                i
                for i, message in enumerate(recent)
                if isinstance(message, HumanMessage)
            ),
            default=-1,
        )
        if any(
            isinstance(message, ToolMessage)
            and message.additional_kwargs.get("processing_terminal")
            for message in recent[last_human + 1 :]
        ):
            return visible_result(
                AIMessage(
                    content="本次全量计算未完成，尚无完整统计结果。请缩小数据范围或重新查询。"
                ),
                config,
            )

        from common.observability.model_adapter import (
            ensure_model_observability,
            model_call_scope,
        )

        ensure_model_observability(config)
        _obs_scope = model_call_scope(config, role="main")
        _obs_metadata = _obs_scope.runtime_metadata()
        model = ModelRegistry.main_llm
        system_prompt = state.get("system_prompt", "")
        available = self._available_tools(state)

        messages = state["messages"]
        # 模型调用前只读上下文修剪 (注入早期历史摘要 + 保护当期轮次与旧工具治理)
        protected_messages = prepare_model_context(
            messages,
            summary=state.get("summary"),
            summary_covered_message_id=state.get("summary_covered_message_id"),
        )

        full_messages: list[Any] = []
        if system_prompt:
            full_messages.append(SystemMessage(content=system_prompt))
        full_messages.extend(protected_messages)

        schemas = fit_tool_schemas(full_messages, available)
        try:
            metrics = enforce_request_budget(full_messages, schemas)
        except ContextBudgetExceeded as exc:
            metrics = request_metrics(full_messages, schemas)
            emit_event(config, "context_metrics", **metrics)
            return visible_result(AIMessage(content=str(exc)), config, metrics=metrics)
        emit_event(config, "context_metrics", **metrics)

        if available:
            model_with_tools = model.bind_tools(schemas)
        else:
            model_with_tools = model

        data_model_started_at = datetime.now(UTC).isoformat()
        response = None
        for attempt in range(3):
            blocked = await _guard_or_message()
            if blocked is not None:
                return {**visible_result(blocked, config, metrics=metrics)}
            try:
                response = await invoke_with_data_deadline(
                    model_with_tools,
                    full_messages,
                    messages,
                    config,
                    invocation_metadata=_obs_metadata,
                )
                break
            except RuntimeIdentityError as exc:
                return visible_result(
                    AIMessage(content=f"授权状态已变化，本次运行已停止：{exc}"),
                    config,
                    metrics=metrics,
                )
            except DataProcessingFailure as exc:
                status = emit_event(
                    config,
                    "context_status",
                    stage="data",
                    status="failed",
                    message=str(exc),
                )
                return {
                    **visible_result(
                        AIMessage(content=str(exc)), config, metrics=metrics
                    ),
                    "data_status": status,
                }
            except Exception as exc:
                logger.warning(
                    "业务图模型调用失败 (attempt {}): {}",
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    return visible_result(
                        AIMessage(content=_SERVICE_UNAVAILABLE_MESSAGE),
                        config,
                        metrics=metrics,
                    )

        # 空回复检测：content 为空且无 tool_calls -> 追加引导重试
        if (
            isinstance(response, AIMessage)
            and not response.tool_calls
            and not get_message_content(response).strip()
        ):
            logger.info("业务图模型空回复，追加引导消息重试")
            blocked = await _guard_or_message()
            if blocked is not None:
                return {**visible_result(blocked, config, metrics=metrics)}
            nudge_messages = [
                *full_messages,
                response,
                HumanMessage(content=_EMPTY_REPLY_NUDGE),
            ]
            try:
                metrics = enforce_request_budget(nudge_messages, schemas)
                emit_event(config, "context_metrics", **metrics)
                nudge_response = await invoke_with_data_deadline(
                    model_with_tools,
                    nudge_messages,
                    messages,
                    config,
                    invocation_metadata=_obs_metadata,
                )
                if (
                    isinstance(nudge_response, AIMessage)
                    and not nudge_response.tool_calls
                    and not get_message_content(nudge_response).strip()
                ):
                    logger.warning("业务图模型空回复重试仍为空，使用兜底消息")
                    response = AIMessage(content=_FALLBACK_EMPTY_REPLY)
                else:
                    response = nudge_response
            except RuntimeIdentityError as exc:
                return visible_result(
                    AIMessage(content=f"授权状态已变化，本次运行已停止：{exc}"),
                    config,
                    metrics=metrics,
                )
            except Exception as exc:
                logger.warning(
                    "业务图模型空回复重试失败: {}，使用兜底消息", type(exc).__name__
                )
                response = AIMessage(content=_FALLBACK_EMPTY_REPLY)

        try:
            if (
                not response.tool_calls
                and response.content
                and response.content
                not in (_FALLBACK_EMPTY_REPLY, _SERVICE_UNAVAILABLE_MESSAGE)
            ):
                await self.observe_skill_executed(
                    config,
                    state.get("skill_rules_content") or "",
                    metrics.get("elapsed_ms", 0) if isinstance(metrics, dict) else 0,
                )
        except Exception:  # noqa: BLE001 - 观测失败开放
            pass
        return {
            **visible_result(response, config, metrics=metrics),
            "data_model_started_at": data_model_started_at,
        }

    # ──── 工具过滤 ────
