"""智能报告生成图入口."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain.agents.middleware.types import AgentMiddleware
from langchain_quickjs import CodeInterpreterMiddleware

from common.mcp_client import ensure_mcp_tools_sync, get_business_mcp_tools
from common.middleware import TimeContextMiddleware
from common.models import ModelRegistry
from common.prompts import with_main_agent_tool_use_output_guard
from common.skill_discovery import create_find_skill_tool
from common.skill_router import SkillSemanticRouter

SYSTEM_PROMPT = """
    ## 角色职责
    你是中科宇图的空气质量数据查询助手,擅长寻找 skill 和 tool 处理基础空气质量数据查询，以及环境保护和空气治理知识,
    处理空气质量专业问题时，不要仅凭常识直接作答。必须先调用 find_skill 查找匹配 skill；
    find_skill 命中后：若返回单个候选，其完整规则已内联在结果的 skill_instructions 中，直接据此执行；
    若返回多个候选，先从候选摘要中选择最匹配的一个，必要时读取该候选的 references_path 获取完整流程，再使用披露出来的业务工具执行查询与回答。
    如果 find_skill 未命中，则回到 DeepAgents 原生能力：根据 find_skill 返回结果、工具说明和问题语义自行判断，
    可按需读取相关 Skill 的 references 规则并选择工具，但数据必须来自真实工具的有效数据，不得捏造。

"""

SKILLS_DIR = Path(__file__).parent / "skills"


def _build_middleware() -> list[AgentMiddleware[Any, Any, Any]]:
    return [
        TimeContextMiddleware(),
        CodeInterpreterMiddleware(ptc=[]),
    ]


find_skill = create_find_skill_tool(SkillSemanticRouter(SKILLS_DIR))

# MCP 工具懒加载：datacenter MCP 服务不可达时自动降级为空列表，图照常编译
ensure_mcp_tools_sync()
_mcp_tools = get_business_mcp_tools()

graph = create_deep_agent(
    model=ModelRegistry.deepseek_v4_flash,
    tools=[find_skill, *_mcp_tools],
    system_prompt=with_main_agent_tool_use_output_guard(SYSTEM_PROMPT),
    middleware=_build_middleware(),
    backend=CompositeBackend(
        default=StateBackend(),
        routes={
            "/skills/": FilesystemBackend(root_dir=str(SKILLS_DIR), virtual_mode=True),
        },
    ),
    checkpointer=None,
    name="intelligent-report",
)
