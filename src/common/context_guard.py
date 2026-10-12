"""只读投影成功摘要覆盖的历史；完整请求硬上限由 model_budget 校验."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

DEFAULT_TOOL_CLEAR_PLACEHOLDER = "[历史工具数据已归纳]"


def covered_index(messages: Sequence[AnyMessage], watermark: str | None) -> int:
    """水位必须位于完整轮次末尾；失效时保持历史."""
    if not watermark:
        return -1
    for index, message in enumerate(messages):
        if (
            message.id == watermark
            and index + 1 < len(messages)
            and isinstance(messages[index + 1], HumanMessage)
        ):
            return index
    return -1


def prepare_model_context(
    messages: Sequence[AnyMessage],
    *,
    summary: str | None = None,
    summary_covered_message_id: str | None = None,
    keep_turns: int | None = None,
    budget_tokens: int | None = None,
    placeholder: str = DEFAULT_TOOL_CLEAR_PLACEHOLDER,
) -> list[AnyMessage]:
    """无有效水位时保留全部；不随最新轮次数量猜测摘要覆盖范围."""
    index = covered_index(messages, summary_covered_message_id)
    if summary and summary.strip() and index >= 0:
        return _close_abandoned_calls(
            [
                SystemMessage(content=f"## 早期对话历史摘要\n{summary.strip()}"),
                *messages[index + 1 :],
            ]
        )
    return _close_abandoned_calls(messages)


def _close_abandoned_calls(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    result: list[AnyMessage] = []
    pending: dict[str, str] = {}
    for message in messages:
        if isinstance(message, HumanMessage) and pending:
            result.extend(
                ToolMessage(
                    content="前次运行未完成此工具调用，请重新获取资料，不据此给出统计。",
                    tool_call_id=call_id,
                    name=name,
                    status="error",
                )
                for call_id, name in pending.items()
            )
            pending.clear()
        if isinstance(message, AIMessage):
            pending.update(
                {
                    str(call["id"]): str(call["name"])
                    for call in message.tool_calls
                    if call.get("id")
                }
            )
        if isinstance(message, ToolMessage):
            pending.pop(message.tool_call_id, None)
        result.append(message)
    return result
