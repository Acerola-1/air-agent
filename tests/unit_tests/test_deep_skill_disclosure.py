"""真实 LangChain 请求保留授权守卫，不预加载事先指定的 Skill 规则."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from common import capabilities
from common.identity.runtime import RuntimeIdentityError
from common.middleware.tenant_runtime_guard_middleware import (
    TenantRuntimeGuardMiddleware,
)
from common.observability import skill_adapter

pytestmark = pytest.mark.anyio


class CapturingModel(FakeMessagesListChatModel):
    """记录框架最终交给模型的请求，模型没有调用 find_skill 的机会."""

    captured: list = []

    def _generate(self, messages, *args, **kwargs):
        self.captured = list(messages)
        return super()._generate(messages, *args, **kwargs)


@pytest.fixture
def guarded_agent(monkeypatch):
    import common.mcp_scoped as mcp

    monkeypatch.setattr(
        "common.middleware.tenant_runtime_guard_middleware.guard_request_call",
        AsyncMock(),
    )
    monkeypatch.setattr(capabilities, "refresh_authorized_mcp_tools", AsyncMock())
    monkeypatch.setattr(mcp, "build_proxy_tools", lambda: [])
    model = CapturingModel(responses=[AIMessage(content="ok")])
    agent = create_agent(
        model,
        tools=[],
        middleware=[TenantRuntimeGuardMiddleware()],
        system_prompt="基础规则",
    )
    config = {
        "configurable": {"capability_mcp_mode": "allowlist", "capability_tool_ids": []}
    }
    return agent, model, config


async def test_first_request_does_not_preload_skill_rules(guarded_agent):
    agent, model, config = guarded_agent
    result = await agent.ainvoke({"messages": [("user", "你好")]}, config)
    assert result["messages"][-1].content == "ok"
    assert model.captured[0].content == "基础规则"


async def test_revocation_stops_before_model(guarded_agent, monkeypatch):
    agent, model, config = guarded_agent
    monkeypatch.setattr(
        "common.middleware.tenant_runtime_guard_middleware.guard_request_call",
        AsyncMock(side_effect=RuntimeIdentityError("已撤权")),
    )
    with pytest.raises(RuntimeIdentityError):
        await agent.ainvoke({"messages": [("user", "你好")]}, config)
    assert model.captured == []


def test_late_execution_cannot_consume_other_submission(monkeypatch):
    context = SimpleNamespace(tenant_id="t", attempt_id="a", submission_id="new")
    old = {("t", "a", "old"): {"s1": {"source": "manual", "version": "old"}}}
    monkeypatch.setattr(skill_adapter, "_LOADED", old)
    monkeypatch.setattr(
        skill_adapter, "observation_context_from_config", lambda _: context
    )
    emit = []
    monkeypatch.setattr(
        skill_adapter, "emit_skill_stage", lambda *a, **kw: emit.append(kw)
    )
    skill_adapter.record_loaded_execution({}, succeeded=True)
    assert emit == []
    assert ("t", "a", "old") in old
