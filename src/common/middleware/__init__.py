"""DeepAgent 中间件包.

当前仅保留：
- TimeContextMiddleware: 当前时间上下文注入中间件

（历史中间件已随 DeepAgents 精简移除，相关能力待重新设计。）
"""

from __future__ import annotations

from common.middleware.time_context_middleware import TimeContextMiddleware

__all__ = [
    "TimeContextMiddleware",
]
