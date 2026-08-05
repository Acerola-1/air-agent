"""业务图 Skill 规则内容加载、缓存与 load_skill 工具.

resolve_skill 节点与 load_skill 工具共用同一份 (SKILL.md, mode) 缓存，
避免每次请求重复读取和解析静态 Skill 文件。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from loguru import logger

from common.context import get_routing_context
from common.skill_discovery import _read_skill_body, _references_filename
from common.skill_router import SkillSemanticRouter

# (SKILL.md 路径, mode) -> (SKILL.md mtime, references mtime, 合并后的规则文本)
_rules_cache: dict[tuple[str, str], tuple[float, float, str]] = {}


def clear_skill_rules_cache() -> None:
    """清空 Skill 规则内容缓存，供测试使用."""
    _rules_cache.clear()


def _resolve_references_file(skill_file: Path, mode: str) -> Path | None:
    """按运行模式解析 references 规则文件路径（缺失时回退 fast.md）."""
    references_dir = skill_file.parent / "references"
    rules_file = references_dir / _references_filename(mode)
    if not rules_file.exists():
        rules_file = references_dir / "fast.md"
    return rules_file if rules_file.exists() else None


def load_skill_rules(
    router: SkillSemanticRouter,
    skill_name: str,
    mode: str,
) -> str:
    """加载命中 Skill 的完整规则（SKILL.md 正文 + mode references），带 mtime 缓存."""
    skill_file = router.resolve_skill_file(skill_name)
    if skill_file is None:
        return ""

    references_file = _resolve_references_file(skill_file, mode)
    cache_key = (str(skill_file), mode)
    try:
        skill_mtime = skill_file.stat().st_mtime
        ref_mtime = references_file.stat().st_mtime if references_file else 0.0
    except OSError:
        skill_mtime = -1.0
        ref_mtime = -1.0

    cached = _rules_cache.get(cache_key)
    if (
        cached
        and skill_mtime >= 0
        and cached[0] == skill_mtime
        and cached[1] == ref_mtime
    ):
        return cached[2]

    try:
        body = _read_skill_body(skill_file)
        references = (
            references_file.read_text(encoding="utf-8") if references_file else ""
        )
    except OSError as exc:
        logger.warning("读取 Skill 规则文件失败: skill={}, error={}", skill_name, exc)
        return ""

    rules = "\n\n=====\n\n".join(
        part for part in (body.strip(), references.strip()) if part
    )
    if skill_mtime >= 0:
        _rules_cache[cache_key] = (skill_mtime, ref_mtime, rules)
    return rules


def create_load_skill_tool(router: SkillSemanticRouter):
    """创建绑定路由器的 load_skill 工具（仅多候选场景披露给模型）."""

    @tool("load_skill")
    async def load_skill(
        skill_name: str,
        config: RunnableConfig,
    ) -> str:
        """按名称加载候选 Skill 的完整执行规则.

        仅当系统提示中的其他候选 Skill 摘要比当前已内联的规则更匹配用户问题时调用，
        skill_name 必须取自候选摘要中的 skill_name 字段。
        """
        mode = get_routing_context(config.get("configurable")).mode
        rules = load_skill_rules(router, skill_name, mode)
        if rules:
            return rules
        return (
            f"未找到名为 {skill_name} 的 Skill，请从候选摘要中选择有效的 skill_name。"
        )

    return load_skill
