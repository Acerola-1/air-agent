"""图无关的工具调用／回执协议配对辅助."""

from collections.abc import Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage


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
