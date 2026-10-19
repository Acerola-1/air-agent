"""业务连接执行层：白名单、真实认证、A/B 隔离、轮换与撤销.

使用受控 fixture 模拟服务（非生产协议证明）。生产连接由
`AGENT_MCP_CONNECTIONS_JSON` 提供共享只读 ipp 连接；本测试重点验证
"业务 + 企业 + 连接"三层解析与发送边界。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_fixture import server as fx  # noqa: E402

from common.business_registry import (  # noqa: E402
    BUSINESS_POLICY_VERSION,
    BusinessConnection,
    BusinessDefinition,
)
from common.identity.runtime import (  # noqa: E402
    RuntimeIdentity,
    RuntimeIdentityRevoked,
)
from common.mcp_authorization import McpAuthorizationError  # noqa: E402
from common.mcp_connections import reset_connections_cache  # noqa: E402
from common.mcp_scoped import (  # noqa: E402
    ScopedMCPToolFactory,
    execute_mcp_tool,
    set_factory,
)

pytestmark = pytest.mark.anyio

SERVER_KEY = "ipp"
BUSINESS = "test-fixture"
PERMISSION = "agent:business:test-fixture:use"
TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"
TOOLS = [
    "helper_get_latest_time",
    "query_air",
    "query",
    "slow_query",
    "flaky_query",
]


@pytest.fixture(autouse=True)
def _business(monkeypatch: pytest.MonkeyPatch) -> None:
    from common import business_registry as registry

    item = BusinessDefinition(
        code=BUSINESS,
        name_zh="受控测试业务",
        permission=PERMISSION,
        graph=BUSINESS,
        connections=(
            BusinessConnection(server_key=SERVER_KEY, allowed_tools=frozenset(TOOLS)),
        ),
    )
    monkeypatch.setitem(registry.BUSINESSES, BUSINESS, item)
    monkeypatch.setitem(registry.BUSINESS_BY_GRAPH, BUSINESS, item)


@pytest.fixture(autouse=True)
def _clear_connections() -> None:
    reset_connections_cache()
    yield
    reset_connections_cache()


@pytest.fixture
async def fixture_server():
    srv = fx.FixtureServer()
    await srv.start()
    await srv.reset()
    yield srv
    await srv.stop()


@pytest.fixture
def factory(fixture_server, monkeypatch):
    fac = ScopedMCPToolFactory({SERVER_KEY: fixture_server.url})
    set_factory(fac)
    monkeypatch.setattr(
        "common.business_graph.tool_wrappers.get_mcp_tool_names",
        lambda: sorted(fac.known_tool_names()),
    )
    yield fac
    set_factory(None)


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tenant_a_credential: str = "env:FIXTURE_TOK_A",
    tenant_b_credential: str = "env:FIXTURE_TOK_B",
    revision: int = 1,
    disable_a: bool = False,
) -> None:
    servers = [
        {
            "server_key": SERVER_KEY,
            "url": "https://fixture.invalid/mcp",
            "scope": "tenant",
            "tenant_id": TENANT_A,
            "auth_mode": "bearer_env",
            "credential_ref": tenant_a_credential,
            "enabled": not disable_a,
            "approved_query_tools": TOOLS,
        },
        {
            "server_key": SERVER_KEY,
            "url": "https://fixture.invalid/mcp",
            "scope": "tenant",
            "tenant_id": TENANT_B,
            "auth_mode": "bearer_env",
            "credential_ref": tenant_b_credential,
            "enabled": True,
            "approved_query_tools": TOOLS,
        },
    ]
    payload = {
        "schema_version": "agent.mcp-connections.v1",
        "revision": revision,
        "servers": servers,
    }
    monkeypatch.setenv("AGENT_MCP_CONNECTIONS_JSON", json.dumps(payload))


def _identity(
    tenant: str = TENANT_A,
    membership: str = "m-a",
    permissions: frozenset[str] | None = None,
    verification_ref: str | None = None,
    session_ctx_version: int | None = 1,
    user_auth_revision: int | None = 1,
    tenant_auth_revision: int | None = 1,
    membership_revision: int | None = 1,
) -> RuntimeIdentity:
    perms = frozenset({PERMISSION}) if permissions is None else permissions
    return RuntimeIdentity(
        user_id="u-1",
        tenant_id=tenant,
        membership_id=membership,
        session_id="s-1",
        permissions=perms,
        business_code=BUSINESS,
        business_policy_version=BUSINESS_POLICY_VERSION,
        business_permission=PERMISSION,
        business_graph=BUSINESS,
        verification_ref=verification_ref,
        session_ctx_version=session_ctx_version,
        user_auth_revision=user_auth_revision,
        tenant_auth_revision=tenant_auth_revision,
        membership_revision=membership_revision,
    )


def _run_config(identity: RuntimeIdentity) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": "th",
            "ledger_business_code": identity.business_code,
            "ledger_business_policy_version": identity.business_policy_version,
            "ledger_business_permission": identity.business_permission,
            "ledger_graph_id": identity.business_graph,
            "langgraph_auth_user": {
                "identity": identity.user_id,
                "tenant_id": identity.tenant_id,
                "membership_id": identity.membership_id,
                "session_id": identity.session_id,
                "permissions": sorted(identity.permissions),
                "verification_ref": identity.verification_ref,
                "session_ctx_version": identity.session_ctx_version,
                "user_auth_revision": identity.user_auth_revision,
                "tenant_auth_revision": identity.tenant_auth_revision,
                "membership_revision": identity.membership_revision,
            },
        }
    }


async def test_missing_connection_rejected_without_network(
    factory, fixture_server, monkeypatch
) -> None:
    _configure(monkeypatch, disable_a=True)
    with pytest.raises(McpAuthorizationError):
        await execute_mcp_tool(
            SERVER_KEY,
            "query_air",
            {"user_id": "u", "region": "x"},
            _run_config(_identity()),
        )
    assert await fixture_server.call_count("subject-a") == 0


async def test_missing_credential_rejected_without_anonymous_fallback(
    factory, fixture_server, monkeypatch
) -> None:
    monkeypatch.delenv("FIXTURE_TOK_MISSING", raising=False)
    _configure(monkeypatch, tenant_a_credential="env:FIXTURE_TOK_MISSING")
    with pytest.raises(McpAuthorizationError) as err:
        await execute_mcp_tool(
            SERVER_KEY,
            "query_air",
            {"user_id": "u", "region": "x"},
            _run_config(_identity()),
        )
    assert "凭据" in str(err.value)
    assert await fixture_server.call_count("subject-a") == 0


async def test_unlisted_business_tool_rejected(
    factory, fixture_server, monkeypatch
) -> None:
    _configure(monkeypatch)
    with pytest.raises(McpAuthorizationError):
        await execute_mcp_tool(SERVER_KEY, "evil_tool", {}, _run_config(_identity()))
    assert await fixture_server.call_count("subject-a") == 0


async def test_real_bearer_auth_and_no_user_id_spoofing(
    factory, fixture_server, monkeypatch
) -> None:
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    _configure(monkeypatch)
    result = await execute_mcp_tool(
        SERVER_KEY,
        "query_air",
        {"user_id": "model-supplied", "region": "杭州市"},
        _run_config(_identity()),
    )
    payload = json.loads(result)
    # 真实认证主体来自 Bearer；共享/企业连接都不伪造 user_id 参数。
    assert payload["auth_subject"] == "subject-a"
    assert payload["received_user_id"] == "model-supplied"
    assert payload["data"]["region"] == "杭州市"


async def test_ab_cache_and_credentials_are_isolated(
    factory, fixture_server, monkeypatch
) -> None:
    """R1: 真实并发且有确定请求屏障，A慢B先完成，核对认证主体、返回数据、计数与配置隔离."""
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    monkeypatch.setenv("FIXTURE_TOK_B", "tok-tenant-b")
    _configure(monkeypatch)

    # 1. 正对照：单次调用各自能正常通过，主体和返回数据正确
    single_a = json.loads(
        await execute_mcp_tool(
            SERVER_KEY,
            "slow_query",
            {"user_id": "u-pos", "region": "杭州市", "delay_ms": 0, "barrier": False},
            _run_config(_identity(tenant=TENANT_A, membership="m-a")),
        )
    )
    assert single_a["auth_subject"] == "subject-a"
    assert single_a["data"]["city"] == "杭州市"
    assert await fixture_server.call_count("subject-a") == 1
    await fixture_server.reset()

    # 2. 真实并发 + 确定性请求屏障（非仅靠 sleep）：
    # A 先发起并进入 HTTP 请求屏障挂起；B 后发起但在 A 仍在请求阶段时先完成；最后释放 A
    await fixture_server.arm_barrier()
    completion_order: list[str] = []

    async def call_a() -> dict[str, Any]:
        ident = _identity(tenant=TENANT_A, membership="m-a")
        res = await execute_mcp_tool(
            SERVER_KEY,
            "slow_query",
            {"user_id": "u-a", "region": "杭州市", "delay_ms": 0, "barrier": True},
            _run_config(ident),
        )
        completion_order.append("A")
        return json.loads(res)

    async def call_b() -> dict[str, Any]:
        ident = _identity(tenant=TENANT_B, membership="m-b")
        res = await execute_mcp_tool(
            SERVER_KEY,
            "slow_query",
            {"user_id": "u-b", "region": "北京市", "delay_ms": 0, "barrier": False},
            _run_config(ident),
        )
        completion_order.append("B")
        return json.loads(res)

    task_a = asyncio.create_task(call_a())
    # 确定性请求屏障：等待 A 的真实 HTTP 请求到达服务端并被屏障挂起
    await fixture_server.wait_barrier_arrived()
    assert task_a.done() is False

    # A 仍在服务端挂起，此时启动 B，B 先完成
    b = await call_b()
    assert b["auth_subject"] == "subject-b"
    assert b["data"]["city"] == "北京市"
    assert completion_order == ["B"]
    assert task_a.done() is False

    # 释放屏障，A 恢复并完成
    await fixture_server.release_barrier()
    a = await task_a
    assert a["auth_subject"] == "subject-a"
    assert a["data"]["city"] == "杭州市"
    assert completion_order == ["B", "A"]

    # 核对实际 HTTP 认证主体、返回数据、请求计数与缓存隔离
    assert await fixture_server.call_count("subject-a") == 1
    assert await fixture_server.call_count("subject-b") == 1
    scopes_a = factory.authorized_servers_for_scope(TENANT_A, BUSINESS)
    scopes_b = factory.authorized_servers_for_scope(TENANT_B, BUSINESS)
    assert scopes_a == {SERVER_KEY}
    assert scopes_b == {SERVER_KEY}


async def test_handshake_succeeds_then_revocation_blocks_tool_call_without_network(
    factory, fixture_server, monkeypatch
) -> None:
    """R2: 握手 initialize 成功后撤销业务资格、tools/call 发送前真实拦截，真实 HTTP 计数增量为 0."""
    # 模拟合成远程 IAM 授权后端环境（非生产真实凭据）
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "remote")
    monkeypatch.setenv("IAM_BASE_URL", f"http://127.0.0.1:{fixture_server.port}")
    monkeypatch.setenv("IAM_ISSUER", "synthetic-iam")
    monkeypatch.setenv("IAM_INTERNAL_CLIENT_ID", "fixture")
    monkeypatch.setenv("IAM_INTERNAL_CLIENT_SECRET", "fixture")
    monkeypatch.setenv("IAM_RUNTIME_AUDIENCE", "langgraph-runtime")
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setenv("TENANT_BUSINESS_SNAPSHOT_ENFORCED", "true")
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    _configure(monkeypatch)

    ident_valid = _identity(tenant=TENANT_A, verification_ref="ref-valid-1")

    # 1. 握手阶段：使用合法凭据与身份完成 initialize / tools/list 发现
    tools = await factory.tools_for(ident_valid, SERVER_KEY)
    assert any(t.name == "query_air" for t in tools)
    # 发现阶段握手计入已认证请求，但工具调用计数为 0
    assert await fixture_server.call_count("subject-a") == 0

    # 2. 正对照：在有效身份下调用工具，真实 HTTP 请求发出且服务端调用计数为 1
    pos_res = json.loads(
        await execute_mcp_tool(
            SERVER_KEY,
            "query_air",
            {"user_id": "u-pos", "region": "杭州市"},
            _run_config(ident_valid),
        )
    )
    assert pos_res["auth_subject"] == "subject-a"
    assert pos_res["data"]["city"] == "杭州市"
    assert await fixture_server.call_count("subject-a") == 1

    # 3. 动态撤权场景 A：握手已完成（缓存连接存活），撤销业务使用权限码（精确业务权限撤销）
    await fixture_server.reset()
    assert await fixture_server.call_count("subject-a") == 0
    protocol_before = await fixture_server.protocol_counts()
    ident_perm_revoked = _identity(
        tenant=TENANT_A,
        permissions=frozenset(),  # 撤销 agent:business:test-fixture:use
        verification_ref="ref-perm-revoked",
    )

    def _assert_revoked(exc_info: pytest.ExceptionInfo[Any]) -> None:
        val = exc_info.value
        msg = str(val)
        sub_msgs = [str(e) for e in getattr(val, "exceptions", [])]
        combined = msg + " " + " ".join(sub_msgs)
        assert any(
            k in combined for k in ("撤销", "未通过", "拒绝", "RuntimeIdentityRevoked")
        )

    with pytest.raises(
        (RuntimeIdentityRevoked, McpAuthorizationError, ExceptionGroup)
    ) as err_a:
        await execute_mcp_tool(
            SERVER_KEY,
            "query_air",
            {"user_id": "u-revoked", "region": "杭州市"},
            _run_config(ident_perm_revoked),
        )
    _assert_revoked(err_a)
    # 核心安全断言：MCPOutboundAuth 发送前拦截，实际网络发送严格为 0
    assert await fixture_server.call_count("subject-a") == 0
    assert await fixture_server.protocol_counts() == protocol_before

    # 4. 动态撤权场景 B：握手已完成，合成 IAM 后端在执行引用复查时返回 active=false / 上下文已撤销
    await fixture_server.set_iam_state(active=False)
    ident_iam_revoked = _identity(tenant=TENANT_A, verification_ref="ref-iam-revoked")
    with pytest.raises(
        (RuntimeIdentityRevoked, McpAuthorizationError, ExceptionGroup)
    ) as err_b:
        await execute_mcp_tool(
            SERVER_KEY,
            "query_air",
            {"user_id": "u-revoked", "region": "杭州市"},
            _run_config(ident_iam_revoked),
        )
    _assert_revoked(err_b)
    # 核心安全断言：覆盖真实 MCPOutboundAuth 与 SDK 执行层，撤权后真实网络发送严格为 0
    assert await fixture_server.call_count("subject-a") == 0
    assert await fixture_server.protocol_counts() == protocol_before


async def test_retry_after_revocation_not_sent(
    factory, fixture_server, monkeypatch
) -> None:
    """R2: 第一次真实 HTTP 失败后、重试前撤权阻断新网络发送，服务端计数增量为 0."""
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "remote")
    monkeypatch.setenv("IAM_BASE_URL", f"http://127.0.0.1:{fixture_server.port}")
    monkeypatch.setenv("IAM_ISSUER", "synthetic-iam")
    monkeypatch.setenv("IAM_INTERNAL_CLIENT_ID", "fixture")
    monkeypatch.setenv("IAM_INTERNAL_CLIENT_SECRET", "fixture")
    monkeypatch.setenv("IAM_RUNTIME_AUDIENCE", "langgraph-runtime")
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setenv("TENANT_BUSINESS_SNAPSHOT_ENFORCED", "true")
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    _configure(monkeypatch)

    ident_valid = _identity(tenant=TENANT_A, verification_ref="ref-retry-1")
    cfg_valid = _run_config(ident_valid)

    # 预热发现
    await factory.tools_for(ident_valid, SERVER_KEY)
    before = await fixture_server.call_count("subject-a")

    # 1. 工具错误是 MCP 业务层失败，不把载荷里的 503 冒充 HTTP 状态。
    await fixture_server.set_flaky(1)
    first_res = await execute_mcp_tool(
        SERVER_KEY, "flaky_query", {"user_id": "u-1", "region": "杭州市"}, cfg_valid
    )
    # 验证首次 HTTP 调用真实发出并计入服务端，且结果为错误标记
    assert await fixture_server.call_count("subject-a") == before + 1
    assert getattr(first_res, "is_error", False) is True
    assert "503" in str(first_res)

    # 2. 模拟撤销发生在首次 HTTP 失败与重试之间：IAM 撤销主体或成员身份
    await fixture_server.set_iam_state(active=False)
    with pytest.raises((RuntimeIdentityRevoked, McpAuthorizationError, ExceptionGroup)):
        await execute_mcp_tool(
            SERVER_KEY, "flaky_query", {"user_id": "u-1", "region": "杭州市"}, cfg_valid
        )
    # 核心安全断言：重试被执行层/MCPOutboundAuth 拦截，服务端计数严格不再增长（新增为 0）
    assert await fixture_server.call_count("subject-a") == before + 1

    # 3. 正对照：恢复授权后重试，flaky 计数已耗尽，第二次调用正常成功并发出网络请求
    await fixture_server.set_iam_state(active=True)
    retry_res = json.loads(
        await execute_mcp_tool(
            SERVER_KEY, "flaky_query", {"user_id": "u-1", "region": "杭州市"}, cfg_valid
        )
    )
    assert retry_res["ok"] is True
    assert retry_res["auth_subject"] == "subject-a"
    assert await fixture_server.call_count("subject-a") == before + 2

    # 4. 真实传输503：服务端在返回失败时撤销IAM，SDK或调用方的后续重试
    # 必须在发送前被阻断，包括initialize等握手请求，不能只数tools/call。
    await fixture_server.fail_next_http_call(revoke=True)
    with pytest.raises(Exception):
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"user_id": "u-1", "region": "杭州市"}, cfg_valid
        )
    counts_after_failure = await fixture_server.protocol_counts()
    assert counts_after_failure["failed_503"] == 1
    assert await fixture_server.call_count("subject-a") == before + 2
    with pytest.raises((RuntimeIdentityRevoked, McpAuthorizationError, ExceptionGroup)):
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"user_id": "u-1", "region": "杭州市"}, cfg_valid
        )
    assert await fixture_server.protocol_counts() == counts_after_failure
    await fixture_server.set_iam_state(active=True)
    recovered = json.loads(
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"user_id": "u-1", "region": "杭州市"}, cfg_valid
        )
    )
    assert recovered["auth_subject"] == "subject-a"
    assert await fixture_server.call_count("subject-a") == before + 3


async def test_connection_rotation_rediscovers_and_uses_new_credential(
    factory, fixture_server, monkeypatch
) -> None:
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    monkeypatch.setenv("FIXTURE_TOK_AR", "tok-rotated-a")
    _configure(monkeypatch)
    first = json.loads(
        await execute_mcp_tool(
            SERVER_KEY,
            "query_air",
            {"user_id": "u", "region": "x"},
            _run_config(_identity()),
        )
    )
    assert first["auth_subject"] == "subject-a"
    _configure(monkeypatch, tenant_a_credential="env:FIXTURE_TOK_AR", revision=2)
    second = json.loads(
        await execute_mcp_tool(
            SERVER_KEY,
            "query_air",
            {"user_id": "u", "region": "x"},
            _run_config(_identity()),
        )
    )
    assert second["auth_subject"] == "subject-a-rotated"


async def test_discovery_deduplicates_per_connection_scope(
    factory, fixture_server, monkeypatch
) -> None:
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    _configure(monkeypatch)
    calls = {"n": 0}
    original = factory._discover

    async def _counting(resolved):
        calls["n"] += 1
        return await original(resolved)

    monkeypatch.setattr(factory, "_discover", _counting)
    identity = _identity()
    await execute_mcp_tool(
        SERVER_KEY, "query_air", {"user_id": "u", "region": "x"}, _run_config(identity)
    )
    await execute_mcp_tool(
        SERVER_KEY, "query_air", {"user_id": "u", "region": "y"}, _run_config(identity)
    )
    assert calls["n"] == 1


async def test_request_helper_degrades_readably(factory, monkeypatch) -> None:
    from langchain_core.messages import ToolMessage

    from common.business_graph.tool_wrappers import execute_mcp_tool_request

    _configure(monkeypatch, disable_a=True)
    request = type(
        "Req",
        (),
        {
            "tool_call": {"id": "tc-9", "name": "query_air", "args": {"region": "x"}},
            "runtime": type("RT", (), {"config": _run_config(_identity())})(),
        },
    )()
    message = await execute_mcp_tool_request(request)  # type: ignore[arg-type]
    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    body = json.loads(message.content)
    assert body["code"] == 403
    assert "tok-" not in message.content


async def test_user_empty_mcp_set_blocks_before_network(
    factory, fixture_server, monkeypatch
) -> None:
    """用户显式空 MCP 集合仍禁止外呼；取消逐项 RBAC 不等于取消用户选择。"""
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    _configure(monkeypatch)
    config = _run_config(_identity())
    config["configurable"]["capability_mcp_mode"] = "allowlist"
    config["configurable"]["capability_tool_ids"] = []
    with pytest.raises(McpAuthorizationError):
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"user_id": "u", "region": "x"}, config
        )
    assert await fixture_server.call_count("subject-a") == 0
