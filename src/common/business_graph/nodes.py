"""业务图（basic_qa / intelligent_analysis）节点流的所有节点函数.

用显式节点替代 create_deep_agent 的中间件链：
- check_permission: 复用 PermissionClassifyMiddleware 的权限审查（已合并 LLM、并行化）
- resolve_skill: 语义路由前置执行，命中规则直接内联，消除 find_skill 工具往返
- prepare_model: MCP 刷新 + 系统提示词一次性组装（含权限上下文）
- call_model / execute_tools: 标准 ReAct 循环
- finalize_output: 唯一输出出口（确定性清洗 + final_output_delta/done 事件），
  推荐追问并发生成，不阻塞正文
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter
from loguru import logger

from common.business_graph.intent import (
    INTENT_CHITCHAT,
    INTENT_DATA_QUERY,
    INTENT_KNOWLEDGE,
    classify_intent_rule,
)
from common.business_graph.output_cleanup import (
    deterministic_cleanup,
    has_process_residue,
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
from common.mcp_client import ensure_mcp_tools
from common.middleware.final_output_cleanup_middleware import (
    build_final_output_cleanup_prompt,
)
from common.middleware.permission_classify_middleware import (
    PermissionClassifyMiddleware,
)
from common.models import ModelRegistry
from common.permission.rules import is_conversational
from common.prompts import expand_question_prompt
from common.runtime_tools import get_business_tools, tool_name
from common.skill_discovery import _union_allowed_tools
from common.skill_router import SkillRouteCandidate, SkillSemanticRouter

# 空回复时追加的引导消息，促使模型基于工具结果生成结论
_EMPTY_REPLY_NUDGE = "请基于以上工具返回的数据，直接输出分析结论。"

# 用户可见的服务不可用提示
_SERVICE_UNAVAILABLE_MESSAGE = "抱歉，当前智能问答服务暂时不可用，请稍后再试。"

# 空回复重试仍为空时的兜底消息
_FALLBACK_EMPTY_REPLY = "服务暂不可用，请稍后重试。"

# 非 allowed-tools 限制的始终可见业务工具(本地化: knowledge_retriever_tool 依赖 Milvus, 已删除)
_ALWAYS_VISIBLE_TOOL_NAMES = frozenset({"get_beijing_time"})

# final_output_delta 事件的分片大小（字符）
_DELTA_CHUNK_SIZE = 120

# 进程级共享权限审查器：slots 抽取 agent 编译结果跨图复用
_permission_reviewer = PermissionClassifyMiddleware()


def _latest_human_content(messages: Sequence[Any]) -> str:
    """获取最近一条用户消息文本."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return get_message_content(message)
    return ""


def _extract_last_visible_answer(state: BusinessGraphState) -> str:
    """从消息列表中提取最后一条可见业务答案.

    优先提取不含 tool_calls 且内容非空的 AIMessage；
    若所有 AIMessage 都带 tool_calls，则兜底提取最后一条的 content。
    """
    messages = state.get("messages", [])

    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if getattr(message, "tool_calls", None):
            continue
        content = get_message_content(message).strip()
        if content:
            return content

    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        content = get_message_content(message).strip()
        if content:
            return content

    return ""


def _expand_question_enabled(configurable: dict[str, Any]) -> bool:
    """判断是否启用推荐追问（configurable 优先，环境配置兜底）."""
    raw_value = configurable.get(
        "expand_question_enabled",
        configurable.get("enable_expand_question", None),
    )
    if raw_value is None:
        return bool(app_config.EXPAND_QUESTION_ENABLED)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw_value)


async def _generate_expanded_questions(question: str, answer: str) -> list[str]:
    """调用 LLM 生成推荐追问列表（复用原 ExpandQuestionMiddleware 逻辑）."""
    llm = ModelRegistry.deepseek_v4_flash
    prompt = expand_question_prompt(question, answer)

    full_content = ""
    async for chunk in llm.astream([{"role": "user", "content": prompt}]):
        content = chunk.content if hasattr(chunk, "content") else str(chunk)
        if isinstance(content, list):
            content = str(content)
        full_content += content

    if full_content.strip() == "无":
        return []
    return [q.strip() for q in full_content.split("\n") if q.strip()]


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
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """入口意图分类：决定 chitchat/knowledge/data_query 车道（纯规则，零 LLM）.

        不确定时 fail-open 到 data_query 全流程，快车道只对高置信信号开放。
        """
        question = _latest_human_content(state.get("messages", []))
        intent, reason = classify_intent_rule(question)
        logger.info(
            "业务图意图车道: graph={}，intent={}，reason={}，question={}",
            self._graph_name,
            intent,
            reason,
            question[:60],
        )
        return {"intent": intent}

    # ──── 权限节点 ────

    async def check_permission(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """权限审查节点：复用 PermissionClassifyMiddleware.abefore_agent.

        画像预取与槽位抽取已在中间件内并行执行；硬拒绝时写入
        permission_rejection，由条件边直接路由到 finalize_output。
        """
        runtime_shim = SimpleNamespace(stream_writer=writer)
        update = (
            await _permission_reviewer.abefore_agent(
                cast(Any, state),
                cast(Any, runtime_shim),
            )
            or {}
        )

        # 复用中间件的硬拒绝判定（原 wrap_model_call 短路逻辑）
        merged = {**state, **update}
        rejection = _permission_reviewer._rejection_message(merged)
        if rejection:
            logger.info("权限审查硬拒绝，跳过主模型: graph={}", self._graph_name)
            update["permission_rejection"] = rejection
        return update

    # ──── 技能路由节点 ────

    async def resolve_skill(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """语义路由前置节点：替代 find_skill 工具往返.

        单命中：完整规则直接内联到 skill_rules_content；
        多命中：内联 top1 完整规则 + 其他候选摘要，模型可按需调用 load_skill。
        """
        writer(
            {
                "type": "progress",
                "node": "skill_discovery",
                "message": "正在匹配业务技能...",
            }
        )
        configurable = config.get("configurable", {}) or {}
        mode = get_routing_context(configurable).mode
        question = _latest_human_content(state.get("messages", []))

        update: dict[str, Any] = {
            "skill_search_attempted": True,
            "skill_search_question": question,
            "selected_skill": None,
            "selected_skill_path": None,
            "selected_skill_description": None,
            "selected_skill_allowed_tools": [],
            "selected_skill_score": None,
            "skill_multi_candidates": False,
            "skill_rules_content": "",
        }
        if not question:
            return update

        # 纯问候/寒暄轮次不进入 Skill 语义匹配，直接降级通用能力，
        # 避免 "你好" 之类被 embedding 相似度误命中业务 Skill（同时省一次 embedding 调用）。
        if is_conversational(question):
            logger.info(
                "业务图 Skill 跳过：对话/问候轮次，graph={}，question={}",
                self._graph_name,
                question[:60],
            )
            return update

        candidates = await self._router.amatch(question)
        if not candidates:
            logger.info(
                "业务图 Skill 未命中，降级通用能力: graph={}，question={}",
                self._graph_name,
                question[:120],
            )
            return update

        top = candidates[0]
        union_tools = _union_allowed_tools(candidates)
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
            "业务图 Skill 命中: graph={}，skill={}，候选数={}，score={}，allowed_tools={}",
            self._graph_name,
            top.name,
            len(candidates),
            top.similarity_score,
            union_tools,
        )
        update.update(
            {
                "selected_skill": top.name,
                "selected_skill_path": top.path,
                "selected_skill_description": top.description,
                "selected_skill_allowed_tools": union_tools,
                "selected_skill_score": top.similarity_score,
                "skill_multi_candidates": len(candidates) > 1,
                "skill_rules_content": skill_rules,
            }
        )
        return update

    # ──── 模型准备与调用节点 ────

    async def prepare_model(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """模型准备（按意图车道差异化）.

        - chitchat: 小提示词、零工具，跳过 MCP 刷新与权限/技能上下文；
        - knowledge: 仅知识检索指引，本车道工具均为本地工具，无需刷新 MCP；
        - data_query: 全量组装（基础 + 时间 + mode + 权限上下文 + Skill 规则）。
        """
        intent = state.get("intent") or INTENT_DATA_QUERY
        configurable = config.get("configurable", {}) or {}

        if intent == INTENT_CHITCHAT:
            return {"system_prompt": build_chitchat_system_prompt()}

        if intent == INTENT_KNOWLEDGE:
            writer(
                {
                    "type": "progress",
                    "node": "model_preparation",
                    "message": "正在准备知识检索...",
                }
            )
            return {
                "system_prompt": build_knowledge_system_prompt(configurable),
            }

        writer(
            {
                "type": "progress",
                "node": "model_preparation",
                "message": "正在准备分析模型...",
            }
        )

        await ensure_mcp_tools()

        permission_context = _permission_reviewer._permission_context(
            cast(dict[str, Any], state)
        )
        system_prompt = build_system_prompt(
            base_prompt=self._base_system_prompt,
            skill_rules_content=state.get("skill_rules_content", ""),
            permission_context=permission_context,
            configurable=configurable,
        )

        logger.info(
            "业务图模型准备完成: graph={}，system_prompt_length={}",
            self._graph_name,
            len(system_prompt),
        )
        return {"system_prompt": system_prompt}

    def _available_tools(self, state: BusinessGraphState) -> list[Any]:
        """按意图车道与 allowed-tools 过滤业务工具.

        - chitchat 车道：零工具（纯对话，不暴露任何工具 schema）；
        - knowledge 车道：仅始终可见的本地工具（时间 + 知识检索）；
        - data_query 车道：保持原 SkillToolFilterMiddleware(native) 语义：
          allowed 非空 → 交集 + 始终可见；为空 → 全量业务工具；
          多候选场景额外披露 load_skill。
        """
        intent = state.get("intent") or INTENT_DATA_QUERY
        if intent == INTENT_CHITCHAT:
            return []

        business = get_business_tools()
        if intent == INTENT_KNOWLEDGE:
            return [
                t
                for t in business
                if (name := tool_name(t)) is not None
                and name in _ALWAYS_VISIBLE_TOOL_NAMES
            ]

        allowed = state.get("selected_skill_allowed_tools") or []

        if allowed:
            allowed_set = set(allowed) | _ALWAYS_VISIBLE_TOOL_NAMES
            available = [
                t
                for t in business
                if (name := tool_name(t)) is not None and name in allowed_set
            ]
        else:
            available = list(business)

        if state.get("skill_multi_candidates"):
            available = [*available, self._load_skill_tool]
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
        writer(
            {
                "type": "progress",
                "node": "analysis",
                "message": "正在分析数据...",
            }
        )

        model = ModelRegistry.deepseek_v4_flash
        system_prompt = state.get("system_prompt", "")
        available = self._available_tools(state)

        messages = state["messages"]
        full_messages: list[Any] = []
        if system_prompt:
            full_messages.append(SystemMessage(content=system_prompt))
        full_messages.extend(messages)

        if available:
            model_with_tools = model.bind_tools(available)
        else:
            model_with_tools = model

        response = None
        for attempt in range(3):
            try:
                response = await model_with_tools.ainvoke(full_messages)
                break
            except Exception as exc:
                logger.exception(
                    "业务图模型调用失败 (attempt {}): {}", attempt + 1, exc
                )
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    writer(
                        {
                            "type": "final_output_done",
                            "message": _SERVICE_UNAVAILABLE_MESSAGE,
                        }
                    )
                    return {
                        "messages": [AIMessage(content=_SERVICE_UNAVAILABLE_MESSAGE)]
                    }

        # 空回复检测：content 为空且无 tool_calls -> 追加引导重试
        if (
            isinstance(response, AIMessage)
            and not response.tool_calls
            and not get_message_content(response).strip()
        ):
            logger.info("业务图模型空回复，追加引导消息重试")
            nudge_messages = [
                *full_messages,
                response,
                HumanMessage(content=_EMPTY_REPLY_NUDGE),
            ]
            try:
                nudge_response = await model_with_tools.ainvoke(nudge_messages)
                if (
                    isinstance(nudge_response, AIMessage)
                    and not nudge_response.tool_calls
                    and not get_message_content(nudge_response).strip()
                ):
                    logger.warning("业务图模型空回复重试仍为空，使用兜底消息")
                    response = AIMessage(content=_FALLBACK_EMPTY_REPLY)
                else:
                    response = nudge_response
            except Exception as exc:
                logger.warning("业务图模型空回复重试失败: {}，使用兜底消息", exc)
                response = AIMessage(content=_FALLBACK_EMPTY_REPLY)

        return {"messages": [response]}

    # ──── 最终输出节点 ────

    async def _llm_cleanup_fallback(self, answer: str) -> str:
        """LLM 清洗兜底（默认关闭，仅在检测到明显残留时启用）."""
        model = ModelRegistry.deepseek_v4_flash
        prompt = build_final_output_cleanup_prompt(answer)
        async with asyncio.timeout(app_config.FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS):
            response = await model.ainvoke([{"role": "user", "content": prompt}])
        cleaned = get_message_content(response).strip()
        return cleaned or answer

    async def finalize_output(
        self,
        state: BusinessGraphState,
        config: RunnableConfig,
        *,
        writer: StreamWriter,
    ) -> dict[str, Any]:
        """唯一输出出口：确定性清洗 + final_output_delta/done 事件推送.

        推荐追问在正文推送期间并发生成，final_output_done 后短超时等待，
        失败静默跳过，不阻塞主答案。
        """
        answer = state.get("permission_rejection") or _extract_last_visible_answer(
            state
        )
        if not answer:
            logger.warning("业务图最终输出未找到可见业务答案，使用兜底回复")
            answer = _FALLBACK_EMPTY_REPLY

        writer(
            {
                "type": "progress",
                "message": "正在整理最终答案...",
            }
        )

        configurable = config.get("configurable", {}) or {}
        expand_task: asyncio.Task[list[str]] | None = None
        if _expand_question_enabled(configurable):
            question = _latest_human_content(state.get("messages", []))
            expand_task = asyncio.create_task(
                _generate_expanded_questions(question, answer)
            )

        # 确定性清洗；仅当残留明显且显式开启兜底时才走 LLM
        cleaned = deterministic_cleanup(answer)
        if (
            app_config.FINAL_OUTPUT_LLM_CLEANUP_FALLBACK
            and cleaned
            and has_process_residue(cleaned)
        ):
            try:
                cleaned = await self._llm_cleanup_fallback(cleaned)
            except Exception as exc:
                logger.warning("最终输出 LLM 清洗兜底失败，使用规则清洗结果: {}", exc)

        if not cleaned:
            cleaned = _SERVICE_UNAVAILABLE_MESSAGE

        # 分片推送正文增量 + 完成事件（Java 端契约：final_output_delta/done）
        for start in range(0, len(cleaned), _DELTA_CHUNK_SIZE):
            writer(
                {
                    "type": "final_output_delta",
                    "message": cleaned[start : start + _DELTA_CHUNK_SIZE],
                }
            )
        writer(
            {
                "type": "final_output_done",
                "message": cleaned,
            }
        )

        # 正文完成后短超时等待推荐追问
        if expand_task is not None:
            try:
                expanded_questions = await asyncio.wait_for(
                    expand_task,
                    timeout=app_config.EXPAND_QUESTION_TIMEOUT_SECONDS,
                )
                writer(
                    {
                        "node": "expand_question",
                        "type": "expanded_questions",
                        "message": expanded_questions,
                    }
                )
            except Exception as exc:
                expand_task.cancel()
                logger.warning("推荐追问生成失败，已跳过: {}", exc)

        return {"messages": [AIMessage(content=cleaned)]}
