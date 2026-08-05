"""data_analysis 节点流状态定义."""

from __future__ import annotations

from typing import Annotated, NotRequired

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class DataAnalysisState(TypedDict):
    """data_analysis LangGraph 节点流状态.

    字段说明:
        messages: 对话消息列表，使用 add_messages reducer 合并
        selected_skill: 当前匹配到的 Skill 名称
        selected_skill_path: Skill 在后端可见的虚拟路径
        selected_skill_description: Skill 业务描述
        selected_skill_allowed_tools: Skill 允许的业务工具名列表
        skill_rules_content: Skill 规则文件全文（references/fast.md 或 expert.md）
        skill_search_attempted: 是否已尝试 Skill 匹配
        skill_search_question: 传入的问题文本
        system_prompt: 组装后的完整系统提示词
    """

    messages: Annotated[list[AnyMessage], add_messages]
    selected_skill: NotRequired[str | None]
    selected_skill_path: NotRequired[str | None]
    selected_skill_description: NotRequired[str | None]
    selected_skill_allowed_tools: NotRequired[list[str]]
    skill_rules_content: NotRequired[str]
    skill_search_attempted: NotRequired[bool]
    skill_search_question: NotRequired[str]
    system_prompt: NotRequired[str]
