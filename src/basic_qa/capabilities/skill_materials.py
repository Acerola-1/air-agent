"""Basic QA 静态 Skill 材料快照；身份、受理和实时工具范围由调用方校验."""

from __future__ import annotations

import hashlib
import os
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from common.skill_tool_scope import SkillToolScope, ToolScopeError

_BASIC_QA_ROOT = Path(__file__).resolve().parents[1] / "skills"
_SKILL_ID = re.compile(r"skill:basic-qa:([a-z0-9][a-z0-9_-]{0,63})")
_VERSION = re.compile(r"sha256:[0-9a-f]{64}")
_REFERENCE = re.compile(
    r"`((?:mcp_|chart_|text2sql_)[A-Za-z0-9_]+|helper_get_latest_time|"
    r"broadcastHour|integratedIndexRatio|cityFactorRankings|oneIndexManyCityLine)`"
)
_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_CACHE_LIMIT = 64


class SkillMaterialError(ValueError):
    """材料不可用或与请求版本不一致；不包含文件内容."""


@dataclass(frozen=True)
class _Material:
    rules: str
    allowed: tuple[str, ...]
    required: tuple[str, ...]
    optional: tuple[str, ...]


@dataclass(frozen=True)
class LoadedSkillMaterial:
    """静态材料与本次经调用方重新计算的工具范围，不代表已激活."""

    skill_id: str
    material_version: str
    mode: str
    rules: str
    tool_scope: SkillToolScope
    cache_hit: bool
    schema_versions: tuple[tuple[str, str], ...] = ()
    catalog_scope: str = ""


# 回调只由可信服务端构造，必须依次对业务、连接、受理选择与可信工具绑定收窄。
ScopeResolver = Callable[
    [tuple[str, ...], tuple[str, ...], tuple[str, ...]], SkillToolScope
]
_cache: OrderedDict[tuple[str, str, str], _Material] = OrderedDict()
_cache_lock = RLock()


def clear_material_cache() -> None:
    """清空静态材料缓存，不改变任何运行状态或权限."""
    with _cache_lock:
        _cache.clear()


def _directory(skill_id: str) -> Path:
    if not isinstance(skill_id, str) or not (match := _SKILL_ID.fullmatch(skill_id)):
        raise SkillMaterialError("invalid Basic QA skill id")
    directory = _BASIC_QA_ROOT / match.group(1)
    if directory.is_symlink() or not directory.is_dir():
        raise SkillMaterialError("skill directory unavailable")
    return directory


def _read_snapshot(directory: Path) -> dict[str, bytes]:
    """严格读取与目录指纹同一文件集合；异常、符号链接均失败关闭."""
    files: dict[str, bytes] = {}

    def fail_walk(_error: OSError) -> None:
        raise SkillMaterialError("skill material read failed") from _error

    try:
        for base, dirs, names in os.walk(
            directory, onerror=fail_walk, followlinks=False
        ):
            base_path = Path(base)
            if any((base_path / name).is_symlink() for name in dirs):
                raise SkillMaterialError("symbolic link in skill material")
            for name in names:
                if name.startswith("."):
                    continue
                path = base_path / name
                if path.is_symlink() or not path.is_file():
                    raise SkillMaterialError("invalid skill material file")
                files[path.relative_to(directory).as_posix()] = path.read_bytes()
    except OSError as exc:
        raise SkillMaterialError("skill material read failed") from exc
    return files


def _fingerprint(snapshot: dict[str, bytes]) -> str:
    hasher = hashlib.sha256()
    for path, content in sorted(snapshot.items()):
        hasher.update(f"{path}:".encode())
        hasher.update(content)
        hasher.update(b"\n")
    return f"sha256:{hasher.hexdigest()}"


def _parse(snapshot: dict[str, bytes], mode: str) -> _Material:
    try:
        main = snapshot["SKILL.md"].decode("utf-8")
        references = snapshot[f"references/{mode}.md"].decode("utf-8")
    except (KeyError, UnicodeDecodeError) as exc:
        raise SkillMaterialError("required skill material unavailable") from exc
    parts = main.split("---", 2)
    if (
        len(parts) != 3
        or parts[0].strip()
        or not parts[2].strip()
        or not references.strip()
    ):
        raise SkillMaterialError("invalid skill material")
    fields: dict[str, tuple[str, ...]] = {}
    for field in ("allowed-tools", "required-tools", "optional-tools"):
        matches = re.findall(rf"^{field}:[ \t]*([^\n\r]*)$", parts[1], re.MULTILINE)
        if len(matches) != 1:
            raise SkillMaterialError("invalid skill dependency declaration")
        names = tuple(matches[0].split())
        if len(set(names)) != len(names) or any(
            not _TOOL_NAME.fullmatch(n) for n in names
        ):
            raise SkillMaterialError("invalid skill dependency declaration")
        fields[field] = names
    allowed = set(fields["allowed-tools"])
    required = set(fields["required-tools"])
    optional = set(fields["optional-tools"])
    if not allowed or required & optional or required | optional != allowed:
        raise SkillMaterialError("invalid skill dependency classification")
    if required != {
        name for name in allowed if not name.startswith("chart_")
    } or optional != {name for name in allowed if name.startswith("chart_")}:
        raise SkillMaterialError("invalid skill dependency classification")
    if not set(_REFERENCE.findall(references)) <= allowed:
        raise SkillMaterialError("undeclared referenced skill dependency")
    return _Material(
        rules="\n\n=====\n\n".join((parts[2].strip(), references.strip())),
        allowed=fields["allowed-tools"],
        required=fields["required-tools"],
        optional=fields["optional-tools"],
    )


def verify_basic_qa_material_version(skill_id: str, expected_version: str) -> None:
    """提交活动状态前再次核对版本；本检查不能代替提交时的身份和范围复查."""
    if not isinstance(expected_version, str) or not _VERSION.fullmatch(
        expected_version
    ):
        raise SkillMaterialError("invalid material version")
    if _fingerprint(_read_snapshot(_directory(skill_id))) != expected_version:
        raise SkillMaterialError("skill material version changed")


def load_basic_qa_material(
    skill_id: str,
    expected_version: str,
    mode: str,
    *,
    resolve_scope: ScopeResolver,
) -> LoadedSkillMaterial:
    """读取版本一致的材料并重新计算工具范围；只缓存静态材料，不提交活动状态.

    调用方必须在调用前验证可信受理、显式选择和业务身份，传入按当前连接、
    授权及已受理范围过滤的 resolver；在提交状态前再次复核版本与身份资格。
    """
    if mode not in ("fast", "expert"):
        raise SkillMaterialError("invalid server skill mode")
    if not isinstance(expected_version, str) or not _VERSION.fullmatch(
        expected_version
    ):
        raise SkillMaterialError("invalid material version")
    directory = _directory(skill_id)
    snapshot = _read_snapshot(directory)
    if _fingerprint(snapshot) != expected_version:
        raise SkillMaterialError("skill material version changed")
    key = (skill_id, expected_version, mode)
    with _cache_lock:
        material = _cache.get(key)
        cache_hit = material is not None
        if material is not None:
            _cache.move_to_end(key)
    if material is None:
        material = _parse(snapshot, mode)
    try:
        scope = resolve_scope(material.allowed, material.required, material.optional)
    except ToolScopeError as exc:
        raise SkillMaterialError("skill tool scope unavailable") from exc
    if not isinstance(scope, SkillToolScope):
        raise SkillMaterialError("invalid skill tool scope")
    if snapshot != _read_snapshot(directory):
        raise SkillMaterialError("skill material changed during load")
    with _cache_lock:
        _cache[key] = material
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)
    return LoadedSkillMaterial(
        skill_id, expected_version, mode, material.rules, scope, cache_hit
    )
