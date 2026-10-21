"""运营后台查询作用域、业务日窗口、过滤适用矩阵与游标。

设计（change add-agent-admin-console D3/D4）：

- 每次请求实时权限检查后再设置事务本地 GUC；平台身份不构成跨企业旁路
  （这里始终以 `platform=False` 设置业务 GUC）。
- 统计聚合需要读取全企业事实，因此 `agent:metrics:read` 也会开启
  `air_agent.agent_operations`，但**可识别用户/正文端点仍各自要求 ops/content**；
  GUC 只是 RLS 执行条件，不是路由授权的替代。
- 业务日窗口使用 Asia/Shanghai 半开区间；服务端回读 UTC 边界。
- 游标绑定企业、上下文版本、过滤指纹、排序与原始数据截止（as_of）；
  翻页期间新增数据不会改变已签发页的截止。

本模块不返回正文；正文模块单独设置 `answer_content` / `feedback_content` GUC。
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import TenantContext, apply_tenant_context
from app.services.identity import Identity
from app.services.ledger import LedgerError

CONSOLE_SCHEMA_VERSION = "admin-console-v1"
CONSOLE_METRIC_VERSION = "console-metrics-v1"
CONSOLE_TZ = "Asia/Shanghai"
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_WINDOW_DAYS = 90
CONTENT_PAGE_CHARS = 8000
ANSWER_SNAPSHOT_MAX_CHARS = 65536

WINDOW_KINDS = ("today", "last_7_days", "last_30_days", "custom")


def _cursor_key() -> bytes:
    secret = get_settings().internal_api_token
    if not secret:
        error = ConsoleError("后台游标签名未配置", code="cursor_signing_unavailable")
        error.status_code = 503
        raise error
    return hmac.digest(secret.encode(), b"air-agent.console.cursor.v2", "sha256")


class ConsoleError(LedgerError):
    """运营后台读模型错误；路由按 code 映射 400/403/404/422."""

    status_code = 400


class ConsoleForbidden(ConsoleError):
    status_code = 403


def forbidden(message: str, *, code: str = "permission_denied") -> ConsoleForbidden:
    return ConsoleForbidden(message, code=code)


@dataclass(frozen=True)
class ConsoleWindow:
    """规范化业务窗口（UTC 半开）+ 展示元数据."""

    kind: str
    start: dt.datetime
    end: dt.datetime
    as_of: dt.datetime
    timezone: str = CONSOLE_TZ
    partial_day: bool = False

    def meta(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "timezone": self.timezone,
            "kind": self.kind,
            "partial_day": self.partial_day,
        }


@dataclass(frozen=True)
class ConsoleCursor:
    tenant_id: str
    context_version: int
    filter_fingerprint: str
    sort: str
    as_of: dt.datetime
    start: dt.datetime
    end: dt.datetime
    key: str


def business_day_start(moment: dt.datetime) -> dt.datetime:
    zone = ZoneInfo(CONSOLE_TZ)
    local = moment.astimezone(zone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(dt.UTC)


def _aware(value: str, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConsoleError(f"{label} 时间格式非法", code="invalid_window") from exc
    if parsed.tzinfo is None:
        raise ConsoleError(f"{label} 必须带时区", code="invalid_window")
    return parsed.astimezone(dt.UTC)


def parse_window(
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    as_of: dt.datetime,
) -> ConsoleWindow:
    """规范化窗口：默认近 7 个业务日（含今日）；最长 90 天."""
    kind = (window or "last_7_days").strip()
    if kind not in WINDOW_KINDS:
        raise ConsoleError("不支持的窗口类型", code="invalid_window")
    today_start = business_day_start(as_of)
    if kind == "custom":
        if not start or not end:
            raise ConsoleError("custom 窗口必须同时提供 start 与 end", code="invalid_window")
        window_start = _aware(start, "start")
        window_end = _aware(end, "end")
        if window_start >= window_end:
            raise ConsoleError("start 必须早于 end", code="invalid_window")
        if window_end > as_of + dt.timedelta(minutes=5):
            raise ConsoleError("窗口结束时间不能晚于当前时间", code="invalid_window")
        if window_end - window_start > dt.timedelta(days=MAX_WINDOW_DAYS):
            raise ConsoleError(f"窗口最长 {MAX_WINDOW_DAYS} 天", code="invalid_window")
        partial = window_end > today_start
    else:
        if start or end:
            raise ConsoleError("预设窗口不接受 start/end", code="invalid_window")
        days = {"today": 1, "last_7_days": 7, "last_30_days": 30}[kind]
        window_start = today_start - dt.timedelta(days=days - 1)
        window_end = as_of
        partial = True
    return ConsoleWindow(
        kind=kind,
        start=window_start,
        end=window_end,
        as_of=as_of,
        partial_day=partial,
    )


def parse_limit(limit: int | None, *, default: int = DEFAULT_LIMIT) -> int:
    value = default if limit is None else int(limit)
    if value < 1 or value > MAX_LIMIT:
        raise ConsoleError(f"limit 必须在 1..{MAX_LIMIT}", code="invalid_limit")
    return value


def filter_fingerprint(filters: dict[str, Any], *, window_key: str) -> str:
    canonical = json.dumps(
        {"filters": filters, "window": window_key}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def window_key(window: str | None, start: str | None, end: str | None) -> str:
    return f"{window or 'last_7_days'}|{start or ''}|{end or ''}"


def encode_cursor(
    *,
    tenant_id: str,
    context_version: int,
    user_id: str | None = None,
    filter_key: str,
    sort: str,
    window: ConsoleWindow,
    key: str,
) -> str:
    payload = {
        "v": 1,
        "t": tenant_id,
        "cv": context_version,
        "u": user_id,
        "f": filter_key,
        "s": sort,
        "a": window.as_of.isoformat(),
        "ws": window.start.isoformat(),
        "we": window.end.isoformat(),
        "k": key,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    body = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
    key = _cursor_key()
    return body + "." + hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


def decode_cursor(
    cursor: str,
    *,
    tenant_id: str,
    context_version: int,
    user_id: str | None = None,
    filter_key: str,
    sort: str,
) -> ConsoleCursor:
    try:
        body, signature = cursor.rsplit(".", 1)
        key = _cursor_key()
        expected = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("signature")
        raw = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ConsoleError("游标非法", code="bad_cursor") from exc
    if not isinstance(raw, dict) or raw.get("v") != 1:
        raise ConsoleError("游标非法", code="bad_cursor")
    if raw.get("t") != tenant_id or raw.get("cv") != context_version or raw.get("u") != user_id:
        # 跨企业/旧上下文游标不返回任何数据，也不泄露原范围。
        raise ConsoleError("游标与当前企业上下文不匹配", code="bad_cursor")
    if raw.get("f") != filter_key or raw.get("s") != sort:
        raise ConsoleError("游标与查询条件不匹配", code="cursor_filter_mismatch")
    try:
        as_of = dt.datetime.fromisoformat(str(raw["a"]))
        window_start = dt.datetime.fromisoformat(str(raw["ws"]))
        window_end = dt.datetime.fromisoformat(str(raw["we"]))
        key = str(raw["k"])
    except (KeyError, ValueError) as exc:
        raise ConsoleError("游标非法", code="bad_cursor") from exc
    now = dt.datetime.now(dt.UTC)
    if (
        any(value.tzinfo is None for value in (as_of, window_start, window_end))
        or as_of > now + dt.timedelta(minutes=5)
        or now - as_of > dt.timedelta(hours=24)
        or not window_start < window_end <= as_of + dt.timedelta(minutes=5)
        or window_end - window_start > dt.timedelta(days=MAX_WINDOW_DAYS)
    ):
        raise ConsoleError("游标已过期或窗口非法", code="bad_cursor")
    return ConsoleCursor(
        tenant_id=tenant_id,
        context_version=context_version,
        filter_fingerprint=filter_key,
        sort=sort,
        as_of=as_of,
        start=window_start,
        end=window_end,
        key=key,
    )


def cursor_window(cursor: ConsoleCursor, kind: str) -> ConsoleWindow:
    """翻页沿用签发时的规范化窗口与数据截止（不重新按 now 计算）."""
    today_start = business_day_start(cursor.as_of)
    return ConsoleWindow(
        kind=kind,
        start=cursor.start,
        end=cursor.end,
        as_of=cursor.as_of,
        partial_day=cursor.end > today_start,
    )


def build_meta(
    *,
    identity: Identity,
    window: ConsoleWindow,
    filters: dict[str, Any],
    limitations: list[str] | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    return {
        "schema_version": CONSOLE_SCHEMA_VERSION,
        "metric_version": CONSOLE_METRIC_VERSION,
        "as_of": window.as_of.isoformat(),
        "window": window.meta(),
        "filters": filters,
        "scope": {
            "tenant_id": str(identity.tenant.id) if identity.tenant else None,
            "context_version": identity.context_version,
        },
        "projected_at": None,
        "data_lag_seconds": 0,
        "measurement_source": "direct_sql",
        "status": status,
        "limitations": limitations or [],
    }


async def scope_session(
    session: AsyncSession,
    identity: Identity,
    *,
    require_metrics: bool = False,
    require_ops: bool = False,
    require_content: bool = False,
    require_manage: bool = False,
) -> None:
    """权限检查 + 事务本地 GUC（平台旁路关闭）.

    统计端点会开启 `agent_operations`，因为聚合必须读取全企业事实；但
    可识别用户、运行明细、反馈处理仍必须显式 `require_ops`。正文端点必须
    `require_content`，不能只靠 ops 推断。
    """
    permissions = identity.permissions
    if require_metrics and "agent:metrics:read" not in permissions:
        raise forbidden("需要统计权限")
    if require_ops and "agent:operations:read" not in permissions:
        raise forbidden("需要企业运营明细权限")
    if require_content and "agent:content:read" not in permissions:
        raise forbidden("需要受控正文权限")
    if require_manage and "agent:feedback:manage" not in permissions:
        raise forbidden("需要反馈处理权限")
    if identity.tenant is None:
        raise forbidden("尚未选择企业", code="no_tenant_context")
    if identity.membership is None or identity.membership_status != "active":
        raise forbidden("成员身份无效", code="membership_inactive")
    if identity.tenant_status not in (None, "active"):
        raise forbidden("企业已停用", code="tenant_inactive")
    # 平台身份不在这条路径上打开平台 GUC：运营读取始终以企业 + 本人为边界。
    await apply_tenant_context(
        session,
        TenantContext(
            user_id=str(identity.user.id),
            tenant_id=str(identity.tenant.id),
            session_id=str(identity.session.id),
            platform=False,
        ),
    )
    if require_metrics or require_ops:
        await session.execute(text("SELECT set_config('air_agent.agent_operations', 'on', true)"))
    if require_content:
        await session.execute(text("SELECT set_config('air_agent.answer_content', 'on', true)"))
        await session.execute(text("SELECT set_config('air_agent.feedback_content', 'on', true)"))
    if require_manage:
        await session.execute(text("SELECT set_config('air_agent.feedback_manage', 'on', true)"))


def ensure_identifiable_access(identity: Identity, *, group_by: str | None = None) -> None:
    """任何按用户分组/过滤或返回可识别用户的查询都必须另有 ops."""
    if group_by == "user" and "agent:operations:read" not in identity.permissions:
        raise forbidden("按用户分组需要运营明细权限", code="user_scope_requires_operations")
    if "agent:operations:read" not in identity.permissions:
        raise forbidden("需要企业运营明细权限", code="user_scope_requires_operations")


def tenant_id_of(identity: Identity) -> str:
    if identity.tenant is None:
        raise forbidden("尚未选择企业", code="no_tenant_context")
    return str(identity.tenant.id)


def parse_uuid(value: str, *, label: str = "标识") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError) as exc:
        raise ConsoleError(f"{label}非法", code="invalid_id") from exc


def clamp_content_page(offset: int, limit: int) -> tuple[int, int]:
    if offset < 0:
        raise ConsoleError("offset 不能为负", code="invalid_offset")
    if limit < 1 or limit > CONTENT_PAGE_CHARS:
        raise ConsoleError(f"正文每页最多 {CONTENT_PAGE_CHARS} 字符", code="invalid_limit")
    return offset, limit


def content_page(
    *,
    text_value: str | None,
    offset: int,
    limit: int,
    status: str,
    **extras: Any,
) -> dict[str, Any]:
    """正文分页统一响应；不把全量正文塞入列表/URL."""
    offset, limit = clamp_content_page(offset, limit)
    if text_value is None:
        return {
            "status": status,
            "offset": offset,
            "next_offset": None,
            "total_chars": 0,
            "text": None,
            "content_type": "text/plain",
            "cache_control": "no-store",
            **extras,
        }
    total = len(text_value)
    page = text_value[offset : offset + limit]
    next_offset = offset + len(page)
    return {
        "status": status,
        "offset": offset,
        "next_offset": next_offset if next_offset < total else None,
        "total_chars": total,
        "text": page,
        "content_type": "text/plain",
        "cache_control": "no-store",
        **extras,
    }
