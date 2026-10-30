"""Basic QA 会话状态与受限输入定义."""

from __future__ import annotations

from typing import Annotated, Any, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from common.runtime_events import merge_processing_status

# intent 通道记录入口校验结果，取值属于持久化契约。
INPUT_ACCEPTED = "data_query"
INPUT_OVERFLOW = "input_overflow"


class BasicQAState(TypedDict):
    """本图的消息、会话能力、当次工具定义和上下文处理状态.

    intent 区分输入超限与入口通过。身份来自可信运行上下文，
    当前规则由有效材料重建。
    data_model_started_at 是本图、数据分析图与公共计算工具共享的计时字段，
    更名需要同步所有读写方。

    兼容字段：bqa_active_skill 由 session_state 校验后用于 v1 → v2 迁移；
    user_id／skill_rules_content／needs_planning 保留为检查点兼容通道，
    当前业务和路由不依赖其值。
    """

    messages: Annotated[list[AnyMessage], add_messages]
    bqa_turn_key: NotRequired[str]
    bqa_turn_digest: NotRequired[str]
    bqa_capabilities: NotRequired[dict[str, Any]]
    bqa_disclosure: NotRequired[dict[str, Any] | None]
    intent: NotRequired[str]  # 持久化兼容：data_query=入口通过，input_overflow=超限。
    system_prompt: NotRequired[str]
    summary: NotRequired[str]
    summary_covered_message_id: NotRequired[str]
    planning_draft: NotRequired[str]
    context_metrics: NotRequired[dict[str, Any]]
    context_status: NotRequired[dict[str, Any]]
    data_status: Annotated[dict[str, Any], merge_processing_status]
    data_model_started_at: NotRequired[str]

    # v1 迁移输入；session_state 复核历史调用与回执后才转换为 v2 能力。
    bqa_active_skill: NotRequired[dict[str, Any] | None]
    # 检查点兼容通道，当前业务和路由不依赖这些值。
    user_id: NotRequired[str]
    skill_rules_content: NotRequired[str]
    needs_planning: NotRequired[bool]


class BasicQAInput(TypedDict):
    """Only messages enter from a new invocation; capabilities come from checkpoints."""

    messages: list[AnyMessage]
