"""Skill 目录依赖来自真实材料，不依赖已撤回的用户选择协议。"""

from common.capabilities import (
    GRAPH_SKILLS_DIRS,
    _parse_skill_frontmatter,
    list_graph_skills,
)


def test_required_tool_ids_agree_with_skill_material():
    items = list_graph_skills("basic-qa")
    assert len(items) == 9
    for item in items:
        material = GRAPH_SKILLS_DIRS["basic-qa"] / item.name / "SKILL.md"
        meta = _parse_skill_frontmatter(material.read_text())
        declared = {f"mcp:ipp:{name}" for name in meta["required-tools"]}
        assert set(item.required_tool_ids) == declared
        assert declared
        assert all("chart_" not in tool for tool in declared)
