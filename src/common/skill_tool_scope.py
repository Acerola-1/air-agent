"""Skill 工具元数据与纯范围运算；不发现连接、不读取身份、不授予权限.

调用方必须先生成经过业务、连接和已受理选择过滤的工具目录，并从可信
登记提供材料名称到稳定 ID 的绑定。本模块只能收窄目录，不授予运行许可。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class ToolKind(StrEnum):
    """服务端登记的执行类别，未知工具默认领域工具."""

    CONTROL = "control"
    GENERAL = "general"
    EVIDENCE = "evidence"
    DOMAIN = "domain"


_LOCAL_KINDS = {
    "search_skills": ToolKind.CONTROL,
    "load_skill": ToolKind.CONTROL,
    "search_mcp": ToolKind.CONTROL,
    "load_mcp_tool": ToolKind.CONTROL,
    "finish_planning": ToolKind.CONTROL,
    "get_beijing_time": ToolKind.GENERAL,
    "knowledge_search": ToolKind.GENERAL,
    "create_artifact": ToolKind.GENERAL,
    "compute_artifact": ToolKind.EVIDENCE,
}
_MCP_ID = re.compile(r"mcp:[a-z0-9][a-z0-9_-]{0,63}:[a-zA-Z0-9_-]+")
_LOCAL_ID = re.compile(r"local:[a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class ScopedTool:
    """调用方提供的已收窄工具条目；aliases 不构成跨服务绑定."""

    id: str
    name: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """拒绝不一致的稳定 ID 和原始名称."""
        if not (_MCP_ID.fullmatch(self.id) or _LOCAL_ID.fullmatch(self.id)):
            raise ValueError("invalid stable tool id")
        if self.id.rsplit(":", 1)[1] != self.name:
            raise ValueError("tool id and original name differ")

    @property
    def kind(self) -> ToolKind:
        """远端工具不继承同名本地工具的通用类别."""
        # 远端即使自称 load_skill / get_beijing_time，也不是本地通用工具。
        if self.id.startswith("mcp:"):
            return ToolKind.DOMAIN
        return _LOCAL_KINDS.get(self.name, ToolKind.DOMAIN)


class ToolScopeError(ValueError):
    """材料映射错误；不包含用户数据或连接信息."""


def registered_required_tool_ids(graph: str, references: Iterable[str]) -> list[str]:
    """将必需依赖转成业务登记的稳定 ID；不声明连接当前可用."""
    from common.business_registry import business_for_graph

    definition = business_for_graph(graph)
    if definition is None:
        raise ToolScopeError("unknown business graph")
    if isinstance(references, str):
        references = references.split()
    ids = []
    for reference in references:
        candidates = {
            f"mcp:{connection.server_key}:{name}"
            for connection in definition.connections
            for name in connection.allowed_tools
            if reference in (name, f"mcp:{connection.server_key}:{name}")
        }
        if len(candidates) != 1:
            raise ToolScopeError("required dependency has no unique business binding")
        ids.append(next(iter(candidates)))
    return sorted(set(ids))


def _canonical_id(reference: str, bindings: Mapping[str, str]) -> str:
    """将材料名称映射到可信登记的稳定 ID，不使用动态目录猜测身份."""
    if not isinstance(reference, str) or not reference:
        raise ToolScopeError("invalid tool reference")
    if reference.startswith(("mcp:", "local:")):
        canonical = reference
    else:
        canonical = bindings.get(reference)
        if canonical is None:
            raise ToolScopeError("missing trusted tool binding")
    if not isinstance(canonical, str) or not (
        _MCP_ID.fullmatch(canonical) or _LOCAL_ID.fullmatch(canonical)
    ):
        raise ToolScopeError("invalid bound stable tool id")
    return canonical


def resolve_tool(
    reference: str,
    catalog: Iterable[ScopedTool],
    *,
    bindings: Mapping[str, str] | None = None,
) -> ScopedTool | None:
    """按稳定 ID 查目录；裸名称必须来自可信绑定，目录唯一性不构成绑定.

    bindings 必须由调用方从受保护的业务登记读取，不可来自模型、浏览器或
    已过滤的动态发现结果。它定义预期服务和工具，不代表当前获准执行。
    """
    canonical = _canonical_id(reference, bindings or {})
    by_id: dict[str, ScopedTool] = {}
    for item in catalog:
        if item.id in by_id and by_id[item.id] != item:
            raise ToolScopeError("conflicting stable tool id")
        by_id[item.id] = item
    return by_id.get(canonical)


@dataclass(frozen=True)
class SkillToolScope:
    """从已收窄目录解析出的工具集合及不可用可选项."""

    allowed_ids: frozenset[str]
    unavailable_optional: tuple[str, ...]


def resolve_skill_tools(
    *,
    allowed: Iterable[str],
    required: Iterable[str],
    optional: Iterable[str],
    catalog: Iterable[ScopedTool],
    bindings: Mapping[str, str] | None = None,
    chart_tool_ids: Iterable[str] = (),
    unavailable_chart_names: Iterable[str] = (),
) -> SkillToolScope:
    """解析必需/可选声明并收窄目录；所有非 chart 工具必须是必需项.

    bindings 是独立于动态可用目录的可信名称到稳定 ID 映射。别名在判定
    类别和冲突前先归一，缺失绑定失败关闭；缺少可选图表仅记录不可用。
    chart_tool_ids 必须来自可信登记，完整覆盖本 Skill 声明中的图表工具，
    不可由模型或动态目录生成，也不代表当前获准执行。
    未登记图表只可由可信审计列入 unavailable_chart_names；不虚构稳定 ID。
    """
    tools = tuple(catalog)
    trusted_bindings = bindings or {}
    allowed_set, required_set, optional_set = map(set, (allowed, required, optional))
    if required_set & optional_set:
        raise ToolScopeError("dependency is both required and optional")
    if required_set | optional_set != allowed_set:
        raise ToolScopeError("allowed dependencies must be explicitly classified")
    missing_charts = set(unavailable_chart_names)
    if not missing_charts <= optional_set or any(
        not re.fullmatch(r"chart_[A-Za-z0-9_]+", name) for name in missing_charts
    ):
        raise ToolScopeError("unavailable charts must be audited optional chart names")
    if any(name in trusted_bindings for name in missing_charts):
        raise ToolScopeError("bound chart must be checked by its stable identity")
    canonical = {
        reference: _canonical_id(reference, trusted_bindings)
        for reference in allowed_set - missing_charts
    }
    required_ids = {canonical[reference] for reference in required_set}
    optional_ids = {canonical[reference] for reference in optional_set - missing_charts}
    if required_ids & optional_ids:
        raise ToolScopeError("canonical dependency is both required and optional")
    chart_ids = set(chart_tool_ids)
    if any(_canonical_id(item, {}) != item for item in chart_ids):
        raise ToolScopeError("chart identities must be stable ids")
    if not optional_ids <= chart_ids:
        raise ToolScopeError("optional dependency must be a registered chart tool")
    if required_ids & chart_ids:
        raise ToolScopeError("chart dependency must be optional")
    resolved: set[str] = set()
    unavailable: list[str] = sorted(missing_charts)
    for reference in sorted(allowed_set - missing_charts):
        item = resolve_tool(canonical[reference], tools)
        if item is None:
            if reference in required_set:
                raise ToolScopeError("required dependency unavailable")
            unavailable.append(reference)
        else:
            if item.kind == ToolKind.CONTROL:
                raise ToolScopeError("control tool cannot be a skill dependency")
            resolved.add(item.id)
    return SkillToolScope(frozenset(resolved), tuple(sorted(unavailable)))
