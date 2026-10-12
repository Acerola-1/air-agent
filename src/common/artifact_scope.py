"""验证同源服务签发的用户／线程归属，不接受模型提供的任意用户 ID."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


def verify_scope(token: str, thread_id: str) -> dict[str, Any]:
    """验证带过期时间的 HMAC 线程授权."""
    secret = os.getenv("AUTH_SECRET", "")
    if not secret or not token:
        raise PermissionError("缺少会话授权")
    try:
        body, signature = token.split(".")
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("签名无效")
        scope = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if (
            not scope.get("userId")
            or scope.get("threadId") != thread_id
            or float(scope["exp"]) <= time.time()
        ):
            raise ValueError("归属或有效期无效")
        return scope
    except (ValueError, KeyError, TypeError) as exc:
        raise PermissionError("会话授权无效或已过期") from exc


def runtime_scope(run_config: dict[str, Any]) -> tuple[str, str, str]:
    """校验工具 runtime 的平台线程与服务端签发授权一致."""
    cfg = run_config.get("configurable", {}) or {}
    thread_id = str(cfg.get("thread_id") or "")
    scope = verify_scope(str(cfg.get("__artifact_scope") or ""), thread_id)
    run_id = str(
        (run_config.get("metadata", {}) or {}).get("run_id")
        or cfg.get("run_id")
        or cfg.get("langgraph_run_id")
        or cfg.get("processing_run_id")
        or ""
    )
    if not run_id:
        raise PermissionError("缺少运行身份")
    return str(scope["userId"]), thread_id, run_id
