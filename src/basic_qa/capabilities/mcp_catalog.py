"""Scoped MCP metadata and schema versions; no whole catalog enters the model."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from common.tool_execution.schema import content_version, schema_version_for
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool


@dataclass(frozen=True)
class ToolDocument:
    """One program-side, stable service-qualified tool definition."""

    tool_id: str
    name: str
    description: str
    schema: dict[str, Any]
    schema_version: str

    @property
    def subject(self) -> str | None:
        """Read an object facet from public naming, without creating an ACL."""
        parts = self.name.lower().split("_")
        if (
            len(parts) > 1
            and parts[0] == "mcp"
            and parts[1] in {"city", "station", "grid", "weather"}
        ):
            return parts[1]
        words = set(re.sub(r"([a-z])([A-Z])", r"\1_\2", self.name).lower().split("_"))
        subjects = words & {"city", "station", "grid", "weather"}
        return next(iter(subjects)) if len(subjects) == 1 else None

    @property
    def purpose(self) -> str:
        """Exclude response-field tables from the retrieval purpose summary."""
        purpose = self.description.split("【返回字段", 1)[0]
        return " ".join(
            line for line in purpose.splitlines() if not line.lstrip().startswith("|")
        )[:1000]

    @property
    def text(self) -> str:
        """Use purpose and parameter semantics for embedding, without credentials."""
        subject = next(
            (
                label
                for prefix, label in (
                    ("mcp_station_", "站点"),
                    ("mcp_grid_", "网格"),
                    ("mcp_city_", "城市"),
                    ("mcp_weather_", "气象"),
                )
                if self.name.startswith(prefix)
            ),
            "环境监测",
        )
        parameters = []
        for name, value in self.schema.get("properties", {}).items():
            parameters.append(
                {
                    "name": name,
                    "type": value.get("type"),
                    "description": value.get("description", "")[:80],
                    "required": name in self.schema.get("required", []),
                }
            )
        # Uniform purpose-first documents keep large filter lists from drowning
        # the purpose, without a query-specific boost or new permission filter.
        parameter_text = json.dumps(parameters, ensure_ascii=False, sort_keys=True)[
            :500
        ]
        return f"用途：{self.purpose[:500]}\n对象：{subject}\n参数：{parameter_text}\n工具：{self.tool_id}"

    def candidate(self, score: float) -> dict[str, Any]:
        """Disclose a brief candidate, not its complete JSON schema."""
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "description": self.purpose[:500],
            "schema_version": self.schema_version,
            "status": "available",
            "score": score,
        }

    def proxy(self) -> StructuredTool:
        """Build a non-executable schema proxy with a factory-issued provider alias."""
        from common.mcp_scoped import get_factory, server_clean_key

        server = self.tool_id.split(":", 2)[1]
        factory = get_factory()
        # A raw short name is safe only if its current factory mapping is unique.
        name = (
            self.name
            if factory.server_of(self.name) == server
            else f"mcp_{server_clean_key(server)}__{self.name}"
        )
        if factory.server_of(name) != server:
            raise ValueError("ambiguous MCP provider alias")

        async def blocked(**kwargs: Any) -> Any:
            raise RuntimeError("MCP tools execute only through the protected factory")

        return StructuredTool.from_function(
            coroutine=blocked,
            name=name,
            description=self.description or self.name,
            args_schema=self.schema,
            metadata={"server_key": server, "stable_id": self.tool_id, "is_mcp": True},
        )


@dataclass(frozen=True)
class ToolCatalog:
    """Definitions and revision scope from the current protected connection."""

    documents: tuple[ToolDocument, ...]
    scope_key: str
    catalog_version: str
    tenant_id: str
    user_id: str
    membership_id: str
    available: bool = True


def document_for(server_key: str, tool: Any) -> ToolDocument:
    """Version the exact tool description and parameter schema."""
    function = convert_to_openai_tool(tool)["function"]
    name = function["name"]
    description = function.get("description") or name
    schema = function.get("parameters") or {"type": "object", "properties": {}}
    return ToolDocument(
        f"mcp:{server_key}:{name}",
        name,
        description,
        schema,
        schema_version_for(server_key, tool),
    )


async def resolve_tool_catalog(config: dict[str, Any]) -> ToolCatalog:
    """Filter by business, connection and accepted selection before ranking/loading."""
    from basic_qa.capabilities.runtime import BasicQARuntimeError
    from basic_qa.capabilities.skill_search import _scoped_catalog
    from common.business_registry import BUSINESS_POLICY_VERSION, business_for_code
    from common.capabilities import discover_authorized_mcp_catalog
    from common.identity.runtime import guard_runtime_call
    from common.mcp_connections import load_connections
    from common.mcp_scoped import get_factory

    identity = await guard_runtime_call(config)
    _scoped_catalog(config, identity)
    cfg = config.get("configurable") or {}
    mode = cfg.get("capability_mcp_mode")
    if mode not in {"auto", "allowlist"}:
        raise BasicQARuntimeError("缺少可信工具选择")
    definition = business_for_code("basic-qa")
    connections = load_connections()
    revisions = []
    documents = {}
    available = True
    if mode == "allowlist" and not cfg.get("capability_tool_ids"):
        services, discovered = [], []
    else:
        services, discovered = await discover_authorized_mcp_catalog(
            identity, business_code="basic-qa"
        )
        available = all(s.status == "available" for s in services) and bool(services)
    allowed = {
        t.id
        for t in discovered
        if t.status == "available"
        and (mode == "auto" or t.id in cfg.get("capability_tool_ids", []))
    }
    if cfg.get("capability_skill_mode") == "explicit":
        # Natural-language Skill choice does not set this accepted API field.
        selected = _scoped_catalog(config, identity)
        allowed &= set(selected[0].required_tool_ids)
    for scope in definition.connections:
        connection = connections.resolve(scope.server_key, identity.tenant_id)
        if connection is None:
            continue
        revisions.append(
            [
                scope.server_key,
                connection.revision,
                connection.config_revision,
                connection.credential_digest,
            ]
        )
        if not any(t.startswith(f"mcp:{scope.server_key}:") for t in allowed):
            continue
        # Read schema from this scope's discovered tools, not the global schema
        # union: another tenant may use a different definition for the same ID.
        for tool in await get_factory().tools_for(identity, scope.server_key):
            tool_id = f"mcp:{scope.server_key}:{tool.name}"
            if (
                tool_id in allowed
                and scope.allows(tool.name)
                and connection.allows_tool(tool.name)
            ):
                documents[tool_id] = document_for(scope.server_key, tool)
    after = await guard_runtime_call(config)
    _scoped_catalog(config, after)
    if (
        after.tenant_id != identity.tenant_id
        or after.user_id != identity.user_id
        or load_connections() != connections
    ):
        raise BasicQARuntimeError("发现期间连接或身份范围变化")
    scope_key = content_version(
        {
            "tenant": identity.tenant_id,
            "business": "basic-qa",
            "policy": BUSINESS_POLICY_VERSION,
            "connections": sorted(revisions),
        }
    )
    docs = tuple(documents[k] for k in sorted(documents))
    version = content_version(
        {"scope": scope_key, "documents": [(d.tool_id, d.schema_version) for d in docs]}
    )
    return ToolCatalog(
        docs,
        scope_key,
        version,
        identity.tenant_id,
        identity.user_id,
        identity.membership_id,
        available,
    )
