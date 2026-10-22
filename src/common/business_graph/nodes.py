"""业务图（basic_qa）节点流的所有节点函数.

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
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import StreamWriter
from loguru import logger
from pydantic import BaseModel

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
from common.capabilities import refresh_authorized_mcp_tools
from common.config import config as app_config
from common.context import get_message_content, get_routing_context
from common.context_guard import prepare_model_context
from common.conversation_summarizer import update_conversation_summary
from common.data_governance import DataProcessingFailure, invoke_with_data_deadline
from common.model_budget import (
    ContextBudgetExceeded,
    enforce_request_budget,
    fit_tool_schemas,
    request_metrics,
)
from common.models import ModelRegistry
from common.output_streaming.answer import stream_final_answer, strip_reasoning_text
from common.output_streaming.controls import publish_control_message
from common.output_streaming.publisher import (
    OutputPublisher,
    runtime_authorization_probe,
)
from common.permission.rules import is_conversational
from common.runtime_events import emit_event
from common.runtime_tools import get_business_tools, tool_name
from common.skill_router import SkillSemanticRouter

# 始终可见的本地工具名集合 (不被 skill allowed-tools 过滤掉).
# knowledge_search 是三图共享的受管本地 HTTP 工具：纯问候轮次也保持 schema 可见，
# 但不主动调用；关闭 MCP 不会移除它，任何 Skill 过滤也不能隐藏它。
# 始终可见的工具：时间工具 + create_artifact 画布工具 + 知识检索.
# create_artifact 是跨技能的通用画布输出能力，不受技能 allowed_tools 限制，
# 保证 Agent 在任何技能命中场景下都能自主产出 HTML/Markdown/SVG 画布内容.
_ALWAYS_VISIBLE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "get_beijing_time",
        "create_artifact",
        "compute_artifact",
        "knowledge_search",
    }
)

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
        self._finish_planning_tool = _finish_planning_tool()

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
        """语义路由前置节点：由向量路由器唯一决定业务 Skill.

        只内联 top1 完整规则；不向模型披露候选摘要，也不提供 load_skill
        二次选择入口，保证全自动路由可解释、可复现。
        """
        configurable = config.get("configurable", {}) or {}
        mode = get_routing_context(configurable).mode
        question = _latest_human_content(state.get("messages", []))

        update: dict[str, Any] = {"skill_rules_content": ""}
        if not question:
            return update

        # 任务 4.2：显式 Skill 单选绕过语义匹配直接加载
        skill_mode = configurable.get("capability_skill_mode") or "auto"
        if skill_mode == "explicit":
            skill_id = configurable.get("capability_skill_id") or ""
            explicit_slug = skill_id.split(":")[-1] if skill_id else ""
            if explicit_slug:
                skill_rules = load_skill_rules(self._router, explicit_slug, mode)
                from common.observability.skill_adapter import emit_skill_stage

                emit_skill_stage(
                    config, explicit_slug, "matched", body=skill_rules, source="manual"
                )
                emit_skill_stage(
                    config, explicit_slug, "selected", body=skill_rules, source="manual"
                )
                emit_skill_stage(
                    config,
                    explicit_slug,
                    "loaded",
                    body=skill_rules,
                    status="succeeded" if skill_rules else "failed",
                    source="manual",
                )
                logger.info(
                    "业务图显式 Skill 直接加载: graph={}，skill={}",
                    self._graph_name,
                    explicit_slug,
                )
                return {"skill_rules_content": skill_rules}

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
            await refresh_authorized_mcp_tools(config)
            system_prompt = build_system_prompt(
                base_prompt=self._base_system_prompt,
                graph_name=self._graph_name,
                skill_rules_content=state.get("skill_rules_content", ""),
                configurable=configurable,
            )
        updates: dict[str, Any] = {"system_prompt": system_prompt, "planning_draft": ""}
        try:
            curr_tools = self._available_tools(state, config)
        except TypeError:
            curr_tools = self._available_tools(state)
        updates.update(
            await update_conversation_summary(
                {**state, "system_prompt": system_prompt},
                config,
                tools=curr_tools,
            )
        )
        updates["needs_planning"] = self._needs_planning(state, config)
        return updates

    def _needs_planning(
        self,
        state: BusinessGraphState,
        config: RunnableConfig | None = None,
    ) -> bool:
        """Whether this turn needs a tool-planning stage before the answer.

        Only pure chitchat skips planning. Knowledge questions retain retrieval
        and artifact tools; streaming the final answer must not remove them.
        """
        intent = state.get("intent") or INTENT_DATA_QUERY
        if intent == INTENT_CHITCHAT:
            return False
        try:
            return bool(self._available_tools(state, config))
        except TypeError:
            return bool(self._available_tools(state))

    def _available_tools(
        self,
        state: BusinessGraphState,
        config: RunnableConfig | None = None,
    ) -> list[Any]:
        """按意图车道与能力选择过滤业务工具.

        - chitchat 车道：仅保留 knowledge_search 的 schema（不主动调用）；
        - knowledge 车道：始终可见的本地工具（时间 + create_artifact + 知识检索）；
        - data_query 车道：业务工具（按语义路由 top1 Skill 的 allowed_tools 收窄）。
        本图不向模型披露 load_skill，Skill 只能由 resolve_skill 的向量路由决定。
        """
        intent = state.get("intent") or INTENT_DATA_QUERY
        if intent == INTENT_CHITCHAT:
            return [
                tool
                for tool in get_business_tools(include_computation=False)
                if tool_name(tool) == "knowledge_search"
            ]

        business = get_business_tools(include_computation=True)
        if intent == INTENT_KNOWLEDGE:
            from common.business_graph.tool_recovery import recoverable_tools

            return recoverable_tools(
                [
                    t
                    for t in business
                    if (name := tool_name(t)) is not None
                    and name in _ALWAYS_VISIBLE_TOOL_NAMES
                ],
                state.get("messages", []),
            )

        # data_query 车道：全量业务工具
        from common.capabilities import filter_capability_mcp_tools

        available = filter_capability_mcp_tools(
            list(business), config, business_code=self._graph_name
        )

        from common.business_graph.tool_recovery import recoverable_tools

        return recoverable_tools(available, state.get("messages", []))

    def _planning_tools(
        self,
        state: BusinessGraphState,
        config: RunnableConfig | None = None,
    ) -> list[Any]:
        """Planning adds only the zero-side-effect terminator on top of lane tools."""
        try:
            tools = list(self._available_tools(state, config))
        except TypeError:
            tools = list(self._available_tools(state))
        if tools:
            tools.append(self._finish_planning_tool)
        return tools

    async def call_model(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """工具规划阶段：决定调用工具或结束规划（不发布正文）.

        规划调用保留工具 schema 与供应商兼容的非流式模式；最终正文由
        ``answer_model`` 用无工具流式生成。规划阶段的任何自然语言内容都不进入
        用户正文，只作为下一阶段的参考材料记录。
        """
        from common.identity.runtime import RuntimeIdentityError, guard_runtime_call

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

        from common.business_graph.tool_recovery import planning_exhausted

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
        _obs_scope = model_call_scope(config, role="planning")
        _obs_metadata = _obs_scope.runtime_metadata()
        model = ModelRegistry.main_llm
        system_prompt = state.get("system_prompt", "")
        try:
            available = self._planning_tools(state, config)
        except TypeError:
            available = self._planning_tools(state)

        messages = state["messages"]
        protected_messages = prepare_model_context(
            messages,
            summary=state.get("summary"),
            summary_covered_message_id=state.get("summary_covered_message_id"),
        )

        full_messages: list[Any] = []
        if system_prompt:
            full_messages.append(SystemMessage(content=system_prompt))
        full_messages.append(SystemMessage(content=_PLANNING_ADDENDUM))
        full_messages.extend(protected_messages)

        schemas = fit_tool_schemas(full_messages, available)
        try:
            metrics = enforce_request_budget(full_messages, schemas)
        except ContextBudgetExceeded as exc:
            metrics = request_metrics(full_messages, schemas)
            emit_event(config, "context_metrics", **metrics)
            return await _control(AIMessage(content=str(exc)), "context_budget")

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
                return await _control(blocked, "authorization_stopped")
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
                return await _control(
                    AIMessage(content=f"授权状态已变化，本次运行已停止：{exc}"),
                    "authorization_stopped",
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
                    **await _control(AIMessage(content=str(exc)), "data_failure"),
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
                    return await _control(
                        AIMessage(content=_SERVICE_UNAVAILABLE_MESSAGE),
                        "service_unavailable",
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
                return await _control(blocked, "authorization_stopped")
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
                return await _control(
                    AIMessage(content=f"授权状态已变化，本次运行已停止：{exc}"),
                    "authorization_stopped",
                )
            except Exception as exc:
                logger.warning(
                    "业务图模型空回复重试失败: {}，使用兜底消息", type(exc).__name__
                )
                response = AIMessage(content=_FALLBACK_EMPTY_REPLY)

        # 服务端依据门禁：带 tool_calls 的草稿正文清空；无工具正文属于规划材料，
        # 不直接公开，交由 answer 阶段生成。
        from common.knowledge.policy import apply_answer_policy

        response = apply_answer_policy(response, messages)
        calls = list(getattr(response, "tool_calls", []) or [])
        remaining_calls = [
            call for call in calls if call.get("name") != _FINISH_PLANNING_TOOL_NAME
        ]
        finish_only = bool(calls) and not remaining_calls
        draft = (
            ""
            if remaining_calls
            else strip_reasoning_text(get_message_content(response))
        )
        planning_message = AIMessage(
            content="",
            id=response.id,
            tool_calls=remaining_calls,
            additional_kwargs={
                **response.additional_kwargs,
                "ui_visible": False,
                "planning": True,
                "planning_finished": finish_only or not remaining_calls,
            },
        )
        update: dict[str, Any] = {
            "messages": [planning_message],
            "data_model_started_at": data_model_started_at,
            "planning_draft": draft,
            "context_metrics": {
                **(metrics or {}),
                "latest_usage": getattr(response, "usage_metadata", None) or {},
                "planning_draft_chars": len(draft),
            },
        }
        return update

    async def answer_model(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """最终回答阶段：无工具、真实流式生成并通过共享发布器输出."""
        from common.identity.runtime import RuntimeIdentityError, guard_runtime_call
        from common.observability.model_adapter import (
            ensure_model_observability,
            invocation_config,
            model_call_scope,
        )

        async def _abort_visibility(reason: str) -> dict[str, Any]:
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

        try:
            await guard_runtime_call(dict(config or {}))
        except RuntimeIdentityError:
            return await _abort_visibility("permission_revoked")

        ensure_model_observability(config)
        model = ModelRegistry.main_llm
        system_prompt = state.get("system_prompt", "")
        messages = state["messages"]
        protected_messages = prepare_model_context(
            messages,
            summary=state.get("summary"),
            summary_covered_message_id=state.get("summary_covered_message_id"),
        )
        full_messages: list[Any] = []
        if system_prompt:
            full_messages.append(SystemMessage(content=system_prompt))
        full_messages.append(SystemMessage(content=_ANSWER_ADDENDUM))
        if state.get("planning_draft"):
            full_messages.append(
                SystemMessage(
                    content=(
                        "规划阶段留下的候选材料（仅供整理，不得原样复述内部规划；"
                        "最终正文必须完整、可独立阅读）：\n"
                        + str(state.get("planning_draft"))[:4000]
                    )
                )
            )
        full_messages.extend(protected_messages)

        metrics = request_metrics(full_messages, [])
        try:
            metrics = enforce_request_budget(full_messages, [])
        except ContextBudgetExceeded as exc:
            emit_event(config, "context_metrics", **metrics)
            published = await publish_control_message(
                dict(config or {}),
                graph_id=self._graph_name,
                text=str(exc),
                writer=writer,
                control_code="context_budget",
            )
            return {"messages": [published], "context_metrics": metrics}
        emit_event(config, "context_metrics", **metrics)

        observed = model_call_scope(config, role="answer")
        publisher = OutputPublisher(
            dict(config or {}),
            graph_id=self._graph_name,
            writer=writer,
            authorization_probe=runtime_authorization_probe(dict(config or {})),
        )
        result = await stream_final_answer(
            model,
            full_messages,
            invocation_config(config, role="answer"),
            publisher=publisher,
            policy_messages=messages,
            question=_latest_human_content(messages),
            invocation_metadata=observed.runtime_metadata(),
        )

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
                return {
                    "messages": [published],
                    "context_metrics": {**metrics, "latest_usage": latest_usage},
                }
            return {
                "messages": [result.message],
                "context_metrics": {**metrics, "latest_usage": latest_usage},
            }

        try:
            await self.observe_skill_executed(
                config,
                state.get("skill_rules_content") or "",
                metrics.get("elapsed_ms", 0) if isinstance(metrics, dict) else 0,
            )
        except Exception:  # noqa: BLE001 - 观测失败开放
            pass
        return {
            "messages": [result.message],
            "context_metrics": {**metrics, "latest_usage": latest_usage},
        }

    # ──── 工具过滤 ────
