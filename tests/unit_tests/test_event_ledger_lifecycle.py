"""公开图生命周期适配测试（无需 PG；纯逻辑与导入安全）。

覆盖 spec `agent-run-lifecycle` 的浏览器流/内部文本/终态映射场景，以及
"缺台账上下文时不产生事实"（未登记入口不造假）。
"""

from __future__ import annotations

from common.event_ledger.lifecycle import (
    PUBLIC_GRAPHS,
    LedgerContextMissing,
    LedgerLifecycleMiddleware,
    authorization_stop_from_messages,
    business_state_from_native,
    ledger_ids_from_config,
    ledger_scope_from_config,
    make_ledger_finalize_node,
    require_ledger_context,
)

USER = {
    "identity": "22222222-2222-4222-8222-222222222222",
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "membership_id": "33333333-3333-4333-8333-333333333333",
    "session_id": "44444444-4444-4444-8444-444444444444",
}


def _config(**extra: object) -> dict:
    configurable = {"langgraph_auth_user": dict(USER), **extra}
    return {"configurable": configurable}


def test_public_graphs_declared() -> None:
    assert set(PUBLIC_GRAPHS) == {
        "basic-qa",
        "data-analysis",
        "intelligent-tracing",
    }


def test_scope_only_from_trusted_auth_user() -> None:
    assert ledger_scope_from_config(_config()) is not None
    # 客户端自报字段不构成身份
    assert (
        ledger_scope_from_config({"configurable": {"user_id": "x", "tenant_id": "y"}})
        is None
    )


def test_missing_ledger_context_raises_for_unregistered_entry() -> None:
    try:
        require_ledger_context(_config())
    except LedgerContextMissing:
        pass
    else:  # pragma: no cover - 断言失败路径
        raise AssertionError("缺受理上下文必须拒绝（未登记入口不开放）")


def test_ledger_ids_extracted() -> None:
    ids = ledger_ids_from_config(
        _config(ledger_request_id="r1", ledger_attempt_id="a1")
    )
    assert ids == ("r1", "a1")
    assert ledger_ids_from_config(_config()) is None


def test_native_status_mapping_never_guesses_success() -> None:
    assert business_state_from_native("success") == "succeeded"
    assert business_state_from_native("interrupted") == "waiting_input"
    assert business_state_from_native("error") == "failed"
    assert business_state_from_native("timeout") == "timed_out"
    # 未知/缺失不得推定为成功、取消或超时
    assert business_state_from_native("weird") == "status_unknown"
    assert business_state_from_native(None) == "status_unknown"


def test_finalize_node_factory_returns_async_node() -> None:
    """图内收尾节点：保持编译结果为 Graph 类型（不用 with_listeners）。

    平台按 "Graph or Graph factory" 校验导出对象；with_listeners 会变成
    RunnableBinding 而被拒绝，因此改用图内节点。
    """
    import inspect

    node = make_ledger_finalize_node("basic-qa")
    assert inspect.iscoroutinefunction(node)
    assert node.__name__ == "ledger_finalize_basic_qa"


def test_finalize_node_noop_without_context() -> None:
    """无台账受理上下文（未登记入口）：节点不产生事实，且不改动 state。"""
    import asyncio

    node = make_ledger_finalize_node("basic-qa")
    result = (
        asyncio.get_event_loop_policy()
        .new_event_loop()
        .run_until_complete(node({"messages": []}, None))
    )
    assert result == {}


def test_deepagent_middleware_wired_and_fail_open() -> None:
    """deepagents 图用的中间件：可作为 AgentMiddleware，且无上下文时失败开放。"""
    import asyncio

    from langchain.agents.middleware.types import AgentMiddleware

    middleware = LedgerLifecycleMiddleware("intelligent-tracing")
    assert isinstance(middleware, AgentMiddleware)
    # 无受理上下文：不抛错、不改变输出（失败开放）
    result = asyncio.new_event_loop().run_until_complete(
        middleware.aafter_agent({"messages": []}, None)
    )
    assert result is None


def test_visible_final_message_excludes_internal_and_drafts() -> None:
    from common.event_ledger.lifecycle import _visible_final_message

    messages = [
        {"type": "human", "content": "问题", "id": "u1"},
        {
            "type": "ai",
            "content": "内部推理",
            "id": "m1",
            "additional_kwargs": {"ui_visible": False},
        },
        {"type": "tool", "content": "工具输出", "id": "m2"},
        {
            "type": "ai",
            "content": "草稿",
            "id": "m3",
            "additional_kwargs": {"ui_visible": False},
        },
        {"type": "ai", "content": "最终可见回答", "id": "m4"},
    ]
    visible = _visible_final_message(messages)
    assert visible is not None and visible["id"] == "m4"


def test_visible_final_message_none_when_only_internal() -> None:
    from common.event_ledger.lifecycle import _visible_final_message

    messages = [
        {
            "type": "ai",
            "content": "只有推理",
            "id": "m1",
            "additional_kwargs": {"ui_visible": False},
        },
        {"type": "tool", "content": "工具", "id": "m2"},
    ]
    assert _visible_final_message(messages) is None
    assert _visible_final_message("not-a-list") is None


def test_native_run_payload_extracts_ledger_attempt_id() -> None:
    """原生 create_run 载荷提取：metadata 优先，兼容 kwargs.config.configurable。

    实测载荷形状（镜像 0.15.1）：config 位于 kwargs 内而非顶层。
    """
    from common.identity.langgraph_auth import _ledger_attempt_id

    assert _ledger_attempt_id({"metadata": {"ledger_attempt_id": "m1"}}) == "m1"
    assert (
        _ledger_attempt_id(
            {"kwargs": {"config": {"configurable": {"ledger_attempt_id": "k1"}}}}
        )
        == "k1"
    )
    assert (
        _ledger_attempt_id({"config": {"configurable": {"ledger_attempt_id": "c1"}}})
        == "c1"
    )
    # 缺失/形态不符一律空串 → 原生入口拒绝创建（不是"放行"）
    assert _ledger_attempt_id({}) == ""
    assert _ledger_attempt_id({"kwargs": {}}) == ""
    assert _ledger_attempt_id({"metadata": {}}) == ""
    assert _ledger_attempt_id(None) == ""


def test_ledger_scope_requires_full_trusted_identity() -> None:
    """作用域必须来自可信身份四项，缺一即无法校验预留（返回 None）."""
    from common.identity.langgraph_auth import _ledger_scope

    assert _ledger_scope({}) is None
    partial = {"tenant_id": "t", "user_id": "u"}
    assert _ledger_scope(partial) is None
    full = {"tenant_id": "t", "user_id": "u", "membership_id": "m"}
    scope = _ledger_scope(full)
    assert scope is not None and scope.tenant_id == "t"


def test_authorization_stop_is_not_a_success_answer() -> None:
    assert authorization_stop_from_messages(
        [
            {"type": "ai", "content": "授权状态已变化，本次运行已停止：业务使用权限已撤销"},
        ]
    )
    assert authorization_stop_from_messages(
        [
            {"type": "tool", "content": "授权状态已变化，工具调用被拒绝"},
            {"type": "ai", "content": "本次运行已停止"},
        ]
    )
    assert not authorization_stop_from_messages(
        [{"type": "ai", "content": "杭州市今天 AQI 为 84，等级良。"}]
    )
