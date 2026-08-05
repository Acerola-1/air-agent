"""Agent 工具模块.

整合 DeepAgent 工具和基础工具，精简结构。
所有 @tool 定义统一排列在文件底部。

本地化说明:
- 知识检索 (knowledge_retriever_tool) 与 Text2SQL (text2sql_tool) 依赖 Milvus/Ollama,
  本地环境无这两项服务,已整体删除。相关依赖模块(multi_query_retriever /
  milvus_vector_store / metadata_query_helper / vanna_sql_adapter / pg_store)一并移除。
- 保留的工具仅依赖:公网 LLM (deepseek_v4_flash) / Serper API / 本地区划树 JSON。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx
from common.config import config
from langchain_core.tools import tool
from loguru import logger
from pydantic import BaseModel, Field

from common.models import ModelRegistry
from common.prompts import region_extraction_prompt

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def shanghai_now() -> datetime:
    """返回当前北京时间."""
    return datetime.now(SHANGHAI_TZ)


# =========================================================================
# 区划树(供 parse_region_tool 展开省/市/区下级)
# =========================================================================
_region_tree: list[dict[str, Any]] = []


def _get_region_tree() -> list[dict[str, Any]]:
    """加载行政区划树(延迟加载)."""
    global _region_tree
    if not _region_tree:
        region_tree_path = (
            Path(__file__).parent.parent.parent / "static" / "region_tree.json"
        )
        try:
            with open(region_tree_path, encoding="utf-8") as f:
                _region_tree = json.load(f)
            logger.info(f"区划树加载成功: {len(_region_tree)} 个省份")
        except Exception as e:
            logger.error(f"区划树加载失败: {e}")
    return _region_tree


# =========================================================================
# 行政区划提取模型(parse_region_tool 的 LLM bind_tools 目标)
# =========================================================================
class RegionItem(BaseModel):
    """行政区划提取项."""

    name: str = Field(description="行政区划名称")
    type: str = Field(description="行政区划类型: province, city, district")
    operation: str = Field(
        description="操作类型: query, expand_cities, expand_districts, "
        "query_parent, query_sibling"
    )


class RegionExtraction(BaseModel):
    """行政区划提取结果."""

    items: List[RegionItem] = Field(default_factory=list)


# =========================================================================
# @tool 定义
# =========================================================================


@tool
async def websearch_tool(query: str, top_k: int = 5) -> Dict[str, Any]:
    """联网搜索工具,用于搜索互联网获取最新信息.

    通过 Serper API 抓取搜索结果,返回包含网页标题、链接、摘要的列表。
    """
    api_key = config.SERPER_API_KEY
    if not api_key:
        return {"snippets": [], "links": [], "error": "SERPER_API_KEY 未配置"}

    url = "https://google.serper.dev/search"
    payload = {"q": query, "gl": "cn", "hl": "zh-cn", "num": top_k}
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=config.DDGS_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"websearch_tool 调用失败: {e}")
        return {"snippets": [], "links": [], "error": str(e)}

    organic = data.get("organic", []) or []
    snippets: list[str] = []
    links: list[str] = []
    for item in organic[:top_k]:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        link = item.get("link", "")
        if title or snippet:
            snippets.append(f"{title}\n{snippet}".strip())
        if link:
            links.append(link)

    return {"snippets": snippets, "links": links}


@tool
def parse_region_tool(question: str) -> Dict[str, Any]:
    """解析用户问题中的行政区划信息,支持省市区的展开和查询.

    当用户问题包含省/市/区等地名且需要解析其下级区域列表时调用此工具。
    例如"浙江省的所有城市"会返回浙江省下辖城市列表。

    参数:
        question: 用户的问题文本

    返回:
        解析结果字典,包含:
        - has_region: bool, 是否包含行政区划信息
        - regions: list[str], 解析后的区域列表
        - raw_entities: list[dict], 原始提取的行政区划实体
    """
    region_trigger_keywords = (
        "省",
        "市",
        "区",
        "县",
        "自治州",
        "地区",
        "盟",
        "旗",
        "街道",
        "乡",
        "镇",
        "北京",
        "上海",
        "天津",
        "重庆",
        "全国",
        "区域",
    )
    if not any(k in question for k in region_trigger_keywords):
        return {"has_region": False, "regions": [], "raw_entities": []}

    region_tree = _get_region_tree()
    if not region_tree:
        return {"has_region": False, "regions": [], "raw_entities": []}

    final_regions: List[str] = []

    try:
        prompt = region_extraction_prompt(question)
        # deepseek 通道不支持强制结构化输出,改用 bind_tools(auto) +
        # 显式提示要求调用工具,再用 Pydantic 校验 tool_calls 参数。
        model_with_tool = ModelRegistry.deepseek_v4_flash.bind_tools([RegionExtraction])
        response = model_with_tool.invoke(
            [
                HumanMessage(
                    content=(
                        f"{prompt}\n\n"
                        "请务必调用 RegionExtraction 工具返回抽取结果,"
                        "不要直接以文本回答。"
                    )
                )
            ]
        )
        tool_calls = getattr(response, "tool_calls", None) or []
        extraction_result = (
            RegionExtraction.model_validate(tool_calls[0].get("args") or {})
            if tool_calls
            else None
        )
        logger.debug(f"LLM 提取结果: {extraction_result}")

        if extraction_result and extraction_result.items:
            municipalities = ["北京市", "上海市", "天津市", "重庆市"]

            for item in extraction_result.items:
                name = item.name
                op = item.operation
                rtype = item.type
                expanded: List[str] = []

                # 1. 直接查询
                if op == "query":
                    expanded = [name]

                # 2. 展开城市
                elif op == "expand_cities":
                    for prov in region_tree:
                        if prov["name"] == name:
                            if name in municipalities:
                                for city in prov.get("cities", []):
                                    expanded.extend(city.get("districts", []))
                            else:
                                for city in prov.get("cities", []):
                                    expanded.append(city["name"])
                            break

                # 3. 展开区县
                elif op == "expand_districts":
                    for prov in region_tree:
                        if prov["name"] == name:
                            for city in prov.get("cities", []):
                                expanded.extend(city.get("districts", []))
                            break

                    if not expanded:
                        if name in municipalities:
                            for prov in region_tree:
                                if prov["name"] == name:
                                    for city in prov.get("cities", []):
                                        expanded.extend(city.get("districts", []))
                                    break
                        else:
                            for prov in region_tree:
                                for city in prov.get("cities", []):
                                    if city["name"] == name:
                                        expanded = city.get("districts", [])
                                        break
                                if expanded:
                                    break

                # 4. 查询上级
                elif op == "query_parent":
                    if rtype == "district":
                        for prov in region_tree:
                            found = False
                            for city in prov.get("cities", []):
                                if name in city.get("districts", []):
                                    if prov["name"] in municipalities:
                                        expanded = [prov["name"]]
                                    else:
                                        expanded = [city["name"]]
                                    found = True
                                    break
                            if found:
                                break
                    elif rtype == "city":
                        for prov in region_tree:
                            for city in prov.get("cities", []):
                                if city["name"] == name:
                                    expanded = [prov["name"]]
                                    break
                            if prov["name"] == name:
                                expanded = ["中国"]
                                break
                            if expanded:
                                break

                # 5. 查询同级
                elif op == "query_sibling":
                    if rtype == "district":
                        for prov in region_tree:
                            found = False
                            for city in prov.get("cities", []):
                                if name in city.get("districts", []):
                                    expanded = [
                                        d
                                        for d in city.get("districts", [])
                                        if d != name
                                    ]
                                    found = True
                                    break
                            if found:
                                break
                    elif rtype == "city":
                        for prov in region_tree:
                            found = False
                            for city in prov.get("cities", []):
                                if city["name"] == name:
                                    expanded = [
                                        c["name"]
                                        for c in prov.get("cities", [])
                                        if c["name"] != name
                                    ]
                                    found = True
                                    break
                            if found:
                                break
                        if not expanded and name in municipalities:
                            expanded = [m for m in municipalities if m != name]

                if expanded:
                    final_regions.extend(expanded)

        seen = set()
        unique_regions: List[str] = []
        for r in final_regions:
            if r not in seen:
                unique_regions.append(r)
                seen.add(r)

        raw_entities = (
            [
                {"name": item.name, "type": item.type, "operation": item.operation}
                for item in extraction_result.items
            ]
            if extraction_result
            else []
        )

        return {
            "has_region": True,
            "regions": unique_regions,
            "raw_entities": raw_entities,
        }

    except Exception as e:
        logger.error(f"区划解析失败: {e}")
        return {"has_region": False, "regions": [], "raw_entities": []}


@tool
def get_beijing_time() -> Dict[str, Any]:
    """获取当前北京时间.

    返回:
        包含 ISO 时间戳、北京时间字符串、Unix 时间戳、时区的字典。
    """
    now = shanghai_now()
    return {
        "iso": now.isoformat(),
        "beijing_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": int(now.timestamp()),
        "timezone": "Asia/Shanghai",
    }


__all__ = [
    "shanghai_now",
    "get_beijing_time",
    "parse_region_tool",
    "websearch_tool",
    "RegionItem",
    "RegionExtraction",
]
