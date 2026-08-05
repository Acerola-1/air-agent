"""工具调用进度事件中间件."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, override

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.tools import BaseTool


@dataclass(frozen=True)
class ProgressSpec:
    """面向前端的进度事件配置."""

    node: str
    message: str


class ProgressToolName(StrEnum):
    """需要固定进度文案的系统/框架工具名."""

    FIND_SKILL = "find_skill"
    WRITE_TODOS = "write_todos"
    LS = "ls"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    EDIT_FILE = "edit_file"
    GLOB = "glob"
    GREP = "grep"
    EXECUTE = "execute"
    EVAL = "eval"
    TASK = "task"
    START_ASYNC_TASK = "start_async_task"
    CHECK_ASYNC_TASK = "check_async_task"
    UPDATE_ASYNC_TASK = "update_async_task"
    CANCEL_ASYNC_TASK = "cancel_async_task"
    LIST_ASYNC_TASKS = "list_async_tasks"
    COMPACT_CONVERSATION = "compact_conversation"
    GET_BEIJING_TIME = "get_beijing_time"
    HELPER_GET_LATEST_TIME = "helper_get_latest_time"
    PARSE_REGION = "parse_region_tool"
    WEB_SEARCH = "websearch_tool"


TOOL_PROGRESS_SPECS: Mapping[ProgressToolName, ProgressSpec] = {
    ProgressToolName.FIND_SKILL: ProgressSpec(
        node="skill_discovery",
        message="正在寻找可用技能...",
    ),
    ProgressToolName.WRITE_TODOS: ProgressSpec(
        node="planning",
        message="正在规划处理步骤...",
    ),
    ProgressToolName.LS: ProgressSpec(
        node="filesystem",
        message="正在浏览可用资料...",
    ),
    ProgressToolName.READ_FILE: ProgressSpec(
        node="filesystem",
        message="正在读取业务规则...",
    ),
    ProgressToolName.WRITE_FILE: ProgressSpec(
        node="filesystem",
        message="正在保存处理结果...",
    ),
    ProgressToolName.EDIT_FILE: ProgressSpec(
        node="filesystem",
        message="正在更新处理结果...",
    ),
    ProgressToolName.GLOB: ProgressSpec(
        node="filesystem",
        message="正在查找相关资料...",
    ),
    ProgressToolName.GREP: ProgressSpec(
        node="filesystem",
        message="正在检索相关内容...",
    ),
    ProgressToolName.EXECUTE: ProgressSpec(
        node="execution",
        message="正在执行辅助分析...",
    ),
    ProgressToolName.EVAL: ProgressSpec(
        node="execution",
        message="正在执行辅助分析...",
    ),
    ProgressToolName.TASK: ProgressSpec(
        node="task",
        message="正在拆解处理任务...",
    ),
    ProgressToolName.START_ASYNC_TASK: ProgressSpec(
        node="task",
        message="正在启动后台分析任务...",
    ),
    ProgressToolName.CHECK_ASYNC_TASK: ProgressSpec(
        node="task",
        message="正在检查后台任务进度...",
    ),
    ProgressToolName.UPDATE_ASYNC_TASK: ProgressSpec(
        node="task",
        message="正在更新后台任务...",
    ),
    ProgressToolName.CANCEL_ASYNC_TASK: ProgressSpec(
        node="task",
        message="正在取消后台任务...",
    ),
    ProgressToolName.LIST_ASYNC_TASKS: ProgressSpec(
        node="task",
        message="正在查看后台任务...",
    ),
    ProgressToolName.COMPACT_CONVERSATION: ProgressSpec(
        node="memory",
        message="正在整理上下文...",
    ),
    ProgressToolName.GET_BEIJING_TIME: ProgressSpec(
        node="time",
        message="正在确认数据时间...",
    ),
    ProgressToolName.HELPER_GET_LATEST_TIME: ProgressSpec(
        node="time",
        message="正在确认数据时间...",
    ),
    ProgressToolName.PARSE_REGION: ProgressSpec(
        node="parse",
        message="正在识别查询范围...",
    ),
}


class ToolProgressMiddleware(AgentMiddleware):
    """在工具调用前推送用户可读的 custom 进度事件.

    该中间件只负责可见进度，不修改 ToolMessage，不参与工具降级。
    未显式识别的工具统一归为数据查询阶段，避免维护海量工具名映射。
    """

    tools: Sequence[BaseTool] = ()

    _DEFAULT_SPEC = ProgressSpec(
        node="query",
        message="正在查询相关数据...",
    )

    _MCP_SPEC = ProgressSpec(
        node="mcp",
        message="正在查询相关数据...",
    )

    _KEYWORD_SPECS: tuple[tuple[tuple[str, ...], ProgressSpec], ...] = (
        (
            ("policy", "standard", "regulation"),
            ProgressSpec(node="search", message="正在查询相关政策..."),
        ),
        (
            ("retriever", "retrieve", "vector", "search"),
            ProgressSpec(node="search", message="正在检索相关资料..."),
        ),
        (
            ("weather", "meteorolog"),
            ProgressSpec(node="weather", message="正在查询气象数据..."),
        ),
        (
            ("chart", "plot", "visual"),
            ProgressSpec(node="visualization", message="正在生成可视化结果..."),
        ),
        (
            ("analysis", "analyze", "calculator", "calculate"),
            ProgressSpec(node="analysis", message="正在分析查询结果..."),
        ),
        (
            ("sql", "database", "db"),
            ProgressSpec(node="query", message="正在查询相关数据..."),
        ),
    )

    def __init__(
        self,
        exact_specs: Mapping[str | ProgressToolName, ProgressSpec] | None = None,
    ) -> None:
        """初始化工具进度映射.

        Args:
            exact_specs: 可选的项目级精确工具名覆盖映射。
        """
        self._exact_specs: dict[str, ProgressSpec] = {
            str(tool_name): spec for tool_name, spec in TOOL_PROGRESS_SPECS.items()
        }
        self._exact_specs.update(
            {str(tool_name): spec for tool_name, spec in (exact_specs or {}).items()}
        )

    def _spec_for_tool(self, tool_name: str) -> ProgressSpec:
        """根据工具名返回进度文案配置."""
        if not tool_name:
            return self._DEFAULT_SPEC

        exact = self._exact_specs.get(tool_name)
        if exact is not None:
            return exact

        normalized = tool_name.lower()
        if normalized.startswith("mcp_"):
            return self._MCP_SPEC

        for keywords, spec in self._KEYWORD_SPECS:
            if any(keyword in normalized for keyword in keywords):
                return spec

        return self._DEFAULT_SPEC

    def _push_progress(self, request: ToolCallRequest) -> None:
        """向 custom stream 推送进度事件."""
        writer = getattr(request.runtime, "stream_writer", None)
        if not callable(writer):
            return

        tool_name = str(request.tool_call.get("name") or "")
        spec = self._spec_for_tool(tool_name)
        writer(
            {
                "type": "progress",
                "node": spec.node,
                "name": tool_name,
                "message": spec.message,
            }
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        """同步工具调用前推送进度."""
        self._push_progress(request)
        return handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """异步工具调用前推送进度."""
        self._push_progress(request)
        return await handler(request)
