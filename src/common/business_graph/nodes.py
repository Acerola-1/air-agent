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
from typing import Any

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
from common.business_graph.prompting import (
    build_chitchat_system_prompt,
    build_knowledge_system_prompt,
    build_system_prompt,
)
from common.business_graph.skill_content import load_skill_rules
from common.business_graph.state import BusinessGraphState
from common.context import get_message_content, get_routing_context
from common.mcp_client import ensure_mcp_tools
from common.models import ModelRegistry
from common.permission.rules import is_conversational
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
    {"get_beijing_time", "create_artifact", "load_skill"}
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
        intent, reason = classify_intent_rule(question)
        logger.info(
            "业务图意图车道: graph={}，intent={}，reason={}，question={}",
            self._graph_name,
            intent,
            reason,
            question[:60],
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
        return update

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
            return {"system_prompt": build_chitchat_system_prompt()}
        if intent == INTENT_KNOWLEDGE:
            return {
                "system_prompt": build_knowledge_system_prompt(configurable),
            }

        await ensure_mcp_tools()

        system_prompt = build_system_prompt(
            base_prompt=self._base_system_prompt,
            skill_rules_content=state.get("skill_rules_content", ""),
            configurable=configurable,
        )

        logger.info(
            "业务图模型准备完成: graph={}，system_prompt_length={}",
            self._graph_name,
            len(system_prompt),
        )
        return {"system_prompt": system_prompt}

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

        business = get_business_tools()
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

    # ──── 工具过滤 ────
