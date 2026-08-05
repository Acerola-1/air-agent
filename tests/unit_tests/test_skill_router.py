"""Skill 语义路由元数据测试."""

from __future__ import annotations

from common.skill_router import SkillSemanticRouter, load_skill_metadata


def test_load_skill_metadata_parses_allowed_tools(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "air-quality-basic-query"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: air-quality-basic-query
description: |
    查询空气质量
    - 今日某地空气质量怎么样？
allowed-tools: helper_get_latest_time mcp_city_common_get_air_quality_realtime_stat helper_get_latest_time
---

# 技能
""",
        encoding="utf-8",
    )

    skills = load_skill_metadata(tmp_path / "skills")

    assert len(skills) == 1
    assert skills[0].name == "air-quality-basic-query"
    assert skills[0].allowed_tools == (
        "helper_get_latest_time",
        "mcp_city_common_get_air_quality_realtime_stat",
    )


def test_load_skill_metadata_defaults_missing_allowed_tools_to_empty(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "general-query"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: general-query
description: 查询通用问题
---

# 技能
""",
        encoding="utf-8",
    )

    skills = load_skill_metadata(tmp_path / "skills")

    assert len(skills) == 1
    assert skills[0].allowed_tools == ()


def test_load_skill_metadata_parses_allowed_tools_list(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "broadcast-hour"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: broadcast-hour
description: |
    查询小时播报数据
    - 今天某地小时空气质量如何？
allowed-tools:
    - helper_get_latest_time
    - broadcastHour
    - helper_get_latest_time
metadata:
    mode: fast
---

# 技能
""",
        encoding="utf-8",
    )

    skills = load_skill_metadata(tmp_path / "skills")

    assert len(skills) == 1
    assert skills[0].name == "broadcast-hour"
    assert skills[0].allowed_tools == ("helper_get_latest_time", "broadcastHour")


def test_skill_router_exposes_virtual_skill_path(tmp_path) -> None:
    skill_file = tmp_path / "skills" / "air-quality-basic-query" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# 技能\n", encoding="utf-8")
    router = SkillSemanticRouter(tmp_path / "skills")

    assert (
        router._exposed_skill_path(skill_file)
        == "/skills/air-quality-basic-query/SKILL.md"
    )
