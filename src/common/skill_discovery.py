"""渐进式 Skill 发现状态与工具."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

from common.context import get_routing_context
from common.skill_router import SkillRouteCandidate, SkillSemanticRouter


class SkillDiscoveryState(AgentState):
    """渐进式 Skill 发现使用的 Agent 状态字段."""

    skill_search_attempted: NotRequired[bool]
    skill_search_question: NotRequired[str | None]
    selected_skill: NotRequired[str | None]
    selected_skill_path: NotRequired[str | None]
    selected_skill_description: NotRequired[str | None]
    selected_skill_allowed_tools: NotRequired[list[str]]
    selected_skill_score: NotRequired[float | None]
    permission_need: NotRequired[str]
    permission_result: NotRequired[dict[str, object]]
    permission_query_overrides: NotRequired[dict[str, object]]
    user_id: NotRequired[str]


def _read_skill_body(skill_file: Path) -> str:
    """读取 SKILL.md frontmatter 之后的正文（路由摘要与通用约束）."""
    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return ""
    return parts[2].strip()


def _references_filename(mode: str) -> str:
    """按运行模式选择 references 规则文件名."""
    return "expert.md" if mode == "expert" else "fast.md"


def _read_references(skill_file: Path, mode: str) -> str:
    """读取命中 Skill 对应 mode 的完整 references 规则内容."""
    references_dir = skill_file.parent / "references"
    rules_file = references_dir / _references_filename(mode)
    if not rules_file.exists():
        rules_file = references_dir / "fast.md"
    if not rules_file.exists():
        return ""
    return rules_file.read_text(encoding="utf-8")


def _references_exposed_path(candidate: SkillRouteCandidate, mode: str) -> str:
    """将候选的 references 规则文件映射为后端可见的虚拟路径."""
    filename = _references_filename(mode)
    if candidate.path.endswith("SKILL.md"):
        return candidate.path[: -len("SKILL.md")] + f"references/{filename}"
    return candidate.path


def _union_allowed_tools(candidates: Sequence[SkillRouteCandidate]) -> list[str]:
    """返回所有候选 allowed-tools 的并集（保持稳定顺序，便于跨 Skill 组合）."""
    union: list[str] = []
    for candidate in candidates:
        for name in candidate.allowed_tools:
            if name not in union:
                union.append(name)
    return union


def _no_match_payload() -> dict[str, object]:
    """返回标准的未命中工具输出载荷."""
    return {
        "matched": False,
        "skill_name": None,
        "skill_path": None,
        "description": None,
        "allowed_tools": [],
        "similarity_score": None,
        "next_step": "未找到明确匹配 Skill，请澄清问题或使用通用能力回答。",
    }


def _no_match_update(tool_call_id: str, question: str) -> dict[str, Any]:
    """未命中时的 state 更新（清空所选 Skill，降级为通用能力）."""
    return {
        "messages": [
            ToolMessage(
                content=json.dumps(_no_match_payload(), ensure_ascii=False),
                tool_call_id=tool_call_id,
            )
        ],
        "skill_search_attempted": True,
        "skill_search_question": question,
        "selected_skill": None,
        "selected_skill_path": None,
        "selected_skill_description": None,
        "selected_skill_allowed_tools": [],
        "selected_skill_score": None,
    }


def _single_candidate_payload(
    candidate: SkillRouteCandidate,
    mode: str,
    router: SkillSemanticRouter,
    allowed_tools: list[str],
) -> dict[str, object]:
    """单一命中：直接内联 SKILL.md 正文与对应 mode 的完整 references，免去 read_file 双跳."""
    skill_file = router.resolve_skill_file(candidate.name)
    instructions = ""
    if skill_file is not None:
        body = _read_skill_body(skill_file)
        references = _read_references(skill_file, mode)
        instructions = "\n\n=====\n\n".join(part for part in (body, references) if part)

    if instructions:
        next_step = "已加载该 Skill 的完整规则，请直接据此执行数据获取与回答，无需再读取文件。"
    else:
        next_step = "请读取 skill_path 对应的完整 Skill 说明，再执行查询。"

    return {
        "matched": True,
        "candidate_count": 1,
        "skill_name": candidate.name,
        "skill_path": candidate.path,
        "description": candidate.description,
        "allowed_tools": allowed_tools,
        "similarity_score": candidate.similarity_score,
        "skill_instructions": instructions,
        "next_step": next_step,
    }


def _multi_candidate_payload(
    candidates: Sequence[SkillRouteCandidate],
    mode: str,
    allowed_tools: list[str],
) -> dict[str, object]:
    """多命中：仅内联各候选摘要与 references 路径，由模型自选后按需读取."""
    items: list[dict[str, object]] = [
        {
            "skill_name": candidate.name,
            "skill_path": candidate.path,
            "description": candidate.description,
            "similarity_score": candidate.similarity_score,
            "allowed_tools": list(candidate.allowed_tools),
            "references_path": _references_exposed_path(candidate, mode),
        }
        for candidate in candidates
    ]
    return {
        "matched": True,
        "candidate_count": len(candidates),
        "allowed_tools": allowed_tools,
        "candidates": items,
        "next_step": (
            "以下为多个候选 Skill 的摘要，请结合问题选择最匹配的一个；"
            "如需完整执行流程，读取该候选的 references_path 再执行查询与回答。"
        ),
    }


def create_find_skill_tool(
    router: SkillSemanticRouter,
    *,
    name: str = "find_skill",
):
    """创建绑定到路由器的当前业务图 Skill 发现工具."""

    @tool(name)
    async def find_skill(
        question: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        config: RunnableConfig,
    ) -> Command[Any]:
        """根据用户问题查找最匹配的业务 Skill，命中后直接内联其规则与允许的工具."""
        candidates: Sequence[SkillRouteCandidate] = await router.amatch(question)
        if not candidates:
            return Command(update=_no_match_update(tool_call_id, question))

        mode = get_routing_context(config.get("configurable")).mode
        union_tools = _union_allowed_tools(candidates)
        top = candidates[0]

        if len(candidates) == 1:
            payload = _single_candidate_payload(top, mode, router, union_tools)
        else:
            payload = _multi_candidate_payload(candidates, mode, union_tools)

        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps(payload, ensure_ascii=False),
                        tool_call_id=tool_call_id,
                    )
                ],
                "skill_search_attempted": True,
                "skill_search_question": question,
                "selected_skill": top.name,
                "selected_skill_path": top.path,
                "selected_skill_description": top.description,
                "selected_skill_allowed_tools": union_tools,
                "selected_skill_score": top.similarity_score,
            }
        )

    return find_skill
