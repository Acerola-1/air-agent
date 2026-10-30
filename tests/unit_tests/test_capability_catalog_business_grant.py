"""能力目录使用实时业务许可与可信身份，不访问真实 MCP 或 IAM。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from common.business_registry import BUSINESS_POLICY_VERSION
from common.capabilities import discover_authorized_mcp_catalog
from common.identity.business_access import BusinessAccessError
from common.identity.runtime import (
    RuntimeIdentity,
    RuntimeIdentityRevoked,
    _revalidate_remote,
)


@pytest.fixture
def catalog_env(monkeypatch):
    import common.identity.runtime as runtime
    import common.mcp_authorization as authorization
    import common.mcp_connections as connections
    import common.mcp_scoped as scoped

    identity = RuntimeIdentity(
        user_id="user",
        tenant_id="tenant",
        membership_id="member",
        session_id="session",
        session_ctx_version=1,
        user_auth_revision=2,
        tenant_auth_revision=3,
        membership_revision=4,
        verification_ref="trusted-reference",
        permissions=frozenset({"agent:business:basic-qa:use"}),
    )
    connection = SimpleNamespace(
        server_key="ipp",
        display_name="IPP",
        enabled=True,
        allows_tool=lambda name: name == "broadcastHour",
    )
    tool = SimpleNamespace(name="broadcastHour", description="query")
    factory = SimpleNamespace(tools_for=AsyncMock(return_value=[tool]))
    recheck = Mock()
    lookup = Mock(
        return_value=SimpleNamespace(available_for_tenant=lambda _: [connection])
    )
    monkeypatch.setattr(runtime, "_enforce_enabled", lambda: True)
    monkeypatch.setattr(runtime, "_business_enforce_enabled", lambda: True)
    monkeypatch.setattr(runtime, "_remote_enabled", lambda: True)
    monkeypatch.setattr(runtime, "_revalidate_remote", recheck)
    monkeypatch.setattr(connections, "load_connections", lookup)
    monkeypatch.setattr(
        authorization.DEFAULT_PROVIDER, "credential_available", lambda _: True
    )
    monkeypatch.setattr(scoped, "get_factory", lambda: factory)
    return identity, factory, recheck, lookup


@pytest.mark.asyncio
async def test_catalog_grant_supplies_full_snapshot_and_rechecks_cached_access(
    catalog_env,
):
    identity, factory, recheck, _ = catalog_env

    for _ in range(2):
        services, tools = await discover_authorized_mcp_catalog(
            identity, business_code="basic-qa"
        )
        assert services[0].status == "available"
        assert [item.id for item in tools] == ["mcp:ipp:broadcastHour"]

    assert recheck.call_count == 2
    used_identity = factory.tools_for.await_args.args[0]
    assert used_identity.verification_ref == identity.verification_ref
    assert used_identity.permissions == identity.permissions
    assert (
        used_identity.business_code,
        used_identity.business_policy_version,
        used_identity.business_permission,
        used_identity.business_graph,
    ) == (
        "basic-qa",
        BUSINESS_POLICY_VERSION,
        "agent:business:basic-qa:use",
        "basic-qa",
    )
    assert identity.business_code is None


@pytest.mark.asyncio
async def test_catalog_denies_missing_business_permission_before_connection_lookup(
    catalog_env,
):
    identity, factory, recheck, lookup = catalog_env
    with pytest.raises(BusinessAccessError):
        await discover_authorized_mcp_catalog(
            replace(identity, permissions=frozenset()), business_code="basic-qa"
        )
    lookup.assert_not_called()
    recheck.assert_not_called()
    factory.tools_for.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_denies_revoked_identity_before_connection_lookup(catalog_env):
    identity, factory, recheck, lookup = catalog_env
    recheck.side_effect = RuntimeIdentityRevoked("revoked")
    with pytest.raises(RuntimeIdentityRevoked):
        await discover_authorized_mcp_catalog(identity, business_code="basic-qa")
    lookup.assert_not_called()
    factory.tools_for.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_denies_missing_execution_reference_before_connection_lookup(
    catalog_env, monkeypatch
):
    import common.identity.runtime as runtime

    identity, factory, _, lookup = catalog_env
    monkeypatch.setattr(runtime, "_revalidate_remote", _revalidate_remote)
    with pytest.raises(RuntimeIdentityRevoked, match="缺少服务端执行引用"):
        await discover_authorized_mcp_catalog(
            replace(identity, verification_ref=None), business_code="basic-qa"
        )
    lookup.assert_not_called()
    factory.tools_for.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["old-policy", None])
async def test_catalog_never_replaces_existing_bad_run_snapshot(catalog_env, policy):
    identity, factory, recheck, lookup = catalog_env
    identity = replace(
        identity,
        business_code="basic-qa",
        business_policy_version=policy,
        business_permission="agent:business:basic-qa:use",
        business_graph="basic-qa",
    )
    with pytest.raises(RuntimeIdentityRevoked):
        await discover_authorized_mcp_catalog(identity, business_code="basic-qa")
    lookup.assert_not_called()
    recheck.assert_not_called()
    factory.tools_for.assert_not_awaited()
