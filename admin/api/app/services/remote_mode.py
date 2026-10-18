"""部署后端模式读取（admin/api 侧，与 src/common/identity/remote.py 语义一致）.

local/remote 是启动时显式选择；非法取值失败关闭（不是"回退 local"）。
两个进程树（admin/api 与 src）不共享 import，各自维护同语义实现。
"""

from __future__ import annotations

import os

_VALID = ("local", "remote")


class BackendConfigError(RuntimeError):
    """AGENT_IDENTITY_BACKEND 取值非法。调用方一律失败关闭。"""


def identity_backend_mode() -> str:
    raw = os.environ.get("AGENT_IDENTITY_BACKEND", "").strip().lower()
    if not raw:
        return "local"
    if raw not in _VALID:
        raise BackendConfigError(f"AGENT_IDENTITY_BACKEND 非法取值：{raw!r}（仅 local|remote）")
    return raw
