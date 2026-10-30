"""企业运行守卫单元测试（两种图机制、撤销下一调用拒绝、无秘密持久化）."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from common.identity import runtime as rt
from common.identity.db import IdentityBackendUnavailable, IdentityState
from common.identity.runtime import (
    RuntimeIdentityError,
    RuntimeIdentityRevoked,
    guard_runtime_call,
    identity_from_config,
)

pytestmark = pytest.mark.anyio

_IDENTITY = {
    "identity": "u-1",
    "tenant_id": "t-1",
    "membership_id": "m-1",
    "session_id": "s-1",
    "display_name": "alice",
    # F02：可信上下文携带完整修订快照（与 _state() 默认一致）
    "session_ctx_version": 1,
    "user_auth_revision": 0,
    "tenant_auth_revision": 1,
    "membership_revision": 1,
}


def _config(user: dict[str, Any] | None = _IDENTITY) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": "th-1"}
    if user is not None:
        configurable["langgraph_auth_user"] = user
    return {"configurable": configurable}


def _state(**overrides: Any) -> IdentityState:
    base = {
        "session_active": True,
        "session_unexpired": True,
        "session_ctx_version": 1,
        "user_active": True,
        "account": "alice",
        "user_auth_revision": 0,
        "tenant_status": "active",
        "tenant_auth_revision": 1,
        "tenant_code": "a",
        "membership_status": "active",
        "membership_revision": 1,
    }
    base.update(overrides)
    return IdentityState(**base)  # type: ignore[arg-type]


def test_identity_only_from_trusted_configurable() -> None:
    """metadata/input 自报字段不被采信；只认平台注入的 langgraph_auth_user."""
    assert identity_from_config(_config()) is not None
    spoofed = {
        "configurable": {"user_id": "SP00F", "metadata": {"user_id": "SP00F"}},
        "metadata": {"user_id": "SP00F", "tenant_id": "SP00F"},
    }
    assert identity_from_config(spoofed) is None
    # 不完整身份同样拒绝
    partial = {k: v for k, v in _IDENTITY.items() if k != "membership_id"}
    assert identity_from_config(_config(partial)) is None


async def test_guard_rejects_missing_identity_in_enforce_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    with pytest.raises(RuntimeIdentityError):
        await guard_runtime_call(_config(user=None))


async def test_guard_skips_missing_identity_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "false")
    assert await guard_runtime_call(_config(user=None)) is None


async def test_guard_missing_revision_snapshot_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F02：强制模式下缺修订快照（升级前旧运行）不得降级为仅查 active."""
    from common.identity.runtime import RuntimeIdentityError

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(rt, "fetch_identity_state", lambda *a, **k: _async(_state()))
    legacy_user = {
        k: v
        for k, v in _IDENTITY.items()
        if not k.endswith(("revision", "ctx_version"))
    }
    with pytest.raises(RuntimeIdentityError):
        await guard_runtime_call(_config(legacy_user))
    # 非法快照（非数字）同样按缺失处理 → 拒绝
    broken = {**_IDENTITY, "membership_revision": "not-a-number"}
    with pytest.raises(RuntimeIdentityError):
        await guard_runtime_call(_config(broken))


async def test_guard_revalidates_live_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(rt, "fetch_identity_state", lambda *a, **k: _async(_state()))
    identity = await guard_runtime_call(_config())
    assert identity is not None
    assert identity.tenant_id == "t-1"


async def test_guard_blocks_after_membership_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行中成员被停用 → 下一次调用拒绝（两种图机制共用本守卫）."""
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(
        rt,
        "fetch_identity_state",
        lambda *a, **k: _async(_state(membership_status="disabled")),
    )
    with pytest.raises(RuntimeIdentityRevoked):
        await guard_runtime_call(_config())


async def test_guard_blocks_after_tenant_suspend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(
        rt,
        "fetch_identity_state",
        lambda *a, **k: _async(_state(tenant_status="suspended")),
    )
    with pytest.raises(RuntimeIdentityRevoked):
        await guard_runtime_call(_config())


async def test_guard_blocks_backend_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """身份后端故障失败关闭：不继续外部调用，也不匿名放行."""
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")

    async def _down(*a: Any, **k: Any) -> None:
        raise IdentityBackendUnavailable("down")

    monkeypatch.setattr(rt, "fetch_identity_state", _down)
    with pytest.raises(RuntimeIdentityRevoked):
        await guard_runtime_call(_config())


async def test_deepagents_tool_wrapper_rejects_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepAgents 中间件机制：工具请求被守卫拒绝（不发外部请求）."""
    from common.middleware.tenant_runtime_guard_middleware import (
        TenantRuntimeGuardMiddleware,
    )

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(
        rt, "fetch_identity_state", lambda *a, **k: _async(_state(session_active=False))
    )
    mw = TenantRuntimeGuardMiddleware()
    request = SimpleNamespace(
        runtime=SimpleNamespace(config=_config()),
        tool_call={"id": "tc-1", "name": "mcp_city_x"},
    )
    called = False

    async def _handler(req: Any) -> str:
        nonlocal called
        called = True
        return "SHOULD NOT RUN"

    result = await mw.awrap_tool_call(request, _handler)  # type: ignore[arg-type]
    assert called is False
    assert result.status == "error"
    assert "拒绝" in result.content


async def test_explicit_tool_wrapper_rejects_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式图机制（composed_tool_wrapper）：守卫在 handler 之前拒绝."""
    from basic_qa.tool_runtime import composed_tool_wrapper

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(
        rt, "fetch_identity_state", lambda *a, **k: _async(_state(user_active=False))
    )
    request = SimpleNamespace(
        tool=SimpleNamespace(name="get_beijing_time"),
        tool_call={"id": "tc-2", "name": "get_beijing_time", "args": {}},
        runtime=SimpleNamespace(config=_config(), stream_writer=None),
        state={"messages": []},
    )

    async def _handler(req: Any) -> str:
        return "SHOULD NOT RUN"

    result = await composed_tool_wrapper(request, _handler)  # type: ignore[arg-type]
    assert getattr(result, "status", None) == "error"
    assert "拒绝" in result.content


def test_runtime_identity_log_has_no_secrets() -> None:
    identity = identity_from_config(_config())
    assert identity is not None
    blob = str(identity.as_log_dict())
    for secret in ("password", "token", "credential"):
        assert secret not in blob.lower()


async def _async(value: Any) -> Any:
    return value
