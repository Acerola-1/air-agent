"""对已锁定DeepAgents的子代理/内置摘要显式接线，不替换其业务策略."""

from __future__ import annotations

from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from deepagents.middleware.summarization import (
    DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
    SummarizationMiddleware,
    compute_summarization_defaults,
)
from langchain_core.runnables.config import var_child_runnable_config

from common.observability.context import current_observation_config
from common.observability.middleware import ObservabilityModelMiddleware
from common.observability.model_adapter import invocation_config, model_call_scope


class ObservedSummarizationMiddleware(SummarizationMiddleware):
    """用SDK同名覆盖入口保留默认阈值/归档/重试，只补逐调用回调."""

    @property
    def name(self) -> str:
        """以官方同名slot替换，避免注册第二套摘要逻辑."""
        return "SummarizationMiddleware"

    def _create_summary(self, messages_to_summarize):
        config = current_observation_config()
        with model_call_scope(config, role="summarizer") as scope:
            token = var_child_runnable_config.set(
                invocation_config(
                    config, role="summarizer", logical_call_id=scope.logical_call_id
                )
            )
            try:
                return super()._create_summary(messages_to_summarize)
            finally:
                var_child_runnable_config.reset(token)

    async def _acreate_summary(self, messages_to_summarize):
        config = current_observation_config()
        with model_call_scope(config, role="summarizer") as scope:
            token = var_child_runnable_config.set(
                invocation_config(
                    config, role="summarizer", logical_call_id=scope.logical_call_id
                )
            )
            try:
                return await super()._acreate_summary(messages_to_summarize)
            finally:
                var_child_runnable_config.reset(token)


def observed_summarization(model, backend):
    """沿用SDK公开的模型自适应配置，只替换观测包装类."""
    defaults = compute_summarization_defaults(model)
    return ObservedSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=defaults["trigger"],
        keep=defaults["keep"],
        summary_prompt=DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
        truncate_args_settings=defaults["truncate_args_settings"],
    )


def observed_subagents(model, backend):
    """默认GP的提示/工具继承规则不变，显式携带守卫和观测."""
    from common.middleware.tenant_runtime_guard_middleware import (
        TenantRuntimeGuardMiddleware,
    )

    return [
        {
            **GENERAL_PURPOSE_SUBAGENT,
            "middleware": [
                TenantRuntimeGuardMiddleware(),
                ObservabilityModelMiddleware(role="subagent"),
                observed_summarization(model, backend),
            ],
        }
    ]
