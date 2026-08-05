"""权限需求规则预分类与审查中间件.

采用"事实抽取层 + 可信上下文层 + 确定性规则引擎层"的三层设计：
1. LLM 只输出 PermissionExtractedSlots（信息抽取）
2. MCP 工具提供可信事实（用户画像、行政区解析）
3. 代码规则引擎做判定（check_permission -> PermissionResult）
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_config
from langgraph.runtime import Runtime
from loguru import logger

from common import mcp_client
from common.context import get_message_content
from common.mcp_utils import coerce_jsonish
from common.models import ModelRegistry
from common.permission import (
    PERMISSION_SLOT_EXTRACTION_PROMPT,
    PermissionExtractedSlots,
    PermissionFacts,
    PermissionResult,
    build_permission_facts,
    check_permission,
    classify_permission_need,
    normalize_target_level,
)
from common.skill_discovery import SkillDiscoveryState

# 安全降级配置：权限失败时是否关闭（拒绝访问）
PERMISSION_FAIL_CLOSED = os.environ.get("PERMISSION_FAIL_CLOSED", "true").lower() in (
    "true",
    "1",
    "yes",
)

PERMISSION_REGION_FULL_QUESTION_FALLBACK = os.environ.get(
    "PERMISSION_REGION_FULL_QUESTION_FALLBACK", "false"
).lower() in ("true", "1", "yes")

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _coerce_jsonish(value: Any) -> Any:
    """兼容旧测试入口，委托统一 MCP JSON 解析工具."""
    return coerce_jsonish(value)


def _get_beijing_time_payload() -> dict[str, Any]:
    """轻量获取当前北京时间，避免导入 common.tools 的重依赖."""
    now = datetime.now(SHANGHAI_TZ)
    return {
        "success": True,
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "error": None,
    }


def _build_query_overrides(permission_result: dict[str, Any]) -> dict[str, Any] | None:
    """从 PermissionResult 提取后续查询必须使用的修正参数."""
    fix_strategy = str(permission_result.get("fix_strategy") or "")
    overrides: dict[str, Any] = {
        "fix_strategy": fix_strategy,
        "correction_text": permission_result.get("correction_text") or "",
    }
    allowed_regions = permission_result.get("allowed_regions")
    if isinstance(allowed_regions, list) and allowed_regions:
        overrides["regions"] = allowed_regions
        overrides["region_mode"] = permission_result.get("region_mode") or ""
        if len(allowed_regions) == 1 and isinstance(allowed_regions[0], dict):
            overrides["region"] = allowed_regions[0]

    station_overrides = permission_result.get("station_overrides")
    if isinstance(station_overrides, list) and station_overrides:
        overrides["station_overrides"] = station_overrides

    if fix_strategy == "truncate_time":
        legal_time_span = permission_result.get("legal_time_span")
        if not legal_time_span:
            return None
        overrides["time_span"] = legal_time_span
        if "regions" in overrides:
            return overrides
        if "station_overrides" in overrides:
            return overrides
        allowed_region = _coerce_allowed_region(permission_result.get("allowed_region"))
        if permission_result.get("auto_filled") and allowed_region:
            overrides["region"] = allowed_region
            overrides["auto_filled"] = True
        return overrides

    if fix_strategy == "replace_region":
        if "regions" in overrides:
            return overrides
        allowed_region = _coerce_allowed_region(permission_result.get("allowed_region"))
        if not isinstance(allowed_region, dict):
            return None
        overrides["region"] = allowed_region
        return overrides

    # auto_filled 场景：虽然没有 fix_strategy，但需要告知主流程区域被自动补全了
    if permission_result.get("auto_filled"):
        if "regions" in overrides:
            overrides["auto_filled"] = True
            return overrides
        allowed_region = _coerce_allowed_region(permission_result.get("allowed_region"))
        if isinstance(allowed_region, dict):
            overrides["region"] = allowed_region
            overrides["auto_filled"] = True
            return overrides

    if "regions" in overrides:
        return overrides

    if "station_overrides" in overrides:
        return overrides

    return None


def _coerce_allowed_region(value: Any) -> dict[str, Any] | None:
    """将 allowed_region 统一转为 dict."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return value if isinstance(value, dict) else None


def _needs_permission_disclosure(permission_result: dict[str, Any]) -> bool:
    """判断本次权限结果是否需要在最终回复中向用户披露."""
    correction_text = permission_result.get("correction_text")
    if isinstance(correction_text, str) and correction_text.strip():
        return True

    fix_strategy = str(permission_result.get("fix_strategy") or "").strip()
    if fix_strategy:
        return True

    if permission_result.get("auto_filled"):
        return True

    for key in (
        "rejected_regions",
        "unresolved_regions",
        "station_overrides",
    ):
        value = permission_result.get(key)
        if isinstance(value, list) and value:
            return True

    region_results = permission_result.get("region_results")
    if isinstance(region_results, list):
        for item in region_results:
            if not isinstance(item, dict):
                continue
            item_correction = item.get("correction_text")
            if isinstance(item_correction, str) and item_correction.strip():
                return True
            if str(item.get("fix_strategy") or "").strip():
                return True
            if item.get("auto_filled") or item.get("permitted") is False:
                return True

    return permission_result.get("permitted") is False


def _clean_region_slot(value: Any) -> str | None:
    """清洗 LLM 抽取出的行政区槽位空值."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value in {"", "None", "none", "null", "NULL"}:
        return None
    return value


def _clean_region_slots(slots: dict[str, Any]) -> dict[str, str | None]:
    """返回清洗后的省/市/区县槽位."""
    return {
        "province": _clean_region_slot(slots.get("province")),
        "city": _clean_region_slot(slots.get("city")),
        "district": _clean_region_slot(slots.get("district")),
    }


def _has_region_slots(slots: dict[str, Any]) -> bool:
    """判断 slots 是否包含用户明确指定的行政区."""
    cleaned = _clean_region_slots(slots)
    return bool(cleaned["province"] or cleaned["city"] or cleaned["district"])


def _region_slot_supported_by_question(value: str | None, question: str | None) -> bool:
    """判断抽取出的行政区是否有用户原问题文本依据."""
    if not value:
        return False
    question_text = (question or "").strip()
    if not question_text:
        return False
    candidates = {value}
    for suffix in ("省", "市", "区", "县"):
        if value.endswith(suffix) and len(value) > len(suffix):
            candidates.add(value.removesuffix(suffix))
    return any(candidate and candidate in question_text for candidate in candidates)


def _filter_unsupported_region_slots(
    slots: dict[str, Any],
    question: str | None,
) -> dict[str, Any]:
    """移除没有原问题文本依据的行政区槽位，防止 LLM 幻觉默认全国."""
    if not slots:
        return slots
    filtered = dict(slots)
    cleaned = _clean_region_slots(filtered)
    for key, value in cleaned.items():
        if value and not _region_slot_supported_by_question(value, question):
            logger.warning(
                "Slots 行政区缺少原问题依据，已忽略: field={}, value={}",
                key,
                value,
            )
            filtered[key] = None
    if not _has_region_slots(filtered):
        filtered["target_level"] = None
    return filtered


def _region_from_user_bound_region(
    user_profile: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """从用户画像构造可信默认查询区域."""
    if not isinstance(user_profile, dict):
        return None
    bound = user_profile.get("bound_region")
    if not isinstance(bound, dict):
        return None

    name = str(bound.get("name") or "").strip()
    level = str(bound.get("level") or "").strip()
    if not name or level not in {"province", "city", "district"}:
        return None

    province_obj = user_profile.get("province")
    city_obj = user_profile.get("city")
    region: dict[str, Any] = {
        "found": True,
        "ambiguous": False,
        "region": {"name": name, "level": level},
        "province": province_obj if isinstance(province_obj, dict) else None,
        "city": city_obj if isinstance(city_obj, dict) else None,
        "auto_filled": True,
    }
    return region


def _allowed_region_from_requested_region(
    requested_region: dict[str, Any],
) -> dict[str, Any] | None:
    """从请求区域构造后续查询可执行的区域覆盖参数."""
    region_obj = requested_region.get("region")
    source = region_obj if isinstance(region_obj, dict) else requested_region
    name = str(source.get("name") or source.get("region_name") or "").strip()
    level = str(source.get("level") or "").strip()
    if not name or level not in {"province", "city", "district"}:
        return None
    return {
        "name": name,
        "level": level,
        "data_type": "detail" if level == "district" else "summary",
    }


def _region_name_from_allowed_region(region: dict[str, Any] | None) -> str:
    """从 allowed/query region 摘要中提取区域名称."""
    if not isinstance(region, dict):
        return ""
    return str(region.get("name") or "").strip()


_CHILD_LEVEL_BELOW = {"province": "city", "city": "district"}


def _child_level_below(parent_level: str | None) -> str | None:
    """返回父级层级的直接下一级（province→city、city→district、其余 None）.

    行政区为 province/city/district 三级层级，集合/下钻查询的子级由父级层级
    确定性推断，不依赖 LLM 抽取的 child_level，避免层级误判。
    """
    return _CHILD_LEVEL_BELOW.get(str(parent_level or "").strip())


def _build_drilldown_requested_region(
    parent_resolved: dict[str, Any],
    child_level: str,
) -> dict[str, Any]:
    """将"父级范围 + 子级层级"下钻查询构造为单个 requested_region.

    权限判定按"子级定级"：如"河南省有多少城市"定级为 city（父级河南省作为
    省份范围上下文），"平顶山各区县"定级为 district（父级平顶山作为地市范围
    上下文）。这样可直接复用引擎的单区域归属判定（same_province/same_city），
    子级的物理展开交给数据工具的 scope=CHILDREN，不在权限层做。
    """
    parent_allowed = _allowed_region_from_requested_region(parent_resolved) or {}
    parent_name = str(parent_allowed.get("name") or "")
    province_obj = parent_resolved.get("province")
    province_name = (
        str(province_obj.get("name") or "") if isinstance(province_obj, dict) else ""
    )
    region: dict[str, Any] = {
        "found": True,
        "ambiguous": False,
        "region": {"name": parent_name, "level": child_level},
    }
    if child_level == "city":
        # 父级是省：省份范围上下文即父级自身
        region["province"] = {"name": parent_name}
    elif child_level == "district":
        # 父级是市：地市范围上下文为父级，省份沿用父级所属省
        region["city"] = {"name": parent_name}
        if province_name:
            region["province"] = {"name": province_name}
    return region


@dataclass(frozen=True)
class RegionRequestItem:
    """区域请求计划中的单个区域项."""

    query: str
    source: str = "explicit"
    requested_level: str | None = None
    province: str | None = None
    city: str | None = None
    role: str = "query"
    child_level: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegionRequestPlan:
    """权限链路使用的区域请求计划."""

    mode: str
    source_text: str
    items: list[RegionRequestItem] = field(default_factory=list)
    parent: RegionRequestItem | None = None
    child_level: str | None = None


_REGION_SUFFIX_RE = re.compile(r"([\u4e00-\u9fa5]{2,12}(?:省|市|区|县|州|盟|旗))")
_COLLECTION_PATTERNS = (
    "下的区县",
    "下辖区县",
    "各区县",
    "所有区县",
    "辖区内区县",
    "区县分别",
    "各县市区",
    "有多少个城市",
    "有多少城市",
    "多少个城市",
    "哪些城市",
    "各城市",
    "各地市",
    "下辖城市",
    "下的城市",
    "所有城市",
)
_MULTI_REGION_HINTS = ("和", "及", "与", "、", ",", "，", "分别", "对比", "比较")


def _dedupe_region_names(names: Sequence[str]) -> list[str]:
    """按出现顺序去重行政区名称."""
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        cleaned = name.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _drop_composite_region_names(names: Sequence[str]) -> list[str]:
    """移除由多个已识别行政区拼接成的复合捕获."""
    result: list[str] = []
    for name in names:
        contained = [other for other in names if other != name and other in name]
        if len(contained) >= 2:
            continue
        result.append(name)
    return result


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    """按出现顺序去重字符串列表."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _is_station_query(slots: dict[str, Any]) -> bool:
    """判断 slots 是否表示站点数据查询."""
    return str(slots.get("data_scope") or "").strip() == "station"


def _station_filters(slots: dict[str, Any]) -> tuple[list[str], list[str]]:
    """读取并清洗站点名称和站点类型筛选条件."""
    from common.permission.slots import normalize_string_list

    return (
        normalize_string_list(slots.get("station_names")),
        normalize_string_list(slots.get("station_types")),
    )


def _dedupe_time_spans(values: Sequence[list[str]]) -> list[list[str]]:
    """按出现顺序去重时间范围."""
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for value in values:
        key = tuple(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _dedupe_region_dicts(values: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 name/level/data_type 去重区域 dict."""
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        name = str(value.get("name") or "")
        level = str(value.get("level") or "")
        data_type = str(value.get("data_type") or "")
        key = (name, level, data_type)
        if not name or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _extract_region_names_from_question(question: str) -> list[str]:
    """从问题文本中提取带行政区后缀的实体."""
    return _dedupe_region_names(
        match.group(1) for match in _REGION_SUFFIX_RE.finditer(question)
    )


def _infer_region_level(query: str, fallback: str | None = None) -> str | None:
    """根据后缀推断行政区层级."""
    if query.endswith("省"):
        return "province"
    if query.endswith("市"):
        return "city"
    if query.endswith(("区", "县", "旗")):
        return "district"
    return fallback if fallback in {"province", "city", "district"} else None


def _question_has_collection_intent(question: str, slots: dict[str, Any]) -> bool:
    """判断是否存在集合/下钻行政区语义."""
    intent = slots.get("region_collection_intent")
    if isinstance(intent, str) and intent.strip():
        return True
    return any(pattern in question for pattern in _COLLECTION_PATTERNS)


def _question_has_multi_region_intent(question: str) -> bool:
    """判断是否存在显式多行政区语义提示."""
    return any(hint in question for hint in _MULTI_REGION_HINTS)


def _normalize_region_request_item(value: Any) -> RegionRequestItem | None:
    """将 slots regions 项归一化为内部请求项."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        return None
    text = _clean_region_slot(value.get("text"))
    if not text:
        return None
    level_hint = normalize_region_level(value.get("level_hint")) or _infer_region_level(
        text
    )
    source = str(value.get("source") or "explicit").strip() or "explicit"
    role = str(value.get("role") or "query").strip() or "query"
    child_level = normalize_region_level(value.get("child_level"))
    return RegionRequestItem(
        query=text,
        source=source,
        requested_level=level_hint,
        province=_clean_region_slot(value.get("province_hint")),
        city=_clean_region_slot(value.get("city_hint")),
        role=role,
        child_level=child_level,
        raw={
            "text": text,
            "level_hint": level_hint,
            "province_hint": _clean_region_slot(value.get("province_hint")),
            "city_hint": _clean_region_slot(value.get("city_hint")),
            "source": source,
            "role": role,
            "child_level": child_level,
        },
    )


def normalize_region_level(value: Any) -> str | None:
    """归一化区域层级字符串."""
    return normalize_target_level(str(value)) if value is not None else None


def _region_items_from_slots(slots: dict[str, Any]) -> list[RegionRequestItem]:
    """读取 slots regions 列表并归一化."""
    raw_regions = slots.get("regions")
    if not isinstance(raw_regions, list):
        return []
    items: list[RegionRequestItem] = []
    for raw_region in raw_regions:
        item = _normalize_region_request_item(raw_region)
        if item:
            items.append(item)
    return items


def build_region_request_plan(
    question: str,
    slots: dict[str, Any],
) -> RegionRequestPlan:
    """根据原始问题和 slots 生成区域请求计划."""
    region_items = _region_items_from_slots(slots)
    raw_mode = str(slots.get("region_mode") or "").strip()
    # 抽取模型偶发把含显式行政区的问题误判为 auto_fill（丢弃区域）。
    # 若问题正则可提取到显式行政区，则忽略该 auto_fill，改走实体解析，
    # 避免"河南省有多少城市"被补全成用户绑定区域。
    if (
        raw_mode == "auto_fill"
        and not region_items
        and _extract_region_names_from_question(question)
    ):
        raw_mode = ""
    if region_items or raw_mode == "auto_fill":
        mode = (
            raw_mode
            if raw_mode
            else ("multi_explicit" if len(region_items) > 1 else "single")
        )
        if mode == "collection":
            parent = next(
                (item for item in region_items if item.role == "collection_parent"),
                region_items[0] if region_items else None,
            )
            return RegionRequestPlan(
                mode="collection",
                source_text=question,
                parent=parent,
                child_level=(parent.child_level if parent else None) or "district",
            )
        if mode == "auto_fill":
            return RegionRequestPlan(mode="auto_fill", source_text=question)
        return RegionRequestPlan(
            mode="multi_explicit" if len(region_items) > 1 else "single",
            source_text=question,
            items=region_items,
        )

    cleaned_slots = _clean_region_slots(slots)
    slot_entities = slots.get("region_entities")
    entities: list[str] = []
    if isinstance(slot_entities, list):
        entities.extend(
            str(item).strip() for item in slot_entities if str(item).strip()
        )
    entities.extend(_extract_region_names_from_question(question))
    entities = _drop_composite_region_names(_dedupe_region_names(entities))

    province = cleaned_slots["province"]
    city = cleaned_slots["city"]
    district = cleaned_slots["district"]
    target_level = slots.get("target_level")

    if _question_has_collection_intent(question, slots):
        parent_query = entities[0] if entities else city or province or district
        parent_level = _infer_region_level(parent_query or "", "city")
        if parent_query:
            parent = RegionRequestItem(
                query=parent_query,
                source="collection_parent",
                requested_level=parent_level,
                province=province,
                city=city if parent_level != "city" else None,
            )
            return RegionRequestPlan(
                mode="collection",
                source_text=question,
                parent=parent,
                child_level="district",
            )

    if len(entities) > 1 and _question_has_multi_region_intent(question):
        items = [
            RegionRequestItem(
                query=query,
                source="explicit",
                requested_level=_infer_region_level(query, target_level),
                province=province,
                city=(
                    city
                    if _infer_region_level(query, target_level) == "district"
                    else None
                ),
            )
            for query in entities
        ]
        return RegionRequestPlan(
            mode="multi_explicit",
            source_text=question,
            items=items,
        )

    if district or city or province:
        query = district or city or province or ""
        return RegionRequestPlan(
            mode="single",
            source_text=question,
            items=[
                RegionRequestItem(
                    query=query,
                    source="single",
                    requested_level=_infer_region_level(query, target_level),
                    province=province,
                    city=city if district else None,
                )
            ],
        )

    return RegionRequestPlan(mode="auto_fill", source_text=question)


def _region_item_payload(item: RegionRequestItem) -> dict[str, Any]:
    """将内部区域项转为上下文载荷."""
    if item.raw:
        return dict(item.raw)
    return {
        "text": item.query,
        "level_hint": item.requested_level,
        "province_hint": item.province,
        "city_hint": item.city,
        "source": item.source,
        "role": item.role,
        "child_level": item.child_level,
    }


def _permission_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """生成单项权限结果的有界摘要."""
    return {
        "permitted": result.get("permitted"),
        "fix_strategy": result.get("fix_strategy") or "",
        "exemption": result.get("exemption") or "",
        "matched_rules": result.get("matched_rules") or [],
    }


def _build_region_permission_result(
    *,
    item: RegionRequestItem,
    resolved_region: dict[str, Any] | None,
    status: str,
    query_region: dict[str, Any] | None,
    permission_result: dict[str, Any],
    reason: str | None = None,
) -> dict[str, Any]:
    """构造逐项区域权限结果."""
    return {
        "requested": _region_item_payload(item),
        "resolved": resolved_region,
        "status": status,
        "query_region": query_region,
        "legal_time_span": permission_result.get("legal_time_span"),
        "correction_text": permission_result.get("correction_text") or "",
        "reason": reason or permission_result.get("reason") or "",
        "permission_result": _permission_result_summary(permission_result),
    }


def _build_unresolved_region_permission_result(
    unresolved_region: dict[str, Any],
) -> dict[str, Any]:
    """构造无法解析区域的逐项拒绝结果."""
    query = str(unresolved_region.get("query") or "")
    reason = str(unresolved_region.get("reason") or "行政区解析失败")
    return {
        "requested": {
            "text": query,
            "level_hint": unresolved_region.get("level_hint"),
            "province_hint": unresolved_region.get("province_hint"),
            "city_hint": unresolved_region.get("city_hint"),
            "source": unresolved_region.get("source") or "explicit",
            "role": unresolved_region.get("role") or "query",
            "child_level": unresolved_region.get("child_level"),
        },
        "resolved": None,
        "status": "rejected",
        "query_region": None,
        "legal_time_span": None,
        "correction_text": reason,
        "reason": reason,
        "permission_result": {
            "permitted": False,
            "fix_strategy": "reject",
            "exemption": "",
            "matched_rules": ["requested_region_unresolved"],
        },
    }


class PermissionClassifyMiddleware(AgentMiddleware[SkillDiscoveryState, Any, Any]):
    """在主模型循环前完成权限需求预分类，按需调用规则引擎.

    确定性计算（get_beijing_time、permission_region_context_tool）在中间件层
    直接完成。规则引擎负责最终权限判定，LLM 只做信息抽取。
    """

    state_schema = SkillDiscoveryState
    tools: Sequence[BaseTool] = ()

    def __init__(self) -> None:
        """初始化缓存字段，不预编译 agent."""
        self._slot_extractor: Any = None

    def _latest_human_content(self, messages: Sequence[Any]) -> str | None:
        """获取最近一条用户消息文本."""
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return get_message_content(message)
            if isinstance(message, dict) and message.get("role") in {"user", "human"}:
                content = message.get("content")
                return content if isinstance(content, str) else str(content)
        return None

    def _permission_context(self, state: dict[str, Any]) -> str | None:
        """生成注入主模型的权限上下文；no_check 时返回 None."""
        permission_need = state.get("permission_need", "no_check")
        if permission_need == "no_check":
            return None
        permission_result = state.get("permission_result")
        if not permission_result:
            return None

        # 确保 permission_result 是 dict（可能是 Pydantic 模型）
        if hasattr(permission_result, "model_dump"):
            permission_result = permission_result.model_dump()
        elif not isinstance(permission_result, dict):
            permission_result = {}

        payload: dict[str, Any] = {
            "permission_need": permission_need,
            "permission_result": permission_result,
        }

        # 确保 query_overrides 是 dict
        query_overrides = state.get("permission_query_overrides")
        if hasattr(query_overrides, "model_dump"):
            query_overrides = query_overrides.model_dump()
        if isinstance(query_overrides, dict):
            payload["permission_query_overrides"] = query_overrides

        # 提取修正说明，用于最终披露
        correction_text = ""
        if isinstance(permission_result, dict):
            ct = permission_result.get("correction_text")
            if isinstance(ct, str) and ct.strip():
                correction_text = ct.strip()
        needs_disclosure = _needs_permission_disclosure(permission_result)

        # 安全序列化 payload
        try:
            payload_str = json.dumps(payload, ensure_ascii=False)
        except TypeError as exc:
            logger.warning("权限上下文序列化失败，尝试强制转换: {}", exc)
            # 强制将所有值转为字符串
            safe_payload = {k: str(v) for k, v in payload.items()}
            payload_str = json.dumps(safe_payload, ensure_ascii=False)

        lines = [
            "<permission_context>",
            payload_str,
            "</permission_context>",
            "",
            "权限审查已完成，请按以下规则调整后续查询：",
            "- permission_query_overrides 存在时，后续 skill 查找、参数抽取和工具调用必须以该对象为准",
            "- permission_query_overrides.regions 存在时，必须使用 regions 作为查询行政区列表，不得只取第一个区域，也不得退回原问题中的单个行政区",
            "- permission_query_overrides.station_overrides 存在时，后续站点类 Skill 和工具调用必须只使用该站点集合，不得按原始区域重新扩大站点范围",
            "- permission_result.region_results 存在时，必须用它理解每个请求区域的解析、允许、替换、拒绝和时间截断结果",
            "- permission_query_overrides.region 仅用于单区域兼容；permission_query_overrides.regions 存在时不得用 region 覆盖 regions",
            "- permission_result.region_mode 为 multi_explicit 或 collection 时，表示本次查询已完成多区域拆分或下级行政区展开",
            "- permission_query_overrides.region 存在时，无论 fix_strategy 是否为空，都必须使用该 region 作为查询行政区",
            "- fix_strategy=truncate_time：必须使用 permission_query_overrides.time_span 作为查询时间范围",
            "- fix_strategy=replace_region：必须使用 permission_query_overrides.region 作为查询行政区",
            "- fix_strategy=filter_stations：必须使用 permission_query_overrides.station_overrides 作为站点查询范围",
            "- fix_strategy 为空字符串且 permitted=true：不修正，按原问题继续",
            "- fix_strategy 为空字符串且 permitted=true 且 auto_filled=true：使用 permission_query_overrides.region 继续查询",
            "- fix_strategy 为空字符串且 permitted=false 且无 permission_query_overrides：拒绝该查询，告知用户无权访问",
            "- permission_result.auto_filled=true 时，表示行政区已被自动补全，请知晓用户未指定具体区域",
        ]

        if needs_disclosure:
            lines.extend(
                [
                    "- permission_result.correction_text 是用户可见权限披露的主要依据；permission_result.reason 是解释原请求为什么被拦截或修正的辅助依据",
                    "- 存在权限修正、拒绝、截断、过滤、自动补全或部分区域处理时，最终回复必须结合 correction_text 与 reason，用自然语言说明发生了什么变化以及实际查询范围是什么",
                ]
            )

        # 如果有修正说明，要求以自然语言说明权限修正
        if needs_disclosure and correction_text:
            lines.extend(
                [
                    "",
                    "【重要】本次查询存在权限修正，你必须在回复中向用户说明以下内容：",
                    f"- 主要说明：{correction_text}",
                    "- 请结合以上信息用自然语言解释权限修正原因和实际查询范围，不要按字段名或 JSON 结构输出",
                    "",
                    "注意：权限修正说明应自然融入回复中，让用户理解为什么查询范围与原请求不同。",
                ]
            )

        return "\n".join(lines)

    def _rejection_message(self, state: dict[str, Any]) -> str | None:
        """返回硬拒绝场景的最终答复；非硬拒绝返回 None."""
        permission_need = state.get("permission_need", "no_check")
        if permission_need == "no_check":
            return None

        permission_result = state.get("permission_result")
        if not isinstance(permission_result, dict):
            return None

        permitted = permission_result.get("permitted")
        fix_strategy = str(permission_result.get("fix_strategy") or "")
        query_overrides = state.get("permission_query_overrides")
        if (
            permission_result.get("auto_filled")
            and isinstance(query_overrides, dict)
            and isinstance(query_overrides.get("region"), dict)
        ):
            return None
        if (
            fix_strategy == "filter_stations"
            and isinstance(query_overrides, dict)
            and isinstance(query_overrides.get("station_overrides"), list)
            and query_overrides.get("station_overrides")
        ):
            return None
        if permitted is not False or fix_strategy not in {"", "reject"}:
            return None

        correction_text = permission_result.get("correction_text")
        if isinstance(correction_text, str) and correction_text.strip():
            return correction_text.strip()

        reason = permission_result.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()

        return "因数据权限限制，您无权访问该数据。"

    def _ensure_slot_extractor(self) -> Any:
        """懒创建 slots 抽取模型（deepseek-v4-flash + bind_tools）.

        deepseek 走的 OpenAI 兼容通道（Console Go）不支持强制
        tool_choice / json_schema（稳定 400），改用 bind_tools(auto) 方式
        让模型调用工具，再在应用层用 Pydantic 校验 tool_calls 参数。
        """
        if self._slot_extractor is not None:
            return self._slot_extractor

        try:
            self._slot_extractor = ModelRegistry.deepseek_v4_flash.bind_tools(
                [PermissionExtractedSlots],
            )
            logger.debug("Slots 抽取模型创建成功")
        except Exception as exc:
            logger.warning("Slots 抽取模型构建失败: {}", exc)
            self._slot_extractor = None
        return self._slot_extractor

    async def _prefetch_deterministic_context(
        self,
        user_id: str,
        latest_question: str | None,
    ) -> dict[str, Any]:
        """中间件层直接完成确定性计算，返回预置上下文.

        包含：北京时间、用户画像 MCP 调用结果。
        """
        raw_time = _get_beijing_time_payload()
        beijing_time_result = str(raw_time.get("datetime") or "")

        from common.permission.mcp_tools import fetch_user_profile

        user_profile_result = await fetch_user_profile(user_id)

        return {
            "beijing_time": beijing_time_result,
            "user_profile": user_profile_result,
        }

    def _extract_user_bound_region_name(
        self, user_profile: dict[str, Any] | None
    ) -> str:
        """从用户画像的 bound_region 中提取关联行政区名称."""
        region = _region_from_user_bound_region(user_profile)
        if not region:
            return ""
        region_obj = region.get("region")
        if not isinstance(region_obj, dict):
            return ""
        return str(region_obj.get("name") or "").strip()

    async def _resolve_requested_region(
        self,
        slots: dict[str, Any],
        user_profile: dict[str, Any] | None,
        latest_question: str | None,
    ) -> dict[str, Any] | None:
        """使用 slots 中的 province/city/district 解析请求行政区.

        优先使用 LLM 提取的三级字段，未提取时使用用户画像兜底。
        当 slots 中没有任何行政区信息时，直接返回 None，由规则引擎使用默认区域。
        """
        # 从 slots 中提取 LLM 识别的三级行政区
        cleaned_slots = _clean_region_slots(slots)
        llm_province = cleaned_slots["province"]
        llm_city = cleaned_slots["city"]
        llm_district = cleaned_slots["district"]
        target_level = slots.get("target_level")

        # 确定 query：优先使用最细粒度的行政区
        query = None
        query_level = target_level
        if llm_district:
            query = llm_district
            query_level = "district"
        elif llm_city:
            query = llm_city
            query_level = "city"
        elif llm_province:
            query = llm_province
            query_level = "province"

        # 当 slots 中没有任何行政区信息时，直接返回 None，由中间件补全 bound_region。
        if not query:
            logger.debug("Slots 中未提取到行政区信息，跳过 resolve_region_scope")
            return None

        # 消歧参数按查询层级最小化传递：
        # - 区县重名最常见，需要 city/province 消歧
        # - 地市重名可用 province 消歧
        # - 省级查询不应带用户所在 city，否则会干扰解析（如河南省 + 郑州市）
        # - 不使用用户绑定省市作为默认消歧，避免跨省显式城市被用户画像污染
        city = ""
        province = ""
        if query_level == "district":
            city = llm_city or ""
            province = llm_province or ""
        elif query_level == "city":
            province = llm_province or ""

        from common.permission.mcp_tools import resolve_region

        result = await resolve_region(
            query,
            city=city or "",
            province=province or "",
        )
        return result

    async def _resolve_region_request_item(
        self,
        item: RegionRequestItem,
        slots: dict[str, Any],
        user_profile: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """按区域请求项解析行政区，并最小化消歧上下文."""
        query_level = item.requested_level or _infer_region_level(
            item.query,
            slots.get("target_level"),
        )

        city = ""
        province = ""
        if query_level == "district":
            city = item.city or _clean_region_slots(slots)["city"] or ""
            province = item.province or _clean_region_slots(slots)["province"] or ""
        elif query_level == "city":
            province = item.province or _clean_region_slots(slots)["province"] or ""

        from common.permission.mcp_tools import resolve_region

        return await resolve_region(
            item.query, city=city or "", province=province or ""
        )

    async def _resolve_station_facts_for_regions(
        self,
        *,
        item_regions: list[tuple[RegionRequestItem, dict[str, Any]]],
        slots: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按已解析区域逐项调用站点归属工具并合并事实."""
        if not _is_station_query(slots):
            return [], []

        station_names, station_types = _station_filters(slots)
        station_candidates: list[dict[str, Any]] = []
        unavailable_stations: list[dict[str, Any]] = []

        from common.permission.mcp_tools import resolve_station_scope

        # 仅保留可提取区域名的项，逐区域并发调用站点归属工具
        named_items: list[tuple[RegionRequestItem, dict[str, Any] | None, str]] = []
        for item, resolved_region in item_regions:
            source_region = _allowed_region_from_requested_region(resolved_region)
            region_name = _region_name_from_allowed_region(source_region)
            if not region_name:
                continue
            named_items.append((item, source_region, region_name))

        results = await asyncio.gather(
            *[
                resolve_station_scope(
                    region=region_name,
                    station_names=station_names,
                    station_types=station_types,
                )
                for _, _, region_name in named_items
            ]
        )

        for (item, source_region, _), result in zip(named_items, results):
            source_item = _region_item_payload(item)
            for station in result.get("stations") or []:
                if not isinstance(station, dict):
                    continue
                station_candidates.append(
                    {
                        **station,
                        "source_region": source_region,
                        "source_item": source_item,
                    }
                )
            for station in result.get("unavailable_stations") or []:
                if not isinstance(station, dict):
                    continue
                unavailable_stations.append(
                    {
                        **station,
                        "source_region": source_region,
                        "source_item": source_item,
                    }
                )

        return station_candidates, unavailable_stations

    async def _resolve_collection_drilldown(
        self,
        plan: RegionRequestPlan,
        slots: dict[str, Any],
        user_profile: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """将集合/下钻计划解析为单个下钻 requested_region.

        不在权限层物理展开子级（该职责属于数据工具的 scope=CHILDREN），
        而是解析父级、按父级层级确定子级定级，构造一个以子级定级、父级作
        为范围上下文的 requested_region，交给标准单区域 check_permission。

        Returns:
            (下钻 requested_region, 父级查询区域摘要)；父级解析失败时返回 (None, None)，
            由后续流程降级为自动补全/基础权限处理。
        """
        if plan.parent is None:
            return None, None
        parent_resolved = await self._resolve_region_request_item(
            plan.parent, slots, user_profile
        )
        if not parent_resolved or parent_resolved.get("found") is not True:
            return None, None
        parent_allowed = _allowed_region_from_requested_region(parent_resolved)
        if not parent_allowed:
            return None, None

        child_level = _child_level_below(parent_allowed.get("level"))
        if not child_level:
            # 父级已是最细层级，无可下钻：按父级自身单区域查询
            return parent_resolved, parent_allowed

        drilldown_region = _build_drilldown_requested_region(
            parent_resolved, child_level
        )
        return drilldown_region, parent_allowed

    def _merge_batch_permission_results(
        self,
        *,
        plan: RegionRequestPlan,
        item_results: list[tuple[RegionRequestItem, dict[str, Any], PermissionResult]],
        unresolved_regions: list[dict[str, Any]],
        parent_region: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """聚合逐项权限结果和查询覆盖参数."""
        requested_regions: list[dict[str, Any]] = []
        allowed_regions: list[dict[str, Any]] = []
        corrected_regions: list[dict[str, Any]] = []
        rejected_regions = list(unresolved_regions)
        region_corrections: list[dict[str, Any]] = []
        region_results: list[dict[str, Any]] = []
        legal_spans: list[list[str]] = []
        matched_rules: list[str] = []

        for item, requested_region, permission_result in item_results:
            requested_allowed = _allowed_region_from_requested_region(requested_region)
            if requested_allowed:
                requested_regions.append(requested_allowed)

            result = permission_result.model_dump()
            matched_rules.extend(result.get("matched_rules") or [])
            fix_strategy = str(result.get("fix_strategy") or "")
            allowed_region = _coerce_allowed_region(result.get("allowed_region"))

            if result.get("legal_time_span"):
                legal_spans.append(list(result["legal_time_span"]))

            if fix_strategy == "replace_region" and allowed_region:
                allowed_regions.append(allowed_region)
                corrected_regions.append(allowed_region)
                region_results.append(
                    _build_region_permission_result(
                        item=item,
                        resolved_region=requested_allowed,
                        status="replaced",
                        query_region=allowed_region,
                        permission_result=result,
                    )
                )
                region_corrections.append(
                    {
                        "type": "replace_region",
                        "requested_region": requested_allowed,
                        "allowed_region": allowed_region,
                        "correction_text": result.get("correction_text") or "",
                    }
                )
                continue

            if fix_strategy == "truncate_time":
                if requested_allowed:
                    allowed_regions.append(requested_allowed)
                    corrected_regions.append(requested_allowed)
                region_results.append(
                    _build_region_permission_result(
                        item=item,
                        resolved_region=requested_allowed,
                        status="time_truncated",
                        query_region=requested_allowed,
                        permission_result=result,
                    )
                )
                region_corrections.append(
                    {
                        "type": "truncate_time",
                        "requested_region": requested_allowed,
                        "legal_time_span": result.get("legal_time_span"),
                        "correction_text": result.get("correction_text") or "",
                    }
                )
                continue

            if result.get("permitted") is True:
                if allowed_region:
                    allowed_regions.append(allowed_region)
                    query_region = allowed_region
                elif requested_allowed:
                    allowed_regions.append(requested_allowed)
                    query_region = requested_allowed
                else:
                    query_region = None
                region_results.append(
                    _build_region_permission_result(
                        item=item,
                        resolved_region=requested_allowed,
                        status=(
                            "auto_filled"
                            if result.get("auto_filled") or item.source == "auto_fill"
                            else "allowed"
                        ),
                        query_region=query_region,
                        permission_result=result,
                    )
                )
                continue

            rejected_region = {
                "requested_region": requested_allowed,
                "reason": result.get("correction_text")
                or result.get("reason")
                or "无权访问",
            }
            rejected_regions.append(rejected_region)
            region_results.append(
                _build_region_permission_result(
                    item=item,
                    resolved_region=requested_allowed,
                    status="rejected",
                    query_region=None,
                    permission_result=result,
                    reason=str(rejected_region["reason"]),
                )
            )

        for unresolved in unresolved_regions:
            region_results.append(
                _build_unresolved_region_permission_result(unresolved)
            )

        allowed_regions = _dedupe_region_dicts(allowed_regions)
        requested_regions = _dedupe_region_dicts(requested_regions)
        corrected_regions = _dedupe_region_dicts(corrected_regions)

        correction_parts: list[str] = []
        if plan.mode == "multi_explicit" and requested_regions:
            correction_parts.append(
                "已自动拆分查询区域为："
                + "、".join(r["name"] for r in requested_regions)
                + "。"
            )
        if plan.mode == "collection":
            parent_allowed = (
                _allowed_region_from_requested_region(parent_region)
                if parent_region
                else None
            )
            parent_name = str((parent_allowed or {}).get("name") or "父级区域")
            correction_parts.append(f"已自动展开{parent_name}下辖区县作为查询区域。")
        for correction in region_corrections:
            text = correction.get("correction_text")
            if isinstance(text, str) and text and text not in correction_parts:
                correction_parts.append(text)
        if rejected_regions:
            for rejected in rejected_regions:
                reason = rejected.get("reason") if isinstance(rejected, dict) else None
                if (
                    isinstance(reason, str)
                    and reason
                    and reason not in correction_parts
                ):
                    correction_parts.append(reason)
            rejected_names = [
                str(
                    ((item.get("requested_region") or {}).get("name"))
                    or item.get("query")
                    or "未知区域"
                )
                for item in rejected_regions
                if isinstance(item, dict)
            ]
            if allowed_regions:
                correction_parts.append(
                    "因权限限制，部分区域未查询："
                    + "、".join(rejected_names)
                    + "；已仅查询有权限的区域。"
                )
            else:
                correction_parts.append(
                    "因权限限制，您请求的区域均无法查询："
                    + "、".join(rejected_names)
                    + "。"
                )

        if plan.mode == "single" and len(item_results) == 1 and not unresolved_regions:
            _, _, single_permission_result = item_results[0]
            single_result = single_permission_result.model_dump()
            single_result["region_mode"] = "single"
            single_result["requested_regions"] = requested_regions
            single_result["allowed_regions"] = allowed_regions
            single_result["corrected_regions"] = corrected_regions
            single_result["rejected_regions"] = rejected_regions
            single_result["region_corrections"] = region_corrections
            single_result["region_results"] = region_results
            if correction_parts and not single_result.get("correction_text"):
                single_result["correction_text"] = "".join(correction_parts)
            query_overrides = _build_query_overrides(single_result)
            return single_result, query_overrides

        permitted = bool(allowed_regions)
        result = PermissionResult(
            permitted=permitted,
            exemption="normal" if permitted else "",
            fix_strategy="" if permitted else "reject",
            reason="多区域权限聚合完成" if permitted else "全部请求区域不可查询",
            correction_text="".join(correction_parts),
            region_mode=plan.mode,
            requested_regions=requested_regions,
            allowed_regions=allowed_regions,
            corrected_regions=corrected_regions,
            rejected_regions=rejected_regions,
            region_corrections=region_corrections,
            region_results=region_results,
            matched_rules=_dedupe_strings(matched_rules),
        ).model_dump()

        if legal_spans:
            unique_spans = _dedupe_time_spans(legal_spans)
            if len(unique_spans) == 1:
                result["legal_time_span"] = unique_spans[0]
            else:
                result.setdefault("debug_context", {})
                result["debug_context"]["region_legal_time_spans"] = [
                    correction
                    for correction in region_corrections
                    if correction.get("type") == "truncate_time"
                ]

        if not permitted:
            return result, None

        overrides: dict[str, Any] = {
            "fix_strategy": "",
            "correction_text": result.get("correction_text") or "",
            "regions": allowed_regions,
            "region_mode": plan.mode,
        }
        if len(allowed_regions) == 1:
            overrides["region"] = allowed_regions[0]
        if result.get("legal_time_span"):
            overrides["time_span"] = result["legal_time_span"]
        return result, overrides

    async def _extract_slots(
        self,
        latest_question: str,
        beijing_time: str,
    ) -> dict[str, Any] | None:
        """调用 slots 抽取模型获取 PermissionExtractedSlots.

        deepseek 通道走 bind_tools(auto)，从 tool_calls 提取参数并用
        Pydantic 校验归一；未调用工具或异常时返回 None（下游降级）。
        """
        extractor = self._ensure_slot_extractor()
        if extractor is None:
            logger.warning("Slots 抽取模型不可用，跳过抽取")
            return None

        try:
            task_description = (
                "请从以下用户问题中抽取权限审查相关的结构化信息。\n\n"
                f"当前北京时间：{beijing_time}\n"
                f"用户问题：{latest_question}\n\n"
                "相对时间（如今天、昨天、近7天、上月、今年）必须基于当前北京时间"
                "归一化为具体 original_time_span。"
                "请务必调用 PermissionExtractedSlots 工具返回抽取结果，不要直接以文本回答。"
            )
            response = await extractor.ainvoke(
                [
                    SystemMessage(content=PERMISSION_SLOT_EXTRACTION_PROMPT),
                    HumanMessage(content=task_description),
                ],
            )
            tool_calls = getattr(response, "tool_calls", None) or []
            if tool_calls:
                return PermissionExtractedSlots.model_validate(
                    tool_calls[0].get("args") or {}
                ).model_dump()
            logger.warning("Slots 抽取未返回工具调用，降级跳过")
        except Exception as exc:
            logger.warning("Slots 抽取失败: {}", exc)

        return None

    async def _run_rule_engine(
        self,
        user_id: str,
        latest_question: str,
        user_profile: dict[str, Any],
        requested_region: dict[str, Any] | None,
        beijing_time: str,
    ) -> PermissionResult:
        """运行确定性规则引擎."""
        # 构造 facts
        facts = PermissionFacts(
            question=latest_question,
            user_id=user_id,
            beijing_time=beijing_time,
            user_profile=user_profile,
            requested_region=requested_region,
        )
        return check_permission(facts)

    async def abefore_agent(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """执行权限需求预分类，按需调用规则引擎."""
        writer = getattr(runtime, "stream_writer", None)
        update: dict[str, Any] = {}

        # 1. 从 configurable 提取 user_id
        user_id = state.get("user_id") or ""
        if not user_id:
            try:
                config = get_config()
                configurable = config.get("configurable") or {}
                if isinstance(configurable, dict):
                    user_id = str(configurable.get("user_id") or "")
            except Exception:
                pass
        if user_id:
            update["user_id"] = user_id

        # 2. 规则预分类（纯规则；LLM 语义判定已合并进槽位抽取的 need_check 字段）
        permission_need = "no_check"
        latest_question: str | None = None
        try:
            latest_question = self._latest_human_content(state.get("messages", []))
            if latest_question:
                permission_need = await classify_permission_need(latest_question)
        except Exception as exc:
            logger.warning("权限需求预分类失败，降级为 no_check: {}", exc)
            permission_need = "no_check"

        update["permission_need"] = permission_need
        logger.debug("权限需求预分类结果: {}", permission_need)

        # 3. no_check 时快速返回
        if permission_need == "no_check":
            return update

        if callable(writer):
            writer({"type": "progress", "message": "正在验证权限信息..."})

        # 4. user_id 缺失时无法审查，降级为 no_check
        if not user_id:
            logger.warning("user_id 缺失，无法执行权限审查，降级为 no_check")
            update["permission_need"] = "no_check"
            return update

        # 5. 确保 MCP 工具已加载
        await mcp_client.ensure_mcp_tools()

        # 6+7. 并行执行确定性上下文预取与 LLM 槽位抽取（两者互不依赖）。
        # 槽位抽取所需北京时间为本地计算，无需等待画像 MCP 返回。
        local_beijing_time = str(_get_beijing_time_payload().get("datetime") or "")
        slots: dict[str, Any] | None = None
        if latest_question:
            deterministic_context, slots = await asyncio.gather(
                self._prefetch_deterministic_context(user_id, latest_question),
                self._extract_slots(latest_question, local_beijing_time),
            )
        else:
            deterministic_context = await self._prefetch_deterministic_context(
                user_id,
                latest_question,
            )
        beijing_time = (
            str(deterministic_context.get("beijing_time") or "") or local_beijing_time
        )
        user_profile = deterministic_context.get("user_profile") or {}
        if slots:
            slots = _filter_unsupported_region_slots(slots, latest_question)

        # 8. LLM 判断为不需要校验 → 直接放行
        if slots and not slots.get("need_check", True):
            logger.debug("LLM 判断为不需要权限校验，直接放行")
            update["permission_need"] = "no_check"
            return update

        # 9. 使用 slots 解析请求行政区
        requested_region: dict[str, Any] | None = None
        auto_filled_region = False
        auto_filled_allowed_region: dict[str, Any] | None = None
        collection_query_region: dict[str, Any] | None = None
        if latest_question:
            slots_dict = slots or {}
            region_plan = build_region_request_plan(latest_question, slots_dict)
            if region_plan.mode == "collection" and region_plan.parent:
                # 集合/下钻查询（如"河南省有多少城市"）：不在权限层物理展开子级，
                # 而是按"子级定级"构造单个下钻 requested_region，走标准单区域审查
                # （天然含时间豁免与归属判定）；子级展开交给数据工具 scope=CHILDREN。
                (
                    requested_region,
                    collection_query_region,
                ) = await self._resolve_collection_drilldown(
                    region_plan, slots_dict, user_profile
                )
            elif region_plan.mode in {"single", "multi_explicit"}:
                item_regions: list[tuple[RegionRequestItem, dict[str, Any]]] = []
                unresolved_regions: list[dict[str, Any]] = []
                parent_region: dict[str, Any] | None = None

                if region_plan.mode in {"single", "multi_explicit"}:
                    # 各区域解析互不依赖，并发调用行政区解析 MCP
                    resolved_regions = await asyncio.gather(
                        *[
                            self._resolve_region_request_item(
                                item,
                                slots_dict,
                                user_profile,
                            )
                            for item in region_plan.items
                        ]
                    )
                    for item, resolved in zip(region_plan.items, resolved_regions):
                        if resolved:
                            item_regions.append((item, resolved))
                        else:
                            unresolved_regions.append(
                                {
                                    "query": item.query,
                                    "reason": "行政区解析失败",
                                    "level_hint": item.requested_level,
                                    "province_hint": item.province,
                                    "city_hint": item.city,
                                    "source": item.source,
                                    "role": item.role,
                                }
                            )

                item_results: list[
                    tuple[RegionRequestItem, dict[str, Any], PermissionResult]
                ] = []
                if _is_station_query(slots_dict):
                    (
                        station_candidates,
                        unavailable_stations,
                    ) = await self._resolve_station_facts_for_regions(
                        item_regions=item_regions,
                        slots=slots_dict,
                    )
                    facts = build_permission_facts(
                        question=latest_question or "",
                        user_id=user_id,
                        beijing_time=beijing_time,
                        user_profile=user_profile,
                        requested_region=None,
                        slots=slots_dict,
                        station_candidates=station_candidates,
                        unavailable_stations=unavailable_stations,
                        station_region_mode=region_plan.mode,
                        raw_context=deterministic_context,
                    )
                    permission_result = check_permission(facts)
                    result_dict = permission_result.model_dump()
                    source_regions = _dedupe_region_dicts(
                        [
                            region
                            for region in (
                                _allowed_region_from_requested_region(resolved)
                                for _, resolved in item_regions
                            )
                            if region
                        ]
                    )
                    if source_regions and result_dict.get("station_overrides"):
                        result_dict["region_mode"] = region_plan.mode
                        result_dict["requested_regions"] = source_regions
                        result_dict["allowed_regions"] = source_regions
                    elif source_regions:
                        result_dict["region_mode"] = region_plan.mode
                        result_dict["requested_regions"] = source_regions
                    if unresolved_regions:
                        result_dict["rejected_regions"] = unresolved_regions
                    update["permission_result"] = result_dict
                    query_overrides = _build_query_overrides(result_dict)
                    if query_overrides:
                        update["permission_query_overrides"] = query_overrides
                    logger.info(
                        "站点权限审查完成: mode={}, stations={}, accessible={}",
                        region_plan.mode,
                        result_dict.get("total_station_count"),
                        result_dict.get("accessible_station_count"),
                    )
                    return update

                for item, item_region in item_regions:
                    facts = build_permission_facts(
                        question=latest_question or "",
                        user_id=user_id,
                        beijing_time=beijing_time,
                        user_profile=user_profile,
                        requested_region=item_region,
                        slots=slots,
                        raw_context=deterministic_context,
                    )
                    item_results.append((item, item_region, check_permission(facts)))

                result_dict, query_overrides = self._merge_batch_permission_results(
                    plan=region_plan,
                    item_results=item_results,
                    unresolved_regions=unresolved_regions,
                    parent_region=parent_region,
                )
                update["permission_result"] = result_dict
                if query_overrides:
                    update["permission_query_overrides"] = query_overrides
                logger.info(
                    "多区域权限审查完成: mode={}, allowed={}, rejected={}",
                    region_plan.mode,
                    len(result_dict.get("allowed_regions") or []),
                    len(result_dict.get("rejected_regions") or []),
                )
                return update

            if region_plan.mode == "auto_fill" and requested_region is None:
                requested_region = _region_from_user_bound_region(user_profile)
                if requested_region:
                    auto_filled_region = True
                    auto_filled_allowed_region = _allowed_region_from_requested_region(
                        requested_region
                    )
                    bound_name = self._extract_user_bound_region_name(user_profile)
                    logger.info(
                        "用户问题未指定行政区，自动补全为 bound_region: {}", bound_name
                    )

        # 9. 构造 PermissionFacts 并调用规则引擎
        try:
            station_candidates: list[dict[str, Any]] = []
            unavailable_stations: list[dict[str, Any]] = []
            station_region_mode = ""
            if _is_station_query(slots or {}) and auto_filled_allowed_region:
                auto_fill_item = RegionRequestItem(
                    query=auto_filled_allowed_region["name"],
                    source="auto_fill",
                    requested_level=auto_filled_allowed_region["level"],
                    role="query",
                    raw={
                        "text": auto_filled_allowed_region["name"],
                        "level_hint": auto_filled_allowed_region["level"],
                        "province_hint": None,
                        "city_hint": None,
                        "source": "auto_fill",
                        "role": "query",
                        "child_level": None,
                    },
                )
                (
                    station_candidates,
                    unavailable_stations,
                ) = await self._resolve_station_facts_for_regions(
                    item_regions=[(auto_fill_item, requested_region or {})],
                    slots=slots or {},
                )
                station_region_mode = "auto_fill"

            facts = build_permission_facts(
                question=latest_question or "",
                user_id=user_id,
                beijing_time=beijing_time,
                user_profile=user_profile,
                requested_region=requested_region,
                slots=slots,
                station_candidates=station_candidates,
                unavailable_stations=unavailable_stations,
                station_region_mode=station_region_mode,
                raw_context=deterministic_context,
            )
            permission_result = check_permission(facts)
            result_dict = permission_result.model_dump()

            # collection 下钻放行：以父级（如河南省）作为查询区域下发，
            # 模型再按 scope=CHILDREN 展开子级；被驳回/修正时保留引擎结果。
            if collection_query_region and permission_result.permitted:
                result_dict["region_mode"] = "collection"
                result_dict["allowed_regions"] = [collection_query_region]

            # 如果自动补全了行政区，在结果中标记
            if auto_filled_region:
                result_dict["auto_filled"] = True
                if auto_filled_allowed_region:
                    result_dict["allowed_region"] = auto_filled_allowed_region
                    result_dict["region_mode"] = "auto_fill"
                    result_dict["allowed_regions"] = [auto_filled_allowed_region]
                    result_dict["requested_regions"] = []
                # 如果没有其他修正，补充一个自动补全的说明
                if not result_dict.get("correction_text"):
                    bound_name = self._extract_user_bound_region_name(user_profile)
                    result_dict["correction_text"] = (
                        f"根据您的关联区域，已自动补全查询区域为{bound_name}。"
                    )
                if auto_filled_allowed_region:
                    auto_fill_item = RegionRequestItem(
                        query=auto_filled_allowed_region["name"],
                        source="auto_fill",
                        requested_level=auto_filled_allowed_region["level"],
                        role="query",
                        raw={
                            "text": auto_filled_allowed_region["name"],
                            "level_hint": auto_filled_allowed_region["level"],
                            "province_hint": None,
                            "city_hint": None,
                            "source": "auto_fill",
                            "role": "query",
                            "child_level": None,
                        },
                    )
                    result_dict["region_results"] = [
                        _build_region_permission_result(
                            item=auto_fill_item,
                            resolved_region=auto_filled_allowed_region,
                            status="auto_filled",
                            query_region=auto_filled_allowed_region,
                            permission_result=result_dict,
                        )
                    ]

            update["permission_result"] = result_dict
            query_overrides = _build_query_overrides(result_dict)
            if query_overrides:
                if auto_filled_region and auto_filled_allowed_region:
                    query_overrides["regions"] = [auto_filled_allowed_region]
                    query_overrides["region_mode"] = "auto_fill"
                update["permission_query_overrides"] = query_overrides
            logger.info(
                "权限审查完成(规则引擎): permitted={}, fix_strategy={}, exemption={}",
                permission_result.permitted,
                permission_result.fix_strategy,
                permission_result.exemption,
            )
        except Exception as exc:
            logger.error("规则引擎执行失败: {}", exc)
            if PERMISSION_FAIL_CLOSED:
                # 安全降级：硬拒绝
                reject_result = PermissionResult(
                    permitted=False,
                    fix_strategy="reject",
                    reason=f"规则引擎异常: {exc}",
                    correction_text="因系统异常，暂时无法完成权限审查，请稍后重试。",
                )
                update["permission_result"] = reject_result.model_dump()
            else:
                # 宽松降级：跳过审查
                update["permission_need"] = "no_check"

        return update

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any] | AIMessage:
        """同步模型调用前注入权限上下文."""
        rejection_message = self._rejection_message(request.state)
        if rejection_message:
            logger.info("权限审查硬拒绝，短路主模型调用")
            return AIMessage(content=rejection_message)

        context = self._permission_context(request.state)
        if not context:
            return handler(request)
        system_message = append_to_system_message(request.system_message, context)
        return handler(request.override(system_message=system_message))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any] | AIMessage:
        """异步模型调用前注入权限上下文."""
        rejection_message = self._rejection_message(request.state)
        if rejection_message:
            logger.info("权限审查硬拒绝，短路主模型调用")
            return AIMessage(content=rejection_message)

        context = self._permission_context(request.state)
        if not context:
            return await handler(request)
        system_message = append_to_system_message(request.system_message, context)
        return await handler(request.override(system_message=system_message))
