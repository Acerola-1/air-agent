from common.context import get_routing_context
from common.middleware.mode_routing_middleware import ModeRoutingMiddleware
from common.prompts import EXPERT_MODE_SYSTEM_PROMPT, FAST_MODE_SYSTEM_PROMPT


def test_chat_mode_sets_graph_local_mode() -> None:
    context = get_routing_context({"module": "basic-qa", "chat_mode": "expert"})

    assert context.mode == "expert"
    assert "mode: expert" in context.prompt
    assert "subagent_type" not in context.prompt
    assert "module" not in context.prompt


def test_module_is_ignored_for_business_graph_routing() -> None:
    context = get_routing_context({"module": "default", "mode": "fast"})

    assert context.mode == "fast"
    assert context.prompt == "<runtime_context>\nmode: fast\n</runtime_context>"


def test_invalid_mode_defaults_to_fast() -> None:
    context = get_routing_context({"module": "intelligent-report", "mode": "slow"})

    assert context.mode == "fast"


def test_mode_routing_middleware_appends_expert_prompt(monkeypatch) -> None:
    middleware = ModeRoutingMiddleware()

    monkeypatch.setattr(
        "common.middleware.mode_routing_middleware.get_config",
        lambda: {"configurable": {"mode": "expert"}},
    )

    context = middleware._get_runtime_context()

    assert "<runtime_context>\nmode: expert\n</runtime_context>" in context
    assert EXPERT_MODE_SYSTEM_PROMPT in context
    assert FAST_MODE_SYSTEM_PROMPT not in context


def test_mode_routing_middleware_appends_fast_prompt_for_fast(monkeypatch) -> None:
    middleware = ModeRoutingMiddleware()

    monkeypatch.setattr(
        "common.middleware.mode_routing_middleware.get_config",
        lambda: {"configurable": {"mode": "fast"}},
    )

    context = middleware._get_runtime_context()

    assert "<runtime_context>\nmode: fast\n</runtime_context>" in context
    assert FAST_MODE_SYSTEM_PROMPT in context
    assert EXPERT_MODE_SYSTEM_PROMPT not in context
