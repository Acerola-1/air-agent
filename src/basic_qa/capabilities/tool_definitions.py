"""本图的稳定工具身份与当次模型工具定义记录.

仅解释工具目录和传入材料，不依赖能力投影、执行策略或观测流程。
"""

from __future__ import annotations

from typing import Any


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
