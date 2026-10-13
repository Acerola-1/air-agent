"""归档授权 scope v2：验证同源服务签发的租户/用户/线程归属（任务 6.1）.

v2 协议字段：`{v: 2, tenantId, userId, threadId, aud, exp}`，独立签名密钥
`ARCHIVE_SCOPE_KEYS`（JSON 数组，当前/上一密钥有界轮换；支持 kid），不再复用
浏览器会话密钥，也不接受 v1（旧 `{userId, threadId, exp}` 无租户维度）——
版本协议升级属 BREAKING，与统一身份切换同批发布。

客户端与模型都不能伪造：token 由同源 BFF 用服务端会话（admin API 已验）签发，
LangGraph 自定义归档路由与工具执行链各自独立校验（探针证实自定义 app 不走
平台认证中间件）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

ARCHIVE_AUDIENCE = "air-agent-archive"


def _scope_keys() -> list[tuple[str, str]]:
    raw = os.environ.get("ARCHIVE_SCOPE_KEYS", "").strip()
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1]
    if not raw:
        # 未配置：失败关闭（返回空列表，verify 侧直接拒绝）。签发侧必须配置。
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PermissionError("归档授权配置无效") from exc
    if not isinstance(parsed, list) or not all(
        isinstance(k, str) and k for k in parsed
    ):
        raise PermissionError("归档授权配置无效")
    return [(f"k{index + 1}", secret) for index, secret in enumerate(parsed)]


def verify_scope(token: str, thread_id: str) -> dict[str, Any]:
    """验证 v2 归档授权；任何异常都失败关闭."""
    keys = _scope_keys()
    if not keys or not token:
        raise PermissionError("缺少会话授权")
    try:
        body, signature = token.split(".")
        expected_any = [
            hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            for _kid, secret in keys
        ]
        if not any(
            hmac.compare_digest(expected, signature) for expected in expected_any
        ):
            raise ValueError("签名无效")
        scope = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if (
            not isinstance(scope, dict)
            or scope.get("v") != 2
            or scope.get("aud") != ARCHIVE_AUDIENCE
            or not scope.get("tenantId")
            or not scope.get("userId")
            or scope.get("threadId") != thread_id
            or float(scope["exp"]) <= time.time()
        ):
            raise ValueError("版本、归属、受众或有效期无效")
        return scope
    except (ValueError, KeyError, TypeError) as exc:
        raise PermissionError("会话授权无效或已过期") from exc


def sign_scope(tenant_id: str, user_id: str, thread_id: str, ttl: int = 3600) -> str:
    """签发 v2 scope（同源 BFF 使用；需要 ARCHIVE_SCOPE_KEYS 配置）."""
    keys = _scope_keys()
    if not keys:
        raise PermissionError("归档授权签名未配置")
    kid, secret = keys[0]
    body = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 2,
                    "kid": kid,
                    "tenantId": tenant_id,
                    "userId": user_id,
                    "threadId": thread_id,
                    "aud": ARCHIVE_AUDIENCE,
                    "exp": int(time.time()) + ttl,
                },
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def runtime_scope(run_config: dict[str, Any]) -> tuple[str, str, str, str]:
    """校验工具 runtime 的平台线程与服务端签发授权一致，返回四元组.

    与 LangGraph 平台注入的 `langgraph_auth_user` 交叉核对：scope 声明的
    tenant/user 必须与已认证运行身份一致，防止跨线程重放合法签名的 scope。
    """
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
    user = cfg.get("langgraph_auth_user")
    if user is not None:
        get = (
            user.get
            if isinstance(user, dict)
            else (lambda k, d=None: getattr(user, k, d))
        )
        if str(get("identity") or "") != str(scope["userId"]) or str(
            get("tenant_id") or ""
        ) != str(scope["tenantId"]):
            raise PermissionError("授权主体与认证身份不一致")
    return str(scope["tenantId"]), str(scope["userId"]), thread_id, run_id
