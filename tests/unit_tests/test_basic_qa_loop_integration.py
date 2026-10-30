"""受控编译图验证 Basic QA 按需加载、会话延续及工具执行范围."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

import basic_qa.builder as builder
import basic_qa.capabilities.projection as integration
import basic_qa.capabilities.runtime as runtime
import basic_qa.nodes as nodes
from basic_qa.capabilities import tool_definitions
from common.models import ModelRegistry

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "payload",
    [
        '{"code":200,"data":{"ranking":3}}',
        '{"shares":[0.3,0.7]}',
        '[{"ranking":3,"share":0.3}]',
        "接口返回的文本说明，没有时间或城市回显字段。",
    ],
)
async def test_result_without_echoed_scope_can_reach_answer(
    controlled, monkeypatch, payload
):
    import common.mcp_scoped as mcp

    graph, model, _ = controlled
    execute = AsyncMock(return_value=payload)
    monkeypatch.setattr(mcp, "execute_mcp_tool", execute)
    model.ainvoke.side_effect = [
        call("load_skill", "load", {"skill_id": _SKILL, "expected_version": _VERSION}),
        call("demo", "query", {"city": "杭州"}),
        call("finish_planning", "finish", {}),
    ]
    seen = []

    async def answer(messages, *args, **kwargs):
        assert kwargs.get("tool_choice") == "none"
        seen.extend(messages)
        yield AIMessageChunk(content="根据工具返回组织回答。")

    model.astream = answer
    await graph.ainvoke({"messages": [HumanMessage(content="查询杭州")]}, _CONFIG)
    execute.assert_awaited_once()
    assert any(isinstance(m, ToolMessage) and m.content == payload for m in seen)
    assert graph.nodes  # 实际编译图运行，不只直接调用字符串辅助函数。


async def test_ordinary_followup_keeps_prior_answer_without_extra_declarations(
    controlled, tmp_path
):
    from langgraph.checkpoint.memory import InMemorySaver

    _, model, calls = controlled
    graph = builder.build_basic_qa_graph(
        skills_dir=tmp_path, name="basic-qa", checkpointer=InMemorySaver()
    )
    model.ainvoke.return_value = AIMessage(content="已具备回答材料。")
    captured = []

    async def answer(messages, *args, **kwargs):
        captured.append(list(messages))
        yield AIMessageChunk(content="AQI 是空气质量指数。")

    model.astream = answer
    first_config = {"configurable": {**_CONFIG["configurable"], "thread_id": "summary"}}
    await graph.ainvoke(
        {"messages": [HumanMessage(content="AQI 是什么？")]}, first_config
    )
    second_config = {
        "configurable": {**first_config["configurable"], "ledger_attempt_id": "next"}
    }
    await graph.ainvoke(
        {"messages": [HumanMessage(content="把刚才解释缩成一句")]}, second_config
    )
    assert not calls
    assert len(captured) == 2
    assert any(
        isinstance(m, AIMessage) and "AQI 是空气质量指数" in str(m.content)
        for m in captured[-1]
    )


def test_finish_tool_requires_no_business_field_declarations():
    schema = nodes._finish_planning_tool().args
    assert schema == {}


async def test_scoped_computation_result_reaches_answer_without_field_validation(
    controlled, monkeypatch, tmp_path
):
    _, model, calls = controlled

    async def compute(artifact_id: str, code: str) -> str:
        """此处只验证图传递结果；真实资料作用域另由 computation_scope 测试覆盖."""
        return '{"complete":true,"result":{"mean":42},"scope":"已有资料"}'

    tool = StructuredTool.from_function(coroutine=compute, name="compute_artifact")
    monkeypatch.setattr(builder, "get_business_tools", lambda **_: [tool])
    monkeypatch.setattr(nodes, "get_business_tools", lambda **_: [tool])
    graph = builder.build_basic_qa_graph(skills_dir=tmp_path, name="basic-qa")
    model.ainvoke.side_effect = [
        call("compute_artifact", "calc", {"artifact_id": "existing", "code": "test"}),
        call("finish_planning", "finish", {}),
    ]
    seen = []

    async def answer(messages, *args, **kwargs):
        seen.extend(messages)
        yield AIMessageChunk(content="计算结果为42。")

    model.astream = answer
    await graph.ainvoke(
        {"messages": [HumanMessage(content="对上轮资料计算均值")]}, _CONFIG
    )
    assert not calls
    assert any(
        isinstance(m, ToolMessage)
        and m.tool_call_id == "calc"
        and '"mean":42' in str(m.content)
        for m in seen
    )


_CONFIG = {
    "configurable": {
        "thread_id": "test-thread",
        "ledger_attempt_id": "attempt",
        "ledger_submission_id": "submission",
        "capability_selection_id": "selection",
        "capability_selection_digest": "digest",
        "ledger_graph_id": "basic-qa",
    }
}


_SKILL = "skill:basic-qa:test"


_VERSION = "sha256:" + "a" * 64


_MATERIAL = SimpleNamespace(
    skill_id=_SKILL,
    material_version=_VERSION,
    mode="fast",
    rules="仅使用可信数据。",
    catalog_scope="test-scope",
    tool_scope=SimpleNamespace(
        allowed_ids=frozenset({"mcp:ipp:demo"}), unavailable_optional=()
    ),
)


@pytest.fixture
def controlled(monkeypatch, tmp_path):
    import common.capabilities as capabilities
    import common.identity.runtime as identity
    import common.mcp_scoped as mcp

    calls = []
    monkeypatch.setattr(
        runtime,
        "guard_runtime_call",
        AsyncMock(return_value=SimpleNamespace(tenant_id="tenant", user_id="user")),
    )

    async def demo(city: str) -> str:
        """Synthetic remote schema; the wrapper handles its execution."""
        raise AssertionError("schema must never execute directly")

    tool = StructuredTool.from_function(
        coroutine=demo, name="demo", description="test", metadata={"is_mcp": True}
    )
    monkeypatch.setattr(builder, "get_business_tools", lambda **_: [tool])
    monkeypatch.setattr(nodes, "get_business_tools", lambda **_: [tool])
    monkeypatch.setattr(
        identity, "guard_runtime_call", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        identity, "guard_request_call", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(runtime, "resolve_material", AsyncMock(return_value=_MATERIAL))
    monkeypatch.setattr(
        tool_definitions,
        "mcp_stable_id",
        lambda name: "mcp:ipp:demo" if name == "demo" else None,
    )
    monkeypatch.setattr(
        capabilities, "filter_capability_mcp_tools", lambda tools, *a, **k: tools
    )
    from basic_qa.capabilities import mcp_catalog as mcp_tool_catalog

    document = mcp_tool_catalog.document_for("ipp", tool)
    monkeypatch.setattr(
        mcp_tool_catalog,
        "resolve_tool_catalog",
        AsyncMock(
            return_value=mcp_tool_catalog.ToolCatalog(
                (document,),
                "test-scope",
                "test-version",
                "tenant",
                "user",
                "membership",
            )
        ),
    )
    monkeypatch.setattr(mcp.get_factory(), "server_of", lambda name: "ipp")

    async def call(*args, **kwargs):
        calls.append(args)
        return '{"code":200,"success":true,"data":[]}'

    monkeypatch.setattr(mcp, "execute_mcp_tool", call)

    async def finalize(state, config):
        return {}

    monkeypatch.setattr(builder, "make_ledger_finalize_node", lambda _: finalize)
    model = AsyncMock()
    model.bind_tools = Mock(return_value=model)

    async def answer(*args, **kwargs):
        yield AIMessageChunk(content="杭州空气质量数据为 42。")

    model.astream = answer
    monkeypatch.setattr(ModelRegistry, "main_llm", model)
    return (
        builder.build_basic_qa_graph(skills_dir=tmp_path, name="basic-qa"),
        model,
        calls,
    )


def call(name, call_id, args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "id": call_id, "args": args}]
    )


@pytest.mark.parametrize("legacy_planning_flag", [None, False, True])
async def test_current_node_path_always_decides_then_answers(
    controlled, legacy_planning_flag
):
    graph, model, calls = controlled
    from langgraph.checkpoint.memory import InMemorySaver

    graph = graph.builder.compile(checkpointer=InMemorySaver())
    if legacy_planning_flag is not None:
        await graph.aupdate_state(_CONFIG, {"needs_planning": legacy_planning_flag})
    model.ainvoke.return_value = AIMessage(content="可以解释已有资料。")
    path = [
        name
        async for update in graph.astream(
            {"messages": [HumanMessage(content="AQI 是什么？")]},
            _CONFIG,
            stream_mode="updates",
        )
        for name in update
    ]
    assert path == [
        "classify_intent",
        "prepare_model",
        "call_model",
        "answer_model",
        "ledger_finalize",
    ]
    assert not calls
    assert model.ainvoke.await_count == 1
    saved = await graph.aget_state(_CONFIG)
    assert saved.values["messages"][-1].additional_kwargs["ui_visible"]


async def test_compiled_input_overflow_stops_before_model(controlled, monkeypatch):
    from common.config import config

    graph, model, calls = controlled
    monkeypatch.setattr(config, "MAX_INPUT_CHARS", 10)
    path = [
        name
        async for update in graph.astream(
            {"messages": [HumanMessage(content="x" * 11)]},
            _CONFIG,
            stream_mode="updates",
        )
        for name in update
    ]
    assert path == ["classify_intent", "ledger_finalize"]
    model.ainvoke.assert_not_awaited()
    assert not calls


def _prior_topology(current):
    """重建补修前拓扑，复用实际节点以验证持久化路由升级，不模拟业务工具."""
    from langgraph.graph import StateGraph

    from basic_qa.state import BasicQAInput, BasicQAState

    previous = StateGraph(BasicQAState, input_schema=BasicQAInput)
    prepare = current.builder.nodes["prepare_model"].runnable

    async def prepare_with_old_flag(state, config):
        return {**await prepare.ainvoke(state, config), "needs_planning": True}

    for name, spec in current.builder.nodes.items():
        previous.add_node(
            name, prepare_with_old_flag if name == "prepare_model" else spec.runnable
        )
    previous.add_node("resolve_skill", lambda state: {})
    for source, target in current.builder.edges:
        if source != "prepare_model":
            previous.add_edge(source, target)
    previous.add_edge("resolve_skill", "prepare_model")
    for source, branches in current.builder.branches.items():
        for branch in branches.values():
            targets = dict(branch.ends)
            if source == "classify_intent":
                targets["resolve_skill"] = "resolve_skill"
            previous.add_conditional_edges(source, branch.path, targets)
    previous.add_conditional_edges(
        "prepare_model",
        lambda state: "call_model" if state.get("needs_planning") else "answer_model",
        {"call_model": "call_model", "answer_model": "answer_model"},
    )
    return previous


@pytest.mark.parametrize(
    "pause_at",
    [
        "classify_intent",
        "prepare_model",
        "call_model",
        "execute_tools",
        "answer_model",
        "ledger_finalize",
        "tool_result",
    ],
)
async def test_prior_topology_checkpoint_resumes_without_replaying_tools(
    controlled, tmp_path, pause_at
):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    graph, model, calls = controlled
    model.ainvoke.side_effect = [
        call("load_skill", "load", {"skill_id": _SKILL, "expected_version": _VERSION}),
        call("demo", "query", {"city": "杭州"}),
        call("finish_planning", "finish", {}),
    ]
    answers = []

    async def answer(*args, **kwargs):
        answers.append(1)
        yield AIMessageChunk(content="杭州空气质量数据为 42。")

    model.astream = answer
    database = str(tmp_path / "checkpoint.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        old = _prior_topology(graph).compile(
            checkpointer=saver,
            interrupt_before=None if pause_at == "tool_result" else [pause_at],
            interrupt_after=["execute_tools"] if pause_at == "tool_result" else None,
        )
        await old.ainvoke(
            {"messages": [HumanMessage(content="杭州今天空气质量？")]}, _CONFIG
        )
        if pause_at == "tool_result":
            await old.ainvoke(None, _CONFIG)
            assert len(calls) == 1
        checkpoint = await old.aget_state(_CONFIG)
        assert checkpoint.next == (
            "call_model" if pause_at == "tool_result" else pause_at,
        )
    # 关闭旧连接、重开数据库，再用新编译图读取和恢复真实序列化状态。
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        upgraded = graph.builder.compile(checkpointer=saver)
        result = await upgraded.ainvoke(None, _CONFIG)
        assert not (await upgraded.aget_state(_CONFIG)).next
    assert len(calls) == 1
    assert model.ainvoke.await_count == 3
    assert answers == [1]
    assert result["messages"][-1].content == "杭州空气质量数据为 42。"


async def test_compiled_load_then_query_and_finish(controlled):
    graph, model, calls = controlled
    model.ainvoke.side_effect = [
        call("load_skill", "load", {"skill_id": _SKILL, "expected_version": _VERSION}),
        call("demo", "query", {"city": "杭州"}),
        call(
            "finish_planning",
            "finish",
            {},
        ),
    ]
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="杭州今天空气质量？")]}, _CONFIG
    )
    assert len(calls) == 1
    assert _SKILL in result["bqa_capabilities"]["active_skill_ids"]
    assert any(
        isinstance(m, ToolMessage)
        and m.tool_call_id == "query"
        and m.status == "success"
        for m in result["messages"]
    )
    assert model.ainvoke.await_count == 3
    assert any(
        "杭州" in str(m.content) for m in result["messages"] if isinstance(m, AIMessage)
    )


async def test_compiled_conflict_never_calls_domain_and_pairs_messages(controlled):
    graph, model, calls = controlled
    model.ainvoke.side_effect = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "load_skill",
                    "id": "load",
                    "args": {"skill_id": _SKILL, "expected_version": _VERSION},
                },
                {"name": "demo", "id": "query", "args": {"city": "杭州"}},
            ],
        ),
        call("finish_planning", "finish", {}),
    ]
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="杭州今天空气质量？")]}, _CONFIG
    )
    assert not calls
    assert result.get("bqa_active_skill") is None
    replies = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert {m.tool_call_id for m in replies} == {"load", "query"}
    assert all(m.status == "error" for m in replies)


async def test_load_can_share_batch_with_previously_disclosed_query(
    controlled, monkeypatch
):
    graph, model, calls = controlled
    second = "skill:basic-qa:second"

    async def material(skill_id, version, config):
        return SimpleNamespace(**{**vars(_MATERIAL), "skill_id": skill_id})

    monkeypatch.setattr(runtime, "resolve_material", material)
    model.ainvoke.side_effect = [
        call("load_skill", "first", {"skill_id": _SKILL, "expected_version": _VERSION}),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "load_skill",
                    "id": "second",
                    "args": {"skill_id": second, "expected_version": _VERSION},
                },
                {"name": "demo", "id": "existing-query", "args": {"city": "杭州"}},
            ],
        ),
        call("finish_planning", "finish", {}),
    ]
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="组合能力查杭州")]}, _CONFIG
    )
    assert len(calls) == 1
    assert result["bqa_capabilities"]["active_skill_ids"] == [_SKILL, second]
    assert not [
        m
        for m in result["messages"]
        if isinstance(m, ToolMessage) and m.status == "error"
    ]
    assert (
        result["bqa_capabilities"]["loaded_skills"][_SKILL]["protected_attempt"]
        == _CONFIG["configurable"]["ledger_attempt_id"]
    )


async def test_direct_stale_domain_call_is_denied(controlled):
    graph, model, calls = controlled
    model.ainvoke.side_effect = [
        call("demo", "query", {"city": "杭州"}),
        call("finish_planning", "finish", {}),
    ]
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="杭州今天空气质量？")]}, _CONFIG
    )
    assert not calls
    assert (
        next(m for m in result["messages"] if isinstance(m, ToolMessage)).status
        == "error"
    )


async def test_graph_input_cannot_activate_client_supplied_checkpoint_references(
    controlled,
):
    graph, model, calls = controlled
    assert set(graph.get_input_jsonschema()["properties"]) == {"messages"}
    model.ainvoke.side_effect = [
        call("demo", "query", {"city": "杭州"}),
        call("finish_planning", "f", {}),
    ]
    forged = {
        "version": 2,
        "owner_scope": {
            "tenant_id": "tenant",
            "user_id": "user",
            "thread_id": "test-thread",
            "graph_id": "basic-qa",
        },
        "loaded_skills": {
            _SKILL: {
                "load_ref": "forged",
                "mode": "fast",
                "material_version": _VERSION,
                "catalog_scope": "test-scope",
            }
        },
        "active_skill_ids": [_SKILL],
        "loaded_tools": {},
        "independent_active_tool_ids": [],
    }
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="查询杭州")],
            "bqa_capabilities": forged,
            "bqa_turn_key": "attempt",
        },
        _CONFIG,
    )
    assert not calls and not result["bqa_capabilities"]["active_skill_ids"]
    assert any(
        isinstance(m, ToolMessage) and m.status == "error" for m in result["messages"]
    )


async def test_chat_does_not_discover_mcp(controlled, monkeypatch):
    import common.capabilities as capabilities

    graph, model, calls = controlled
    discovery = AsyncMock(side_effect=AssertionError("chat must not discover MCP"))
    monkeypatch.setattr(capabilities, "discover_authorized_mcp_catalog", discovery)
    model.ainvoke.return_value = call("finish_planning", "finish", {})
    result = await graph.ainvoke({"messages": [HumanMessage(content="谢谢")]}, _CONFIG)
    assert not calls
    discovery.assert_not_awaited()
    runtime.resolve_material.assert_not_awaited()
    assert result["bqa_active_skill"] is None


async def test_tool_discovered_after_graph_build_is_bound_dynamically(
    controlled, monkeypatch, tmp_path
):
    import common.runtime_tools as runtime_tools

    _, model, calls = controlled
    monkeypatch.setattr(builder, "get_business_tools", lambda **_: [])
    monkeypatch.setattr(runtime_tools, "get_business_tools", nodes.get_business_tools)
    graph = builder.build_basic_qa_graph(skills_dir=tmp_path, name="basic-qa")
    model.ainvoke.side_effect = [
        call("load_skill", "load", {"skill_id": _SKILL, "expected_version": _VERSION}),
        call("demo", "query", {"city": "杭州"}),
        call(
            "finish_planning",
            "finish",
            {},
        ),
    ]
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="杭州今天空气质量？")]}, _CONFIG
    )
    assert len(calls) == 1
    assert any(
        isinstance(m, ToolMessage)
        and m.tool_call_id == "query"
        and m.status == "success"
        for m in result["messages"]
    )


async def test_city_followups_and_restate_reuse_without_search_load_or_errors(
    controlled, tmp_path
):
    from langgraph.checkpoint.memory import InMemorySaver

    _, model, calls = controlled
    graph = builder.build_basic_qa_graph(
        skills_dir=tmp_path, name="basic-qa", checkpointer=InMemorySaver()
    )
    script = [
        call("load_skill", "load1", {"skill_id": _SKILL, "expected_version": _VERSION})
    ]
    questions = [
        "湖州市今天的空气质量如何？",
        "那杭州呢？",
        "那金华呢？",
        "缩成两句",
        "谢谢",
        "再查湖州",
    ]
    for i, city in enumerate(["湖州", "杭州", "金华", None, None, "湖州"]):
        if city:
            script.append(call("demo", f"query-{i}", {"city": city}))
        script.append(call("finish_planning", f"finish-{i}", {}))
    model.ainvoke.side_effect = script
    for i, question in enumerate(questions):
        cfg = {
            "configurable": {
                **_CONFIG["configurable"],
                "thread_id": "cities",
                "ledger_attempt_id": f"attempt-{i}",
            }
        }
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=question)]}, cfg
        )
        assert _SKILL in result["bqa_capabilities"]["active_skill_ids"]
        last = max(
            j
            for j, msg in enumerate(result["messages"])
            if isinstance(msg, HumanMessage)
        )
        turn = result["messages"][last + 1 :]
        assert not [
            m for m in turn if isinstance(m, ToolMessage) and m.status == "error"
        ]
        if i:
            assert not [
                c
                for m in turn
                if isinstance(m, AIMessage)
                for c in m.tool_calls
                if c["name"] in {"search_skills", "load_skill"}
            ]
    assert [args[2]["city"] for args in calls] == ["湖州", "杭州", "金华", "湖州"]
    for binding, invocation in zip(
        model.bind_tools.call_args_list, model.ainvoke.await_args_list
    ):
        names = {item["function"]["name"] for item in binding.args[0]}
        if "demo" in names:
            assert any(_MATERIAL.rules in str(m.content) for m in invocation.args[0])
    assert result["bqa_turn_key"] == "attempt-5"


async def test_multi_skill_loads_merge_and_return_to_first_without_reload(
    controlled, monkeypatch, tmp_path
):
    from langgraph.checkpoint.memory import InMemorySaver

    _, model, calls = controlled
    second = "skill:basic-qa:second"

    async def material(skill_id, version, config):
        if skill_id == "bad":
            raise runtime.BasicQARuntimeError("invalid")
        return SimpleNamespace(
            **{
                **vars(_MATERIAL),
                "skill_id": skill_id,
                "rules": f"{skill_id} complete rules",
            }
        )

    monkeypatch.setattr(runtime, "resolve_material", material)
    graph = builder.build_basic_qa_graph(
        skills_dir=tmp_path, name="basic-qa", checkpointer=InMemorySaver()
    )
    model.ainvoke.side_effect = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "load_skill",
                    "id": f"load-{i}",
                    "args": {"skill_id": k, "expected_version": _VERSION},
                }
                for i, k in enumerate([_SKILL, second, "bad"])
            ],
        ),
        call("demo", "q1", {"city": "湖州"}),
        call("finish_planning", "f1", {}),
        call("demo", "q2", {"city": "金华"}),
        call("finish_planning", "f2", {}),
    ]
    for i in range(2):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=["组合A和B", "再用A查金华"][i])]},
            {
                "configurable": {
                    **_CONFIG["configurable"],
                    "ledger_attempt_id": f"multi-{i}",
                }
            },
        )
    assert result["bqa_capabilities"]["active_skill_ids"] == [_SKILL, second]
    receipts = {
        m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)
    }
    assert receipts["load-0"].status == receipts["load-1"].status == "success"
    assert receipts["load-2"].status == "error"
    for invocation in model.ainvoke.await_args_list[1:]:
        text = "\n".join(str(m.content) for m in invocation.args[0])
        assert f"{_SKILL} complete rules" in text and f"{second} complete rules" in text
    assert len(calls) == 2


async def test_repeated_failed_target_cannot_disable_other_skill_loads(
    controlled, monkeypatch
):
    graph, model, calls = controlled
    attempts = []

    async def material(skill_id, version, config):
        attempts.append(skill_id)
        if skill_id == "skill:basic-qa:bad":
            raise runtime.BasicQARuntimeError("unavailable")
        return _MATERIAL

    monkeypatch.setattr(runtime, "resolve_material", material)
    model.ainvoke.side_effect = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "load_skill",
                    "id": f"bad-{i}",
                    "args": {
                        "skill_id": "skill:basic-qa:bad",
                        "expected_version": _VERSION,
                    },
                }
                for i in range(3)
            ]
            + [
                {
                    "name": "load_skill",
                    "id": "good",
                    "args": {"skill_id": _SKILL, "expected_version": _VERSION},
                }
            ],
        ),
        call("demo", "q", {"city": "杭州"}),
        call("finish_planning", "f", {}),
    ]
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="尝试可用能力")]}, _CONFIG
    )
    assert attempts.count("skill:basic-qa:bad") == 2
    assert result["bqa_capabilities"]["active_skill_ids"] == [_SKILL]
    assert len(calls) == 1
    receipts = {
        m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)
    }
    assert (
        receipts["bad-2"].status == "error"
        and "control_retry_budget" in receipts["bad-2"].content
    )
    assert receipts["good"].status == "success"


def test_real_authorization_denial_still_exhausts_tool():
    from basic_qa.tool_recovery import exhausted_tools

    messages = [
        HumanMessage(content="查询杭州"),
        call("demo", "denied", {"city": "杭州"}),
        ToolMessage(
            content='{"code":403,"success":false,"message":"业务权限已撤销"}',
            name="demo",
            tool_call_id="denied",
            status="error",
        ),
    ]
    assert "demo" in exhausted_tools(messages)


@pytest.mark.parametrize("error_kind", ["identity", "business"])
async def test_identity_or_business_denial_is_not_a_reload_hint(
    monkeypatch, error_kind
):
    from langgraph.config import var_child_runnable_config

    from basic_qa.tool_policy import basic_qa_tool_wrapper
    from common.identity.business_access import BusinessAccessError
    from common.identity.runtime import RuntimeIdentityError

    error_type = (
        RuntimeIdentityError if error_kind == "identity" else BusinessAccessError
    )
    monkeypatch.setattr(
        "common.identity.runtime.guard_request_call",
        AsyncMock(side_effect=error_type("已撤销")),
    )
    message = call("get_beijing_time", "denied", {})
    message.id = "model-message"
    request = SimpleNamespace(
        tool=SimpleNamespace(metadata={}),
        tool_call=message.tool_calls[0],
        runtime=SimpleNamespace(config=_CONFIG),
        state={
            "messages": [message],
            "bqa_disclosure": {
                "model_message_id": message.id,
                "tools": {"local:get_beijing_time": "get_beijing_time"},
            },
        },
    )
    token = var_child_runnable_config.set(_CONFIG)
    try:
        if error_kind == "business":
            with pytest.raises(error_type):
                await basic_qa_tool_wrapper(request, lambda _: None)
        else:
            result = await basic_qa_tool_wrapper(request, lambda _: None)
            assert result.status == "error"
            assert json.loads(result.content)["code"] == 403
            assert "load_skill" not in result.content
    finally:
        var_child_runnable_config.reset(token)


def test_identical_search_failure_stops_after_one_retry():
    from basic_qa.tool_recovery import (
        control_request_exhausted,
        exhausted_tools,
    )

    messages = [HumanMessage(content="查询")]
    for index in range(2):
        messages.extend(
            [
                call("search_skills", str(index), {"query": "空气质量"}),
                ToolMessage(
                    content='{"status":"search_timeout"}',
                    name="search_skills",
                    tool_call_id=str(index),
                ),
            ]
        )
    assert control_request_exhausted(
        messages, {"name": "search_skills", "args": {"query": "空气质量"}}
    )
    assert not control_request_exhausted(
        messages, {"name": "search_skills", "args": {"query": "趋势分析"}}
    )
    assert "search_skills" not in exhausted_tools(messages)


async def test_send_rechecks_skill_scope(monkeypatch):
    from basic_qa.capabilities.runtime import BasicQARuntimeError

    monkeypatch.setattr(
        integration, "current_material", AsyncMock(return_value=_MATERIAL)
    )
    context, material = await integration.prompt_context({}, {})
    changed = SimpleNamespace(
        skill_id=_SKILL,
        rules=_MATERIAL.rules,
        tool_scope=SimpleNamespace(allowed_ids=frozenset()),
    )
    monkeypatch.setattr(
        integration, "current_material", AsyncMock(return_value=changed)
    )
    with pytest.raises(BasicQARuntimeError):
        await integration.verify_prompt_context({}, {}, context, material)


async def test_malformed_load_keeps_old_skill_and_idempotent_reload(controlled):
    graph, model, calls = controlled
    model.ainvoke.side_effect = [
        call("load_skill", "first", {"skill_id": _SKILL, "expected_version": _VERSION}),
        call("load_skill", "malformed", {"skill_id": _SKILL}),
        call(
            "load_skill", "second", {"skill_id": _SKILL, "expected_version": _VERSION}
        ),
        call("demo", "query", {"city": "杭州"}),
        call(
            "finish_planning",
            "finish",
            {},
        ),
    ]
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="杭州今天空气质量？")]}, _CONFIG
    )
    assert len(calls) == 1
    error = next(
        m
        for m in result["messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id == "malformed"
    )
    assert error.status == "error"
    assert result["bqa_capabilities"]["loaded_skills"][_SKILL]["load_ref"] == "first"
    assert any(
        m.tool_call_id == "second" and m.status == "success"
        for m in result["messages"]
        if isinstance(m, ToolMessage)
    )


async def test_load_exception_preserves_previous_active_and_returns_paired_error(
    controlled, monkeypatch
):
    graph, model, calls = controlled
    model.ainvoke.side_effect = [
        call("load_skill", "first", {"skill_id": _SKILL, "expected_version": _VERSION}),
        call(
            "load_skill", "failed", {"skill_id": _SKILL, "expected_version": _VERSION}
        ),
        call(
            "load_skill", "second", {"skill_id": _SKILL, "expected_version": _VERSION}
        ),
        call("finish_planning", "finish", {}),
    ]

    # Prompt and send guards also resolve active materials; count only actual load invocation.
    # Inject a paired failure for this call without changing capability references.
    async def resolve_by_phase(skill_id, version, config):
        return _MATERIAL

    monkeypatch.setattr(runtime, "resolve_material", resolve_by_phase)
    from basic_qa import tool_policy as tool_wrappers

    prior_handler = tool_wrappers.composed_tool_wrapper

    async def injected(request, handler, **kwargs):
        if request.tool_call.get("id") == "failed":
            return ToolMessage(
                content='{"code":500,"success":false}',
                tool_call_id="failed",
                name="load_skill",
                status="error",
            )
        return await prior_handler(request, handler, **kwargs)

    monkeypatch.setattr(tool_wrappers, "composed_tool_wrapper", injected)
    result = await graph.ainvoke({"messages": [HumanMessage(content="查询")]}, _CONFIG)
    assert not calls
    assert (
        next(
            m
            for m in result["messages"]
            if isinstance(m, ToolMessage) and m.tool_call_id == "failed"
        ).status
        == "error"
    )
    assert result["bqa_capabilities"]["loaded_skills"][_SKILL]["load_ref"] == "first"


@pytest.mark.parametrize("selected", [0, 1, 2, 3])
async def test_search_candidates_independent_loads_and_reuse(
    controlled, monkeypatch, tmp_path, selected
):
    import json

    from langgraph.checkpoint.memory import InMemorySaver

    from basic_qa.capabilities import mcp_catalog as mcp_tool_catalog
    from basic_qa.capabilities import mcp_search as mcp_tool_search

    _, model, calls = controlled
    names = [
        "mcp_city_base_get_district_by_lnglat",
        "mcp_grid_common_air_data",
        "mcp_city_base_get_parent_district_by_child",
        "unused_fourth",
    ]
    documents = []
    for name in names:

        async def query(region: str):
            """Synthetic query schema."""
            raise AssertionError("factory only")

        documents.append(
            mcp_tool_catalog.document_for(
                "ipp",
                StructuredTool.from_function(
                    coroutine=query, name=name, description=name
                ),
            )
        )
    cat = mcp_tool_catalog.ToolCatalog(
        tuple(documents), "test-scope", "test-version", "tenant", "user", "membership"
    )
    monkeypatch.setattr(
        mcp_tool_catalog, "resolve_tool_catalog", AsyncMock(return_value=cat)
    )
    monkeypatch.setattr(
        mcp_tool_search, "resolve_tool_catalog", AsyncMock(return_value=cat)
    )
    monkeypatch.setattr(
        mcp_tool_search,
        "search_catalog",
        AsyncMock(
            return_value={
                "status": "ok",
                "catalog_version": "test-version",
                "candidates": [d.candidate(1) for d in documents[:3]],
            }
        ),
    )
    monkeypatch.setattr(
        tool_definitions,
        "mcp_stable_id",
        lambda name: "mcp:ipp:" + name if name in names else None,
    )
    graph = builder.build_basic_qa_graph(
        skills_dir=tmp_path, name="basic-qa", checkpointer=InMemorySaver()
    )
    script = [
        call("search_mcp", "search", {"query": "核对坐标（120.15,30.27）所属行政区"})
    ]
    if selected:
        loads = [
            {
                "name": "load_mcp_tool",
                "id": f"load-{i}",
                "args": {
                    "tool_id": d.tool_id,
                    "expected_schema_version": d.schema_version,
                },
            }
            for i, d in enumerate(documents[:selected])
        ]
        loads.append(
            {
                "name": "load_mcp_tool",
                "id": "bad",
                "args": {
                    "tool_id": "mcp:ipp:unknown",
                    "expected_schema_version": documents[0].schema_version,
                },
            }
        )
        script.extend(
            [
                AIMessage(content="", tool_calls=loads),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": names[i],
                            "id": f"query-{i}",
                            "args": {"region": "120.15,30.27"},
                        }
                        for i in range(selected)
                    ],
                ),
            ]
        )
    script.append(call("finish_planning", "finish", {}))
    if selected:
        script.extend(
            [
                call(names[0], "reuse", {"region": "120.2,30.3"}),
                call("finish_planning", "finish2", {}),
            ]
        )
    model.ainvoke.side_effect = script
    cfg = {
        "configurable": {
            **_CONFIG["configurable"],
            "thread_id": f"independent-{selected}",
        }
    }
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="核对坐标行政区")]}, cfg
    )
    assert result["bqa_capabilities"]["active_skill_ids"] == []
    assert set(result["bqa_capabilities"]["independent_active_tool_ids"]) == {
        d.tool_id for d in documents[:selected]
    }
    assert len(calls) == selected
    if selected:
        cfg["configurable"]["ledger_attempt_id"] = "reuse-attempt"
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="那（120.2,30.3）呢？")]}, cfg
        )
        assert len(calls) == selected + 1
        assert calls[-1][2] == {"region": "120.2,30.3"}
        assert not [
            m
            for m in result["messages"]
            if isinstance(m, ToolMessage) and m.name == names[0] and m.status == "error"
        ]
        for binding in model.bind_tools.call_args_list:
            schemas = binding.args[0]
            names_sent = {s["function"]["name"] for s in schemas}
            assert "unused_fourth" not in names_sent
        # Search/load control operations sent zero business requests; only the
        # scripted query batches reached the counting execution seam.
        queries = [
            m
            for m in result["messages"]
            if isinstance(m, ToolMessage) and m.name in names
        ]
        assert len(queries) == selected + 1
        assert all(m.status == "success" for m in queries)
        receipt = next(
            m
            for m in result["messages"]
            if isinstance(m, ToolMessage) and m.tool_call_id == "load-0"
        )
        assert "properties" not in receipt.content
        assert json.loads(receipt.content)["status"] == "loaded"


async def test_shared_skill_and_independent_source_survives_skill_dormancy(
    controlled, monkeypatch, tmp_path
):
    import json

    from langgraph.checkpoint.memory import InMemorySaver

    from basic_qa.capabilities import mcp_catalog as mcp_tool_catalog
    from basic_qa.capabilities import mcp_search as mcp_tool_search

    _, model, calls = controlled
    cat = await mcp_tool_catalog.resolve_tool_catalog(_CONFIG)
    monkeypatch.setattr(
        mcp_tool_search, "resolve_tool_catalog", AsyncMock(return_value=cat)
    )
    d = cat.documents[0]
    graph = builder.build_basic_qa_graph(
        skills_dir=tmp_path, name="basic-qa", checkpointer=InMemorySaver()
    )
    model.ainvoke.side_effect = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "load_skill",
                    "id": "s",
                    "args": {"skill_id": _SKILL, "expected_version": _VERSION},
                },
                {
                    "name": "load_mcp_tool",
                    "id": "t",
                    "args": {
                        "tool_id": d.tool_id,
                        "expected_schema_version": d.schema_version,
                    },
                },
            ],
        ),
        call(
            "load_mcp_tool",
            "again",
            {"tool_id": d.tool_id, "expected_schema_version": d.schema_version},
        ),
        call(
            "load_mcp_tool",
            "old-version",
            {"tool_id": d.tool_id, "expected_schema_version": "old"},
        ),
        call("demo", "q1", {"city": "湖州"}),
        call("finish_planning", "f1", {}),
        call("demo", "q2", {"city": "杭州"}),
        call("finish_planning", "f2", {}),
    ]
    cfg = {"configurable": {**_CONFIG["configurable"], "thread_id": "sources"}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="组合已知Skill及工具")]}, cfg
    )
    assert len(calls) == 1
    receipts = {
        m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)
    }
    assert json.loads(receipts["again"].content)["status"] == "already_active"
    assert receipts["old-version"].status == "error"
    caps = result["bqa_capabilities"]
    assert caps["active_skill_ids"] == [_SKILL] and caps[
        "independent_active_tool_ids"
    ] == [d.tool_id]
    caps["active_skill_ids"] = []
    await graph.aupdate_state(cfg, {"bqa_capabilities": caps})
    cfg["configurable"]["ledger_attempt_id"] = "next"
    result = await graph.ainvoke({"messages": [HumanMessage(content="那杭州呢")]}, cfg)
    assert len(calls) == 2 and calls[-1][2] == {"city": "杭州"}
    assert result["bqa_capabilities"]["independent_active_tool_ids"] == [d.tool_id]
    for binding in model.bind_tools.call_args_list:
        assert sum(s["function"]["name"] == "demo" for s in binding.args[0]) <= 1


async def test_business_queries_still_run_in_parallel(controlled, monkeypatch):
    import asyncio

    import common.mcp_scoped as mcp

    graph, model, _ = controlled
    both_started = asyncio.Event()
    started = []

    async def query(server, name, args, config):
        started.append(args["city"])
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return '{"data":42}'

    monkeypatch.setattr(mcp, "execute_mcp_tool", query)
    model.ainvoke.side_effect = [
        call("load_skill", "s", {"skill_id": _SKILL, "expected_version": _VERSION}),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "demo", "id": f"q{i}", "args": {"city": city}}
                for i, city in enumerate(["湖州", "杭州"])
            ],
        ),
        call("finish_planning", "f", {}),
    ]
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="同时查询湖州杭州")]}, _CONFIG
    )
    assert set(started) == {"湖州", "杭州"}
    assert all(
        m.status == "success" for m in result["messages"] if isinstance(m, ToolMessage)
    )


async def test_actual_budget_dormancy_persists_but_keeps_complete_needed_rules(
    controlled, monkeypatch, tmp_path
):
    from dataclasses import replace

    from langgraph.checkpoint.memory import InMemorySaver

    from basic_qa.context.preparation import prepare_model_request
    from common.model_capacity import capacity_for_role

    _, model, calls = controlled
    first = "skill:basic-qa:large"
    second = "skill:basic-qa:small"

    async def material(skill_id, version, config):
        return SimpleNamespace(
            **{
                **vars(_MATERIAL),
                "skill_id": skill_id,
                "rules": ("OLD_COMPLETE_RULE " * 12000)
                if skill_id == first
                else "NEEDED_COMPLETE_RULE",
            }
        )

    monkeypatch.setattr(runtime, "resolve_material", material)
    limit = {"window": 1000000}
    monkeypatch.setattr(
        "basic_qa.context.preparation.summarize_conversation_history",
        AsyncMock(return_value="Prior controlled conversation summary."),
    )

    async def prepare(state, config, **kwargs):
        cap = replace(
            capacity_for_role("main"),
            window_tokens=limit["window"],
            output_reserved_tokens=500,
            safety_tokens=100,
            provider_input_limit_tokens=None,
            trigger_ratio=0.99,
        )
        return await prepare_model_request(state, config, capacity=cap, **kwargs)

    monkeypatch.setattr(nodes, "prepare_model_request", prepare)
    graph = builder.build_basic_qa_graph(
        skills_dir=tmp_path, name="basic-qa", checkpointer=InMemorySaver()
    )
    model.ainvoke.side_effect = [
        call("load_skill", "large", {"skill_id": first, "expected_version": _VERSION}),
        call("finish_planning", "f1", {}),
        call("load_skill", "small", {"skill_id": second, "expected_version": _VERSION}),
        AIMessage(content=""),
        call("demo", "q", {"city": "杭州"}),
        call("finish_planning", "f2", {}),
    ]
    cfg = {"configurable": {**_CONFIG["configurable"], "thread_id": "capacity"}}
    initial_result = await graph.ainvoke(
        {"messages": [HumanMessage(content="加载第一个能力")]}, cfg
    )
    assert initial_result["bqa_capabilities"]["active_skill_ids"] == [first], (
        initial_result["bqa_capabilities"],
        initial_result.get("context_metrics"),
        [(m.type, str(m.content)[:200]) for m in initial_result["messages"]],
    )
    limit["window"] = 20000
    cfg["configurable"]["ledger_attempt_id"] = "next"
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="使用第二个能力查询杭州")]}, cfg
    )
    assert result["bqa_capabilities"]["active_skill_ids"] == [second], (
        result["bqa_capabilities"],
        result.get("context_metrics"),
        [(m.type, str(m.content)[:200]) for m in result["messages"]],
    )
    assert result["bqa_capabilities"]["loaded_skills"][first]["load_ref"] == "large"
    assert len(calls) == 1
    # Capture the actual serialized request produced after an empty response;
    # nudge still carries complete current rules and only current tool schemas.
    request = model.ainvoke.await_args_list[-2].args[0]
    text = "\n".join(str(m.content) for m in request)
    assert "NEEDED_COMPLETE_RULE" in text and "OLD_COMPLETE_RULE" not in text
    assert (
        result["context_metrics"]["input_tokens"]
        <= result["context_metrics"]["input_budget_tokens"]
    )


@pytest.mark.parametrize("reject_twice", [False, True])
async def test_compiled_overflow_retries_model_without_replaying_tool(
    controlled, monkeypatch, reject_twice
):
    """真实编译图在工具返回后的超窗中只重发模型，保留历史与工具结果。"""
    from basic_qa.context import preparation

    class ProviderOverflow(Exception):
        status_code = 400

    graph, model, calls = controlled
    summary = AsyncMock(return_value="受控历史摘要，原消息仍保留。")
    monkeypatch.setattr(preparation, "summarize_conversation_history", summary)
    failure = ProviderOverflow("maximum context length exceeded")
    model.ainvoke.side_effect = [
        call("load_skill", "load", {"skill_id": _SKILL, "expected_version": _VERSION}),
        call("demo", "query", {"city": "杭州"}),
        failure,
        failure if reject_twice else call("finish_planning", "finish", {}),
    ]
    history = [
        item
        for index in range(5)
        for item in (
            HumanMessage(content="已完成的历史查询。" * 40, id=f"h-{index}"),
            AIMessage(content="已完成的历史答案。" * 40, id=f"a-{index}"),
        )
    ]
    result = await graph.ainvoke(
        {"messages": [*history, HumanMessage(content="现在查询杭州", id="current")]},
        _CONFIG,
    )
    assert len(calls) == 1
    assert model.ainvoke.await_count == 4
    assert summary.await_count == 1
    assert result.get("summary_covered_message_id")
    assert {m.id for m in history} <= {m.id for m in result["messages"]}
    assert (
        sum(isinstance(m, ToolMessage) and m.name == "demo" for m in result["messages"])
        == 1
    )
    if reject_twice:
        assert "自动整理后模型上下文仍超限" in result["messages"][-1].content
    else:
        assert "杭州空气质量数据" in result["messages"][-1].content


async def test_search_then_load_without_catalog_disclosure_and_followup_reuses(
    controlled, monkeypatch, tmp_path
):
    from langgraph.checkpoint.memory import InMemorySaver

    from basic_qa.capabilities import skill_search
    from common.business_registry import BUSINESS_POLICY_VERSION, business_for_graph
    from common.capabilities import SkillCatalogItem
    from common.identity.runtime import RuntimeIdentity
    from common.skill_router import SkillSemanticRouter

    _, model, business_calls = controlled
    business = business_for_graph("basic-qa")
    identity = RuntimeIdentity(
        user_id="user",
        tenant_id="tenant",
        membership_id="membership",
        session_id="session",
        permissions=frozenset({business.permission}),
        business_graph="basic-qa",
        business_code=business.code,
        business_permission=business.permission,
        business_policy_version=BUSINESS_POLICY_VERSION,
    )
    items = [
        SkillCatalogItem(
            id=_SKILL if index == 0 else f"skill:basic-qa:unseen-{index}",
            name="test" if index == 0 else f"unseen-{index}",
            description=f"PRIVATE_CATALOG_DESCRIPTION_{index}",
            material_version=_VERSION,
            status="available",
            required_tool_ids=[],
        )
        for index in range(90)
    ]
    catalog_read = Mock(return_value=items)
    monkeypatch.setattr(skill_search, "list_graph_skills", catalog_read)
    monkeypatch.setattr(
        skill_search, "guard_runtime_call", AsyncMock(return_value=identity)
    )
    search = AsyncMock(
        return_value=[SimpleNamespace(name="test", similarity_score=0.9)]
    )
    monkeypatch.setattr(SkillSemanticRouter, "amatch", search)
    graph = builder.build_basic_qa_graph(
        skills_dir=tmp_path, name="basic-qa", checkpointer=InMemorySaver()
    )
    responses = [
        call("search_skills", "search", {"query": "杭州实时空气质量"}),
        call("load_skill", "load", {"skill_id": _SKILL, "expected_version": _VERSION}),
        call("demo", "query", {"city": "杭州"}),
        call("finish_planning", "finish", {}),
    ]

    async def decide(messages, *args, **kwargs):
        if len(responses) == 4:
            catalog_read.assert_not_called()
        return responses.pop(0)

    model.ainvoke.side_effect = decide
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="查询杭州空气质量")]}, _CONFIG
    )
    assert _SKILL in result["bqa_capabilities"]["active_skill_ids"]
    first_request = model.ainvoke.await_args_list[0].args[0]
    assert not any(
        "PRIVATE_CATALOG_DESCRIPTION_" in str(m.content) for m in first_request
    )
    assert not any(_SKILL in str(m.content) for m in first_request)
    after_search = model.ainvoke.await_args_list[1].args[0]
    assert any("PRIVATE_CATALOG_DESCRIPTION_0" in str(m.content) for m in after_search)
    assert not any(
        "PRIVATE_CATALOG_DESCRIPTION_89" in str(m.content) for m in after_search
    )
    after_load = model.ainvoke.await_args_list[2].args[0]
    assert _MATERIAL.rules in str(after_load[0].content)
    search.assert_awaited_once_with("杭州实时空气质量", strict=True)
    assert len(business_calls) == 1

    model.ainvoke.side_effect = [
        call("demo", "next-query", {"city": "湖州"}),
        call("finish_planning", "next-finish", {}),
    ]
    next_config = {
        "configurable": {
            **_CONFIG["configurable"],
            "ledger_attempt_id": "next-attempt",
            "ledger_submission_id": "next-submission",
            "capability_selection_id": "next-selection",
        }
    }
    await graph.ainvoke({"messages": [HumanMessage(content="那湖州呢")]}, next_config)
    assert len(business_calls) == 2
    assert search.await_count == 1
