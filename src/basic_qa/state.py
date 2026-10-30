"""Basic QA 会话状态与受限输入定义."""

from __future__ import annotations

from typing import Annotated, Any, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from common.runtime_events import merge_processing_status


class BasicQAState(TypedDict):
    """本图的消息、会话能力、当次工具定义和上下文处理状态.

    intent 沿用旧字段名，仅区分输入超限与入口通过，不做意图分类。
    bqa_active_skill、user_id、skill_rules_content、needs_planning 仅保留为
    旧检查点通道；当前身份取自可信运行上下文，能力取自 bqa_capabilities，
    规则按当前材料组装。旧 needs_planning 值不再决定路由。
    """

    messages: Annotated[list[AnyMessage], add_messages]
    bqa_turn_key: NotRequired[str]
    bqa_turn_digest: NotRequired[str]
    bqa_active_skill: NotRequired[dict[str, Any] | None]
    bqa_capabilities: NotRequired[dict[str, Any]]
    bqa_disclosure: NotRequired[dict[str, Any] | None]
    intent: NotRequired[str]
    user_id: NotRequired[str]
    skill_rules_content: NotRequired[str]
    system_prompt: NotRequired[str]
    summary: NotRequired[str]
    summary_covered_message_id: NotRequired[str]
    needs_planning: NotRequired[bool]  # 仅兼容旧检查点，不再读写或决定路由。
    planning_draft: NotRequired[str]
    context_metrics: NotRequired[dict[str, Any]]
    context_status: NotRequired[dict[str, Any]]
    data_status: Annotated[dict[str, Any], merge_processing_status]
    data_model_started_at: NotRequired[str]


class BasicQAInput(TypedDict):
    """Only messages enter from a new invocation; capabilities come from checkpoints."""

    messages: list[AnyMessage]
