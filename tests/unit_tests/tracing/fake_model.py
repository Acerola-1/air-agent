"""Scripted chat models for G0 DeepAgents wiring tests."""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class ScriptedChatModel(BaseChatModel):
    """Return a fixed sequence of AIMessages and implement bind_tools."""

    responses: list[AIMessage]
    model_name: str = "scripted-tracing"
    _index: int = PrivateAttr(default=0)
    _bound_tools: dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "scripted-tracing"

    def _get_ls_params(self, **kwargs: Any) -> dict[str, Any]:
        return {"ls_provider": "openai", "ls_model_name": self.model_name}

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        """Record bound tool names/descriptions; calls are already scripted."""
        for tool in tools:
            name = getattr(tool, "name", None) or (
                tool.get("name") if isinstance(tool, dict) else None
            )
            description = getattr(tool, "description", None) or (
                tool.get("description") if isinstance(tool, dict) else None
            )
            if name:
                self._bound_tools[str(name)] = str(description or "")
        return self

    @property
    def bound_tools(self) -> dict[str, str]:
        """Return the union of tools passed to ``bind_tools``."""
        return dict(self._bound_tools)

    @property
    def calls(self) -> int:
        """Return the number of model invocations consumed."""
        return self._index

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if not self.responses:
            raise RuntimeError("ScriptedChatModel has no responses")
        response = self.responses[min(self._index, len(self.responses) - 1)]
        self._index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])
