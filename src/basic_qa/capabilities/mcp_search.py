"""Model-controlled Top 3 discovery and independent tool activation."""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from basic_qa.capabilities.mcp_catalog import resolve_tool_catalog
from basic_qa.capabilities.mcp_index import search_catalog
from common.identity.business_access import BusinessAccessError
from common.identity.runtime import RuntimeIdentityError


def create_search_mcp_tool(*, timeout: float = 10.0):
    """Create bounded semantic search; never auto-load a candidate."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive")

    @tool("search_mcp")
    async def search_mcp(query: str, config: RunnableConfig) -> dict[str, Any]:
        """仅为Skill未覆盖需求搜索MCP工具，最多Top 3；选择零个或多个再独立加载."""
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            return {"status": "invalid_query", "candidates": []}

        async def search():
            catalog = await resolve_tool_catalog(dict(config or {}))
            if not catalog.documents:
                return {
                    "status": "ok" if catalog.available else "index_unavailable",
                    "catalog_version": catalog.catalog_version,
                    "candidates": [],
                }

            async def verify():
                current = await resolve_tool_catalog(dict(config or {}))
                if (
                    current.scope_key != catalog.scope_key
                    or current.catalog_version != catalog.catalog_version
                ):
                    raise RuntimeError("catalog changed during indexing")

            result = await search_catalog(
                query.strip(), catalog, dict(config or {}), verify=verify
            )
            await verify()
            return result

        try:
            return await asyncio.wait_for(search(), timeout)
        except (RuntimeIdentityError, BusinessAccessError):
            raise
        except TimeoutError:
            return {"status": "search_timeout", "candidates": []}
        except Exception as exc:  # noqa: BLE001 - no full-schema fallback
            from common.skill_router import _propagate_identity_failure

            _propagate_identity_failure(exc)
            return {"status": "index_unavailable", "candidates": []}

    return search_mcp


def create_load_mcp_tool():
    """Activate one current stable tool ID without requiring a parent Skill."""

    @tool("load_mcp_tool")
    async def load_mcp_tool(
        tool_id: str,
        expected_schema_version: str,
        runtime: ToolRuntime,
        config: RunnableConfig,
    ) -> Command:
        """独立加载/激活指定tool_id和schema版本，不执行查询，不创建连接."""
        from basic_qa.capabilities.runtime import (
            BasicQARuntimeError,
            session_state,
            trusted_turn_key,
        )

        state = runtime.state or {}
        try:
            if state.get("bqa_turn_key") != trusted_turn_key(config):
                raise BasicQARuntimeError("运行轮次不匹配")
            caps = await session_state(state, config)
            catalog = await resolve_tool_catalog(dict(config or {}))
            item = next((d for d in catalog.documents if d.tool_id == tool_id), None)
            if item is None or item.schema_version != expected_schema_version:
                raise BasicQARuntimeError("工具ID不存在或schema已变化")
            old = caps["loaded_tools"].get(tool_id)
            same = old and old.get("schema_version") == item.schema_version
            status = (
                "already_active"
                if same and tool_id in caps["independent_active_tool_ids"]
                else "activated"
                if same
                else "loaded"
            )
            caps["loaded_tools"][tool_id] = {
                "schema_version": item.schema_version,
                "load_ref": old["load_ref"] if same else runtime.tool_call_id,
                "last_used": len(state.get("messages") or []),
                "protected_attempt": trusted_turn_key(config),
                "catalog_scope": catalog.scope_key,
            }
            if tool_id not in caps["independent_active_tool_ids"]:
                caps["independent_active_tool_ids"].append(tool_id)
            return Command(
                update={
                    "bqa_capabilities": caps,
                    "messages": [
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "status": status,
                                    "tool_id": tool_id,
                                    "schema_version": item.schema_version,
                                },
                                ensure_ascii=False,
                            ),
                            tool_call_id=runtime.tool_call_id,
                            name="load_mcp_tool",
                        )
                    ],
                }
            )
        except (RuntimeIdentityError, BusinessAccessError):
            raise
        except Exception:  # noqa: BLE001 - a failed target cannot clear others
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "status": "unavailable",
                                    "message": "工具加载失败，请检查稳定ID、schema版本和当前范围。",
                                },
                                ensure_ascii=False,
                            ),
                            tool_call_id=runtime.tool_call_id,
                            name="load_mcp_tool",
                            status="error",
                        )
                    ]
                }
            )

    return load_mcp_tool
