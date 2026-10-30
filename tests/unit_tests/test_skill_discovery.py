"""旧find_skill流程退役；材料读取与v2检索的边界保留."""

from pathlib import Path

from common.skills.materials import _read_skill_body, _references_filename


def test_material_reading_is_independent_of_legacy_state_machine(tmp_path):
    material = tmp_path / "SKILL.md"
    material.write_text("---\nname: sample\n---\n完整领域规则")
    assert _read_skill_body(material) == "完整领域规则"
    assert _references_filename("expert") == "expert.md"
    assert _references_filename("fast") == "fast.md"


def test_legacy_find_skill_cannot_become_a_production_entry():
    assert not Path("src/common/skill_discovery.py").exists()
    from basic_qa.capabilities.skill_search import create_search_skills_tool

    assert create_search_skills_tool(None).name == "search_skills"
