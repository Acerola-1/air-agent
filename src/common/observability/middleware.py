"""观测采集中间件：deepagents 图（intelligent-tracing）.

wrap_model_call 在每次真实模型调用前把观测回调**原地**放进执行期 runtime
config（平台后台运行的 config 由库重建，auth 期注入的 Python 对象不跨进程
存活）。去重护栏保证主模型、子代理与汇总模型不重复计数；观测关闭或无可信
受理上下文时零开销跳过。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from common.observability.model_adapter import invocation_config, model_call_scope


class ObservabilityModelMiddleware(AgentMiddleware):  # type: ignore[misc]
    """deepagents 图的模型调用观测注入（wrap_model_call 收口）."""

    def __init__(self, *, role: str = "main") -> None:
        """固定当前图层角色，不依赖客户端或SDK命名猜测."""
        self.role = role

    async def awrap_model_call(
        self,
        request: Any,  # noqa: ANN401 - deepagents ModelRequest
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        """每次模型调用前注入观测回调（幂等）."""
        from common.observability.context import current_observation_config

        config = current_observation_config(getattr(request, "runtime", None))
        if not isinstance(config, dict):
            return await handler(request)
        from langchain_core.runnables.config import var_child_runnable_config

        with model_call_scope(config, role=self.role) as scope:
            token = var_child_runnable_config.set(
                invocation_config(
                    config, role=self.role, logical_call_id=scope.logical_call_id
                )
            )
            try:
                return await handler(request)
            finally:
                var_child_runnable_config.reset(token)

    async def awrap_tool_call(self, request, handler):
        """从实际文件读取返回记录Skill加载，采集不保存正文."""
        from common.observability.context import current_observation_config
        from common.observability.model_adapter import model_tool_scope

        config = current_observation_config(getattr(request, "runtime", None))
        with model_tool_scope(config, (request.tool_call or {}).get("id")):
            result = await handler(request)
        call = getattr(request, "tool_call", {}) or {}
        args = call.get("args") or {}
        path = str(args.get("file_path") or args.get("path") or "")
        if (
            isinstance(config, dict)
            and call.get("name") == "read_file"
            and path.startswith("/skills/")
            and path.endswith("/SKILL.md")
        ):
            from common.observability.skill_adapter import emit_skill_stage

            name = path.rsplit("/", 2)[-2]
            cfg = config.get("configurable") or {}
            source = (
                "manual"
                if cfg.get("capability_skill_mode") == "explicit"
                else "agent-read"
            )
            emit_skill_stage(config, name, "selected", source=source)
            body = getattr(result, "content", None)
            ok = (
                getattr(result, "status", None) != "error"
                and isinstance(body, str)
                and not body.lower().startswith("error")
            )
            emit_skill_stage(
                config,
                name,
                "loaded",
                status="succeeded" if ok else "failed",
                body=body if ok else None,
                source=source,
            )
        return result

    async def aafter_agent(self, state, runtime):
        """Agent完成时记录已加载Skill执行证据."""
        from common.observability.context import current_observation_config
        from common.observability.skill_adapter import record_loaded_execution

        config = current_observation_config(runtime)
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if (
            isinstance(config, dict)
            and last is not None
            and getattr(last, "type", None) == "ai"
            and not getattr(last, "tool_calls", None)
        ):
            record_loaded_execution(
                config, succeeded=bool(getattr(last, "content", None))
            )
        return None
