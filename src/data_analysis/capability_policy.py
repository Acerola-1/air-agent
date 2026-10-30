"""数据分析目录的菜单、标题和工具依赖规则."""

from collections.abc import Iterable

from data_analysis.menu_skill_mapping import (
    DATA_ANALYSIS_SKILL_MAPPINGS,
    match_menu_skill,
)


def titles() -> dict[str, str]:
    """目录沿用菜单映射中维护的标题."""
    return {
        mapping.skill_name: mapping.title for mapping in DATA_ANALYSIS_SKILL_MAPPINGS
    }


def menu_skill(menu_name: str | None) -> str | None:
    """目录保持原来的大小写敏感菜单精确匹配."""
    for mapping in DATA_ANALYSIS_SKILL_MAPPINGS:
        if menu_name in mapping.menu_names:
            return mapping.skill_name
    return None


def required_tool_ids(references: Iterable[str]) -> list[str]:
    """数据分析材料的 required-tools 直接作为目录依赖."""
    return list(references)


def validate_selection(skill_slug: str, menu_name: str | None) -> None:
    """受理保留归一化菜单匹配；映射冲突或策略故障拒绝."""
    matched = match_menu_skill(menu_name)
    if matched is not None and matched.skill_name != skill_slug:
        raise ValueError(f"所选技能 {skill_slug} 与当前菜单 {menu_name} 映射不一致")
