"""业务图（basic_qa）节点流状态定义."""

from __future__ import annotations

from typing import Annotated, Any, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from common.runtime_events import merge_processing_status


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
    summary: NotRequired[str]
    summary_covered_message_id: NotRequired[str]
    context_metrics: NotRequired[dict[str, Any]]
    context_status: NotRequired[dict[str, Any]]
    data_status: Annotated[dict[str, Any], merge_processing_status]
    data_model_started_at: NotRequired[str]
