"""业务图（basic_qa / intelligent_analysis）节点流状态定义."""

from __future__ import annotations

from typing import Annotated, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class BusinessGraphState(TypedDict):
    """业务图 LangGraph 节点流状态.

    字段说明:
        messages: 对话消息列表，使用 add_messages reducer 合并
        intent: 入口意图车道（chitchat/knowledge/data_query）
        user_id: 当前请求用户 ID（来自 configurable）
        skill_rules_content: 内联注入系统提示的 Skill 规则全文（top1 完整规则 + 其他候选摘要）
        system_prompt: 组装后的完整系统提示词
    """

    messages: Annotated[list[AnyMessage], add_messages]
    intent: NotRequired[str]
    user_id: NotRequired[str]
    skill_rules_content: NotRequired[str]
    system_prompt: NotRequired[str]
