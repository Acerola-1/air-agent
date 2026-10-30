"""Basic QA 按可信受理范围计算活动 Skill；不缓存运行权限."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from basic_qa.capabilities.errors import BasicQARuntimeError
from basic_qa.capabilities.skill_materials import (
    load_basic_qa_material,
    verify_basic_qa_material_version,
)
from basic_qa.capabilities.skill_search import _scoped_catalog
from common.business_registry import business_for_code
from common.capabilities import discover_authorized_mcp_catalog
from common.identity.business_access import BusinessAccessError
from common.identity.runtime import RuntimeIdentityError, guard_runtime_call
from common.runtime_context import get_routing_context
from common.skill_tool_scope import ScopedTool, resolve_skill_tools

# 仅列出当前材料审计确认、且尚未注册的 chart 名称；它们没有稳定 ID。
_UNAVAILABLE_CHARTS = frozenset(
    {
        "chart_city_broadcastHour",
        "chart_city_calendar_day_calculations",
        "chart_city_composite_index_six_factor_ratio",
        "chart_city_concentration_line_chart",
        "chart_city_factor_rankings",
        "chart_city_indexes_month_year_line_chart",
        "chart_city_integrated_index_percentage",
        "chart_city_monthly_yearly_comparison",
    }
)


def trusted_turn_key(config: RunnableConfig) -> str:
    """从服务端受理关联构造轮次标识；缺关联失败关闭."""
    cfg = config.get("configurable") or {}
    keys = (
        "ledger_attempt_id",
        "ledger_submission_id",
        "capability_selection_id",
        "capability_selection_digest",
    )
    values = [cfg.get(key) for key in keys]
    if any(not isinstance(value, str) or not value for value in values):
        raise BasicQARuntimeError("缺少可信受理关联")
    return str(values[0])


async def resolve_material(skill_id: str, version: str, config: RunnableConfig):
    """校验当前身份、受理、材料和动态目录，并返回本次计算的材料范围."""
    identity = await guard_runtime_call(dict(config or {}))
    if identity is None:
        raise BasicQARuntimeError("缺少可信身份")
    items = _scoped_catalog(config, identity)
    if not any(
        item.id == skill_id and item.material_version == version for item in items
    ):
        raise BasicQARuntimeError("技能不在当前受理范围或材料版本已变化")
    definition = business_for_code("basic-qa")
    connection = definition.connection("ipp") if definition else None
    if connection is None:
        raise BasicQARuntimeError("缺少受保护工具登记")
    _, discovered = await discover_authorized_mcp_catalog(
        identity, business_code="basic-qa"
    )
    identity = await guard_runtime_call(dict(config or {}))
    if identity is None or not any(
        item.id == skill_id and item.material_version == version
        for item in _scoped_catalog(config, identity)
    ):
        raise BasicQARuntimeError("发现期间受理范围已变化")
    cfg = config.get("configurable") or {}
    mcp_mode = cfg.get("capability_mcp_mode")
    if mcp_mode not in {"auto", "allowlist"}:
        raise BasicQARuntimeError("缺少可信工具选择")
    selected_ids = set(cfg.get("capability_tool_ids") or ())
    catalog = [
        ScopedTool(id=item.id, name=item.name)
        for item in discovered
        if item.status == "available"
        and (mcp_mode == "auto" or item.id in selected_ids)
    ]
    bindings = {name: f"mcp:ipp:{name}" for name in connection.allowed_tools}

    def resolve(allowed, required, optional):
        charts = set(optional)
        if not charts <= _UNAVAILABLE_CHARTS:
            raise BasicQARuntimeError("绘图工具未经审计")
        return resolve_skill_tools(
            allowed=allowed,
            required=required,
            optional=optional,
            catalog=catalog,
            bindings=bindings,
            unavailable_chart_names=charts,
        )

    mode = get_routing_context(cfg).mode
    material = load_basic_qa_material(skill_id, version, mode, resolve_scope=resolve)
    identity = await guard_runtime_call(dict(config or {}))
    if identity is None or not any(
        item.id == skill_id and item.material_version == version
        for item in _scoped_catalog(config, identity)
    ):
        raise BasicQARuntimeError("加载期间受理范围已变化")
    verify_basic_qa_material_version(skill_id, version)
    from dataclasses import replace

    from basic_qa.capabilities.mcp_catalog import resolve_tool_catalog

    catalog = await resolve_tool_catalog(dict(config or {}))
    versions = {item.tool_id: item.schema_version for item in catalog.documents}
    if not material.tool_scope.allowed_ids <= versions.keys():
        raise BasicQARuntimeError("必需工具定义不可用")
    material = replace(
        material,
        schema_versions=tuple(
            sorted((key, versions[key]) for key in material.tool_scope.allowed_ids)
        ),
        catalog_scope=catalog.scope_key,
    )
    return material


async def owner_scope(config: RunnableConfig) -> dict[str, str]:
    """Bind server references to the verified owner, never model arguments."""
    identity = await guard_runtime_call(dict(config or {}))
    cfg = config.get("configurable") or {}
    scope = {
        "tenant_id": getattr(identity, "tenant_id", ""),
        "user_id": getattr(identity, "user_id", ""),
        "thread_id": cfg.get("thread_id", ""),
        "graph_id": "basic-qa",
    }
    if any(not isinstance(value, str) or not value for value in scope.values()):
        raise BasicQARuntimeError("缺少可信会话归属")
    return scope


async def session_state(state: dict[str, Any], config: RunnableConfig):
    """Read v2 references or migrate only a proven server-side v1 active record."""
    from basic_qa.capabilities.state import read_capabilities

    owner = await owner_scope(config)
    caps = read_capabilities(state, owner)
    if state.get("bqa_capabilities") is not None:
        return caps
    active = state.get("bqa_active_skill")
    if not isinstance(active, dict) or active.get("turn_key") != state.get(
        "bqa_turn_key"
    ):
        return caps
    skill_id, version, call_id = (
        active.get(k) for k in ("skill_id", "material_version", "load_call_id")
    )
    messages = state.get("messages") or []
    calls = [
        (i, c)
        for i, m in enumerate(messages)
        if isinstance(m, AIMessage)
        for c in m.tool_calls
        if c.get("id") == call_id
    ]
    if len(calls) != 1 or not call_id:
        return caps
    index, call = calls[0]
    if call.get("name") != "load_skill" or call.get("args") != {
        "skill_id": skill_id,
        "expected_version": version,
    }:
        return caps
    receipts = [
        m
        for m in messages[index + 1 :]
        if isinstance(m, ToolMessage) and m.tool_call_id == call_id
    ]
    if (
        len(receipts) != 1
        or receipts[0].name != "load_skill"
        or receipts[0].status == "error"
    ):
        return caps
    try:
        payload = json.loads(receipts[0].content)
        if (
            payload.get("status") != "loaded"
            or payload.get("skill_id") != skill_id
            or payload.get("material_version") != version
        ):
            return caps
        material = await resolve_material(skill_id, version, config)
        if material.mode != active.get("mode"):
            return caps
    except (RuntimeIdentityError, BusinessAccessError):
        raise
    except Exception:  # noqa: BLE001 - unprovable legacy references stay uninitialized
        return caps
    caps["loaded_skills"][skill_id] = {
        "material_version": version,
        "mode": material.mode,
        "load_ref": call_id,
        "last_used": 0,
        "schema_versions": dict(getattr(material, "schema_versions", ())),
        "catalog_scope": getattr(material, "catalog_scope", ""),
    }
    caps["active_skill_ids"].append(skill_id)
    return caps


async def resolve_session(
    state: dict[str, Any], config: RunnableConfig, *, allow_dormant: bool = False
):
    """Rebuild all active rules and tools under the current accepted scope."""
    from basic_qa.capabilities.state import ResolvedCapabilities

    caps = await session_state(state, config)
    skills, tools, sources = [], [], {}
    domain_documents = ()
    catalog = None
    if caps["active_skill_ids"] or caps["independent_active_tool_ids"]:
        from basic_qa.capabilities.mcp_catalog import resolve_tool_catalog

        catalog = await resolve_tool_catalog(dict(config or {}))
        domain_documents = catalog.documents
    for skill_id in list(caps["active_skill_ids"]):
        ref = caps["loaded_skills"][skill_id]
        try:
            if not ref.get("load_ref"):
                raise BasicQARuntimeError("无效加载引用")
            material = await resolve_material(skill_id, ref["material_version"], config)
            if material.mode != ref.get("mode"):
                raise BasicQARuntimeError("技能模式已变化，请重新加载")
            if dict(getattr(material, "schema_versions", ())) != ref.get(
                "schema_versions", {}
            ):
                raise BasicQARuntimeError("技能工具定义已变化，请重新加载")
            if ref.get("catalog_scope", catalog.scope_key) != catalog.scope_key:
                raise BasicQARuntimeError("工具连接已变化，请重新提交")
        except (RuntimeIdentityError, BusinessAccessError):
            raise
        except Exception:  # noqa: BLE001 - only a new turn may make stale entries dormant
            if not allow_dormant:
                raise BasicQARuntimeError(
                    "技能材料或工具范围已变化，请重新提交"
                ) from None
            caps["active_skill_ids"].remove(skill_id)
            continue
        skills.append(material)
        for tool_id in material.tool_scope.allowed_ids:
            sources.setdefault(tool_id, []).append(skill_id)
    if caps["independent_active_tool_ids"]:
        from basic_qa.capabilities.mcp_catalog import resolve_tool_catalog

        by_id = {t.tool_id: t for t in catalog.documents}
        for tool_id in list(caps["independent_active_tool_ids"]):
            ref = caps["loaded_tools"][tool_id]
            item = by_id.get(tool_id)
            if (
                item is None
                or not ref.get("load_ref")
                or item.schema_version != ref.get("schema_version")
                or ref.get("catalog_scope", catalog.scope_key) != catalog.scope_key
            ):
                if not allow_dormant:
                    raise BasicQARuntimeError("工具定义或范围已变化，请重新提交")
                caps["independent_active_tool_ids"].remove(tool_id)
                continue
            tools.append(item)
            sources.setdefault(tool_id, []).append("independent")
    resolved = ResolvedCapabilities(
        tuple(skills),
        tuple(tools),
        {k: tuple(v) for k, v in sources.items()},
        tuple(d for d in domain_documents if d.tool_id in sources),
    )
    return caps, resolved if skills or tools else None


async def current_material(state: dict[str, Any], config: RunnableConfig):
    """Restore session abilities without a latest-HumanMessage receipt requirement."""
    if state.get("bqa_turn_key") != trusted_turn_key(config):
        raise BasicQARuntimeError("运行轮次未初始化")
    _, resolved = await resolve_session(state, config)
    return resolved


def create_load_skill_tool():
    """Activate one skill atomically; a failed target leaves other abilities intact."""

    @tool("load_skill")
    async def load_skill(
        skill_id: str,
        expected_version: str,
        runtime: ToolRuntime,
        config: RunnableConfig,
    ) -> Command:
        """加载或重新激活指定版本Skill；已激活则幂等返回，不查询业务数据."""
        state = runtime.state or {}
        tool_call_id = runtime.tool_call_id
        try:
            if state.get("bqa_turn_key") != trusted_turn_key(config):
                raise BasicQARuntimeError("运行轮次不匹配")
            caps = await session_state(state, config)
            material = await resolve_material(skill_id, expected_version, config)
            old = caps["loaded_skills"].get(skill_id)
            same = (
                old
                and old.get("material_version") == expected_version
                and old.get("mode") == material.mode
                and old.get("schema_versions", {})
                == dict(getattr(material, "schema_versions", ()))
                and old.get("catalog_scope", "")
                == getattr(material, "catalog_scope", "")
            )
            status = (
                "already_active"
                if same and skill_id in caps["active_skill_ids"]
                else "activated"
                if same
                else "loaded"
            )
            caps["loaded_skills"][skill_id] = {
                "material_version": material.material_version,
                "mode": material.mode,
                "load_ref": old["load_ref"] if same else tool_call_id,
                "last_used": len(state.get("messages") or []),
                "protected_attempt": trusted_turn_key(config),
                "schema_versions": dict(getattr(material, "schema_versions", ())),
                "catalog_scope": getattr(material, "catalog_scope", ""),
            }
            if skill_id not in caps["active_skill_ids"]:
                caps["active_skill_ids"].append(skill_id)
            payload = {
                "status": status,
                "skill_id": skill_id,
                "material_version": material.material_version,
                "unavailable_optional": list(material.tool_scope.unavailable_optional),
            }
            return Command(
                update={
                    "bqa_capabilities": caps,
                    "messages": [
                        ToolMessage(
                            content=json.dumps(payload, ensure_ascii=False),
                            tool_call_id=tool_call_id,
                            name="load_skill",
                        )
                    ],
                }
            )
        except (RuntimeIdentityError, BusinessAccessError):
            raise
        except Exception:  # noqa: BLE001 - the failed load cannot erase successful sources
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "status": "unavailable",
                                    "message": "技能加载失败，请检查ID、版本、模式及必需工具。",
                                },
                                ensure_ascii=False,
                            ),
                            tool_call_id=tool_call_id,
                            name="load_skill",
                            status="error",
                        )
                    ]
                }
            )

    return load_skill


LOAD_TOOLS = frozenset({"load_skill", "load_mcp_tool"})
CONTROL_TOOLS = frozenset(
    {*LOAD_TOOLS, "search_skills", "search_mcp", "finish_planning"}
)


def reject_conflicting_batch(
    calls: list[dict[str, Any]], *, disclosed_names: set[str] | None = None
) -> list[ToolMessage]:
    """Permit multiple loads; reject loads mixed with unpublished domain queries."""
    names = [call.get("name") for call in calls]
    harmless = CONTROL_TOOLS - {"finish_planning"} | {
        "get_beijing_time",
        "knowledge_search",
        "create_artifact",
    }
    # A tool already present in the exact planning request does not depend on
    # reading the new load result. Execution still checks that disclosure and
    # current permission/schema; loading cannot add to this pre-batch set.
    harmless |= set(disclosed_names or ()) - {"finish_planning"}
    if not (
        any(name in LOAD_TOOLS for name in names)
        and any(name not in harmless for name in names)
    ):
        return []
    return [
        ToolMessage(
            content=json.dumps(
                {
                    "code": 422,
                    "success": False,
                    "message": "能力加载与领域查询或结束必须分批；先读取新规则和schema再查询。",
                },
                ensure_ascii=False,
            ),
            tool_call_id=str(c.get("id") or ""),
            name=str(c.get("name") or ""),
            status="error",
        )
        for c in calls
    ]
