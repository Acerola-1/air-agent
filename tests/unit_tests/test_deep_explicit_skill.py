"""真实 LangChain 模型请求中确定性加载已选规则，不依赖 find_skill 工具调用."""

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
from common.model_budget import ContextBudgetExceeded
from common.observability import skill_adapter

pytestmark = pytest.mark.anyio


class CapturingModel(FakeMessagesListChatModel):
    """记录框架最终交给模型的请求，模型没有调用 find_skill 的机会."""

    captured: list = []

    def _generate(self, messages, *args, **kwargs):
        self.captured = list(messages)
        return super()._generate(messages, *args, **kwargs)


@pytest.fixture
def explicit_agent(tmp_path, monkeypatch):
    for slug in ("s1", "s2"):
        folder = tmp_path / slug
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: 受控技能\n---\nRULE_{slug}"
        )
    monkeypatch.setitem(capabilities.GRAPH_SKILLS_DIRS, "deep-research", tmp_path)
    monkeypatch.setattr(
        "common.middleware.tenant_runtime_guard_middleware.guard_request_call",
        AsyncMock(),
    )

    async def no_bindings(*args):
        return []

    monkeypatch.setattr(capabilities, "fetch_active_mcp_bindings", no_bindings)
    model = CapturingModel(responses=[AIMessage(content="ok")])
    agent = create_agent(model, tools=[], middleware=[TenantRuntimeGuardMiddleware()])
    config = {
        "configurable": {
            "langgraph_auth_user": {
                "identity": "u",
                "tenant_id": "t",
                "membership_id": "m",
                "session_id": "s",
            },
            "capability_skill_mode": "explicit",
            "capability_skill_id": "skill:deep-research:s1",
            "capability_mcp_mode": "allowlist",
            "capability_tool_ids": [],
        }
    }
    return agent, model, config


async def test_first_model_request_already_contains_selected_rules(explicit_agent):
    agent, model, config = explicit_agent
    result = await agent.ainvoke({"messages": [("user", "你好")]}, config)
    assert result["messages"][-1].content == "ok"
    system = str(model.captured[0].content)
    assert "RULE_s1" in system and "RULE_s2" not in system


async def test_changed_material_stops_before_model(explicit_agent):
    agent, model, config = explicit_agent
    config["configurable"]["capability_material_version"] = "sha256:forged"
    with pytest.raises(RuntimeIdentityError, match="材料已变化"):
        await agent.ainvoke({"messages": [("user", "你好")]}, config)
    assert model.captured == []


async def test_selected_rules_still_obey_complete_request_budget(
    explicit_agent, monkeypatch
):
    from common.model_budget import config as settings

    agent, model, config = explicit_agent
    monkeypatch.setattr(settings, "MODEL_CONTEXT_BUDGET_TOKENS", 1)
    with pytest.raises(ContextBudgetExceeded):
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
