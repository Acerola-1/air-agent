"""从 Langfuse 拉取提示词，失败时回落到代码内默认值.

设计约束（对应 Langfuse Prompt Management 的真实语义）：

- **拉取模型**：Langfuse 不会向运行中的应用推送变更；SDK 按 TTL 在后台重新拉取。
  必须由本模块在请求路径上主动调用，远程改动才会生效。
- **绝不阻断业务**：冷启动（缓存空）时 Langfuse 不可达会让 ``get_prompt`` 抛错，
  故每次调用都必须给 ``fallback``，拿不到就用代码内默认值继续跑。
- **失败短路**：SDK 的 fallback 结果**不写入自身缓存**，若不额外处理，Langfuse
  长时间不可用时每个请求都会重试网络（默认 5s 超时）。这里对降级结果加进程内
  短时短路，避免把提示词服务故障放大成业务延迟。
- **成功路径交给 SDK 缓存**：远程值命中的缓存由 SDK 自行维护（含过期后台刷新），
  本模块不重复缓存，以免延长热切换生效时间。

远程提示词命名约定：``air-agent/<graph>/<用途>``，与代码内常量一一对应。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from loguru import logger

# 远程拉取的默认缓存 TTL（秒）。过期后由 SDK 在后台刷新，
# 期间继续以旧值提供服务，因此不会给单次请求带来额外延迟。
DEFAULT_CACHE_TTL_SECONDS = 60

# 降级结果的短路时长：Langfuse 不可用时，此窗口内直接返回代码内默认值，
# 不再对每个请求重试网络。窗口过后重新尝试，便于服务恢复后自动回归远程值。
_FALLBACK_HOLD_SECONDS = 30

# 每个提示词名只记录一次降级告警，避免刷屏。
_warned: set[str] = set()
_fallback_until: dict[str, float] = {}
_lock = threading.Lock()


def _langfuse_enabled() -> bool:
    """判断是否启用了 Langfuse 提示词管理."""
    if os.getenv("LANGFUSE_PROMPTS_ENABLED", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    required = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
    return all(os.getenv(name) for name in required)


def _warn_once(name: str, reason: str) -> None:
    with _lock:
        if name in _warned:
            return
        _warned.add(name)
    logger.warning("提示词 '{}' 使用代码内默认值（{}）", name, reason)


def _in_fallback_hold(name: str) -> bool:
    with _lock:
        until = _fallback_until.get(name)
        return until is not None and until > time.monotonic()


def _enter_fallback_hold(name: str) -> None:
    with _lock:
        _fallback_until[name] = time.monotonic() + _FALLBACK_HOLD_SECONDS


def clear_prompt_caches() -> None:
    """清空降级短路与告警状态（测试用）."""
    with _lock:
        _warned.clear()
        _fallback_until.clear()


def get_managed_prompt(
    name: str,
    fallback: str,
    *,
    prompt_type: str = "text",
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> str:
    """获取受管提示词；任何失败都回落到 ``fallback``.

    Args:
        name: Langfuse 中的提示词名（约定 ``air-agent/<graph>/<用途>``）。
        fallback: 代码内默认内容，Langfuse 未启用或不可用时原样返回。
        prompt_type: ``text`` 或 ``chat``；chat 类型会拼成单段文本返回。
        cache_ttl_seconds: SDK 缓存有效期，过期后后台刷新。

    Returns:
        提示词文本，保证非空（最差情况即 ``fallback``）。
    """
    if not _langfuse_enabled():
        return fallback
    if _in_fallback_hold(name):
        return fallback

    try:
        from langfuse import get_client

        prompt = get_client().get_prompt(
            name,
            type=prompt_type,  # type: ignore[arg-type]
            cache_ttl_seconds=cache_ttl_seconds,
            fallback=fallback,
        )
        if getattr(prompt, "is_fallback", False):
            _warn_once(name, "Langfuse 不可达或该提示词不存在")
            _enter_fallback_hold(name)
            return fallback
        text = (
            _chat_prompt_to_text(prompt) if prompt_type == "chat" else prompt.compile()
        )
        if not text or not str(text).strip():
            _warn_once(name, "远程内容为空")
            _enter_fallback_hold(name)
            return fallback
        return str(text)
    except Exception as exc:  # 兜底：任何异常都不允许影响主链路
        _warn_once(name, f"{type(exc).__name__}: {exc}")
        _enter_fallback_hold(name)
        return fallback


def _chat_prompt_to_text(prompt: Any) -> str:
    """把 chat 类型提示词拼成单段文本.

    本项目的系统提示词是单段文本；若远程以 chat 类型维护，则按顺序拼接各条消息，
    非 system 角色保留 ``[role]`` 前缀，便于在组装结果中辨认。
    """
    messages = prompt.compile()
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "system"))
        content = str(message.get("content", ""))
        parts.append(content if role == "system" else f"[{role}]\n{content}")
    return "\n\n".join(part for part in parts if part.strip())
