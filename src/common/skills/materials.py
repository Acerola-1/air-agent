"""共享 Skill 材料读取、references 选择与文件缓存.

本模块不创建加载工具、不选择 Skill，也不拥有图的会话状态。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

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
