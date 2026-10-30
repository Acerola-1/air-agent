"""真实 MCP 发送边界：受控身份、冻结 schema 和服务端调用计数。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from basic_qa import tool_policy
from basic_qa.capabilities import mcp_catalog
from basic_qa.capabilities.state import empty_capabilities
from common import business_registry as registry
from common.business_registry import BusinessConnection
from common.mcp_authorization import McpAuthorizationError
from common.mcp_connections import reset_connections_cache
from common.mcp_scoped import ScopedMCPToolFactory, execute_mcp_tool, set_factory
from common.tool_execution import policy
from common.tool_execution.mcp import execute_mcp_tool_request
from tests.unit_tests.test_mcp_connection_execution import (
    SERVER_KEY,
    TOOLS,
    _configure,
    _identity,
    fx,
)
from tests.unit_tests.test_mcp_connection_execution import (
    _run_config as base_config,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def boundary(monkeypatch):
    server = fx.FixtureServer()
    await server.start()
    await server.reset()
    reset_connections_cache()
    monkeypatch.setenv("FIXTURE_TOK_A", "tok-tenant-a")
    _configure(monkeypatch)
    # This suite exercises MCP/IAM, not knowledge snapshots. Never query a
    # deployment database while validating its synthetic thread IDs.
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "common.knowledge.validation.validate_thread_knowledge",
        AsyncMock(return_value=None),
    )
    for key, value in {
        "AGENT_IDENTITY_BACKEND": "remote",
        "IAM_BASE_URL": f"http://127.0.0.1:{server.port}",
        "IAM_ISSUER": "synthetic-iam",
        "IAM_INTERNAL_CLIENT_ID": "fixture",
        "IAM_INTERNAL_CLIENT_SECRET": "fixture",
        "IAM_RUNTIME_AUDIENCE": "langgraph-runtime",
        "TENANT_ENFORCE_RUNTIME_IDENTITY": "true",
        "TENANT_BUSINESS_SNAPSHOT_ENFORCED": "true",
    }.items():
        monkeypatch.setenv(key, value)
    for graph in ("basic-qa", "data-analysis", "intelligent-tracing"):
        definition = replace(
            registry.BUSINESSES[graph],
            connections=(
                BusinessConnection(
                    server_key=SERVER_KEY, allowed_tools=frozenset(TOOLS)
                ),
            ),
        )
        monkeypatch.setitem(registry.BUSINESSES, graph, definition)
        monkeypatch.setitem(registry.BUSINESS_BY_GRAPH, graph, definition)
    monkeypatch.setattr(policy, "_POLICIES", {})
    policy.register_execution_policy("basic-qa", tool_policy.authorize_send)
    from data_analysis.tool_policy import authorize_send as data_authorize_send

    policy.register_execution_policy(
        "data-analysis", data_authorize_send, requires_request=False
    )
    policy.register_execution_policy(
        "intelligent-tracing", policy.admitted_business_policy, requires_request=False
    )
    from common.event_ledger import store

    snapshots = {
        graph: {
            "id": "selection",
            "selection_digest": "digest",
            "graph_id": graph,
            "attempt_id": f"attempt-{graph}",
            "submission_id": f"submission-{graph}",
            "membership_id": "m-a",
            "thread_id": "th",
            "mcp_mode": "auto",
            "tool_ids": [],
        }
        for graph in (
            "basic-qa",
            "data-analysis",
            "intelligent-tracing",
            "test-fixture",
        )
    }

    async def fetch(scope, *, attempt_id, submission_id):
        assert (scope.tenant_id, scope.user_id, scope.membership_id) == (
            _identity().tenant_id,
            "u-1",
            "m-a",
        )
        return next(
            (
                item.copy()
                for item in snapshots.values()
                if item["attempt_id"] == attempt_id
                and item["submission_id"] == submission_id
            ),
            None,
        )

    monkeypatch.setattr(store, "fetch_capability_selection_snapshot", fetch)
    fac = ScopedMCPToolFactory({SERVER_KEY: server.url})
    fac.test_snapshots = snapshots
    set_factory(fac)
    try:
        yield server, fac
    finally:
        set_factory(None)
        reset_connections_cache()
        await server.stop()


def _run_config(identity):
    cfg = base_config(identity)
    graph = identity.business_graph
    cfg["configurable"].update(
        {
            "ledger_attempt_id": f"attempt-{graph}",
            "ledger_submission_id": f"submission-{graph}",
            "capability_selection_id": "selection",
            "capability_selection_digest": "digest",
            "capability_mcp_mode": "auto",
            "capability_tool_ids": [],
        }
    )
    return cfg


def ident(graph="basic-qa"):
    permission = f"agent:business:{graph}:use"
    return replace(
        _identity(permissions=frozenset({permission}), verification_ref="ref-valid-1"),
        business_code=graph,
        business_permission=permission,
        business_graph=graph,
    )


async def request_for(boundary, monkeypatch, name="query_air"):
    server, fac = boundary
    identity = ident()
    cfg = _run_config(identity)
    tools = await fac.tools_for(identity, SERVER_KEY)
    fac.register_catalog(tools, server_key=SERVER_KEY)
    tool = next(t for t in tools if t.name == name)
    document = mcp_catalog.document_for(SERVER_KEY, tool)
    catalog = mcp_catalog.ToolCatalog(
        (document,),
        "fixture-scope",
        "fixture-version",
        identity.tenant_id,
        identity.user_id,
        identity.membership_id,
    )

    async def frozen_catalog(_config):
        return catalog

    monkeypatch.setattr(mcp_catalog, "resolve_tool_catalog", frozen_catalog)
    owner = {
        "tenant_id": identity.tenant_id,
        "user_id": identity.user_id,
        "thread_id": "th",
        "graph_id": "basic-qa",
    }
    caps = empty_capabilities(owner)
    caps["loaded_tools"][document.tool_id] = {
        "schema_version": document.schema_version,
        "catalog_scope": catalog.scope_key,
        "load_ref": "loaded",
    }
    caps["independent_active_tool_ids"] = [document.tool_id]
    call = {
        "name": name,
        "id": "call-1",
        "args": {"user_id": "synthetic", "region": "杭州"},
    }
    state = {
        "bqa_turn_key": "attempt-basic-qa",
        "bqa_capabilities": caps,
        "messages": [AIMessage(content="", id="message-1", tool_calls=[call])],
        "bqa_disclosure": {
            "model_message_id": "message-1",
            "tools": {document.tool_id: name},
            "schema_versions": {document.tool_id: document.schema_version},
        },
    }
    req = SimpleNamespace(
        tool_call=call,
        tool=SimpleNamespace(metadata={"is_mcp": True}),
        runtime=SimpleNamespace(config=cfg),
        state=state,
    )
    return req, document


async def test_s01_direct_helper_scope_and_schema(boundary, monkeypatch):
    server, _ = boundary
    req, doc = await request_for(boundary, monkeypatch)
    result = await execute_mcp_tool_request(req)
    assert result.status == "success"
    assert await server.call_count("subject-a") == 1
    mutations = [
        lambda: req.state.update(bqa_disclosure={}),
        lambda: setattr(req, "state", None),
        lambda: req.state["bqa_disclosure"].update(model_message_id="old"),
        lambda: req.state["bqa_disclosure"]["schema_versions"].update(
            {doc.tool_id: "stale"}
        ),
        lambda: req.tool_call["args"].update(region="forged"),
    ]
    from copy import deepcopy

    baseline_state, baseline_call = deepcopy(req.state), deepcopy(req.tool_call)
    for mutate in mutations:
        req.state, req.tool_call = deepcopy(baseline_state), deepcopy(baseline_call)
        mutate()
        result = await execute_mcp_tool_request(req)
        assert result.status == "error"
        assert await server.call_count("subject-a") == 1


async def test_s02_missing_context_and_s03_policy_failure(boundary, monkeypatch):
    server, fac = boundary
    identity = ident()
    cfg = _run_config(identity)
    before = await server.protocol_counts()
    for operation in (
        lambda: execute_mcp_tool(SERVER_KEY, "query_air", {}, cfg),
        lambda: fac.call(identity, SERVER_KEY, "query_air", {}, cfg),
    ):
        with pytest.raises(McpAuthorizationError):
            await operation()
    assert await server.protocol_counts() == before
    req, _ = await request_for(boundary, monkeypatch)
    monkeypatch.delitem(policy._POLICIES, "basic-qa")
    before = await server.protocol_counts()
    assert (await execute_mcp_tool_request(req)).status == "error"
    assert await server.protocol_counts() == before

    calls = []

    async def broken(*_args):
        calls.append(1)
        raise RuntimeError("policy failure")

    policy.register_execution_policy("basic-qa", broken)
    assert (await execute_mcp_tool_request(req)).status == "error"
    assert await server.call_count("subject-a") == 0
    from langgraph.config import var_child_runnable_config

    token = var_child_runnable_config.set(req.runtime.config)
    try:
        result = await tool_policy.basic_qa_tool_wrapper(req, lambda _: None)
    finally:
        var_child_runnable_config.reset(token)
    assert result.status == "error"
    assert len(calls) == 2  # helper once, wrapper once; policy failures do not retry
    assert await server.call_count("subject-a") == 0


async def test_s05_s07_async_window_and_mutation_sensitivity(boundary, monkeypatch):
    from common.observability import tool_adapter

    server, _ = boundary
    req, _ = await request_for(boundary, monkeypatch)
    assert tool_policy._tool_call_denial(req) is None
    original_observe = tool_adapter.observed_tool_call

    async def in_window(config, *, tool_name, service_key, invoke):
        req.state["bqa_disclosure"]["model_message_id"] = "wrong-message"
        return await original_observe(
            config, tool_name=tool_name, service_key=service_key, invoke=invoke
        )

    monkeypatch.setattr(tool_adapter, "observed_tool_call", in_window)
    assert (await execute_mcp_tool_request(req)).status == "error"
    assert await server.call_count("subject-a") == 0

    # 测试内仅移除最终检查，保留入口、注册与公共授权；同一反例必须击穿断言。
    req.state["bqa_disclosure"]["model_message_id"] = "message-1"
    assert tool_policy._tool_call_denial(req) is None

    async def disabled_final(*_args, **_kwargs):
        return None

    monkeypatch.setattr(policy, "authorize_send", disabled_final)
    assert (await execute_mcp_tool_request(req)).status == "success"
    with pytest.raises(AssertionError):
        assert await server.call_count("subject-a") == 0
    assert await server.call_count("subject-a") == 1


async def test_s06_other_graphs_and_fixture_policy(boundary, monkeypatch):
    server, _ = boundary
    from common.business_registry import BusinessDefinition
    from tests.unit_tests.test_mcp_connection_execution import BUSINESS, PERMISSION

    definition = BusinessDefinition(
        code=BUSINESS,
        name_zh="测试",
        permission=PERMISSION,
        graph=BUSINESS,
        connections=(
            BusinessConnection(server_key=SERVER_KEY, allowed_tools=frozenset(TOOLS)),
        ),
    )
    monkeypatch.setitem(registry.BUSINESSES, BUSINESS, definition)
    monkeypatch.setitem(registry.BUSINESS_BY_GRAPH, BUSINESS, definition)
    policy.register_execution_policy(
        BUSINESS, policy.admitted_business_policy, requires_request=False
    )
    identities = [
        ident("data-analysis"),
        ident("intelligent-tracing"),
        _identity(verification_ref="ref-valid-1"),
    ]
    for index, identity in enumerate(identities, 1):
        cfg = _run_config(identity)
        await execute_mcp_tool(
            SERVER_KEY, "query_air", {"user_id": "u", "region": "x"}, cfg
        )
        assert await server.call_count("subject-a") == index
        cfg["configurable"].update(
            capability_mcp_mode="allowlist", capability_tool_ids=[]
        )
        with pytest.raises(McpAuthorizationError):
            await execute_mcp_tool(SERVER_KEY, "query_air", {}, cfg)
        assert await server.call_count("subject-a") == index


@pytest.mark.parametrize("invalidated", ["dormant", "disclosure", "schema", "revoked"])
async def test_s04_real_http_failure_rechecks_before_retry(
    boundary, monkeypatch, invalidated
):
    from basic_qa import tool_runtime as results

    server, _ = boundary
    req, doc = await request_for(boundary, monkeypatch)
    await server.fail_next_http_call()
    real_sleep = asyncio.sleep
    backoffs = []

    async def during_backoff(delay, *args, **kwargs):
        if delay == 2.0:
            backoffs.append(delay)
            if invalidated == "dormant":
                req.state["bqa_capabilities"]["independent_active_tool_ids"] = []
            elif invalidated == "disclosure":
                req.state["bqa_disclosure"]["model_message_id"] = "old"
            elif invalidated == "revoked":
                await server.set_iam_state(active=False)
            else:
                req.state["bqa_disclosure"]["schema_versions"][doc.tool_id] = "stale"
            return
        await real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(results.asyncio, "sleep", during_backoff)

    async def unused_handler(_request):
        raise AssertionError("MCP must use the protected path")

    from langgraph.config import var_child_runnable_config

    token = var_child_runnable_config.set(req.runtime.config)
    try:
        output = await tool_policy.basic_qa_tool_wrapper(req, unused_handler)
    finally:
        var_child_runnable_config.reset(token)
    counts = await server.protocol_counts()
    assert backoffs == [2.0]
    assert counts["failed_503"] == 1
    assert await server.call_count("subject-a") == (
        1 if invalidated == "dormant" else 0
    )
    assert output.status == ("success" if invalidated == "dormant" else "error")


async def test_actual_send_args_must_equal_model_call(boundary, monkeypatch):
    server, fac = boundary
    req, _ = await request_for(boundary, monkeypatch)
    with policy.bind_tool_request(req):
        with pytest.raises(McpAuthorizationError):
            await fac.call(
                ident(),
                SERVER_KEY,
                "query_air",
                {**req.tool_call["args"], "region": "北京"},
                req.runtime.config,
            )
        assert await server.call_count("subject-a") == 0
        await fac.call(
            ident(), SERVER_KEY, "query_air", req.tool_call["args"], req.runtime.config
        )
    assert await server.call_count("subject-a") == 1


@pytest.mark.parametrize("graph", ["basic-qa", "data-analysis", "intelligent-tracing"])
async def test_ledger_selection_prevents_deleted_tampered_and_conflicting_claims(
    boundary, monkeypatch, graph
):
    from contextlib import nullcontext
    from copy import deepcopy

    server, fac = boundary
    req = None
    if graph == "basic-qa":
        req, _ = await request_for(boundary, monkeypatch)
        cfg = req.runtime.config
        args = req.tool_call["args"]
    else:
        cfg = _run_config(ident(graph))
        args = {"user_id": "u", "region": "x"}
    snapshot = fac.test_snapshots[graph]

    def context():
        return policy.bind_tool_request(req) if req else nullcontext()

    baseline = deepcopy(cfg)
    before_protocol = await server.protocol_counts()
    # 删除选择声明不能把可信空 allowlist 变为 auto。
    snapshot.update(mcp_mode="allowlist", tool_ids=[])
    del cfg["configurable"]["capability_mcp_mode"]
    del cfg["configurable"]["capability_tool_ids"]
    with context(), pytest.raises(McpAuthorizationError):
        await fac.call(ident(graph), SERVER_KEY, "query_air", args, cfg)
    assert await server.protocol_counts() == before_protocol
    # 显式伪造放大和 metadata 来源冲突也必须在发现前拒绝。
    cases = [
        {"configurable": {**baseline["configurable"], "capability_mcp_mode": "auto"}},
        {
            "configurable": {
                **baseline["configurable"],
                "capability_mcp_mode": "allowlist",
                "capability_tool_ids": ["mcp:ipp:query_air"],
            }
        },
        {
            "configurable": {
                **baseline["configurable"],
                "capability_mcp_mode": "allowlist",
            },
            "metadata": {"capability_mcp_mode": "auto"},
        },
        {
            "configurable": {
                **baseline["configurable"],
                "capability_selection_digest": "forged",
            }
        },
        {
            "configurable": {
                **baseline["configurable"],
                "ledger_submission_id": "missing",
            }
        },
    ]
    for candidate in cases:
        with context(), pytest.raises(McpAuthorizationError):
            await fac.call(ident(graph), SERVER_KEY, "query_air", args, candidate)
        assert await server.protocol_counts() == before_protocol
    snapshot.update(mcp_mode="auto", tool_ids=[], membership_id="other")
    with context(), pytest.raises(McpAuthorizationError):
        await fac.call(ident(graph), SERVER_KEY, "query_air", args, baseline)
    assert await server.protocol_counts() == before_protocol
    snapshot["membership_id"] = "m-a"
    snapshot["thread_id"] = "other"
    with context(), pytest.raises(McpAuthorizationError):
        await fac.call(ident(graph), SERVER_KEY, "query_air", args, baseline)
    assert await server.protocol_counts() == before_protocol
    snapshot.update(thread_id="th", mcp_mode="allowlist")
    # 同一可信快照允许该稳定 ID 时，合法上下文真实发送恰一次。
    snapshot["tool_ids"] = ["mcp:ipp:query_air"]
    cfg = deepcopy(baseline)
    cfg["configurable"].update(
        capability_mcp_mode="allowlist", capability_tool_ids=snapshot["tool_ids"]
    )
    if req:
        req.runtime.config = cfg
    with context():
        await fac.call(ident(graph), SERVER_KEY, "query_air", args, cfg)
    assert await server.call_count("subject-a") == 1


@pytest.mark.parametrize("graph", ["basic-qa", "data-analysis", "intelligent-tracing"])
async def test_ledger_selection_rechecked_after_async_window(
    boundary, monkeypatch, graph
):
    from contextlib import nullcontext

    from common.observability import tool_adapter

    server, fac = boundary
    req = None
    if graph == "basic-qa":
        req, _ = await request_for(boundary, monkeypatch)
        cfg, args = req.runtime.config, req.tool_call["args"]
    else:
        cfg, args = _run_config(ident(graph)), {"user_id": "u", "region": "x"}
    original = tool_adapter.observed_tool_call

    async def in_window(config, *, tool_name, service_key, invoke):
        fac.test_snapshots[graph].update(mcp_mode="allowlist", tool_ids=[])
        return await original(
            config, tool_name=tool_name, service_key=service_key, invoke=invoke
        )

    monkeypatch.setattr(tool_adapter, "observed_tool_call", in_window)
    with (
        policy.bind_tool_request(req) if req else nullcontext(),
        pytest.raises(McpAuthorizationError),
    ):
        await fac.call(ident(graph), SERVER_KEY, "query_air", args, cfg)
    assert await server.call_count("subject-a") == 0


async def test_selection_deleted_during_async_policy_blocks_transport(
    boundary, monkeypatch
):
    server, fac = boundary
    req, _ = await request_for(boundary, monkeypatch)
    original = tool_policy.authorize_send

    async def invalidate(*args):
        validator = await original(*args)
        fac.test_snapshots["basic-qa"]["id"] = "deleted"
        return validator

    monkeypatch.setitem(policy._POLICIES, "basic-qa", policy._Binding(invalidate, True))
    assert (await execute_mcp_tool_request(req)).status == "error"
    assert await server.call_count("subject-a") == 0
    fac.test_snapshots["basic-qa"]["id"] = "selection"
    monkeypatch.setitem(policy._POLICIES, "basic-qa", policy._Binding(original, True))
    assert (await execute_mcp_tool_request(req)).status == "success"
    assert await server.call_count("subject-a") == 1


async def test_request_policy_without_final_validator_fails_closed(
    boundary, monkeypatch
):
    server, _ = boundary
    req, _ = await request_for(boundary, monkeypatch)

    async def missing_validator(*_args):
        return None

    monkeypatch.setitem(
        policy._POLICIES, "basic-qa", policy._Binding(missing_validator, True)
    )
    assert (await execute_mcp_tool_request(req)).status == "error"
    assert await server.call_count("subject-a") == 0


@pytest.mark.parametrize("changed", ["activation", "disclosure", "args", "identity"])
async def test_state_change_during_final_selection_read_blocks_transport(
    boundary, monkeypatch, changed
):
    from common.event_ledger import store

    server, _ = boundary
    req, doc = await request_for(boundary, monkeypatch)
    original_fetch = store.fetch_capability_selection_snapshot
    reads = 0

    async def fetch(*args, **kwargs):
        nonlocal reads
        result = await original_fetch(*args, **kwargs)
        reads += 1
        if reads == 3:
            if changed == "activation":
                req.state["bqa_capabilities"]["independent_active_tool_ids"] = []
            elif changed == "disclosure":
                req.state["bqa_disclosure"]["schema_versions"][doc.tool_id] = "stale"
            elif changed == "args":
                req.tool_call["args"]["region"] = "北京"
            else:
                req.runtime.config["configurable"]["langgraph_auth_user"][
                    "membership_id"
                ] = "other"
        return result

    monkeypatch.setattr(store, "fetch_capability_selection_snapshot", fetch)
    result = await execute_mcp_tool_request(req)
    if changed == "activation":
        assert result.status == "success"
        assert reads >= 3
        assert await server.call_count("subject-a") == 1
    else:
        assert result.status == "error"
        assert reads == 3
        assert await server.call_count("subject-a") == 0


async def test_transport_retry_rechecks_selection(boundary, monkeypatch):
    from langgraph.config import var_child_runnable_config

    from basic_qa import tool_runtime as results

    server, fac = boundary
    req, _ = await request_for(boundary, monkeypatch)
    await server.fail_next_http_call()
    real_sleep = asyncio.sleep
    backoffs = []
    attempts = 0

    async def invalidate_on_retry(*args):
        nonlocal attempts
        validator = await tool_policy.authorize_send(*args)
        attempts += 1
        if attempts == 2:
            fac.test_snapshots["basic-qa"]["id"] = "deleted"
        return validator

    monkeypatch.setitem(
        policy._POLICIES, "basic-qa", policy._Binding(invalidate_on_retry, True)
    )

    async def during_backoff(delay, *args, **kwargs):
        if delay == 2.0:
            backoffs.append(delay)
            return
        await real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(results.asyncio, "sleep", during_backoff)
    token = var_child_runnable_config.set(req.runtime.config)
    try:
        result = await tool_policy.basic_qa_tool_wrapper(req, lambda _: None)
    finally:
        var_child_runnable_config.reset(token)
    assert result.status == "error"
    assert backoffs == [2.0]
    assert attempts == 2
    assert (await server.protocol_counts())["failed_503"] == 1
    assert await server.call_count("subject-a") == 0
    fac.test_snapshots["basic-qa"]["id"] = "selection"
    monkeypatch.setitem(
        policy._POLICIES, "basic-qa", policy._Binding(tool_policy.authorize_send, True)
    )
    assert (await execute_mcp_tool_request(req)).status == "success"
    assert await server.call_count("subject-a") == 1


async def test_disclosed_tool_executes_without_skill_or_load_records(
    boundary, monkeypatch
):
    from unittest.mock import AsyncMock

    from basic_qa.capabilities import runtime

    server, _ = boundary
    req, _ = await request_for(boundary, monkeypatch)
    lookup = AsyncMock(
        side_effect=AssertionError("execution must not read activation or Skill rules")
    )
    monkeypatch.setattr(runtime, "current_material", lookup)
    req.state.pop("bqa_capabilities")
    req.state.pop("bqa_turn_key")
    assert tool_policy._tool_call_denial(req) is None
    assert (await execute_mcp_tool_request(req)).status == "success"
    assert await server.call_count("subject-a") == 1
    # Unrelated Skill references cannot add an execution permission gate either.
    req.state["bqa_capabilities"] = {
        "active_skill_ids": ["unrelated-skill"],
        "loaded_tools": {},
    }
    assert (await execute_mcp_tool_request(req)).status == "success"
    assert await server.call_count("subject-a") == 2
    lookup.assert_not_awaited()
