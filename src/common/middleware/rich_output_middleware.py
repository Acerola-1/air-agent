"""用于附件事件流式推送的富输出中间件."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Sequence

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

VALID_DISPLAY_MODES = {"canvas", "inline", "modal", "sidebar", "float", "none"}


class RichOutputMiddleware(AgentMiddleware):
    """将 MCP 富输出推送到前端，并压缩工具上下文.

    主要协议是：
    `{"code": 200, "success": true, "data": {"rich_outputs": [...]}}`.
    富输出内容通过 `runtime.stream_writer` 发送；随后把原始 ToolMessage
    内容替换成简短成功摘要，避免大型图表、表格和文件载荷进入后续模型上下文。
    """

    tools: Sequence[BaseTool] = []

    def __init__(
        self,
        output_hints_cache: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """初始化中间件."""
        self._hints_cache: dict[str, dict[str, Any]] = output_hints_cache or {}

    def _get_output_hints(self, tool_name: str) -> dict[str, Any]:
        """返回某个工具的可选输出提示."""
        return self._hints_cache.get(tool_name, {})

    def _coerce_mcp_payload(self, value: Any) -> Any:
        """将常见 MCP ToolMessage 内容形态转换为 JSON 载荷."""
        if isinstance(value, list):
            text_parts = [
                item.get("text", "")
                for item in value
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            if text_parts:
                return self._coerce_mcp_payload("".join(text_parts))
            if len(value) == 1:
                return self._coerce_mcp_payload(value[0])
            return value

        if isinstance(value, dict):
            if "text" in value and isinstance(value["text"], str):
                return self._coerce_mcp_payload(value["text"])
            return value

        if not isinstance(value, str):
            return value

        text = value.strip()
        if not text:
            return text

        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return value

    def _is_success_payload(self, payload: Any) -> bool:
        """判断解析后的载荷是否为成功的 MCP 响应."""
        return (
            isinstance(payload, dict)
            and payload.get("success") is True
            and payload.get("code") == 200
        )

    def _has_render_payload(self, value: Any) -> bool:
        """判断附件项是否包含非空渲染数据."""
        return value not in (None, "", [], {})

    def _normalize_display_mode(self, value: Any, default: str) -> str:
        """返回受支持的展示模式，并在异常时使用兜底值."""
        if isinstance(value, str) and value in VALID_DISPLAY_MODES:
            return value
        return default if default in VALID_DISPLAY_MODES else "canvas"

    def _build_rich_output_events(
        self,
        *,
        raw_items: list[Any],
        tool_name: str,
        hints: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """将 MCP 富输出项转换为前端流式事件."""
        events: list[dict[str, Any]] = []
        for index, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                continue

            output_type = item.get("output_type")
            render_data = item.get("data")
            if not isinstance(output_type, str) or not output_type:
                continue
            if not self._has_render_payload(render_data):
                continue

            display_mode = self._normalize_display_mode(
                item.get("display_mode"),
                str(hints.get("display_mode") or "canvas"),
            )
            if display_mode == "none":
                continue

            title = item.get("title") or hints.get("title_template") or tool_name
            chart_subtype = (
                item.get("chart_subtype") or hints.get("chart_subtype") or ""
            )

            events.append(
                {
                    "node": "deepagent_executor",
                    "type": "rich_output",
                    "output_type": output_type,
                    "display_mode": display_mode,
                    "chart_subtype": chart_subtype,
                    "title": title,
                    "tool_name": tool_name,
                    "sequence": item.get("sequence", index),
                    "message": render_data,
                }
            )
        return events

    def _extract_events(
        self,
        *,
        payload: dict[str, Any],
        tool_name: str,
        hints: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """从受支持的 MCP 响应结构中提取前端事件."""
        raw_data = payload.get("data")
        if not isinstance(raw_data, dict):
            return []

        rich_outputs = raw_data.get("rich_outputs")
        if isinstance(rich_outputs, list) and rich_outputs:
            events = self._build_rich_output_events(
                raw_items=rich_outputs,
                tool_name=tool_name,
                hints=hints,
            )
            if events:
                return events

        return []

    def _compact_tool_message(
        self,
        result: ToolMessage,
        events: list[dict[str, Any]],
    ) -> ToolMessage:
        """用简洁上下文摘要替换大型工具载荷."""
        titles = [
            title
            for event in events
            if isinstance(title := event.get("title"), str) and title
        ]
        title_summary = "、".join(titles[:5])
        if len(titles) > 5:
            title_summary = f"{title_summary} 等"

        message = f"工具已生成 {len(events)} 个附件"
        if title_summary:
            message = f"{message}：{title_summary}"
        message = f"{message}。附件已发送给前端渲染，附件内容不进入对话上下文。"

        compact_payload = {
            "code": 200,
            "success": True,
            "message": message,
        }
        return result.model_copy(
            update={"content": json.dumps(compact_payload, ensure_ascii=False)}
        )

    def _handle_tool_result(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Any,
    ) -> ToolMessage | Any:
        """从 ToolMessage 推送富输出，并压缩其内容."""
        tool_name = request.tool_call.get("name", "")
        hints = self._get_output_hints(tool_name)
        if hints.get("display_mode") == "none":
            return result
        if not isinstance(result, ToolMessage):
            return result

        parsed = self._coerce_mcp_payload(getattr(result, "content", ""))
        if not self._is_success_payload(parsed):
            return result

        events = self._extract_events(
            payload=parsed,
            tool_name=tool_name,
            hints=hints,
        )
        if not events:
            return result

        writer = request.runtime.stream_writer
        for event in events:
            writer(event)

        return self._compact_tool_message(result, events)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        """处理同步工具结果."""
        result = handler(request)
        return self._handle_tool_result(request, result)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> ToolMessage | Any:
        """处理异步工具结果."""
        result = await handler(request)
        return self._handle_tool_result(request, result)
