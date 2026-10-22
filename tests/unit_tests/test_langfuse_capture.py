"""Full SDK and native OTel capture under the user's revised Langfuse policy."""

import json
import uuid

import pytest
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langfuse import Langfuse, get_client
from langfuse._client.span_processor import LangfuseSpanProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from common.langfuse_tracing import with_langfuse_tracing


@pytest.fixture
def full_capture(monkeypatch):
    for key, value in {
        "LANGFUSE_ENABLED": "true",
        "LANGFUSE_PUBLIC_KEY": f"pk-lf-capture-{uuid.uuid4().hex}",
        "LANGFUSE_SECRET_KEY": "sk-lf-fixture",
        "LANGFUSE_BASE_URL": "http://127.0.0.1:1",
    }.items():
        monkeypatch.setenv(key, value)
    exporter = InMemorySpanExporter()

    # Simulate a client previously initialized by prompt lookup with the old masks.
    def old_mask(**kwargs):
        raise AssertionError("legacy body mask must not be called")

    client = Langfuse(
        tracer_provider=TracerProvider(),
        span_exporter=exporter,
        mask=old_mask,
        mask_otel_spans=old_mask,
    )
    processors = (
        client._resources.tracer_provider._active_span_processor._span_processors
    )
    processor = next(p for p in processors if isinstance(p, LangfuseSpanProcessor))
    processor._aa_original_export_filter = processor._should_export_span
    processor._should_export_span = lambda span: False
    yield client, exporter, processor
    client.shutdown()


def test_full_graph_and_tool_payloads_survive_preexisting_sdk_masks(full_capture):
    client, exporter, processor = full_capture
    body = "完整开头" + "正文" * 4096 + "完整结尾"
    payload = {
        "messages": [{"role": "user", "content": body}],
        "nested": {"values": [1, 2, 3]},
    }

    @tool
    def echo_payload(content: str) -> dict:
        """Echo the complete synthetic tool payload."""
        return {"result": content, "rows": [{"value": 42}]}

    graph = with_langfuse_tracing(
        RunnableLambda(
            lambda value: echo_payload.invoke(
                {"content": value["messages"][0]["content"]}
            )
        )
    )
    result = graph.invoke(
        payload, {"metadata": {"capture_fixture": "FULL_METADATA_MARKER"}}
    )
    assert result["result"] == body
    client.flush()
    spans = exporter.get_finished_spans()
    root = next(s for s in spans if s.name == "RunnableLambda")
    tool_span = next(s for s in spans if s.name == "echo_payload")
    assert json.loads(root.attributes["langfuse.observation.input"]) == payload
    assert json.loads(root.attributes["langfuse.observation.output"]) == result
    assert (
        json.loads(tool_span.attributes["langfuse.observation.input"])["content"]
        == body
    )
    assert json.loads(tool_span.attributes["langfuse.observation.output"]) == result
    assert "FULL_METADATA_MARKER" in str(root.attributes)
    assert client._resources.mask is None
    assert client._resources.mask_otel_spans is None
    assert not hasattr(processor, "_aa_original_export_filter")


def test_tool_error_keeps_actual_error_content(full_capture):
    client, exporter, _ = full_capture

    @tool
    def fail_tool(value: str) -> str:
        """Raise a controlled tool exception."""
        raise ValueError(f"FULL_TOOL_ERROR_MARKER:{value}")

    traced = with_langfuse_tracing(fail_tool)
    with pytest.raises(ValueError, match="FULL_TOOL_ERROR_MARKER"):
        traced.invoke({"value": "完整错误上下文"})
    client.flush()
    span = next(s for s in exporter.get_finished_spans() if s.name == "fail_tool")
    assert (
        "FULL_TOOL_ERROR_MARKER:完整错误上下文"
        in span.attributes["langfuse.observation.status_message"]
    )


def test_native_otel_exception_span_is_not_dropped(full_capture):
    client, exporter, _ = full_capture
    with_langfuse_tracing(RunnableLambda(lambda value: value))
    current_client = get_client(public_key=client._resources.public_key)
    with pytest.raises(RuntimeError, match="NATIVE_ERROR_MARKER"):
        with current_client.start_as_current_observation(
            name="native-error", input={"native": "NATIVE_INPUT_MARKER"}
        ):
            raise RuntimeError("NATIVE_ERROR_MARKER")
    client.flush()
    span = next(s for s in exporter.get_finished_spans() if s.name == "native-error")
    assert "NATIVE_INPUT_MARKER" in span.attributes["langfuse.observation.input"]
    assert span.events
    assert "NATIVE_ERROR_MARKER" in str(span.events[0].attributes)


def test_chatopenai_records_system_user_and_assistant_messages(full_capture):
    import httpx
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    client, exporter, _ = full_capture

    def respond(request):
        request_body = json.loads(request.content)
        assert request_body["messages"][0]["content"] == "FULL_SYSTEM_MARKER"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "capture-fixture",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "FULL_ASSISTANT_MARKER",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        model = ChatOpenAI(
            model="capture-fixture",
            api_key="fixture",
            base_url="http://model.invalid/v1",
            http_client=http_client,
            max_retries=0,
        )
        traced = with_langfuse_tracing(model)
        response = traced.invoke(
            [
                SystemMessage(content="FULL_SYSTEM_MARKER"),
                HumanMessage(content="FULL_USER_MARKER"),
            ]
        )
        assert response.content == "FULL_ASSISTANT_MARKER"
    client.flush()
    span = next(s for s in exporter.get_finished_spans() if s.name == "ChatOpenAI")
    messages = json.loads(span.attributes["langfuse.observation.input"])
    assert {m["role"]: m["content"] for m in messages} == {
        "system": "FULL_SYSTEM_MARKER",
        "user": "FULL_USER_MARKER",
    }
    output = json.loads(span.attributes["langfuse.observation.output"])
    assert output["content"] == "FULL_ASSISTANT_MARKER"
