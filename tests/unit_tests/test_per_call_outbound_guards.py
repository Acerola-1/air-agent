"""F02 回归（轮次复验修复）：每次实际外呼前复查身份；拒绝不被重试吞掉。

对应评审 runtime-proofs 三组反例的"修复前断言"：
- `model_retry_sent_after_simulated_revision_revoke=true`（守卫在重试循环外，
  首次失败后撤权仍发出第二次模型调用，全程只查一次身份）；
- 空回复重试同样不再查；
- `summary_external_calls_without_identity=1, summary_identity_checks=0`
  （长会话/大输出摘要直接调用模型，不经运行守卫）；
- MCP 重试循环不复查授权。

修复后：以上全部拒绝且外呼计数为 0/1（守卫在每次发送前）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from basic_qa.capabilities.runtime import create_load_skill_tool
from basic_qa.context.preparation import PreparedRequest
from basic_qa.nodes import BasicQANodes
from common.identity.db import IdentityState
from common.identity.runtime import (
    RuntimeIdentityError,
    guard_runtime_call,
)
from common.text_reduction import summarize_complete_text

pytestmark = pytest.mark.anyio


def _patch_prepared(monkeypatch, nodes_mod) -> None:
    """提供不受预算影响的完整待发请求，聚焦身份复查语义."""

    async def fake_prepared(*args: Any, **kwargs: Any) -> PreparedRequest:
        return PreparedRequest(
            messages=[HumanMessage(content="input")],
            schemas=[],
            metrics={
                "input_tokens": 1,
                "input_budget_tokens": 100000,
                "summary_trigger_tokens": 80000,
                "window_tokens": 100000,
                "summary_trigger_ratio": 0.8,
                "measurement_source": "estimate:test",
                "input_breakdown_tokens": {
                    "system": 0,
                    "conversation": 1,
                    "tools": 0,
                    "framing": 0,
                },
                "tool_count": 0,
                "output_reserved_tokens": 0,
                "safety_tokens": 0,
            },
            within_capacity=True,
        )

    monkeypatch.setattr(nodes_mod, "prepare_model_request", fake_prepared)


async def test_sdk_retry_cannot_send_after_revision_revocation(monkeypatch) -> None:
    """真实模型 SDK 遇 500 后不得绕开外层守卫自动重发。"""
    import httpx
    from langchain_openai import ChatOpenAI

    import basic_qa.nodes as nodes_mod
    import common.identity.runtime as rt
    from common.models import ModelRegistry

    state = {"revoked": False, "requests": 0}

    async def fetch(*args, **kwargs):
        return _state(membership_revision=99) if state["revoked"] else _state()

    async def respond(request):
        state["requests"] += 1
        if state["requests"] == 1:
            state["revoked"] = True
            return httpx.Response(500, json={"error": {"message": "synthetic failure"}})
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "synthetic",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "unexpected"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(rt, "fetch_identity_state", fetch)
    monkeypatch.setattr(nodes_mod, "emit_event", lambda *args, **kwargs: None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ChatOpenAI(
            api_key="synthetic",
            base_url="http://synthetic.invalid/v1",
            model="synthetic",
            max_retries=ModelRegistry.main_llm.max_retries,
            http_async_client=client,
        )
        monkeypatch.setattr(nodes_mod, "ModelRegistry", SimpleNamespace(main_llm=model))
        result = await _nodes().plan_tools(
            {"messages": [HumanMessage(content="synthetic")]},
            _config(),
            writer=lambda *args: None,
        )
    assert state["requests"] == 1, "SDK 自动重试绕过撤权守卫"
    assert "授权状态已变化" in result["messages"][-1].content


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


def _config(user: dict[str, Any] | None = _USER) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": "th-1"}
    if user is not None:
        configurable["langgraph_auth_user"] = dict(user)
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
    return IdentityState(**base)


def _nodes() -> BasicQANodes:
    nodes = BasicQANodes(
        router=None,
        load_skill_tool=create_load_skill_tool(),
        graph_name="basic_qa",
    )
    return nodes


async def test_model_retry_rechecks_identity_before_each_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次外呼瞬时失败 + 期间撤权 → 第二次不得发送，返回可读拒绝。"""
    import basic_qa.nodes as nodes_mod
    import common.identity.runtime as rt

    state_now = {"revoked": False, "checks": 0, "calls": 0}

    async def fake_fetch(*a: Any, **k: Any) -> IdentityState:
        state_now["checks"] += 1
        return _state(membership_revision=99) if state_now["revoked"] else _state()

    async def invoke(*a: Any, **k: Any) -> AIMessage:
        state_now["calls"] += 1
        if state_now["calls"] == 1:
            state_now["revoked"] = True
            raise RuntimeError("synthetic transient")
        return AIMessage(content="SHOULD NOT HAPPEN")

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(rt, "fetch_identity_state", fake_fetch)
    _patch_prepared(monkeypatch, nodes_mod)
    monkeypatch.setattr(nodes_mod, "emit_event", lambda *a, **k: None)
    models = SimpleNamespace(
        main_llm=SimpleNamespace(
            ainvoke=invoke, bind_tools=lambda s: SimpleNamespace(ainvoke=invoke)
        )
    )
    monkeypatch.setattr(nodes_mod, "ModelRegistry", models)

    nodes = _nodes()
    result = await nodes.plan_tools(
        {"messages": [HumanMessage(content="q")]},
        _config(),
        writer=lambda *a: None,
    )
    assert state_now["calls"] == 1, "撤权后不得发出第二次模型调用"
    assert state_now["checks"] >= 2, "每次实际外呼前都复查身份（至少首呼+重试前各一次）"
    message = result["messages"][-1]
    assert "授权状态已变化" in message.content


async def test_empty_reply_nudge_rechecks_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空回复触发引导重试：重试外呼前同样复查，撤权即停不发第三次。"""
    import basic_qa.nodes as nodes_mod
    import common.identity.runtime as rt

    counters = {"calls": 0, "checks": 0}

    async def fake_fetch(*a: Any, **k: Any) -> IdentityState:
        counters["checks"] += 1
        # 入口守卫 + 首次 attempt 守卫仍有效；nudge 发送前的守卫必须撤权
        return _state(membership_revision=99) if counters["checks"] >= 3 else _state()

    async def invoke(*a: Any, **k: Any) -> AIMessage:
        counters["calls"] += 1
        return AIMessage(content="")  # 空回复触发 nudge 分支

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(rt, "fetch_identity_state", fake_fetch)
    _patch_prepared(monkeypatch, nodes_mod)
    monkeypatch.setattr(nodes_mod, "emit_event", lambda *a, **k: None)
    models = SimpleNamespace(
        main_llm=SimpleNamespace(
            ainvoke=invoke, bind_tools=lambda s: SimpleNamespace(ainvoke=invoke)
        )
    )
    monkeypatch.setattr(nodes_mod, "ModelRegistry", models)

    nodes = _nodes()
    result = await nodes.plan_tools(
        {"messages": [HumanMessage(content="q")]},
        _config(),
        writer=lambda *a: None,
    )
    assert counters["calls"] == 1, "nudge 重试发送前撤权 → 不得发出第二次模型调用"
    assert counters["checks"] >= 3, "入口/每次 attempt/nudge 前各自复查"
    assert "授权状态已变化" in result["messages"][-1].content


async def test_summarize_requires_identity_and_revalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """摘要外呼必须经过运行守卫：无身份拒绝、撤权拒绝，模型零调用。"""
    import common.identity.runtime as rt
    import common.text_reduction as tr

    calls = {"model": 0}

    async def fake_ainvoke(*a: Any, **k: Any) -> AIMessage:
        calls["model"] += 1
        return AIMessage(content="summary")

    monkeypatch.setattr(
        tr,
        "ModelRegistry",
        SimpleNamespace(summary_llm=SimpleNamespace(ainvoke=fake_ainvoke)),
    )
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")

    # 1) 无任何运行身份（评审反例）
    with pytest.raises(RuntimeIdentityError):
        await summarize_complete_text("synthetic text", instruction="summarize")
    assert calls["model"] == 0

    # 2) 携带身份但运行中被撤权（修订前进）
    async def revoked_fetch(*a: Any, **k: Any) -> IdentityState:
        return _state(membership_revision=99)

    monkeypatch.setattr(rt, "fetch_identity_state", revoked_fetch)
    with pytest.raises(RuntimeIdentityError):
        await summarize_complete_text(
            "synthetic text", instruction="summarize", run_config=_config()
        )
    assert calls["model"] == 0


async def test_mcp_retry_revalidates_before_each_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP 代码级重试：每次重发前重新核验身份；撤权即终止重试且不再发送。"""
    import basic_qa.tool_runtime as tw
    import common.identity.runtime as rt

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setattr(tw, "_MCP_RETRY_BACKOFF_SECONDS", 0.01)

    # 外部首呼已失败（5xx），重试循环开始前撤权生效
    revoked = {"value": True}

    async def fake_fetch(*a: Any, **k: Any) -> IdentityState:
        return _state(membership_revision=99) if revoked["value"] else _state()

    monkeypatch.setattr(rt, "fetch_identity_state", fake_fetch)

    handler_calls = {"n": 0}

    async def handler(request: Any) -> str:
        handler_calls["n"] += 1
        return "SHOULD NOT RUN"

    request = SimpleNamespace(
        runtime=SimpleNamespace(config=_config()),
        tool_call={"id": "tc-1", "name": "mcp_city_x"},
    )
    result = await tw._invoke_mcp_with_retry(
        request, handler, RuntimeError("5xx transient")
    )
    assert handler_calls["n"] == 0, "撤权后的重试不得再发送"
    assert getattr(result, "status", None) == "error"
    assert "授权状态已变化" in str(result.content)


async def test_missing_snapshot_fails_closed_in_enforce_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺/非法修订快照：强制模式安全停止（不降级为仅查 active）。"""
    import common.identity.runtime as rt

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")

    async def live(*a: Any, **k: Any) -> IdentityState:
        return _state()

    monkeypatch.setattr(rt, "fetch_identity_state", live)
    legacy = {
        k: v
        for k, v in _USER.items()
        if not (k.endswith("revision") or k.endswith("_version"))
    }
    with pytest.raises(RuntimeIdentityError):
        await guard_runtime_call(_config(legacy))


@pytest.fixture(autouse=True)
def empty_session_projection(monkeypatch):
    from basic_qa.capabilities import projection

    monkeypatch.setattr(projection, "current_material", AsyncMock(return_value=None))
