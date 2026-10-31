"""旧 find_skill 流程退役；单一 rules.md 材料与 v2 检索的边界保留."""

from pathlib import Path

from basic_qa.capabilities import skill_materials as materials


def test_material_reading_is_independent_of_legacy_state_machine(tmp_path):
    directory = tmp_path / "sample"
    (directory / "references").mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: sample\nallowed-tools: main\n"
        "required-tools: main\noptional-tools:\n---\n完整领域规则",
        encoding="utf-8",
    )
    (directory / "references/rules.md").write_text("统一规则", encoding="utf-8")
    snapshot = materials._read_snapshot(directory)
    assert "references/rules.md" in snapshot
    parsed = materials._parse(snapshot)
    assert parsed.rules == "完整领域规则\n\n=====\n\n统一规则"


def test_legacy_find_skill_cannot_become_a_production_entry():
    assert not Path("src/common/skill_discovery.py").exists()
    assert not Path("src/common/skills/materials.py").exists()
    from basic_qa.capabilities.skill_search import create_search_skills_tool

    assert create_search_skills_tool(None).name == "search_skills"
