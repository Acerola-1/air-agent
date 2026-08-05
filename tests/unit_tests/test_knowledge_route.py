"""知识检索路由快速路径测试."""

from __future__ import annotations

from types import SimpleNamespace

import common.tools as tools


class FailingRouter:
    def invoke(self, _prompt: str):
        raise AssertionError("LLM should not run")


def test_policy_route_fast_path_skips_llm(monkeypatch) -> None:
    tools.clear_knowledge_route_cache()
    monkeypatch.setattr(tools.ModelRegistry, "deepseek_v4_flash", FailingRouter())

    assert tools._route_knowledge_query("大气污染防治法有哪些要求", 5) == "policy"


def test_knowledge_route_fast_path_skips_llm(monkeypatch) -> None:
    tools.clear_knowledge_route_cache()
    monkeypatch.setattr(tools.ModelRegistry, "deepseek_v4_flash", FailingRouter())

    assert tools._route_knowledge_query("PM2.5 是什么意思", 5) == "knowledge"


def test_route_cache_reuses_llm_result(monkeypatch) -> None:
    tools.clear_knowledge_route_cache()
    calls = 0

    def fake_invoke(_prompt: str):
        nonlocal calls
        calls += 1
        return SimpleNamespace(content="policy")

    monkeypatch.setattr(
        tools.ModelRegistry,
        "deepseek_v4_flash",
        SimpleNamespace(invoke=fake_invoke),
    )

    assert tools._route_knowledge_query("请帮我查一下相关材料", 5) == "policy"
    assert tools._route_knowledge_query("请帮我查一下相关材料", 5) == "policy"
    assert calls == 1


def test_ambiguous_route_uses_llm_fallback(monkeypatch) -> None:
    tools.clear_knowledge_route_cache()
    calls = 0

    def fake_invoke(_prompt: str):
        nonlocal calls
        calls += 1
        return SimpleNamespace(content="knowledge")

    monkeypatch.setattr(
        tools.ModelRegistry,
        "deepseek_v4_flash",
        SimpleNamespace(invoke=fake_invoke),
    )

    assert tools._route_knowledge_query("帮我看看相关内容", 5) == "knowledge"
    assert calls == 1
