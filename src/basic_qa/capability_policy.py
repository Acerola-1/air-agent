"""Basic QA catalogue policy for trusted application assembly."""

from collections.abc import Iterable

from common.skill_tool_scope import registered_required_tool_ids


def titles() -> dict[str, str]:
    """Return the current Basic QA catalogue titles."""
    return {
        "air-quality-basic-query": "基础空气质量查询",
        "comparison-composition": "同比与污染物构成",
        "compliance-feasibility": "达标可行性研判",
        "ranking-assessment": "空气质量排名考核",
        "regional-benchmark": "区域空气质量对标",
        "station-extreme": "站点数据与极值",
        "trend-analysis": "空气质量趋势分析",
    }


def menu_skill(menu_name: str) -> str | None:
    """Return no menu-specific Skill for Basic QA."""
    return None


def required_tool_ids(references: Iterable[str]) -> list[str]:
    """Map required references to registered stable tool identities."""
    return registered_required_tool_ids("basic-qa", references)
