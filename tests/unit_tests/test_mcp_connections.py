"""受保护 MCP 连接配置：严格校验、作用域解析与失败关闭."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from common.mcp_connections import (  # noqa: E402
    MCPConnectionConfigError,
    connection_for,
    load_connections,
    parse_connections,
    reset_connections_cache,
)

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"


def _payload(*servers: dict) -> dict:
    return {
        "schema_version": "agent.mcp-connections.v1",
        "revision": 1,
        "servers": list(servers),
    }


def _shared(**overrides) -> dict:
    item = {
        "server_key": "ipp",
        "url": "https://example.test/mcp",
        "scope": "shared_readonly",
        "auth_mode": "none",
        "enabled": True,
        "approved_query_tools": ["query"],
    }
    item.update(overrides)
    return item


def _tenant(**overrides) -> dict:
    item = {
        "server_key": "tenant-svc",
        "url": "https://example.test/mcp",
        "scope": "tenant",
        "tenant_id": TENANT_A,
        "auth_mode": "bearer_env",
        "credential_ref": "env:TENANT_TOKEN",
        "enabled": True,
        "approved_query_tools": ["query"],
    }
    item.update(overrides)
    return item


def test_valid_shared_and_tenant_connections_resolve_by_tenant() -> None:
    config = parse_connections(_payload(_shared(), _tenant()))
    assert config.revision == 1
    shared = config.resolve("ipp", TENANT_A)
    assert shared is not None and shared.is_shared_readonly
    tenant = config.resolve("tenant-svc", TENANT_A)
    assert tenant is not None and tenant.tenant_id == TENANT_A
    assert config.resolve("tenant-svc", TENANT_B) is None
    assert [item.server_key for item in config.available_for_tenant(TENANT_B)] == [
        "ipp"
    ]


def test_invalid_url_and_browser_style_values_rejected() -> None:
    for bad in (
        "ftp://example.test/mcp",
        "https://u:p@example.test/mcp",
        "https://example.test/mcp?token=x",
    ):
        with pytest.raises(MCPConnectionConfigError):
            parse_connections(_payload(_shared(url=bad)))


def test_tenant_requires_uuid_and_real_auth() -> None:
    with pytest.raises(MCPConnectionConfigError):
        parse_connections(_payload(_tenant(tenant_id="t-a")))
    with pytest.raises(MCPConnectionConfigError):
        parse_connections(_payload(_tenant(auth_mode="none", credential_ref=None)))
    with pytest.raises(MCPConnectionConfigError):
        parse_connections(_payload(_tenant(credential_ref="plain-token")))


def test_shared_and_tenant_same_key_is_ambiguous() -> None:
    with pytest.raises(MCPConnectionConfigError):
        parse_connections(_payload(_shared(), _tenant(server_key="ipp")))


def test_duplicate_and_unknown_fields_rejected() -> None:
    with pytest.raises(MCPConnectionConfigError):
        parse_connections(_payload(_shared(), _shared()))
    with pytest.raises(MCPConnectionConfigError):
        parse_connections(_payload(_shared(browser_url="https://evil.test")))
    with pytest.raises(MCPConnectionConfigError):
        parse_connections({"schema_version": "agent.mcp-connections.v1", "revision": 1})


def test_disabled_connection_is_not_resolved() -> None:
    config = parse_connections(
        _payload(_shared(enabled=False, approved_query_tools=[]))
    )
    assert config.resolve("ipp", TENANT_A) is None


def test_env_json_loading_and_change_takes_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_connections_cache()
    first = _payload(_shared(approved_query_tools=["query"]))
    monkeypatch.setenv("AGENT_MCP_CONNECTIONS_JSON", json.dumps(first))
    config = load_connections()
    assert config.resolve("ipp", TENANT_A).allows_tool("query")
    # 同一内容命中缓存
    assert load_connections() is config
    second = _payload(_shared(approved_query_tools=["query", "query2"]))
    second["revision"] = 2
    monkeypatch.setenv("AGENT_MCP_CONNECTIONS_JSON", json.dumps(second))
    changed = load_connections()
    assert changed.revision == 2
    assert connection_for("ipp", TENANT_A).allows_tool("query2")
    reset_connections_cache()


def test_both_sources_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_connections_cache()
    monkeypatch.setenv("AGENT_MCP_CONNECTIONS_JSON", json.dumps(_payload(_shared())))
    monkeypatch.setenv("AGENT_MCP_CONNECTIONS_FILE", "/tmp/connections.json")
    with pytest.raises(MCPConnectionConfigError):
        load_connections()
    reset_connections_cache()
