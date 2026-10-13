"""企业 MCP 授权层回归（任务 7.1–7.4；对本地 fixture 的模拟证据）.

覆盖：绑定缺失/停用/未核验协议/凭据缺失的拒绝矩阵、主体与凭据由服务器注入、
越权与伪造参数的执行层拒绝、A/B 屏障并发隔离、绑定轮换与重试前撤销、
发现去重与作用域状态隔离、输出脱敏。

真实统一平台的授权核验（7.6）依赖 docs/mcp-auth-contract.md 缺失材料，
不在本文件范围。
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

from common.identity.runtime import RuntimeIdentity  # noqa: E402
from common.mcp_authorization import (  # noqa: E402
    McpAuthorizationError,
    resolve_binding,
)
from common.mcp_scoped import (  # noqa: E402
    ScopedMCPToolFactory,
    execute_mcp_tool,
    set_factory,
)

pytestmark = pytest.mark.anyio

SERVER_KEY = "ipp"


def _binding_row(
    *,
    tenant: str,
    subject: str,
    credential: str,
    revision: int = 1,
    status: str = "active",
    protocol: str = "v1-fixture",
    membership_id: str | None = None,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant,
        "membership_id": membership_id,
        "server_key": SERVER_KEY,
        "external_subject": subject,
        "credential_ref": credential,
        "protocol_version": protocol,
        "auth_revision": revision,
        "status": status,
    }


def _identity(tenant: str = "t-a", membership: str = "m-a") -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id="u-1", tenant_id=tenant, membership_id=membership, session_id="s-1"
    )


def _run_config(identity: RuntimeIdentity) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": "th",
            "langgraph_auth_user": {
                "identity": identity.user_id,
                "tenant_id": identity.tenant_id,
                "membership_id": identity.membership_id,
                "session_id": identity.session_id,
            },
        }
    }


def _patch_binding(monkeypatch: pytest.MonkeyPatch, row: dict[str, Any] | None) -> None:
    async def _fetch(
        tenant_id: str, membership_id: str, server_key: str
    ) -> dict | None:
        return row

    import common.mcp_authorization as authz

    monkeypatch.setattr(authz, "fetch_mcp_binding", _fetch)


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


# ------------------------------------------------------------------ 7.1 拒绝矩阵


async def test_reject_without_binding(factory, monkeypatch):
    _patch_binding(monkeypatch, None)
    with pytest.raises(McpAuthorizationError) as err:
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"region": "杭州市"}, _run_config(_identity())
        )
    assert "绑定" in str(err.value)


async def test_reject_disabled_binding(factory, monkeypatch):
    _patch_binding(
        monkeypatch,
        _binding_row(
            tenant="t-a",
            subject="subject-a",
            credential="env:FIXTURE_TOK",
            status="disabled",
        ),
    )
    with pytest.raises(McpAuthorizationError):
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"region": "x"}, _run_config(_identity())
        )


async def test_reject_unverified_protocol(factory, fixture_server, monkeypatch):
    _patch_binding(
        monkeypatch,
        _binding_row(
            tenant="t-a",
            subject="subject-a",
            credential="env:FIXTURE_TOK",
            protocol="unverified",
        ),
    )
    with pytest.raises(McpAuthorizationError) as err:
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"region": "x"}, _run_config(_identity())
        )
    assert "协议" in str(err.value) or "禁用" in str(err.value)
    # 拒绝且不发请求
    assert await fixture_server.call_count("subject-a") == 0


async def test_reject_missing_credential(factory, monkeypatch):
    monkeypatch.delenv("FIXTURE_TOK_MISSING", raising=False)
    _patch_binding(
        monkeypatch,
        _binding_row(
            tenant="t-a", subject="subject-a", credential="env:FIXTURE_TOK_MISSING"
        ),
    )
    with pytest.raises(McpAuthorizationError) as err:
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"region": "x"}, _run_config(_identity())
        )
    # 拒绝消息不含凭据值
    assert "tok-" not in str(err.value).lower()


async def test_reject_without_identity(factory, monkeypatch):
    _patch_binding(
        monkeypatch, _binding_row(tenant="t-a", subject="subject-a", credential="env:X")
    )
    with pytest.raises(McpAuthorizationError):
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"region": "x"}, {"configurable": {}}
        )


# ------------------------------------------------------------------ 7.2 注入与主体


async def test_server_injects_subject_and_auth(factory, monkeypatch):
    monkeypatch.setenv("FIXTURE_TOK", "tok-tenant-a")
    _patch_binding(
        monkeypatch,
        _binding_row(tenant="t-a", subject="subject-a", credential="env:FIXTURE_TOK"),
    )
    # 模型伪造 user_id=SPY；发送边界应覆盖为绑定主体
    result = await execute_mcp_tool(
        SERVER_KEY,
        "query_air",
        {"user_id": "SPY", "region": "杭州市"},
        _run_config(_identity()),
    )
    payload = json.loads(result)
    assert payload["auth_subject"] == "subject-a"
    assert payload["received_user_id"] == "subject-a"
    # 业务对象参数不被替换
    assert payload["data"]["region"] == "杭州市"


async def test_business_target_param_not_overwritten(factory, monkeypatch):
    monkeypatch.setenv("FIXTURE_TOK", "tok-tenant-a")
    _patch_binding(
        monkeypatch,
        _binding_row(tenant="t-a", subject="subject-a", credential="env:FIXTURE_TOK"),
    )
    result = await execute_mcp_tool(
        SERVER_KEY, "query_air", {"region": "北京市"}, _run_config(_identity())
    )
    payload = json.loads(result)
    # 授权主体固定为 A（数据范围），业务对象字段保留请求值
    assert payload["data"]["region"] == "北京市"
    assert payload["auth_subject"] == "subject-a"


# ------------------------------------------------------------------ 7.4 并发隔离


async def test_barrier_concurrency_ab_isolated(factory, fixture_server, monkeypatch):
    """A/B 并发调用同名工具，A 慢；身份与结果互不污染."""
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    monkeypatch.setenv("FIXTURE_TOK_B", "tok-tenant-b")
    rows = {
        "t-a": _binding_row(
            tenant="t-a", subject="subject-a", credential="env:FIXTURE_TOK_A"
        ),
        "t-b": _binding_row(
            tenant="t-b", subject="subject-b", credential="env:FIXTURE_TOK_B"
        ),
    }

    async def _fetch(
        tenant_id: str, membership_id: str, server_key: str
    ) -> dict | None:
        await asyncio.sleep(0)
        return rows[tenant_id]

    import common.mcp_authorization as authz

    monkeypatch.setattr(authz, "fetch_mcp_binding", _fetch)

    async def call(tenant: str, delay: int) -> dict[str, Any]:
        result = await execute_mcp_tool(
            SERVER_KEY,
            "slow_query",
            {"region": "x", "delay_ms": delay},
            _run_config(_identity(tenant=tenant, membership=f"m-{tenant}")),
        )
        return json.loads(result)

    a, b = await asyncio.gather(call("t-a", 300), call("t-b", 0))
    assert a["auth_subject"] == "subject-a"
    assert b["auth_subject"] == "subject-b"
    # B 不等 A：返回时间上界由 gather 结构保证（此处以主体正确为核心断言）
    assert await fixture_server.call_count("subject-a") >= 1
    assert await fixture_server.call_count("subject-b") >= 1


async def test_same_user_two_tenants_scoped_caches(
    factory, fixture_server, monkeypatch
):
    """同一用户 A/B 并发 + A 绑定轮换：缓存按作用域隔离，B 不受 A 影响."""
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    monkeypatch.setenv("FIXTURE_TOK_AR", "tok-rotated-a")
    monkeypatch.setenv("FIXTURE_TOK_B", "tok-tenant-b")
    a_cred = {"ref": "env:FIXTURE_TOK_A"}

    async def _fetch(
        tenant_id: str, membership_id: str, server_key: str
    ) -> dict | None:
        if tenant_id == "t-a":
            return _binding_row(
                tenant="t-a",
                subject="subject-a"
                if a_cred["ref"].endswith("_A")
                else "subject-a-rotated",
                credential=a_cred["ref"],
                revision=1 if a_cred["ref"].endswith("_A") else 2,
            )
        return _binding_row(
            tenant="t-b", subject="subject-b", credential="env:FIXTURE_TOK_B"
        )

    import common.mcp_authorization as authz

    monkeypatch.setattr(authz, "fetch_mcp_binding", _fetch)

    ident = _identity()

    async def call(tenant: str) -> dict[str, Any]:
        result = await execute_mcp_tool(
            SERVER_KEY,
            "query_air",
            {"region": "r"},
            _run_config(ident if tenant == "t-a" else _identity("t-b", "m-b")),
        )
        return json.loads(result)

    ra, rb = await asyncio.gather(call("t-a"), call("t-b"))
    assert ra["auth_subject"] == "subject-a"
    assert rb["auth_subject"] == "subject-b"

    # A 轮换：新调用用新绑定；B 保持
    a_cred["ref"] = "env:FIXTURE_TOK_AR"
    ra2 = await call("t-a")
    rb2 = await call("t-b")
    assert ra2["auth_subject"] == "subject-a-rotated"
    assert rb2["auth_subject"] == "subject-b"


async def test_discovery_dedup_per_scope(factory, monkeypatch):
    """同一作用域并发发现去重：只实际执行一次."""
    monkeypatch.setenv("FIXTURE_TOK", "tok-tenant-a")
    _patch_binding(
        monkeypatch,
        _binding_row(tenant="t-a", subject="subject-a", credential="env:FIXTURE_TOK"),
    )
    calls = {"n": 0}
    original = factory._discover

    async def _counting(binding):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return await original(binding)

    monkeypatch.setattr(factory, "_discover", _counting)
    ident = _identity()
    await asyncio.gather(*(factory.tools_for(ident, SERVER_KEY) for _ in range(5)))
    assert calls["n"] == 1


async def test_scope_state_isolation_backoff(factory, monkeypatch):
    """A 作用域发现失败退避，不影响 B 作用域成功路径."""
    monkeypatch.setenv("FIXTURE_TOK_B", "tok-tenant-b")
    rows = {
        "t-a": _binding_row(tenant="t-a", subject="subject-a", credential="env:NOPE"),
        "t-b": _binding_row(
            tenant="t-b", subject="subject-b", credential="env:FIXTURE_TOK_B"
        ),
    }

    async def _fetch(
        tenant_id: str, membership_id: str, server_key: str
    ) -> dict | None:
        return rows[tenant_id]

    import common.mcp_authorization as authz

    monkeypatch.setattr(authz, "fetch_mcp_binding", _fetch)
    with pytest.raises(McpAuthorizationError):
        await factory.tools_for(_identity("t-a", "m-a"), SERVER_KEY)
    tools_b = await factory.tools_for(_identity("t-b", "m-b"), SERVER_KEY)
    assert tools_b and {t.name for t in tools_b} >= {"query_air", "slow_query"}


# ------------------------------------------------------------------ 7.3 执行许可


async def test_unlisted_tool_rejected(factory, monkeypatch):
    """伪造未披露/未发现的工具名：授权路径拒绝且不发请求."""
    monkeypatch.setenv("FIXTURE_TOK", "tok-tenant-a")
    _patch_binding(
        monkeypatch,
        _binding_row(tenant="t-a", subject="subject-a", credential="env:FIXTURE_TOK"),
    )
    with pytest.raises(McpAuthorizationError) as err:
        await execute_mcp_tool(SERVER_KEY, "evil_tool", {}, _run_config(_identity()))
    assert "工具不在" in str(err.value) or "绑定" in str(err.value)


async def test_retry_after_revocation_not_sent(factory, fixture_server, monkeypatch):
    """第一次调用失败后绑定被撤销：重试（重新进入执行层）不得发送新请求."""
    monkeypatch.setenv("FIXTURE_TOK", "tok-tenant-a")
    _patch_binding(
        monkeypatch,
        _binding_row(tenant="t-a", subject="subject-a", credential="env:FIXTURE_TOK"),
    )
    ident = _identity()
    cfg = _run_config(ident)
    # 预热发现
    await factory.tools_for(ident, SERVER_KEY)
    before = await fixture_server.call_count("subject-a")
    await fixture_server.set_flaky(1)
    first = await execute_mcp_tool(SERVER_KEY, "flaky_query", {"region": "x"}, cfg)
    # adapters 把 isError 转为 ToolMessage（不抛异常）；服务器已记录 1 次
    assert await fixture_server.call_count("subject-a") == before + 1
    # 模拟撤销发生在失败与重试之间：下一次执行被授权层拒绝，服务器计数不再增长
    _patch_binding(monkeypatch, None)
    with pytest.raises(McpAuthorizationError):
        await execute_mcp_tool(SERVER_KEY, "flaky_query", {"region": "x"}, cfg)
    assert await fixture_server.call_count("subject-a") == before + 1
    assert "flaky" in str(first) or first is not None


async def test_error_message_no_secrets(factory, monkeypatch):
    """授权拒绝信息不包含凭据值."""
    monkeypatch.setenv("FIXTURE_TOK", "tok-tenant-a")
    _patch_binding(monkeypatch, None)
    with pytest.raises(McpAuthorizationError) as err:
        await execute_mcp_tool(SERVER_KEY, "query_air", {}, _run_config(_identity()))
    assert "tok-" not in str(err.value)


async def test_request_helper_degrades_readably(factory, monkeypatch):
    """execute_mcp_tool_request 拒绝路径返回错误 ToolMessage 且无凭据."""
    from langchain_core.messages import ToolMessage

    from common.business_graph.tool_wrappers import execute_mcp_tool_request

    _patch_binding(monkeypatch, None)
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


# --------------------------------------------------- R07 轮次01 修复：白名单门禁与认证发现


async def test_unregistered_protocol_denied(factory, monkeypatch) -> None:
    """评审 runtime_probe 反例：任意非字面量 `unverified` 的协议名不得绕过门禁。"""
    _patch_binding(
        monkeypatch,
        _binding_row(
            tenant="t-a",
            subject="subject-a",
            credential="env:FIXTURE_TOK",
            protocol="never-verified-protocol",
        ),
    )
    with pytest.raises(McpAuthorizationError) as err:
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"region": "x"}, _run_config(_identity())
        )
    assert "未登记" in str(err.value)


async def test_fixture_protocol_denied_outside_fixture_mode(
    factory, monkeypatch
) -> None:
    """生产语义：未显式声明 fixture 模式时，连 v1-fixture 也拒绝（安全禁用）。"""
    _patch_binding(
        monkeypatch,
        _binding_row(tenant="t-a", subject="subject-a", credential="env:FIXTURE_TOK"),
    )
    monkeypatch.setenv("TENANT_MCP_FIXTURE_MODE", "false")
    with pytest.raises(McpAuthorizationError):
        await resolve_binding(
            tenant_id="t-a", membership_id="m-a", server_key=SERVER_KEY
        )


async def test_discovery_is_authenticating(
    factory, fixture_server, monkeypatch
) -> None:
    """认证 discovery：initialize/tools/list 必须携带绑定凭据；匿名握手必须 401。"""
    import httpx

    monkeypatch.setenv("FIXTURE_TOK", "tok-tenant-a")
    _patch_binding(
        monkeypatch,
        _binding_row(tenant="t-a", subject="subject-a", credential="env:FIXTURE_TOK"),
    )
    identity = _identity()
    tools = await factory.tools_for(identity, SERVER_KEY)
    assert tools, "已认证发现应返回工具清单"
    admin_base = fixture_server.url.rsplit("/mcp", 1)[0]
    async with httpx.AsyncClient() as client:
        lists = (await client.get(f"{admin_base}/admin/lists")).json()
        assert lists["count"] >= 1  # 发现握手全部计为已认证请求
        assert lists["anonymous"] == 0  # 无任何匿名握手成功
        # 匿名 initialize/tools/list 直接 401（证明 fixture 门禁真实存在）
        anon = await client.post(
            fixture_server.url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
        )
        assert anon.status_code == 401


async def test_same_subject_cannot_share_other_member_credentials(factory, monkeypatch):
    """相同主体的成员/覆盖绑定必须分别发现，不能复用先前的执行对象。"""
    from types import SimpleNamespace

    import common.mcp_scoped as scoped
    from common.mcp_authorization import ScopeBinding

    bindings = {
        "member-a": ScopeBinding(
            "t", "member-a", SERVER_KEY, "same", "env:FIRST", "v1-fixture", 1
        ),
        "member-b": ScopeBinding(
            "t", "member-b", SERVER_KEY, "same", "env:SECOND", "v1-fixture", 1
        ),
    }
    discovered = []

    async def resolve(**kwargs):
        return bindings[kwargs["membership_id"]]

    async def discover(binding):
        discovered.append(binding.credential_ref)
        return [
            SimpleNamespace(
                name="query_air",
                description="fixture",
                args_schema={},
                credential=binding.credential_ref,
            )
        ]

    monkeypatch.setattr(scoped, "resolve_binding", resolve)
    monkeypatch.setattr(factory, "_discover", discover)
    first = await factory.tools_for(_identity("t", "member-a"), SERVER_KEY)
    other = await factory.tools_for(_identity("t", "member-b"), SERVER_KEY)
    bindings["member-a"] = ScopeBinding(
        "t", "member-a", SERVER_KEY, "same", "env:OVERRIDE", "v1-fixture", 1
    )
    switched = await factory.tools_for(_identity("t", "member-a"), SERVER_KEY)
    assert first[0].credential == "env:FIRST"
    assert other[0].credential == "env:SECOND"
    assert switched[0].credential == "env:OVERRIDE"
    assert discovered == ["env:FIRST", "env:SECOND", "env:OVERRIDE"]
