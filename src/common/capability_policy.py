"""公共目录依赖显式注入的宿主策略解析器，不加载业务模块."""

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

_PROVIDER: Callable[[str], Any] | None = None


def register_policy_provider(provider: Callable[[str], Any]) -> None:
    """仅在可信宿主装配阶段登记固定解析器."""
    global _PROVIDER
    if _PROVIDER is not None and _PROVIDER is not provider:
        raise RuntimeError("capability policy provider already registered")
    _PROVIDER = provider


def policy_for(graph_id: str) -> Any:
    """策略缺失或故障拒绝，不默认跳过业务约束."""
    if _PROVIDER is None:
        raise HTTPException(status_code=503, detail="业务策略未装配")
    try:
        return _PROVIDER(graph_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="业务策略不可用") from exc
