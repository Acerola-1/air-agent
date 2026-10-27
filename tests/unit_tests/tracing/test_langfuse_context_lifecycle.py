"""Users/sessions attribution must survive streaming without escaping its scope."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from langchain_core.runnables import RunnableLambda
from opentelemetry import context
from test_langfuse_trace_tree import run_config
from test_langfuse_trace_tree import sdk as sdk

from common.identity.langgraph_auth import _pin_langfuse_metadata
from common.langfuse_tracing import _AttributePropagatingRunnable
from common.observability.context import observation_context_from_config


@pytest.mark.anyio
async def test_repeated_factory_scopes_do_not_accumulate_context_variables(sdk):
    def count_scope_keys():
        return sum(key.name == "langfuse_run_scopes" for key in copy_context())

    before = count_scope_keys()
    for _ in range(20):
        wrapped = _AttributePropagatingRunnable(RunnableLambda(lambda value: value), {})
        async with wrapped:
            pass
    assert count_scope_keys() <= before + 1


@pytest.mark.parametrize("metadata_location", ["metadata", "config", "kwargs"])
def test_missing_trusted_account_drops_client_account(metadata_location):
    config = run_config()
    config["configurable"]["user_account"] = "forged-configurable"
    config["metadata"] = {"user_account": "forged-account"}
    value = {
        "metadata": {"user_account": "forged-account"},
        "config": {"metadata": {"user_account": "forged-account"}},
        "kwargs": {"config": config},
    }
    _pin_langfuse_metadata(
        value,
        graph_id="basic-qa",
        user_id="trusted-user",
        thread_id="thread",
        account="",
    )
    metadata = (
        value["metadata"]
        if metadata_location == "metadata"
        else value["config"]["metadata"]
        if metadata_location == "config"
        else config["metadata"]
    )
    assert "user_account" not in metadata
    assert "user_account" not in config["configurable"]
    # Even a caller that skipped admission cannot promote metadata into identity.
    config["metadata"]["user_account"] = "forged-again"
    assert observation_context_from_config(config).account == ""
    config["configurable"]["langgraph_auth_user"]["display_name"] = "trusted-account"
    assert observation_context_from_config(config).account == "trusted-account"


@pytest.mark.anyio
@pytest.mark.parametrize("events", [False, True])
async def test_stream_close_from_another_task_restores_caller(sdk, events):
    client, exporter = sdk
    token = context.attach(context.Context())
    try:

        async def produce(value):
            for _ in range(2):
                with client.start_as_current_observation(name="inside-stream"):
                    pass
                yield value

        wrapped = _AttributePropagatingRunnable(
            RunnableLambda(produce), {"user_id": "user-a", "session_id": "thread-a"}
        )
        source = (
            wrapped.astream_events("value", version="v2")
            if events
            else wrapped.astream("value")
        )
        await anext(source)
        await asyncio.create_task(source.aclose())
        with client.start_as_current_observation(name="unrelated-after-close"):
            pass
        client.flush()
        unrelated = next(
            s
            for s in exporter.get_finished_spans()
            if s.name == "unrelated-after-close"
        )
        assert unrelated.attributes.get("user.id") is None
        assert unrelated.attributes.get("session.id") is None
    finally:
        context.detach(token)


@pytest.mark.anyio
async def test_alternating_stream_tasks_attribute_every_piece(sdk):
    client, exporter = sdk

    async def produce(value):
        for _ in range(3):
            with client.start_as_current_observation(name="inside-stream"):
                pass
            yield value

    wrapped = _AttributePropagatingRunnable(
        RunnableLambda(produce), {"user_id": "user-a", "session_id": "thread-a"}
    )
    source = wrapped.astream("value")
    while True:
        try:
            await asyncio.create_task(anext(source))
        except StopAsyncIteration:
            break
    with client.start_as_current_observation(name="unrelated-after-stream"):
        pass
    client.flush()
    spans = exporter.get_finished_spans()
    inside = [s for s in spans if s.name == "inside-stream"]
    assert len(inside) == 3
    assert all(s.attributes.get("user.id") == "user-a" for s in inside)
    assert all(s.attributes.get("session.id") == "thread-a" for s in inside)
    assert spans[-1].attributes.get("user.id") is None


def test_sync_stream_closed_on_another_thread_restores_caller(sdk):
    client, exporter = sdk
    token = context.attach(context.Context())
    try:

        def produce(value):
            yield value
            yield value

        wrapped = _AttributePropagatingRunnable(
            RunnableLambda(produce), {"user_id": "user-a", "session_id": "thread-a"}
        )
        source = wrapped.stream("value")
        next(source)
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(source.close).result()
        with client.start_as_current_observation(name="unrelated-after-sync-close"):
            pass
        client.flush()
        span = next(
            s
            for s in exporter.get_finished_spans()
            if s.name == "unrelated-after-sync-close"
        )
        assert span.attributes.get("user.id") is None
        assert span.attributes.get("session.id") is None
    finally:
        context.detach(token)


@pytest.mark.anyio
async def test_cancelled_stream_closes_source_and_restores_context(sdk):
    client, exporter = sdk
    waiting, closed = asyncio.Event(), asyncio.Event()

    async def produce(value):
        try:
            yield value
            waiting.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    wrapped = _AttributePropagatingRunnable(
        RunnableLambda(produce), {"user_id": "user-a", "session_id": "thread-a"}
    )
    source = wrapped.astream("value")
    await anext(source)
    pending = asyncio.create_task(anext(source))
    await asyncio.wait_for(waiting.wait(), timeout=2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert closed.is_set()
    with client.start_as_current_observation(name="after-cancel"):
        pass
    client.flush()
    span = next(s for s in exporter.get_finished_spans() if s.name == "after-cancel")
    assert span.attributes.get("user.id") is None
    assert span.attributes.get("session.id") is None


@pytest.mark.anyio
async def test_protocol_stream_uses_the_same_lifecycle(sdk):
    client, exporter = sdk

    class ProtocolRunnable(RunnableLambda):
        async def astream_protocol_events(self, value, config=None):
            with client.start_as_current_observation(name="protocol-piece"):
                pass
            yield value
            yield value

    wrapped = _AttributePropagatingRunnable(
        ProtocolRunnable(lambda value: value),
        {"user_id": "user-a", "session_id": "thread-a"},
    )
    source = wrapped.astream_protocol_events("value")
    await asyncio.create_task(anext(source))
    await asyncio.create_task(source.aclose())
    with client.start_as_current_observation(name="after-protocol"):
        pass
    client.flush()
    spans = exporter.get_finished_spans()
    assert (
        next(s for s in spans if s.name == "protocol-piece").attributes["user.id"]
        == "user-a"
    )
    assert (
        next(s for s in spans if s.name == "after-protocol").attributes.get("user.id")
        is None
    )


@pytest.mark.anyio
async def test_concurrent_users_and_reused_native_context_do_not_share_attributes(sdk):
    client, exporter = sdk
    barrier = asyncio.Barrier(2)

    async def work(user):
        async def produce(value):
            await barrier.wait()
            for _ in range(2):
                with client.start_as_current_observation(name=f"inside-{user}"):
                    pass
                yield value

        wrapped = _AttributePropagatingRunnable(
            RunnableLambda(produce), {"user_id": user, "session_id": f"thread-{user}"}
        )
        async for _ in wrapped.astream("value"):
            pass
        with client.start_as_current_observation(name=f"after-{user}"):
            pass

    await asyncio.gather(work("a"), work("b"))
    # The same reusable wrapper may be entered by independent native run tasks.
    barrier = asyncio.Barrier(2)
    shared = _AttributePropagatingRunnable(
        RunnableLambda(lambda value: value),
        {"user_id": "shared", "session_id": "thread"},
    )

    async def scope():
        async with shared:
            await barrier.wait()
            await asyncio.sleep(0)
        with client.start_as_current_observation(name="after-native-scope"):
            pass

    await asyncio.gather(scope(), scope())
    client.flush()
    for span in exporter.get_finished_spans():
        if span.name.startswith("inside-"):
            user = span.name.removeprefix("inside-")
            assert span.attributes["user.id"] == user
            assert span.attributes["session.id"] == f"thread-{user}"
        else:
            assert span.attributes.get("user.id") is None, span.name
            assert span.attributes.get("session.id") is None, span.name


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_direct_deepagents_entry_attributes_all_observations(
    sdk, monkeypatch, streaming
):
    from test_langfuse_trace_tree import export_run

    config = run_config()
    user = config["configurable"]["langgraph_auth_user"]["identity"]
    thread = config["configurable"]["thread_id"]
    config["configurable"]["langgraph_auth_user"]["display_name"] = "trusted-account"
    trace_id = await export_run(
        monkeypatch, config, streaming=streaming, native_context=False
    )
    client, exporter = sdk
    with client.start_as_current_observation(name="unrelated-after-direct-run"):
        pass
    client.flush()
    graph_spans = [
        s
        for s in exporter.get_finished_spans()
        if format(s.context.trace_id, "032x") == trace_id
    ]
    # Turn routing adds its before-agent/tool/model spans to the same trace.
    assert len(graph_spans) == 69
    for span in graph_spans:
        assert span.attributes["user.id"] == user
        assert span.attributes["session.id"] == thread
        assert (
            span.attributes["langfuse.trace.metadata.user_account"] == "trusted-account"
        )
    after = next(
        s
        for s in exporter.get_finished_spans()
        if s.name == "unrelated-after-direct-run"
    )
    assert after.attributes.get("user.id") is None
    assert after.attributes.get("session.id") is None
