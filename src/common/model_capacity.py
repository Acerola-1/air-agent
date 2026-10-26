"""按实际模型通道解析上下文容量与摘要水位.

普通部署只需要主模型窗口和触发比例。输出预留、安全余量以及明确配置的
服务商输入上限仍是真实约束，但它们的职责与摘要水位分开，不能重新拼成
另一个人为的固定输入上限。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from common.config import config


class ModelCapacityError(ValueError):
    """模型容量配置缺失或无效."""


def _ratio(value: float | None = None) -> float:
    ratio = config.CONVERSATION_SUMMARY_TRIGGER_RATIO if value is None else value
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        raise ModelCapacityError("摘要触发比例必须是数值")
    if not math.isfinite(float(ratio)) or not 0 < float(ratio) < 1:
        raise ModelCapacityError("摘要触发比例必须在 0 与 1 之间且为有限数值")
    return float(ratio)


def _optional_limit(value: int | None) -> int | None:
    if value is None or value == 0:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ModelCapacityError("服务商输入上限必须是非负整数")
    return value


@dataclass(frozen=True)
class ModelCapacity:
    """一个实际模型通道的上下文容量描述."""

    role: str
    model_name: str
    channel: str
    window_tokens: int
    output_reserved_tokens: int
    safety_tokens: int
    provider_input_limit_tokens: int | None
    tokenizer_encoding: str
    trigger_ratio: float

    @property
    def trigger_tokens(self) -> int:
        """常规摘要水位：窗口 × 比例向下取整."""
        return math.floor(self.window_tokens * self.trigger_ratio)

    @property
    def input_limit_tokens(self) -> int:
        """有效输入边界：窗口扣除生成/安全预留，并遵守明确的服务商上限."""
        physical = self.window_tokens - self.output_reserved_tokens - self.safety_tokens
        if self.provider_input_limit_tokens is not None:
            return min(physical, self.provider_input_limit_tokens)
        return physical

    @property
    def input_budget_tokens(self) -> int:
        """兼容事件/快照字段名的有效输入边界."""
        return self.input_limit_tokens

    @property
    def fingerprint(self) -> str:
        """文件/快照可核对的配置指纹（不含密钥与地址凭据）."""
        material = "|".join(
            (
                self.role,
                self.model_name,
                self.channel,
                str(self.window_tokens),
                str(self.output_reserved_tokens),
                str(self.safety_tokens),
                str(self.provider_input_limit_tokens or ""),
                self.tokenizer_encoding,
                f"{self.trigger_ratio:.6f}",
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def validate(self) -> ModelCapacity:
        """拒绝无效窗口/预留组合，避免静默落回旧固定值."""
        if not isinstance(self.window_tokens, int) or isinstance(
            self.window_tokens, bool
        ):
            raise ModelCapacityError("模型窗口必须是正整数")
        if self.window_tokens <= 0:
            raise ModelCapacityError("模型窗口必须是正整数")
        for label, value in (
            ("生成预留", self.output_reserved_tokens),
            ("安全余量", self.safety_tokens),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ModelCapacityError(f"{label}必须是非负整数")
        _ratio(self.trigger_ratio)
        if self.input_limit_tokens <= 0:
            raise ModelCapacityError("模型窗口扣除生成预留和安全余量后没有可用输入容量")
        return self

    def metrics_fields(self) -> dict[str, Any]:
        """注入 context_metrics 的容量字段."""
        return {
            "model": self.model_name,
            "window_tokens": self.window_tokens,
            "input_budget_tokens": self.input_limit_tokens,
            "output_reserved_tokens": self.output_reserved_tokens,
            "safety_tokens": self.safety_tokens,
            "provider_input_limit_tokens": self.provider_input_limit_tokens,
            "summary_trigger_ratio": self.trigger_ratio,
            "summary_trigger_tokens": self.trigger_tokens,
            "capacity_fingerprint": self.fingerprint,
        }


def _normalise_channel(value: str) -> str:
    return str(value or "").rstrip("/")


def main_capacity() -> ModelCapacity:
    """主模型通道的部署容量."""
    return ModelCapacity(
        role="main",
        model_name=config.MAIN_LLM_MODEL,
        channel=_normalise_channel(config.MAIN_LLM_BASE_URL),
        window_tokens=config.MODEL_MAX_CONTEXT_TOKENS,
        output_reserved_tokens=config.MAIN_LLM_MAX_OUTPUT_TOKENS,
        safety_tokens=config.MODEL_CONTEXT_SAFETY_TOKENS,
        provider_input_limit_tokens=_optional_limit(
            getattr(config, "MODEL_PROVIDER_INPUT_LIMIT_TOKENS", 0)
        ),
        tokenizer_encoding=config.MODEL_TOKENIZER_ENCODING,
        trigger_ratio=_ratio(),
    ).validate()


def summary_capacity() -> ModelCapacity:
    """独立摘要通道的容量；窗口与分块保护不被主窗口放大."""
    return ModelCapacity(
        role="summary",
        model_name=config.SUMMARY_LLM or config.MAIN_LLM_MODEL,
        channel=_normalise_channel(config.SUMMARY_LLM_BASE_URL),
        window_tokens=config.SUMMARY_MODEL_CONTEXT_TOKENS,
        output_reserved_tokens=config.SUMMARY_MODEL_OUTPUT_TOKENS,
        safety_tokens=config.SUMMARY_MODEL_SAFETY_TOKENS,
        provider_input_limit_tokens=_optional_limit(
            getattr(config, "SUMMARY_MODEL_PROVIDER_INPUT_LIMIT_TOKENS", 0)
        ),
        tokenizer_encoding=config.SUMMARY_TOKENIZER_ENCODING,
        trigger_ratio=_ratio(),
    ).validate()


def capacity_for_role(role: str) -> ModelCapacity:
    """按调用角色返回主通道或摘要通道容量."""
    return summary_capacity() if role == "summary" else main_capacity()


def capacity_from_window(
    window_tokens: int,
    *,
    role: str,
    output_reserved_tokens: int,
    safety_tokens: int,
    provider_input_limit_tokens: int | None = None,
    trigger_ratio: float | None = None,
    model_name: str = "",
    channel: str = "",
    tokenizer_encoding: str = "",
) -> ModelCapacity:
    """构造显式容量（测试、受控百万窗口和已登记子任务使用）."""
    return ModelCapacity(
        role=role,
        model_name=model_name,
        channel=channel,
        window_tokens=window_tokens,
        output_reserved_tokens=output_reserved_tokens,
        safety_tokens=safety_tokens,
        provider_input_limit_tokens=_optional_limit(provider_input_limit_tokens),
        tokenizer_encoding=tokenizer_encoding,
        trigger_ratio=_ratio(trigger_ratio),
    ).validate()


def _model_identity(model: Any) -> tuple[str, str]:
    name = str(getattr(model, "model_name", None) or getattr(model, "model", "") or "")
    channel = str(
        getattr(model, "openai_api_base", None)
        or getattr(model, "base_url", None)
        or ""
    )
    return name, _normalise_channel(channel)


def capacity_for_model(model: Any, *, role: str = "main") -> ModelCapacity:
    """解析一个已绑定模型的容量，不按模型名称猜测.

    相同模型/通道继承部署描述；可信 SDK profile 可作为窗口来源；其他未登记
    模型必须由调用方显式传入容量，否则明确报配置缺失。
    """
    name, channel = _model_identity(model)
    for candidate in (main_capacity(), summary_capacity()):
        if name and name == candidate.model_name and channel == candidate.channel:
            if role == "summary":
                return summary_capacity()
            return candidate
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        window = profile.get("max_input_tokens")
        if isinstance(window, int) and not isinstance(window, bool) and window > 0:
            base = summary_capacity() if role == "summary" else main_capacity()
            return capacity_from_window(
                window,
                role=role,
                output_reserved_tokens=base.output_reserved_tokens,
                safety_tokens=base.safety_tokens,
                provider_input_limit_tokens=base.provider_input_limit_tokens,
                trigger_ratio=base.trigger_ratio,
                model_name=name or base.model_name,
                channel=channel or base.channel,
                tokenizer_encoding=base.tokenizer_encoding,
            )
    label = name or type(model).__name__
    raise ModelCapacityError(
        f"模型 {label}（角色 {role}）缺少受保护的容量配置；"
        "不同模型必须显式登记窗口，不能继承父图或按名称猜测。"
    )
