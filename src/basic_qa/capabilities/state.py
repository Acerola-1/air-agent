"""Versioned server checkpoint references, independent of attempts and message windows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from common.skill_tool_scope import SkillToolScope


def empty_capabilities(owner_scope: dict[str, str]) -> dict[str, Any]:
    """Create references only; never persist executable objects or authorization."""
    return {
        "version": 2,
        "owner_scope": dict(owner_scope),
        "loaded_skills": {},
        "active_skill_ids": [],
        "loaded_tools": {},
        "independent_active_tool_ids": [],
    }


def read_capabilities(state: dict[str, Any], owner: dict[str, str]) -> dict[str, Any]:
    """Accept only a server checkpoint owned by this tenant, user, thread and graph."""
    value = state.get("bqa_capabilities")
    if (
        not isinstance(value, dict)
        or value.get("version") != 2
        or value.get("owner_scope") != owner
    ):
        return empty_capabilities(owner)
    result = deepcopy(value)
    for key in ("loaded_skills", "loaded_tools"):
        if not isinstance(result.get(key), dict):
            return empty_capabilities(owner)
    for key, refs in (
        ("active_skill_ids", "loaded_skills"),
        ("independent_active_tool_ids", "loaded_tools"),
    ):
        values = result.get(key)
        if not isinstance(values, list):
            return empty_capabilities(owner)
        result[key] = list(
            dict.fromkeys(
                item
                for item in values
                if isinstance(item, str) and item in result[refs]
            )
        )
    return result


@dataclass(frozen=True)
class ResolvedCapabilities:
    """One request's rules and union of valid sources; no cached authorization."""

    skills: tuple[Any, ...]
    tools: tuple[Any, ...]
    sources: dict[str, tuple[str, ...]]
    domain_documents: tuple[Any, ...] = ()

    @property
    def tool_scope(self) -> SkillToolScope:
        """Expose a deduplicated set to the existing disclosure adapter."""
        return SkillToolScope(frozenset(self.sources), ())

    @property
    def rules(self) -> str:
        """Keep each complete material separate instead of overwriting prior rules."""
        return "\n\n".join(
            f"【Skill {m.skill_id}（仅用于其覆盖任务）】\n{m.rules}"
            for m in self.skills
        )

    @property
    def skill_id(self) -> str:
        """Return identifiers for concise compatibility diagnostics."""
        return ", ".join(m.skill_id for m in self.skills)


def dormant_next(state: dict[str, Any], attempt: str) -> dict[str, Any] | None:
    """Remove one LRU nonessential source, preserving references and new loads."""
    caps = deepcopy(state.get("bqa_capabilities") or {})
    candidates = []
    for active_key, loaded_key in (
        ("active_skill_ids", "loaded_skills"),
        ("independent_active_tool_ids", "loaded_tools"),
    ):
        for key in caps.get(active_key, []):
            ref = caps.get(loaded_key, {}).get(key, {})
            if ref.get("protected_attempt") != attempt:
                candidates.append((ref.get("last_used", 0), key, active_key))
    if not candidates:
        return None
    _, key, active_key = min(candidates)
    caps[active_key].remove(key)
    return caps


def touch_executed(
    state: dict[str, Any], successful_names: set[str], attempt: str
) -> dict[str, Any] | None:
    """Update usage once per business batch, avoiding parallel checkpoint writes."""
    record = state.get("bqa_disclosure") or {}
    caps = deepcopy(state.get("bqa_capabilities") or {})
    touched = False
    for tool_id, name in record.get("tools", {}).items():
        if name not in successful_names:
            continue
        for source in record.get("sources", {}).get(tool_id, []):
            ref = (
                caps.get("loaded_tools", {}).get(tool_id)
                if source == "independent"
                else caps.get("loaded_skills", {}).get(source)
            )
            if ref is not None:
                ref.update(
                    last_used=len(state.get("messages") or []),
                    protected_attempt=attempt,
                )
                touched = True
    return caps if touched else None
