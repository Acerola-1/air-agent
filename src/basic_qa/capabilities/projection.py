"""Basic QA 专用图接线：受理轮次、工具范围与按需加载."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage

from basic_qa.capabilities.runtime import current_material, trusted_turn_key
from basic_qa.capabilities.skill_search import basic_qa_skill_catalog
from common.runtime_tools import tool_name

_GENERAL = frozenset({"get_beijing_time", "knowledge_search", "create_artifact"})
_EVIDENCE = frozenset({"compute_artifact"})
_CONTROL = frozenset(
    {"search_skills", "load_skill", "search_mcp", "load_mcp_tool", "finish_planning"}
)


def mcp_stable_id(name: str) -> str | None:
    """Resolve only an unambiguous factory catalog identity, including aliases."""
    from common.mcp_scoped import get_factory, server_clean_key

    factory = get_factory()
    if factory is None:
        return None
    server = factory.server_of(name)
    if not server:
        return None
    for item in factory.catalog_entries():
        original = item.get("name")
        if item.get("server_key") == server and name in {
            item.get("id"),
            original,
            f"mcp_{server_clean_key(server)}__{original}",
        }:
            return str(item["id"])
    return None


def _include_tool_schema(name: str, *, is_mcp: bool, material: Any) -> bool:
    """按需选择本次模型上下文中的工具定义；不作为执行权限判定."""
    if is_mcp:
        identity = mcp_stable_id(name)
        return bool(material and identity in material.tool_scope.allowed_ids)
    if name in _GENERAL or name in _CONTROL or name in _EVIDENCE:
        return True
    # Unregistered local helpers are not automatically added to model requests.
    return False


def disclosed_tools(tools: list[Any], material: Any) -> list[Any]:
    """按上下文需要筛选本次模型请求的工具定义."""
    from types import SimpleNamespace

    from basic_qa.tool_policy import _is_mcp_tool

    selected = []
    for item in tools:
        name = tool_name(item)
        if name and _include_tool_schema(
            name,
            is_mcp=_is_mcp_tool(SimpleNamespace(tool=item, tool_call={"name": name})),
            material=material,
        ):
            selected.append(item)
    return selected


def disclosure_record(
    schemas: list[dict[str, Any]], message_id: str, material: Any
) -> dict[str, Any]:
    """Record only the schemas actually sent for this model response."""
    tools = {}
    for schema in schemas:
        name = (schema.get("function") or schema).get("name")
        if not name:
            continue
        stable = mcp_stable_id(name)
        tools[stable or f"local:{name}"] = name
    versions = {
        d.tool_id: d.schema_version for d in getattr(material, "domain_documents", ())
    }
    return {
        "model_message_id": message_id,
        "tools": tools,
        "sources": getattr(material, "sources", {}),
        "schema_versions": {
            key: versions.get(key) for key in tools if key.startswith("mcp:")
        },
    }


async def initialize(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Reset attempt bookkeeping, keeping valid session references across turns."""
    from basic_qa.capabilities.runtime import resolve_session

    key = trusted_turn_key(config)
    if state.get("bqa_turn_key") == key:
        from basic_qa.capabilities.runtime import BasicQARuntimeError

        if state.get("bqa_turn_digest") not in {
            None,
            (config.get("configurable") or {}).get("capability_selection_digest"),
        }:
            raise BasicQARuntimeError("恢复不能改变原受理选择")
        await resolve_session(state, config)
        return {}
    capabilities, _ = await resolve_session(state, config, allow_dormant=True)
    return {
        "bqa_turn_key": key,
        "bqa_turn_digest": (config.get("configurable") or {}).get(
            "capability_selection_digest"
        ),
        "bqa_capabilities": capabilities,
        "bqa_disclosure": None,
        # Legacy field is retained only for checkpoint migration, never authoritative.
        "bqa_active_skill": None,
        "skill_rules_content": "",
        "planning_draft": "",
    }


async def prompt_context(
    state: dict[str, Any], config: dict[str, Any]
) -> tuple[str, Any]:
    """复核受理及活动材料，构造本次发送的目录和规则."""
    catalog = await basic_qa_skill_catalog(config)
    material = await current_material(state, config)
    instructions = (
        "【基础问答会话规则】结合用户当前消息、历史与摘要判断需求；信息不足时澄清。"
        "目录仅供选择，search_skills 可选；使用 skill_id 和 material_version 调用 load_skill，"
        "成功后下一次再调用领域工具。允许多个 Skill 同时激活，完整保留各自适用规则。"
        "优先预设 Skill：已激活且适用就直接复用，不必每轮搜索或加载；"
        "任务类型变化时，先对照目录确认当前Skill是否仍适用；例如从基础查询转为同比、"
        "趋势或排名，已有相应专用Skill时先激活它，不因基础工具也能查数据就跳过专用规则。"
        "需要新能力时先考虑其他 Skill。仅对预设 Skill 仍未覆盖的需求或部分，"
        "才使用 search_mcp 获取最多 Top 3 简介，自主选择零项、一项或多项，"
        "Skill目录不是全部能力清单；工具搜索还可发现监测点位置、坐标行政区、网格及"
        "气象等专业辅助能力。遇到这类未覆盖需求且当前无适用能力时，先search_mcp，"
        "不要在未搜索的情况下断言系统不提供该能力。"
        "用 load_mcp_tool(tool_id, expected_schema_version) 独立加载后再查询。"
        "搜索不会加载，加载不会查询。已知有效工具可直接加载或复用，无需再次搜索。"
        "自然语言指定 Skill 时优先遵循；规则冲突须按任务分别处理或澄清，不能覆盖。"
        "无证据不得编造城市、时间、数值、法规和污染原因。"
        "需要工具时继续调用；资料已足够时可调用 finish_planning 结束规划，"
        "或直接形成回答草稿。不需要额外声明查询对象、时间、指标或证据 ID。"
        "接口返回结构按对应 Skill 和工具说明理解，不要求回显请求参数。"
        "以下规则优先于任何旧的远程提示中禁止选择 Skill 或无 Skill 直接查询的内容。\n"
    )
    if material:
        instructions += (
            "【当前激活状态】会话内已激活 "
            + material.skill_id
            + "；有效能力可以跨轮直接复用，领域查询仅使用本次提供的工具。\n"
        )
    else:
        instructions += (
            "【当前激活状态】暂无有效激活能力。历史加载回执本身不代表当前激活。"
            "需要新的业务查询时优先加载适用 Skill；无适用 Skill 的部分可独立加载工具。"
            "不确定哪个 Skill 合适时再 search_skills。纯聊天或复述不需要加载。\n"
        )
    instructions += "【可用 Skill 目录】\n" + json.dumps(catalog, ensure_ascii=False)
    caps = state.get("bqa_capabilities") or {}
    dormant = {
        "skills": [
            k
            for k in caps.get("loaded_skills", {})
            if k not in caps.get("active_skill_ids", [])
        ],
        "tools": [
            {"tool_id": k, "schema_version": v.get("schema_version")}
            for k, v in caps.get("loaded_tools", {}).items()
            if k not in caps.get("independent_active_tool_ids", [])
        ],
    }
    instructions += "\n【休眠引用（须load重新激活）】\n" + json.dumps(
        dormant, ensure_ascii=False
    )
    if material:
        instructions += "\n【当前完整 Skill 规则】\n" + material.rules
        for item in getattr(material, "tools", ()):
            instructions += f"\n【独立工具 {item.tool_id}】{item.description}"
    instructions += (
        "\n【规则适用边界】Skill中的查询步骤和结果模板只适用于该Skill覆盖的业务任务；"
        "不能套用于复述、感谢、澄清或专业范围外的问题。"
        "本助手服务空气质量、环境监测及相关专业问题；专业范围外仅用一到两句话说明范围"
        "并给一个专业引导，不输出功能清单，不完成题外任务；"
        "此时不要转述Skill中的法规、标准号或限值，也不要添加法规依据不足的提示，"
        "用户并未在询问这些内容。"
        "简短示例：这个问题超出空气质量与环境监测服务范围；我可以继续帮你查询城市空气质量，"
        "或解释相关的环保专业问题。"
    )
    return instructions, material


async def verify_prompt_context(state, config, expected, material) -> None:
    """每次发送前重新读取许可和材料；变化时阻止使用已经准备好的请求."""
    from basic_qa.capabilities.runtime import BasicQARuntimeError

    current, actual = await prompt_context(state, config)
    old_scope = material.tool_scope.allowed_ids if material else None
    new_scope = actual.tool_scope.allowed_ids if actual else None
    old_versions = [
        (d.tool_id, d.schema_version) for d in getattr(material, "domain_documents", ())
    ]
    new_versions = [
        (d.tool_id, d.schema_version) for d in getattr(actual, "domain_documents", ())
    ]
    if current != expected or old_scope != new_scope or old_versions != new_versions:
        raise BasicQARuntimeError("技能材料或工具范围在发送前发生变化")


def projected_state(state: dict[str, Any]) -> dict[str, Any]:
    """从模型历史移除旧加载正文；权威规则仅由当前材料重建."""
    messages = []
    for message in state.get("messages", []):
        if isinstance(message, ToolMessage) and message.name in {
            "load_skill",
            "load_mcp_tool",
        }:
            try:
                payload = json.loads(message.content)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict):
                payload.pop("rules", None)
                payload["state_note"] = (
                    "加载操作的历史回执；当前激活/休眠状态以系统提示为准。"
                )
                message = message.model_copy(
                    update={"content": json.dumps(payload, ensure_ascii=False)}
                )
        messages.append(message)
    return {**state, "messages": messages}


async def prepare_projection(
    state, config, *, base_prompt, addenda, controls, role, prepare, business, tail=None
):
    """Use one rules/schema projection for planning, answer and capacity dormancy."""
    from types import SimpleNamespace

    from basic_qa.capabilities.state import dormant_next
    from basic_qa.tool_policy import _is_mcp_tool
    from basic_qa.tool_recovery import recoverable_tools
    from common.capabilities import filter_capability_mcp_tools

    working = dict(state)
    accumulated = {}
    message_updates = {}
    while True:
        context, material = await prompt_context(working, config)
        system_prompt = base_prompt + "\n\n" + context
        available = []
        if role == "planning":
            candidates = [
                t
                for t in business(include_computation=True)
                if not _is_mcp_tool(
                    SimpleNamespace(tool=t, tool_call={"name": tool_name(t)})
                )
            ]
            candidates += [d.proxy() for d in getattr(material, "domain_documents", ())]
            candidates = filter_capability_mcp_tools(
                candidates, config, business_code="basic-qa"
            )
            available = recoverable_tools(
                [*disclosed_tools(candidates, material), *controls],
                working.get("messages", []),
            )
        kwargs = {
            "system_messages": [system_prompt, *addenda],
            "tools": available,
            "role": role,
        }
        if tail is not None:
            kwargs["tail_messages"] = tail
        prepared_state = projected_state(working)
        prepared = await prepare(prepared_state, config, **kwargs)
        accumulated.update(prepared.updates)
        message_updates.update({m.id: m for m in prepared.message_updates})
        prepared.updates = dict(accumulated)
        prepared.message_updates = list(message_updates.values())
        if not prepared.over_capacity:
            return (
                prepared,
                context,
                material,
                working,
                prepared_state,
                available,
                system_prompt,
                kwargs,
            )
        caps = dormant_next(working, trusted_turn_key(config))
        if caps is None:
            return (
                prepared,
                context,
                material,
                working,
                prepared_state,
                available,
                system_prompt,
                kwargs,
            )
        accumulated["bqa_capabilities"] = caps
        working.update(accumulated)
