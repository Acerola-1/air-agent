"""R04 回归（轮次01 修复）：DeepAgents 守卫必须能从**真实框架上下文**取到可信身份。

修复前断言：`TenantRuntimeGuardMiddleware._config_of` 读取
`request.runtime.config` / `get_config()`，而 langgraph 1.2.11 的
`langgraph.runtime.Runtime` 两者都不存在 → 提取恒为 None；在
`TENANT_ENFORCE_RUNTIME_IDENTITY=true` 的部署中每个 DeepAgents 模型调用
都会失败关闭（评审 R04 补充：实际镜像只读核验）。

本测试不用 SimpleNamespace 伪造 config：直接实例化安装了守卫中间件的
langchain agent，并在真实 `RunnableConfig` 中携带平台注入的
`configurable.langgraph_auth_user` 键调用，验证守卫能取到身份并按状态放行/拒绝。
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from common.identity.db import IdentityState
from common.identity.langgraph_auth import auth  # noqa: F401 - 保证模块可导入形状
from common.identity.runtime import RuntimeIdentityError, RuntimeIdentityRevoked
from common.middleware.tenant_runtime_guard_middleware import (
    TenantRuntimeGuardMiddleware,
)

pytestmark = pytest.mark.anyio

_USER: dict[str, Any] = {
    "identity": "u-1",
    "tenant_id": "t-1",
    "membership_id": "m-1",
    "session_id": "s-1",
    "display_name": "alice",
    "session_ctx_version": 1,
    "user_auth_revision": 0,
    "tenant_auth_revision": 1,
    "membership_revision": 1,
}


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
    return IdentityState(**base)


def _agent(monkeypatch, state: IdentityState | None):
    from langchain.agents import create_agent

    async def fake_state(session_id: str, *, user_id: str, tenant_id: str):
        return state

    import common.identity.runtime as rt

    monkeypatch.setattr(rt, "fetch_identity_state", fake_state)
    # 本用例隔离框架身份传递；MCP 目录在无业务快照时返回空，不发外部请求。
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    model = GenericFakeChatModel(messages=iter([AIMessage(content="ok")] * 5))
    return create_agent(model, tools=[], middleware=[TenantRuntimeGuardMiddleware()])


async def test_real_model_call_sees_platform_identity(monkeypatch) -> None:
    """合法身份 + active 状态：真实框架调用必须通过守卫并完成模型调用。"""
    agent = _agent(monkeypatch, _state())
    result = await agent.ainvoke(
        {"messages": [("user", "hi")]},
        config={"configurable": {"langgraph_auth_user": dict(_USER)}},
    )
    assert result["messages"][-1].content == "ok"


async def test_missing_identity_fails_closed_in_real_call(monkeypatch) -> None:
    """无可信身份 + 强制模式：真实模型调用被拒绝（修复前此处也拒绝，但原因错位：
    有身份时同样被误拒）。"""
    agent = _agent(monkeypatch, _state())
    with pytest.raises(RuntimeIdentityError):
        await agent.ainvoke({"messages": [("user", "hi")]}, config={})


async def test_revoked_revision_blocks_next_model_call(monkeypatch) -> None:
    """运行中撤权（成员修订变化、状态仍 active）→ 真实调用拒绝。"""
    agent = _agent(monkeypatch, _state(membership_revision=99))
    with pytest.raises(RuntimeIdentityRevoked):
        await agent.ainvoke(
            {"messages": [("user", "hi")]},
            config={"configurable": {"langgraph_auth_user": dict(_USER)}},
        )
