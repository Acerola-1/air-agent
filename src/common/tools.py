"""Agent 工具模块.

整合 DeepAgent 工具和基础工具，精简结构。
所有 @tool 定义统一排列在文件底部。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from loguru import logger
from pydantic import BaseModel, Field
from pymilvus import MilvusClient  # noqa: E402

from common.config import config
from common.config.vanna_sql_adapter import VannaOllamaSqlAdapter  # noqa: E402
from common.metadata_query_helper import MetadataQueryHelper  # noqa: E402
from common.milvus_vector_store import Milvus_VectorStore_Extended  # noqa: E402
from common.models import ModelRegistry  # noqa: E402
from common.multi_query_retriever import MultiQueryRetriever  # noqa: E402
from common.prompts import (  # noqa: E402
    knowledge_retrieval_route_prompt,
    region_extraction_prompt,
)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def shanghai_now() -> datetime:
    """返回当前北京时间."""
    return datetime.now(SHANGHAI_TZ)


# =========================================================================
# 知识检索器
# =========================================================================
_kb: MultiQueryRetriever | None = None
_knowledge_route_cache: dict[tuple[str, int], tuple[float, str]] = {}

_POLICY_ROUTE_KEYWORDS = (
    "政策",
    "法规",
    "法律",
    "条例",
    "办法",
    "标准",
    "规范",
    "指南",
    "导则",
    "文件",
    "通知",
    "意见",
    "要求",
    "限值",
    "gb",
    "hj",
    "standard",
    "policy",
    "regulation",
)

_KNOWLEDGE_ROUTE_KEYWORDS = (
    "是什么",
    "什么意思",
    "解释",
    "定义",
    "原因",
    "影响",
    "危害",
    "治理",
    "控制",
    "来源",
    "成因",
    "污染物",
    "pm2.5",
    "pm10",
    "臭氧",
    "二氧化硫",
    "二氧化氮",
    "一氧化碳",
    "knowledge",
)


def _get_kb() -> MultiQueryRetriever:
    """获取知识检索器（延迟初始化）."""
    global _kb
    if _kb is None:
        _kb = MultiQueryRetriever()
        logger.info("MultiQueryRetriever 初始化完成")
    return _kb


def _knowledge_route_cache_ttl_seconds() -> int:
    """知识检索路由缓存 TTL."""
    return max(1, int(getattr(config, "KNOWLEDGE_ROUTE_CACHE_TTL_SECONDS", 300)))


def clear_knowledge_route_cache() -> None:
    """清空知识检索路由缓存，供测试使用."""
    _knowledge_route_cache.clear()


def _normalize_route_query(query: str) -> str:
    """归一化知识路由 query."""
    return " ".join(query.lower().strip().split())


def _deterministic_knowledge_route(query: str) -> str | None:
    """高置信度规则路由，无法判断时返回 None."""
    normalized = _normalize_route_query(query)
    policy_hit = any(keyword in normalized for keyword in _POLICY_ROUTE_KEYWORDS)
    knowledge_hit = any(keyword in normalized for keyword in _KNOWLEDGE_ROUTE_KEYWORDS)
    if policy_hit and not knowledge_hit:
        return "policy"
    if knowledge_hit and not policy_hit:
        return "knowledge"
    return None


def _llm_knowledge_route(query: str) -> str:
    """调用 LLM 兜底判断知识检索路由."""
    route_prompt = knowledge_retrieval_route_prompt(query)
    response = ModelRegistry.deepseek_v4_flash.invoke(route_prompt)

    content = response.content if hasattr(response, "content") else str(response)
    if isinstance(content, str):
        result = content.strip().lower()
    else:
        result = str(content).strip().lower() if content else "knowledge"

    return result if result in {"knowledge", "policy"} else "knowledge"


def _route_knowledge_query(query: str, top_k: int) -> str:
    """先走规则和缓存，必要时再调用 LLM 路由."""
    cache_key = (_normalize_route_query(query), top_k)
    now = time.monotonic()
    cached = _knowledge_route_cache.get(cache_key)
    if cached is not None:
        expires_at, route = cached
        if now < expires_at:
            return route
        _knowledge_route_cache.pop(cache_key, None)

    result = _deterministic_knowledge_route(query) or _llm_knowledge_route(query)
    _knowledge_route_cache[cache_key] = (
        now + _knowledge_route_cache_ttl_seconds(),
        result,
    )
    return result


# =========================================================================
# 区划树路径
# =========================================================================
_region_tree: list[dict[str, Any]] = []


def _get_region_tree() -> list[dict[str, Any]]:
    """加载行政区划树（延迟加载）."""
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
# 行政区划提取模型
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
# Vanna Text2SQL 基础设施
# =========================================================================
class AirQualityTextToSqlEngine(  # pyright: ignore[reportUnsafeMultipleInheritance]
    Milvus_VectorStore_Extended, VannaOllamaSqlAdapter
):
    """结合 Milvus 向量存储和 Ollama 集成的自定义 Vanna 类."""

    def __init__(self, vanna_config: Dict[str, Any] | None = None) -> None:
        """使用配置初始化 Text2SQL 引擎."""
        Milvus_VectorStore_Extended.__init__(self, config=vanna_config)
        VannaOllamaSqlAdapter.__init__(self, config=vanna_config)


model_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../model"))
_text2sql_engine: AirQualityTextToSqlEngine | None = None


class LocalEmbeddingWrapper:
    """适配 model2vec 的 embedding 包装类."""

    def __init__(self, local_model: Any) -> None:
        """初始化本地 embedding 模型适配器."""
        self._local_model = local_model

    def encode_documents(self, documents: List[str]) -> List[Any]:
        embeddings = self._local_model.encode(documents)
        return [np.array(e) for e in embeddings]

    def encode_queries(self, queries: List[str]) -> List[Any]:
        embeddings = self._local_model.encode(queries)
        return [np.array(e) for e in embeddings]


def _build_embedding_func() -> LocalEmbeddingWrapper | None:
    """懒加载本地 embedding 模型，避免导入 tools 时执行重初始化."""
    logger.info(f"Model directory: {model_dir}")
    if not os.path.exists(model_dir) or not os.listdir(model_dir):
        logger.warning(
            f"Model directory not found or empty: {model_dir}, using default embedding"
        )
        return None

    from model2vec import StaticModel

    return LocalEmbeddingWrapper(StaticModel.from_pretrained(model_dir))


def _get_text2sql_engine() -> AirQualityTextToSqlEngine:
    """获取 Text2SQL 引擎，首次调用时再连接向量库和业务库."""
    global _text2sql_engine
    if _text2sql_engine is not None:
        return _text2sql_engine

    embedding_func = _build_embedding_func()
    milvus_client = MilvusClient(uri=config.MILVUS_URI, db_name="vanna_db")
    vanna_config = {
        "model": config.OLLAMA_CHAT_MODEL,
        "ollama_host": config.OLLAMA_BASE_URL,
        "milvus_client": milvus_client,
        "n_results": 20,
        "dialect": "MySQL",
        "options": {"temperature": 0, "num_ctx": 14096},
    }
    if embedding_func is not None:
        vanna_config["embedding_function"] = embedding_func

    engine = AirQualityTextToSqlEngine(vanna_config=vanna_config)
    engine.connect_to_mysql(
        host=config.TEXT2SQL_MYSQL_HOST,
        dbname=config.TEXT2SQL_MYSQL_DB,
        user=config.TEXT2SQL_MYSQL_USER,
        password=config.TEXT2SQL_MYSQL_PASSWORD,
        port=config.TEXT2SQL_MYSQL_PORT,
    )
    _text2sql_engine = engine
    logger.success("Vanna Text2SQL 工具加载成功")
    return _text2sql_engine


# 兼容旧导入路径的别名。新代码应导入 AirQualityTextToSqlEngine。
MyVanna = AirQualityTextToSqlEngine


# =========================================================================
# Pydantic 参数模型
# =========================================================================
class TextToSQLArgs(BaseModel):
    """文本转 SQL 工具的参数架构."""

    question: str = Field(
        ...,
        description="自然语言问题，需包含明确的业务指标、时间范围或地理行政单位（省/市/县/乡镇）。支持空气质量(如PM2.5)查询、地理行政单位查询等场景。示例：'昨日平顶山市pm2.5是多少?'、'平顶山市所有乡镇有哪些'、'河南省有哪些地级市'",
        json_schema_extra={
            "examples": [
                "昨日平顶山市pm2.5是多少?",
                "郑州市过去6个小时的空气质量",
                "平顶山市所有乡镇有哪些",
            ]
        },
    )


# =========================================================================
# @tool 定义
# =========================================================================


@tool
async def websearch_tool(query: str, top_k: int = 5) -> Dict[str, Any]:
    """联网搜索工具，用于搜索互联网获取最新信息.

    参数:
        query: 搜索关键词
        top_k: 返回结果数量，默认5

    返回:
        搜索结果字典，包含:
        - snippets: list[str], 搜索结果摘要
        - links: list[str], 搜索结果链接
    """
    api_key = config.SERPER_API_KEY
    if not api_key:
        return {"snippets": [], "links": [], "error": "SERPER_API_KEY 未配置"}
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "num": top_k}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            raw = resp.json()
        organic = raw.get("organic", [])
        return {
            "snippets": [
                item.get("snippet", "") for item in organic if item.get("snippet")
            ],
            "links": [item.get("link", "") for item in organic if item.get("link")],
        }
    except Exception as e:
        logger.error(f"联网搜索失败: {e}")
        return {"snippets": [], "links": [], "error": str(e)}


@tool
def parse_region_tool(question: str) -> Dict[str, Any]:
    """解析用户问题中的行政区划信息，支持省市区的展开和查询.

    当用户问题包含省/市/区等地名且需要解析其下级区域列表时调用此工具。
    例如"浙江省的所有城市"会返回浙江省下辖城市列表。

    参数:
        question: 用户的问题文本

    返回:
        解析结果字典，包含:
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
        # deepseek 通道不支持强制结构化输出，改用 bind_tools(auto) +
        # 显式提示要求调用工具，再用 Pydantic 校验 tool_calls 参数。
        model_with_tool = ModelRegistry.deepseek_v4_flash.bind_tools([RegionExtraction])
        response = model_with_tool.invoke(
            [
                HumanMessage(
                    content=(
                        f"{prompt}\n\n"
                        "请务必调用 RegionExtraction 工具返回抽取结果，"
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

    返回北京时间的日期和时间信息，用于确定查询的日期范围。
    适用于需要获取"今天"、"昨天"等相对日期的场景。

    返回:
        时间信息字典，包含:
        - success: bool, 是否获取成功
        - date: str, 当前日期 (YYYY-MM-DD)
        - datetime: str, 完整时间 (YYYY-MM-DD HH:mm:ss)
        - year/month/day/hour: int, 各时间分量
        - error: str|None, 错误信息
    """
    try:
        now = shanghai_now()
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
    except Exception as e:
        return {
            "success": False,
            "date": "",
            "datetime": "",
            "year": 0,
            "month": 0,
            "day": 0,
            "hour": 0,
            "error": str(e),
        }


@tool
def knowledge_retriever_tool(query: str, top_k: int = 5) -> Dict[str, Any]:
    """检索环境空气知识库，返回与查询相关的政策法规和技术文档.

    用于环保领域知识性问题的检索，如政策法规、技术标准、环境术语解释等。
    当用户问题属于知识查询类（非数据查询）时调用此工具。

    参数:
        query: 检索查询文本
        top_k: 返回的最大文档数量，默认5

    返回:
        检索结果字典，包含:
        - success: bool, 检索是否成功
        - documents: list[dict], 检索到的文档列表
        - query: str, 原始查询文本
        - route: str, 路由结果 (knowledge/policy)
    """
    try:
        kb = _get_kb()

        result = _route_knowledge_query(query, top_k)

        documents: List[Dict[str, Any]] = []

        if result == "knowledge":
            search_result = kb.knowledge_search(query)
            if isinstance(search_result, str):
                documents = [
                    {"content": search_result, "source": "knowledge", "score": 1.0}
                ]
            elif isinstance(search_result, list):
                documents = [
                    {
                        "content": doc.get("content", ""),
                        "source": "knowledge",
                        "score": doc.get("score", 1.0),
                    }
                    for doc in search_result[:top_k]
                ]
            else:
                documents = [
                    {"content": str(search_result), "source": "knowledge", "score": 1.0}
                ]

        elif result == "policy":
            meta_extractor = MetadataQueryHelper()
            filters = meta_extractor.extract_metadata_filter(query)
            search_result = kb.policy_search(query, filters=filters)  # type: ignore[attr-defined]
            if isinstance(search_result, str):
                documents = [
                    {"content": search_result, "source": "policy", "score": 1.0}
                ]
            elif isinstance(search_result, list):
                documents = [
                    {
                        "content": doc.get("content", ""),
                        "source": "policy",
                        "score": doc.get("score", 1.0),
                    }
                    for doc in search_result[:top_k]
                ]
            else:
                documents = [
                    {"content": str(search_result), "source": "policy", "score": 1.0}
                ]

        return {
            "success": True,
            "documents": documents,
            "query": query,
            "route": result,
        }

    except Exception as e:
        logger.error(f"知识检索失败: {e}")
        return {
            "success": False,
            "documents": [],
            "query": query,
            "route": "unknown",
        }


@tool(  # pyright: ignore[reportCallIssue,reportArgumentType]
    "text2sql",
    args_schema=TextToSQLArgs,
    description="将中文自然语言转换为可执行的 SQL 查询.支持 doris 数据库,支持空气质量查询或者或者地理行政区划查询等场景.当问题涉及到空气质量或者地理行政区划时,请使用 text2sql 工具.示例：'昨日平顶山市空气质量情况?'",
)
def text2sql_tool(question: str) -> str:
    """将自然语言转换为 SQL 并执行查询.

    参数:
        question: 需要转换为 SQL 的自然语言问题。

    返回:
        查询结果字符串。
    """
    engine = _get_text2sql_engine()
    result = engine.ask(
        question,
        print_results=False,
        visualize=True,
        auto_train=False,
        allow_llm_to_see_data=True,
    )
    return str(result) if result is not None else "无查询结果"


# =========================================================================
# 导出
# =========================================================================
__all__ = [
    "websearch_tool",
    "parse_region_tool",
    "get_beijing_time",
    "knowledge_retriever_tool",
    "text2sql_tool",
    "clear_knowledge_route_cache",
]
