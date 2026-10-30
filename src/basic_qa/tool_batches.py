"""Basic QA 工具批次：拒绝冲突、串行加载、并发查询及合并写回."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from basic_qa.capabilities.runtime import (
    LOAD_TOOLS,
    reject_conflicting_batch,
    trusted_turn_key,
)
from basic_qa.capabilities.state import touch_executed
from basic_qa.state import BasicQAState
from basic_qa.tool_policy import basic_qa_tool_wrapper
from basic_qa.tool_recovery import control_budget_result, control_request_exhausted


def _updates(result: Any) -> list[dict[str, Any]]:
    """统一 ToolNode 的普通更新与 Command 更新，保留消息顺序."""
    return [
        output.update if isinstance(output, Command) else output
        for output in (result if isinstance(result, list) else [result])
    ]


class ToolBatchExecutor:
    """持有 ToolNode，并拥有本图批次状态写回流程."""

    def __init__(self, tools: list[Any]) -> None:
        """装配本图批次执行器，并记录实际使用的逐调用包装器."""
        self.tool_call_wrapper = basic_qa_tool_wrapper
        self._tool_node = ToolNode(
            tools=tools,
            handle_tool_errors=False,
            awrap_tool_call=self.tool_call_wrapper,
        )

    async def run_tools(self, state: BasicQAState, config: RunnableConfig) -> Any:
        """先核对整批调用，再选择串行加载或并行业务执行."""
        calls = state["messages"][-1].tool_calls
        rejected = self._reject_batch(state, calls)
        if rejected:
            return {"messages": rejected}
        if any(call.get("name") in LOAD_TOOLS for call in calls):
            return await self._run_load_batch(state, config, calls)
        result = await self._tool_node.ainvoke(state, config)
        return self._record_usage(result, state, config)

    @staticmethod
    def _reject_batch(state: BasicQAState, calls: list[Any]) -> list[ToolMessage]:
        rejected = reject_conflicting_batch(
            calls,
            disclosed_names=set(
                (state.get("bqa_disclosure") or {}).get("tools", {}).values()
            ),
        )
        if (
            not rejected
            and len(calls) > 1
            and any(call.get("name") == "finish_planning" for call in calls)
        ):
            return [
                ToolMessage(
                    content="结束声明必须单独调用。",
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="error",
                )
                for call in calls
            ]
        return rejected

    async def _run_load_batch(
        self, state: BasicQAState, config: RunnableConfig, calls: list[Any]
    ) -> dict[str, Any]:
        """加载逐项看到累计状态，完成后只提交一次合并更新."""
        working = dict(state)
        merged: dict[str, Any] = {"messages": []}
        for call in calls:
            if control_request_exhausted(
                [*state["messages"], *merged["messages"]], call
            ):
                merged["messages"].append(control_budget_result(call))
                continue
            single = state["messages"][-1].model_copy(update={"tool_calls": [call]})
            result = await self._tool_node.ainvoke(
                {**working, "messages": [*state["messages"][:-1], single]}, config
            )
            for values in _updates(result):
                for key, value in values.items():
                    if key == "messages":
                        merged[key].extend(value)
                    else:
                        merged[key] = value
                        working[key] = value
        return self._record_usage(merged, working, config)

    @staticmethod
    def _record_usage(
        result: Any, state: dict[str, Any], config: RunnableConfig
    ) -> Any:
        receipts = [
            message
            for update in _updates(result)
            for message in update.get("messages", [])
        ]
        attempted = {
            message.name
            for message in receipts
            if isinstance(message, ToolMessage)
            and message.additional_kwargs.get("network_attempted") is True
        }
        capabilities = touch_executed(state, attempted, trusted_turn_key(config))
        if capabilities is not None:
            if isinstance(result, dict):
                result["bqa_capabilities"] = capabilities
            else:
                result.append(Command(update={"bqa_capabilities": capabilities}))
        return result
