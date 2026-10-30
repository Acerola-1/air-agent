"""Basic QA 服务端 Skill 目录与按需语义搜索."""

from __future__ import annotations

import asyncio
import math
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from common.capabilities import SkillCatalogItem, list_graph_skills
from common.identity.business_access import grant_for_graph
from common.identity.runtime import RuntimeIdentityError, guard_runtime_call
from common.skill_router import SkillSearchUnavailable

_MAX_QUERY_LENGTH = 500
_MAX_CANDIDATES = 5


class SkillSearchRouter(Protocol):
    """仅使用路由候选名称和分数；不信任其描述、路径或工具声明."""

    async def amatch(self, question: str, *, strict: bool = False) -> list[Any]:
        """进行语义匹配."""
        ...


class SkillCatalogScopeError(RuntimeError):
    """可信受理快照缺失、冲突或材料已变化."""


def _scoped_catalog(config: RunnableConfig, identity: Any) -> list[SkillCatalogItem]:
    """复核可信业务与受理关联，返回仅供服务端检索和材料校验的目录."""
    if identity is None:
        raise RuntimeIdentityError("缺少可信身份，拒绝读取 Skill 目录")
    grant = grant_for_graph(graph_id="basic-qa", identity=identity)
    if (
        identity.business_graph != "basic-qa"
        or identity.business_code != grant.business_code
        or identity.business_permission != grant.permission
        or identity.business_policy_version != grant.policy_version
    ):
        raise SkillCatalogScopeError("业务受理快照不匹配")
    cfg = config.get("configurable") or {}
    if not cfg.get("capability_selection_id") or not cfg.get(
        "capability_selection_digest"
    ):
        raise SkillCatalogScopeError("缺少受理选择快照")
    return [
        item for item in list_graph_skills("basic-qa") if item.status == "available"
    ]


def _summary(item: SkillCatalogItem) -> dict[str, str]:
    return {
        "skill_id": item.id,
        "name": item.name,
        "description": item.description,
        "material_version": item.material_version,
        "status": item.status,
    }


def create_search_skills_tool(router: SkillSearchRouter, *, timeout: float = 10.0):
    """创建按需搜索工具，使用严格路由区分无命中与检索故障."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout 必须大于零")

    @tool("search_skills")
    async def search_skills(query: str, config: RunnableConfig) -> dict[str, Any]:
        """根据当前会话形成的业务需求搜索可用 Skill；不会加载或执行."""
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > _MAX_QUERY_LENGTH
        ):
            return {"status": "invalid_query", "candidates": []}
        identity = await guard_runtime_call(dict(config or {}))
        before = _scoped_catalog(config, identity)
        if not before:
            return {"status": "ok", "candidates": []}
        try:
            matches = await asyncio.wait_for(
                router.amatch(query.strip(), strict=True), timeout=timeout
            )
        except TimeoutError:
            identity = await guard_runtime_call(dict(config or {}))
            _scoped_catalog(config, identity)
            return {"status": "search_timeout", "candidates": []}
        except SkillSearchUnavailable:
            identity = await guard_runtime_call(dict(config or {}))
            _scoped_catalog(config, identity)
            return {"status": "search_unavailable", "candidates": []}
        # 等待期间身份、选择或材料可能变化；返回前重新复查。
        identity = await guard_runtime_call(dict(config or {}))
        after = _scoped_catalog(config, identity)
        before_versions = {entry.id: entry.material_version for entry in before}
        permitted = {
            entry.name: entry
            for entry in after
            if before_versions.get(entry.id) == entry.material_version
        }
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in matches:
            item = permitted.get(getattr(match, "name", None))
            if item is None or item.id in seen:
                continue
            score = getattr(match, "similarity_score", None)
            if (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(score)
            ):
                score = None
            candidates.append({**_summary(item), "score": score})
            seen.add(item.id)
            if len(candidates) == _MAX_CANDIDATES:
                break
        return {"status": "ok", "candidates": candidates}

    return search_skills
