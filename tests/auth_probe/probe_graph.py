"""鉴权探针：最小图，供隔离实例 LANGSERVE_GRAPHS 使用。"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class ProbeState(TypedDict):
    messages: Annotated[list, add_messages]


async def echo_node(state: ProbeState):
    return {"messages": [{"role": "assistant", "content": "probe-ok"}]}


_builder = StateGraph(ProbeState)
_builder.add_node("echo", echo_node)
_builder.add_edge(START, "echo")
_builder.add_edge("echo", END)

graph = _builder.compile()
