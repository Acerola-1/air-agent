"""Behavioral checks of the Basic QA-owned history projection."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from basic_qa.context.projection import prepare_model_context
from basic_qa.context.summarization import pending_history


def test_only_complete_watermark_replaces_history_and_preserves_input():
    messages = [
        HumanMessage(content="first", id="h1"),
        AIMessage(content="answer", id="a1"),
        HumanMessage(content="second", id="h2"),
        AIMessage(content="answer 2", id="a2"),
        HumanMessage(content="third", id="h3"),
    ]
    original = list(messages)
    assert (
        prepare_model_context(
            messages, summary="earlier", summary_covered_message_id="missing"
        )
        == original
    )
    assert (
        prepare_model_context(
            messages, summary="earlier", summary_covered_message_id="h2"
        )
        == original
    )
    projected = prepare_model_context(
        messages, summary="earlier", summary_covered_message_id="a1"
    )
    assert isinstance(projected[0], SystemMessage)
    assert projected[1:] == messages[2:]
    assert messages == original
    assert pending_history(messages, watermark="a1", keep_turns=1) == messages[2:4]
