"""可信 Skill 静态材料加载的隔离测试."""

from pathlib import Path

import pytest

from basic_qa.capabilities import skill_materials as materials
from common.capabilities import calculate_skill_material_version
from common.skill_tool_scope import ScopedTool, resolve_skill_tools


@pytest.fixture
def fixture_skill(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    directory = root / "sample"
    references = directory / "references"
    references.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: sample\nallowed-tools: main chart_test\n"
        "required-tools: main\noptional-tools: chart_test\n---\n\n总纲规则\n",
        encoding="utf-8",
    )
    (references / "fast.md").write_text("快速规则\n", encoding="utf-8")
    (references / "expert.md").write_text("专家规则\n", encoding="utf-8")
    monkeypatch.setattr(materials, "_BASIC_QA_ROOT", root)
    materials.clear_material_cache()
    return directory


def _resolver(catalog):
    def resolve(allowed, required, optional):
        return resolve_skill_tools(
            allowed=allowed,
            required=required,
            optional=optional,
            catalog=catalog,
            bindings={"main": "mcp:ipp:main", "chart_test": "local:chart_test"},
            chart_tool_ids={"local:chart_test"},
        )

    return resolve


def _load(directory, *, mode="fast", catalog=None):
    if catalog is None:
        catalog = [ScopedTool(id="mcp:ipp:main", name="main")]
    return materials.load_basic_qa_material(
        "skill:basic-qa:sample",
        calculate_skill_material_version(directory),
        mode,
        resolve_scope=_resolver(catalog),
    )


def test_snapshot_and_cache_revalidate_scope(fixture_skill):
    first = _load(fixture_skill)
    assert first.rules == "总纲规则\n\n=====\n\n快速规则"
    assert first.tool_scope.allowed_ids == frozenset({"mcp:ipp:main"})
    assert first.tool_scope.unavailable_optional == ("chart_test",)
    assert not first.cache_hit
    second = _load(
        fixture_skill,
        catalog=[
            ScopedTool(id="mcp:ipp:main", name="main"),
            ScopedTool(id="local:chart_test", name="chart_test"),
        ],
    )
    assert second.cache_hit
    assert second.tool_scope.allowed_ids == frozenset(
        {"mcp:ipp:main", "local:chart_test"}
    )
    assert second.tool_scope.unavailable_optional == ()
    with pytest.raises(materials.SkillMaterialError, match="scope"):
        _load(fixture_skill, catalog=[])
    assert _load(fixture_skill, mode="expert").rules.endswith("专家规则")


@pytest.mark.parametrize(
    "skill_id",
    [
        "skill:data-analysis:sample",
        "skill:basic-qa:../sample",
        "sample",
        "skill:basic-qa:missing",
    ],
)
def test_rejects_unregistered_and_path_ids(fixture_skill, skill_id):
    with pytest.raises(materials.SkillMaterialError):
        materials.load_basic_qa_material(
            skill_id,
            calculate_skill_material_version(fixture_skill),
            "fast",
            resolve_scope=_resolver([]),
        )


def test_missing_mode_never_falls_back(fixture_skill):
    (fixture_skill / "references/expert.md").unlink()
    with pytest.raises(materials.SkillMaterialError, match="material unavailable"):
        _load(fixture_skill, mode="expert")
    with pytest.raises(materials.SkillMaterialError, match="mode"):
        _load(fixture_skill, mode="not-server-mode")


def test_changed_version_or_read_error_fails_closed(fixture_skill, monkeypatch):
    original = calculate_skill_material_version(fixture_skill)
    (fixture_skill / "references/fast.md").write_text("新规则", encoding="utf-8")
    with pytest.raises(materials.SkillMaterialError, match="version changed"):
        materials.load_basic_qa_material(
            "skill:basic-qa:sample", original, "fast", resolve_scope=_resolver([])
        )
    original_read = Path.read_bytes

    def deny(path):
        if path.name == "SKILL.md":
            raise OSError("denied")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", deny)
    with pytest.raises(materials.SkillMaterialError, match="read failed"):
        materials.verify_basic_qa_material_version("skill:basic-qa:sample", original)


def test_race_after_scope_resolution_is_rejected(fixture_skill):
    version = calculate_skill_material_version(fixture_skill)
    resolver = _resolver([ScopedTool(id="mcp:ipp:main", name="main")])

    def modify_then_resolve(*args):
        (fixture_skill / "references/fast.md").write_text("已改变", encoding="utf-8")
        return resolver(*args)

    with pytest.raises(materials.SkillMaterialError, match="changed during load"):
        materials.load_basic_qa_material(
            "skill:basic-qa:sample", version, "fast", resolve_scope=modify_then_resolve
        )


def test_invalid_classification_and_symlink_rejected(fixture_skill):
    skill_file = fixture_skill / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text().replace(
            "optional-tools: chart_test", "optional-tools: main chart_test"
        )
    )
    with pytest.raises(materials.SkillMaterialError, match="classification"):
        _load(fixture_skill)
    (fixture_skill / "link").symlink_to(fixture_skill / "SKILL.md")
    with pytest.raises(
        materials.SkillMaterialError, match="invalid skill material file"
    ):
        _load(fixture_skill)


def test_real_material_versions_and_both_modes():
    root = Path(__file__).resolve().parents[2] / "src/basic_qa/skills"
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        snapshot = materials._read_snapshot(directory)
        assert materials._fingerprint(snapshot) == calculate_skill_material_version(
            directory
        )
        for mode in ("fast", "expert"):
            parsed = materials._parse(snapshot, mode)
            assert parsed.rules and parsed.required
            assert set(parsed.required).isdisjoint(parsed.optional)
            assert set(parsed.required) | set(parsed.optional) == set(parsed.allowed)
