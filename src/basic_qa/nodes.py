"""基础问答节点：会话准备、按需工具调用，以及独立流式终答."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import StreamWriter
from loguru import logger
from pydantic import BaseModel

from basic_qa.context.preparation import (
    ContextRecoveryFailed,
    PreparedRequest,
    merge_prepared_updates,
    prepare_model_request,
)
from basic_qa.context.recovery import (
    call_with_context_recovery,
    prepare_context_recovery,
)
from basic_qa.state import INPUT_ACCEPTED, INPUT_OVERFLOW, BasicQAState
from common.data_governance import DataProcessingFailure, invoke_with_data_deadline
from common.identity.runtime import RuntimeIdentityError
from common.model_budget import ContextBudgetExceeded
from common.model_errors import is_context_overflow
from common.models import ModelRegistry
from common.output_streaming.answer import stream_final_answer, strip_reasoning_text
from common.output_streaming.controls import publish_control_message
from common.output_streaming.publisher import (
    OutputPublisher,
    runtime_authorization_probe,
)
from common.runtime_context import get_message_content
from common.runtime_events import emit_event
from common.runtime_tools import get_business_tools
from common.skill_router import SkillSemanticRouter

# 兜底消息常量 (模型调用失败 / 空回复时使用, 与 data_analysis/nodes.py 保持一致).
_SERVICE_UNAVAILABLE_MESSAGE = "抱歉，当前智能问答服务暂时不可用，请稍后再试。"
_EMPTY_REPLY_NUDGE = "请基于以上工具返回的数据，直接输出分析结论。"
_FALLBACK_EMPTY_REPLY = "服务暂不可用，请稍后重试。"

# 规划阶段的终止信号：模型用它表示"无需更多工具、可以生成最终正文"。
# 它由节点拦截，不进入 ToolNode，也不产生业务副作用。
_FINISH_PLANNING_TOOL_NAME = "finish_planning"
_PLANNING_ADDENDUM = (
    "【工具规划阶段】当前阶段只决定是否还需要调用工具。"
    "如果需要数据或校验，请调用相应工具；如果已具备生成最终回答的材料，"
    f"请调用 {_FINISH_PLANNING_TOOL_NAME} 工具，不要在本阶段输出最终答案正文。"
    "Skill 说明可能引用当前不可用工具：唯一可调用名单是本次提供的工具 schema，"
    "未提供的名称不得尝试或猜测替代名称。权限拒绝不重试；参数拒绝仅允许修正一次。"
    "无法取得的数据必须说明缺失，不得为补齐模板而编造。"
)
_ANSWER_ADDENDUM = (
    "【最终回答阶段】现在直接生成面向用户的最终回答。"
    "不得调用任何工具（本阶段未提供工具），不得复述内部规划或工具参数，"
    "保留必要的数据、单位、日期、引用与限制说明。"
)


class _EmptyArgs(BaseModel):
    """No-argument schema for the planning terminator."""


def _finish_planning_tool() -> StructuredTool:
    """Build the zero-side-effect planning terminator tool."""

    async def _finish() -> str:
        return "planning complete"

    return StructuredTool.from_function(
        coroutine=_finish,
        name=_FINISH_PLANNING_TOOL_NAME,
        description="规划阶段终止信号：当无需更多工具、可以生成最终回答时调用。",
        args_schema=_EmptyArgs,
        infer_schema=False,
    )


def _latest_human_content(messages: Sequence[Any]) -> str:
    """获取最近一条用户消息文本."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return get_message_content(message)
    return ""


class BasicQANodes:
    """本图的会话准备、模型决策与最终回答实现."""

    def __init__(
        self,
        *,
        router: SkillSemanticRouter,
        load_skill_tool: Any,
        graph_name: str,
    ) -> None:
        """初始化节点集合."""
        self._load_skill_tool = load_skill_tool
        self._graph_name = graph_name
        self._finish_planning_tool = _finish_planning_tool()
        from basic_qa.capabilities.skill_search import create_search_skills_tool

        self._search_skills_tool = create_search_skills_tool(router)
        from basic_qa.capabilities.mcp_search import (
            create_load_mcp_tool,
            create_search_mcp_tool,
        )

        self._search_mcp_tool = create_search_mcp_tool()
        self._load_mcp_tool = create_load_mcp_tool()

    @property
    def control_tools(self) -> tuple[Any, ...]:
        """供构图装配使用的只读控制工具集合."""
        return (
            self._load_skill_tool,
            self._search_skills_tool,
            self._search_mcp_tool,
            self._load_mcp_tool,
            self._finish_planning_tool,
        )

    @property
    def planning_tools(self) -> tuple[Any, ...]:
        """保持已有模型 schema 顺序，同时复用同一组工具实例."""
        load, search, *remaining = self.control_tools
        return (search, load, *remaining)

    # ──── 会话准备 ────

    async def prepare_session(
        self,
        state: BasicQAState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """检查输入并恢复本图会话引用；不按最新一句重选 Skill."""
        question = _latest_human_content(state.get("messages", []))
        from common.input_limits import (
            count_user_characters,
            current_input_limit,
            input_overflow_message,
        )

        limit = current_input_limit()
        count = count_user_characters(question)
        if count > limit.max_chars:
            overflow_msg = input_overflow_message(count, limit.max_chars)
            logger.warning(
                "用户提问超出长度限制，直接拦截: graph={}, length={}, max={}",
                self._graph_name,
                count,
                limit.max_chars,
            )
            return {
                "intent": INPUT_OVERFLOW,
                "messages": [
                    AIMessage(
                        content=overflow_msg, additional_kwargs={"ui_visible": True}
                    )
                ],
            }

        from basic_qa.capabilities.projection import initialize

        return {
            # intent 记录入口校验通过。
            "intent": INPUT_ACCEPTED,
            **await initialize(state, dict(config or {})),
        }

    # ──── 模型准备与调用节点 ────

    async def prepare_prompt(
        self,
        state: BasicQAState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """准备本图固定会话提示；能力按需由后续投影完整计量."""
        from common.identity.runtime import guard_runtime_call

        await guard_runtime_call(dict(config or {}))
        from basic_qa.prompts import build_basic_qa_system_prompt

        return {
            "system_prompt": build_basic_qa_system_prompt(
                config.get("configurable") or {}
            ),
            "planning_draft": "",
        }

    async def plan_tools(
        self,
        state: BasicQAState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """工具规划阶段：决定调用工具或结束规划（不发布正文）.

        规划调用保留工具 schema 与供应商兼容的非流式模式；最终正文由
        ``compose_answer`` 用无工具流式生成。规划阶段的任何自然语言内容都不进入
        用户正文，只作为下一阶段的参考材料记录。
        """
        from common.identity.runtime import guard_runtime_call

        async def _guard_or_message() -> Any:
            try:
                await guard_runtime_call(dict(config or {}))
            except RuntimeIdentityError as exc:
                return AIMessage(content=f"授权状态已变化，本次运行已停止：{exc}")
            return None

        async def _control(message: AIMessage, code: str) -> dict[str, Any]:
            published = await publish_control_message(
                dict(config or {}),
                graph_id=self._graph_name,
                text=get_message_content(message),
                writer=writer,
                control_code=code,
            )
            return {"messages": [published]}

        blocked = await _guard_or_message()
        if blocked is not None:
            return await _control(blocked, "authorization_stopped")

        from basic_qa.tool_recovery import planning_exhausted

        if planning_exhausted(state.get("messages", [])):
            return {
                "messages": [
                    AIMessage(content="", additional_kwargs={"ui_visible": False})
                ],
                "planning_draft": "查询尝试已达本轮上限；只能根据已取得的数据回答，明确说明未取得的数据，不得编造。",
            }

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
            return await _control(
                AIMessage(
                    content="本次全量计算未完成，尚无完整统计结果。请缩小数据范围或重新查询。"
                ),
                "computation_terminal",
            )

        from common.observability.model_adapter import (
            ensure_model_observability,
            model_call_scope,
        )

        ensure_model_observability(config)
        observation = model_call_scope(config, role="planning")
        invocation_metadata = observation.runtime_metadata()
        model = ModelRegistry.main_llm
        system_prompt = state.get("system_prompt", "")
        from basic_qa.capabilities.projection import prepare_projection

        projection = await prepare_projection(
            state,
            dict(config or {}),
            base_prompt=system_prompt,
            addenda=[_PLANNING_ADDENDUM],
            controls=list(self.planning_tools),
            role="planning",
            prepare=prepare_model_request,
            business=get_business_tools,
        )
        prepared = projection.prepared
        context, material = projection.context, projection.material
        state = projection.state
        prepared_state = projection.prepared_state
        prepare_kwargs = projection.prepare_kwargs
        messages = state["messages"]
        metrics = prepared.metrics
        emit_event(config, "context_metrics", **metrics)
        if prepared.over_capacity:
            return {
                **prepared.updates,
                "context_metrics": metrics,
                **await _control(
                    AIMessage(
                        content=prepared.failure_reason
                        or "本次资料超出当前模型可用输入容量，尚未完成处理。请缩小数据范围后重试。"
                    ),
                    "context_budget",
                ),
            }

        def bind(request: PreparedRequest) -> Any:
            return model.bind_tools(request.schemas) if request.schemas else model

        async def invoke(request: PreparedRequest) -> Any:
            from basic_qa.capabilities.projection import (
                verify_prompt_context,
            )

            await verify_prompt_context(state, dict(config or {}), context, material)
            return await invoke_with_data_deadline(
                bind(request),
                request.messages,
                messages,
                config,
                invocation_metadata=invocation_metadata,
            )

        planning_started_at = datetime.now(UTC).isoformat()
        outcome = await self._invoke_plan_with_retry(
            config,
            invoke=invoke,
            prepared=prepared,
            prepared_state=prepared_state,
            prepare_kwargs=prepare_kwargs,
            guard=_guard_or_message,
            control=_control,
        )
        if isinstance(outcome, dict):
            return outcome
        response, prepared = outcome

        if (
            isinstance(response, AIMessage)
            and not response.tool_calls
            and not get_message_content(response).strip()
        ):
            outcome = await self._retry_empty_plan(
                response,
                state,
                config,
                prepared=prepared,
                model=model,
                metadata=invocation_metadata,
                messages=messages,
                material=material,
                guard=_guard_or_message,
                control=_control,
            )
            if isinstance(outcome, dict):
                return outcome
            response, prepared, material = outcome

        return self._planning_updates(
            response,
            messages=messages,
            prepared=prepared,
            material=material,
            started_at=planning_started_at,
        )

    async def compose_answer(
        self,
        state: BasicQAState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """最终回答阶段：无工具、真实流式生成并通过共享发布器输出."""
        from common.identity.runtime import guard_runtime_call
        from common.observability.model_adapter import (
            ensure_model_observability,
            model_call_scope,
        )

        try:
            await guard_runtime_call(dict(config or {}))
        except RuntimeIdentityError:
            return await self._abort_answer_visibility(config, "permission_revoked")

        ensure_model_observability(config)
        model = ModelRegistry.main_llm
        system_prompt = state.get("system_prompt", "")
        answer_addenda = [_ANSWER_ADDENDUM]
        if state.get("planning_draft"):
            answer_addenda.append(
                "规划阶段留下的候选材料（仅供整理，不得原样复述内部规划；最终正文必须完整、可独立阅读）：\n"
                + str(state["planning_draft"])[:4000]
            )
        from basic_qa.capabilities.projection import prepare_projection

        projection = await prepare_projection(
            state,
            dict(config or {}),
            base_prompt=system_prompt,
            addenda=answer_addenda,
            controls=[],
            role="answer",
            prepare=prepare_model_request,
            business=get_business_tools,
        )
        prepared = projection.prepared
        context, material = projection.context, projection.material
        state = projection.state
        prepared_state = projection.prepared_state
        prepare_kwargs = projection.prepare_kwargs
        metrics = prepared.metrics
        emit_event(config, "context_metrics", **metrics)
        if prepared.over_capacity:
            published = await publish_control_message(
                dict(config or {}),
                graph_id=self._graph_name,
                text=prepared.failure_reason
                or "本次资料超出当前模型可用输入容量，尚未完成处理。请缩小数据范围后重试。",
                writer=writer,
                control_code="context_budget",
            )
            return merge_prepared_updates(
                prepared,
                {
                    **prepared.updates,
                    "messages": [published],
                    "context_metrics": metrics,
                },
            )

        observed = model_call_scope(config, role="answer")
        outcome = await self._stream_answer_with_recovery(
            state,
            config,
            writer=writer,
            model=model,
            observed=observed,
            prepared=prepared,
            prepared_state=prepared_state,
            prepare_kwargs=prepare_kwargs,
            context=context,
            material=material,
        )
        if isinstance(outcome, dict):
            return outcome
        result, publisher, prepared = outcome
        return await self._answer_updates(
            config, writer=writer, result=result, publisher=publisher, prepared=prepared
        )

    async def _invoke_plan_with_retry(
        self,
        config: RunnableConfig,
        *,
        invoke: Callable[[PreparedRequest], Awaitable[Any]],
        prepared: PreparedRequest,
        prepared_state: dict[str, Any],
        prepare_kwargs: dict[str, Any],
        guard: Callable[[], Awaitable[AIMessage | None]],
        control: Callable[[AIMessage, str], Awaitable[dict[str, Any]]],
    ) -> tuple[Any, PreparedRequest] | dict[str, Any]:
        """执行规划请求，独立处理普通重试、容量恢复和终止回执."""
        response = None
        for attempt in range(3):
            blocked = await guard()
            if blocked is not None:
                return await control(blocked, "authorization_stopped")
            try:
                response, prepared = await call_with_context_recovery(
                    invoke,
                    prepared,
                    state=prepared_state,
                    run_config=dict(config or {}),
                    prepare_kwargs=prepare_kwargs,
                )
                metrics = prepared.metrics
                emit_event(config, "context_metrics", **metrics)
                break
            except RuntimeIdentityError as exc:
                return await control(
                    AIMessage(content=f"授权状态已变化，本次运行已停止：{exc}"),
                    "authorization_stopped",
                )
            except ContextBudgetExceeded as exc:
                if isinstance(exc, ContextRecoveryFailed):
                    prepared = exc.prepared
                emit_event(config, "context_metrics", **prepared.metrics)
                return merge_prepared_updates(
                    prepared,
                    {
                        **prepared.updates,
                        "context_metrics": prepared.metrics,
                        **await control(AIMessage(content=str(exc)), "context_budget"),
                    },
                )
            except DataProcessingFailure as exc:
                status = emit_event(
                    config,
                    "context_status",
                    stage="data",
                    status="failed",
                    message=str(exc),
                )
                return merge_prepared_updates(
                    prepared,
                    {
                        **prepared.updates,
                        "context_metrics": prepared.metrics,
                        **await control(AIMessage(content=str(exc)), "data_failure"),
                        "data_status": status,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "业务图模型调用失败 (attempt {}): {}",
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    return merge_prepared_updates(
                        prepared,
                        {
                            **prepared.updates,
                            "context_metrics": prepared.metrics,
                            **await control(
                                AIMessage(content=_SERVICE_UNAVAILABLE_MESSAGE),
                                "service_unavailable",
                            ),
                        },
                    )

        return response, prepared

    async def _retry_empty_plan(
        self,
        response: Any,
        state: BasicQAState,
        config: RunnableConfig,
        *,
        prepared: PreparedRequest,
        model: Any,
        metadata: dict[str, Any],
        messages: Sequence[Any],
        material: Any,
        guard: Callable[[], Awaitable[AIMessage | None]],
        control: Callable[[AIMessage, str], Awaitable[dict[str, Any]]],
    ) -> tuple[Any, PreparedRequest, Any] | dict[str, Any]:
        """重新投影空回复引导，保留已完成摘要和资料替换."""
        logger.info("业务图模型空回复，追加引导消息重试")
        blocked = await guard()
        if blocked is not None:
            return await control(blocked, "authorization_stopped")
        try:
            tail = [response, HumanMessage(content=_EMPTY_REPLY_NUDGE)]
            from basic_qa.capabilities.projection import (
                prepare_projection,
            )
            from basic_qa.context.tool_results import apply_replacements

            prior = prepared
            nudge_state = {
                **state,
                **prior.updates,
                "messages": apply_replacements(
                    list(state["messages"]), prior.message_updates
                ),
            }
            projection = await prepare_projection(
                nudge_state,
                dict(config or {}),
                base_prompt=state.get("system_prompt", ""),
                addenda=[_PLANNING_ADDENDUM],
                controls=list(self.planning_tools),
                role="planning",
                prepare=prepare_model_request,
                business=get_business_tools,
                tail=tail,
            )
            nudge = projection.prepared
            context, material = projection.context, projection.material
            state = projection.state
            nudge.updates = {**prior.updates, **nudge.updates}
            nudge.message_updates = list(
                {
                    m.id: m for m in [*prior.message_updates, *nudge.message_updates]
                }.values()
            )
            emit_event(config, "context_metrics", **nudge.metrics)
            if nudge.over_capacity:
                return merge_prepared_updates(
                    nudge,
                    {
                        **nudge.updates,
                        "context_metrics": nudge.metrics,
                        **await control(
                            AIMessage(
                                content=nudge.failure_reason
                                or "本次资料超出当前模型可用输入容量，请缩小数据范围后重试。"
                            ),
                            "context_budget",
                        ),
                    },
                )
            prepared = nudge
            from basic_qa.capabilities.projection import (
                verify_prompt_context,
            )

            await verify_prompt_context(state, dict(config or {}), context, material)
            nudge_response = await invoke_with_data_deadline(
                model.bind_tools(nudge.schemas) if nudge.schemas else model,
                nudge.messages,
                messages,
                config,
                invocation_metadata=metadata,
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
            return await control(
                AIMessage(content=f"授权状态已变化，本次运行已停止：{exc}"),
                "authorization_stopped",
            )
        except Exception as exc:
            logger.warning(
                "业务图模型空回复重试失败: {}，使用兜底消息", type(exc).__name__
            )
            response = AIMessage(content=_FALLBACK_EMPTY_REPLY)

        return response, prepared, material

    @staticmethod
    def _planning_updates(
        response: Any,
        *,
        messages: Sequence[Any],
        prepared: PreparedRequest,
        material: Any,
        started_at: str,
    ) -> dict[str, Any]:
        """将规划结果转换为内部消息、草稿与当次工具定义记录."""
        metrics = prepared.metrics
        # 服务端依据门禁：带 tool_calls 的草稿正文清空；无工具正文属于规划材料，
        # 不直接公开，交由 answer 阶段生成。
        from common.knowledge.policy import apply_answer_policy

        response = apply_answer_policy(response, messages)
        calls = list(getattr(response, "tool_calls", []) or [])
        remaining_calls = [
            call
            for call in calls
            if len(calls) > 1 or call.get("name") != _FINISH_PLANNING_TOOL_NAME
        ]
        finish_only = bool(calls) and not remaining_calls
        draft = (
            ""
            if remaining_calls
            else strip_reasoning_text(get_message_content(response))
        )
        planning_message = AIMessage(
            content="",
            id=response.id or str(uuid4()),
            tool_calls=remaining_calls,
            additional_kwargs={
                **response.additional_kwargs,
                "ui_visible": False,
                "planning": True,
                "planning_finished": finish_only or not remaining_calls,
            },
        )
        update: dict[str, Any] = merge_prepared_updates(
            prepared,
            {
                **prepared.updates,
                "messages": [planning_message],
                "data_model_started_at": started_at,
                "planning_draft": draft,
                "context_metrics": {
                    **(metrics or {}),
                    "latest_usage": getattr(response, "usage_metadata", None) or {},
                    "planning_draft_chars": len(draft),
                },
            },
        )
        from basic_qa.capabilities.tool_definitions import disclosure_record

        update["bqa_disclosure"] = disclosure_record(
            prepared.schemas, planning_message.id, material
        )
        return update

    async def _stream_answer_with_recovery(
        self,
        state: BasicQAState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
        model: Any,
        observed: Any,
        prepared: PreparedRequest,
        prepared_state: dict[str, Any],
        prepare_kwargs: dict[str, Any],
        context: str,
        material: Any,
    ) -> tuple[Any, OutputPublisher, PreparedRequest] | dict[str, Any]:
        """流式终答只在明确超窗后恢复一次，不重放工具或已发布正文."""
        messages = state["messages"]
        metrics = prepared.metrics
        from common.observability.model_adapter import invocation_config

        publisher = OutputPublisher(
            dict(config or {}),
            graph_id=self._graph_name,
            writer=writer,
            authorization_probe=runtime_authorization_probe(dict(config or {})),
        )
        try:
            from basic_qa.capabilities.projection import (
                verify_prompt_context,
            )

            await verify_prompt_context(state, dict(config or {}), context, material)
            result = await stream_final_answer(
                model,
                prepared.messages,
                invocation_config(config, role="answer"),
                publisher=publisher,
                policy_messages=messages,
                question=_latest_human_content(messages),
                invocation_metadata=observed.runtime_metadata(),
                disable_tool_calls=True,
            )
        except Exception as exc:  # noqa: BLE001 - 仅超窗进入一次恢复
            if not is_context_overflow(exc):
                published = await publish_control_message(
                    dict(config or {}),
                    graph_id=self._graph_name,
                    text=_SERVICE_UNAVAILABLE_MESSAGE,
                    writer=writer,
                    control_code="answer_fallback",
                )
                return merge_prepared_updates(
                    prepared,
                    {
                        **prepared.updates,
                        "messages": [published],
                        "context_metrics": metrics,
                    },
                )
            try:
                recovered = await prepare_context_recovery(
                    prepared,
                    state=prepared_state,
                    run_config=dict(config or {}),
                    prepare_kwargs=prepare_kwargs,
                )
            except ContextRecoveryFailed as failure:
                recovered = failure.prepared
                published = await publish_control_message(
                    dict(config or {}),
                    graph_id=self._graph_name,
                    text=str(failure),
                    writer=writer,
                    control_code="context_budget",
                )
                return merge_prepared_updates(
                    recovered,
                    {
                        **recovered.updates,
                        "messages": [published],
                        "context_metrics": recovered.metrics,
                    },
                )
            prepared = recovered
            metrics = recovered.metrics
            emit_event(config, "context_metrics", **metrics)
            publisher = OutputPublisher(
                dict(config or {}),
                graph_id=self._graph_name,
                writer=writer,
                authorization_probe=runtime_authorization_probe(dict(config or {})),
            )
            try:
                from basic_qa.capabilities.projection import (
                    verify_prompt_context,
                )

                await verify_prompt_context(
                    state, dict(config or {}), context, material
                )
                result = await stream_final_answer(
                    model,
                    prepared.messages,
                    invocation_config(config, role="answer"),
                    publisher=publisher,
                    policy_messages=messages,
                    question=_latest_human_content(messages),
                    invocation_metadata=observed.runtime_metadata(),
                    disable_tool_calls=True,
                )
            except Exception as second:  # noqa: BLE001 - 第二次仍超限则有界停止
                logger.warning(
                    "业务图最终正文上下文恢复失败: {}", type(second).__name__
                )
                published = await publish_control_message(
                    dict(config or {}),
                    graph_id=self._graph_name,
                    text=(
                        "模型上下文已超限，自动整理后仍无法完成本次回答。"
                        "请缩小数据范围或新开会话后重试。"
                    ),
                    writer=writer,
                    control_code="context_budget",
                )
                return merge_prepared_updates(
                    prepared,
                    {
                        **prepared.updates,
                        "messages": [published],
                        "context_metrics": metrics,
                    },
                )

        return result, publisher, prepared

    async def _answer_updates(
        self,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
        result: Any,
        publisher: OutputPublisher,
        prepared: PreparedRequest,
    ) -> dict[str, Any]:
        """写回流式结果；中止后只在未发布正文时生成受控回执."""
        metrics = prepared.metrics
        latest_usage = result.usage or {}
        emit_event(
            config,
            "context_metrics",
            **{**metrics, "latest_usage": latest_usage},
        )
        if result.aborted:
            logger.bind(output_stream=True).warning(
                "业务图最终正文中断 graph={} output={} reason={}",
                self._graph_name,
                publisher.output_id,
                result.abort_reason,
            )
            fallback_text: str | None = None
            if publisher.total_chars == 0:
                if result.abort_reason == "policy_blocked" and result.text:
                    fallback_text = result.text
                elif result.abort_reason in {
                    "generation_failed",
                    "empty_answer",
                    "json_error_payload",
                }:
                    fallback_text = _SERVICE_UNAVAILABLE_MESSAGE
            if fallback_text:
                published = await publish_control_message(
                    dict(config or {}),
                    graph_id=self._graph_name,
                    text=fallback_text,
                    writer=writer,
                    control_code="answer_fallback",
                )
                return merge_prepared_updates(
                    prepared,
                    {
                        **prepared.updates,
                        "messages": [published],
                        "context_metrics": {**metrics, "latest_usage": latest_usage},
                    },
                )
            return merge_prepared_updates(
                prepared,
                {
                    **prepared.updates,
                    "messages": [result.message],
                    "context_metrics": {**metrics, "latest_usage": latest_usage},
                },
            )

        answer_message = result.message
        return merge_prepared_updates(
            prepared,
            {
                **prepared.updates,
                "messages": [answer_message],
                "context_metrics": {**metrics, "latest_usage": latest_usage},
            },
        )

    async def _abort_answer_visibility(
        self, config: RunnableConfig, reason: str
    ) -> dict[str, Any]:
        """在终答发送前撤权时发布中止状态."""
        publisher = OutputPublisher(
            dict(config or {}),
            graph_id=self._graph_name,
        )
        publisher.start()
        await publisher.abort(reason)
        return {
            "messages": [
                AIMessage(
                    content="",
                    id=publisher.message_id,
                    additional_kwargs={
                        "ui_visible": False,
                        "output_protocol_version": 1,
                        "output_id": publisher.output_id,
                        "output_aborted": reason,
                    },
                )
            ]
        }
