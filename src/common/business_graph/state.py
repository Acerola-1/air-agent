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
        selected_skill: 语义路由命中的 Skill 名称
        selected_skill_path: Skill 在后端可见的虚拟路径
        selected_skill_description: Skill 业务描述
        selected_skill_allowed_tools: 候选 Skill allowed-tools 并集
        selected_skill_score: 命中候选的相似度分数
        skill_multi_candidates: 是否存在多个候选（决定是否披露 load_skill 工具）
        skill_rules_content: 内联注入系统提示的 Skill 规则全文
        skill_search_attempted: 是否已执行 Skill 匹配
        skill_search_question: 参与匹配的问题文本
        system_prompt: 组装后的完整系统提示词
    """

    messages: Annotated[list[AnyMessage], add_messages]
    intent: NotRequired[str]
    user_id: NotRequired[str]
    selected_skill: NotRequired[str | None]
    selected_skill_path: NotRequired[str | None]
    selected_skill_description: NotRequired[str | None]
    selected_skill_allowed_tools: NotRequired[list[str]]
    selected_skill_score: NotRequired[float | None]
    skill_multi_candidates: NotRequired[bool]
    skill_rules_content: NotRequired[str]
    skill_search_attempted: NotRequired[bool]
    skill_search_question: NotRequired[str]
    system_prompt: NotRequired[str]
