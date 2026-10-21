"""需求洞察作用域、业务日窗口、游标与响应 meta.

与 6 的公共契约同语义，但本域独立实现（不导入 console 服务/内部 SQL）：
- 默认 last_7_days，today/last_7_days/last_30_days/custom，最长 90 天；
- 游标绑定 tenant、ctx 版本、过滤指纹、rank、分析版本、assignment revision、as_of；
- 每次请求实时权限检查后设置事务本地 GUC；平台身份不构成跨企业旁路。
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

from app.db import TenantContext, apply_tenant_context
from app.demand_insights.config import get_insights_settings
from app.demand_insights.errors import InsightsError, forbidden
from app.services.identity import Identity

INSIGHTS_SCHEMA_VERSION = "demand-insights-v1"
INSIGHTS_TZ = "Asia/Shanghai"
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_WINDOW_DAYS = 90
CONTENT_PAGE_CHARS = 8000
WINDOW_KINDS = ("today", "last_7_days", "last_30_days", "custom")
RANKS = ("intents", "users", "requests")
COMPARISON_STATUSES = ("comparable", "not_comparable", "not_requested", "unknown")


class InsightsBadCursor(InsightsError):
    status_code = 400


class InsightsWindowError(InsightsError):
    status_code = 422


@dataclass(frozen=True)
class InsightsWindow:
    kind: str
    start: dt.datetime
    end: dt.datetime
    as_of: dt.datetime
    timezone: str = INSIGHTS_TZ
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
class InsightsCursor:
    tenant_id: str
    context_version: int
    filter_fingerprint: str
    rank: str
    analysis_version: str
    assignment_revision: int
    as_of: dt.datetime
    start: dt.datetime
    end: dt.datetime
    key: str


def business_day_start(moment: dt.datetime) -> dt.datetime:
    zone = ZoneInfo(INSIGHTS_TZ)
    local = moment.astimezone(zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt.UTC)


def _aware(value: str, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InsightsWindowError(f"{label} 时间格式非法", code="invalid_window") from exc
    if parsed.tzinfo is None:
        raise InsightsWindowError(f"{label} 必须带时区", code="invalid_window")
    return parsed.astimezone(dt.UTC)


def parse_window(
    *, window: str | None, start: str | None, end: str | None, as_of: dt.datetime
) -> InsightsWindow:
    kind = (window or "last_7_days").strip()
    if kind not in WINDOW_KINDS:
        raise InsightsWindowError("不支持的窗口类型", code="invalid_window")
    today_start = business_day_start(as_of)
    if kind == "custom":
        if not start or not end:
            raise InsightsWindowError("custom 窗口必须同时提供 start 与 end", code="invalid_window")
        window_start = _aware(start, "start")
        window_end = _aware(end, "end")
        if window_start >= window_end:
            raise InsightsWindowError("start 必须早于 end", code="invalid_window")
        if window_end > as_of + dt.timedelta(minutes=5):
            raise InsightsWindowError("窗口结束时间不能晚于当前时间", code="invalid_window")
        if window_end - window_start > dt.timedelta(days=MAX_WINDOW_DAYS):
            raise InsightsWindowError(f"窗口最长 {MAX_WINDOW_DAYS} 天", code="invalid_window")
        partial = window_end > today_start
    else:
        if start or end:
            raise InsightsWindowError("预设窗口不接受 start/end", code="invalid_window")
        days = {"today": 1, "last_7_days": 7, "last_30_days": 30}[kind]
        window_start = today_start - dt.timedelta(days=days - 1)
        window_end = as_of
        partial = True
    return InsightsWindow(
        kind=kind, start=window_start, end=window_end, as_of=as_of, partial_day=partial
    )


def previous_window(window: InsightsWindow) -> InsightsWindow | None:
    """增长比较窗口：today 两边界各减 1 日；7/30 天整体平移；custom 不比较."""
    if window.kind == "custom":
        return None
    days = {"today": 1, "last_7_days": 7, "last_30_days": 30}[window.kind]
    return InsightsWindow(
        kind=window.kind,
        start=window.start - dt.timedelta(days=days),
        end=window.end - dt.timedelta(days=days),
        as_of=window.as_of - dt.timedelta(days=days),
        partial_day=window.partial_day,
    )


def filter_fingerprint(filters: dict[str, Any], *, window_key: str) -> str:
    canonical = json.dumps(
        {"filters": filters, "window": window_key}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def window_key(window: str | None, start: str | None, end: str | None) -> str:
    return f"{window or 'last_7_days'}|{start or ''}|{end or ''}"


def _cursor_key() -> bytes:
    secret = get_insights_settings().internal_cursor_secret
    if not secret:
        error = InsightsError("游标签名未配置", code="cursor_signing_unavailable")
        error.status_code = 503
        raise error
    return hmac.digest(secret.encode(), b"air-agent.insights.cursor.v1", "sha256")


def encode_cursor(
    *,
    tenant_id: str,
    context_version: int,
    filter_key: str,
    rank: str,
    analysis_version: str,
    assignment_revision: int,
    window: InsightsWindow,
    key: str,
) -> str:
    payload = {
        "v": 1,
        "t": tenant_id,
        "cv": context_version,
        "f": filter_key,
        "r": rank,
        "av": analysis_version,
        "ar": assignment_revision,
        "a": window.as_of.isoformat(),
        "ws": window.start.isoformat(),
        "we": window.end.isoformat(),
        "k": key,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    body = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
    return body + "." + hmac.new(_cursor_key(), body.encode(), hashlib.sha256).hexdigest()


def decode_cursor(
    cursor: str,
    *,
    tenant_id: str,
    context_version: int,
    filter_key: str,
    rank: str,
    analysis_version: str,
    assignment_revision: int,
) -> InsightsCursor:
    try:
        body, signature = cursor.rsplit(".", 1)
        expected = hmac.new(_cursor_key(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("signature")
        raw = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InsightsBadCursor("游标非法", code="bad_cursor") from exc
    if not isinstance(raw, dict) or raw.get("v") != 1:
        raise InsightsBadCursor("游标非法", code="bad_cursor")
    if raw.get("t") != tenant_id or raw.get("cv") != context_version:
        raise InsightsBadCursor("游标与当前企业上下文不匹配", code="bad_cursor")
    if (
        raw.get("f") != filter_key
        or raw.get("r") != rank
        or raw.get("av") != analysis_version
        or raw.get("ar") != assignment_revision
    ):
        raise InsightsBadCursor("游标与当前查询条件不匹配", code="cursor_filter_mismatch")
    try:
        as_of = dt.datetime.fromisoformat(str(raw["a"]))
        window_start = dt.datetime.fromisoformat(str(raw["ws"]))
        window_end = dt.datetime.fromisoformat(str(raw["we"]))
        key = str(raw["k"])
    except (KeyError, ValueError) as exc:
        raise InsightsBadCursor("游标非法", code="bad_cursor") from exc
    now = dt.datetime.now(dt.UTC)
    if (
        any(value.tzinfo is None for value in (as_of, window_start, window_end))
        or as_of > now + dt.timedelta(minutes=5)
        or now - as_of > dt.timedelta(hours=24)
        or not window_start < window_end <= as_of + dt.timedelta(minutes=5)
        or window_end - window_start > dt.timedelta(days=MAX_WINDOW_DAYS)
    ):
        raise InsightsBadCursor("游标已过期或窗口非法", code="bad_cursor")
    return InsightsCursor(
        tenant_id=tenant_id,
        context_version=context_version,
        filter_fingerprint=filter_key,
        rank=rank,
        analysis_version=analysis_version,
        assignment_revision=assignment_revision,
        as_of=as_of,
        start=window_start,
        end=window_end,
        key=key,
    )


def cursor_window(cursor: InsightsCursor, kind: str) -> InsightsWindow:
    today_start = business_day_start(cursor.as_of)
    return InsightsWindow(
        kind=kind,
        start=cursor.start,
        end=cursor.end,
        as_of=cursor.as_of,
        partial_day=cursor.end > today_start,
    )


async def scope_session(
    session: AsyncSession,
    identity: Identity,
    *,
    require_metrics: bool = False,
    require_ops: bool = False,
    require_content: bool = False,
    require_manage: bool = False,
) -> None:
    permissions = identity.permissions
    if require_metrics and "agent:metrics:read" not in permissions:
        raise forbidden("需要统计权限")
    if require_ops and "agent:operations:read" not in permissions:
        raise forbidden("需要企业运营明细权限")
    if require_content and "agent:content:read" not in permissions:
        raise forbidden("需要受控正文权限")
    if require_manage and "agent:insights:manage" not in permissions:
        raise forbidden("需要需求洞察管理权限")
    if identity.tenant is None:
        raise forbidden("尚未选择企业", code="no_tenant_context")
    if identity.membership is None or identity.membership_status != "active":
        raise forbidden("成员身份无效", code="membership_inactive")
    if identity.tenant_status not in (None, "active"):
        raise forbidden("企业已停用", code="tenant_inactive")
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
    if require_metrics:
        await session.execute(text("SELECT set_config('air_agent.insight_metrics', 'on', true)"))
    if require_ops:
        await session.execute(text("SELECT set_config('air_agent.insight_operations', 'on', true)"))
    if require_content:
        await session.execute(text("SELECT set_config('air_agent.insight_content', 'on', true)"))
    if require_manage:
        await session.execute(text("SELECT set_config('air_agent.insight_manage', 'on', true)"))


def require_write_permissions(identity: Identity) -> None:
    permissions = identity.permissions
    for code in ("agent:operations:read", "agent:content:read", "agent:insights:manage"):
        if code not in permissions:
            raise forbidden(f"缺少权限: {code}")


def tenant_id_of(identity: Identity) -> str:
    if identity.tenant is None:
        raise forbidden("尚未选择企业", code="no_tenant_context")
    return str(identity.tenant.id)


def parse_uuid(value: str, *, label: str = "标识") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError) as exc:
        raise InsightsError(f"{label}非法", code="invalid_id") from exc


def clamp_limit(limit: int) -> int:
    if limit < 1 or limit > MAX_LIMIT:
        raise InsightsError(f"limit 必须在 1..{MAX_LIMIT}", code="invalid_limit")
    return limit


def build_meta(
    *,
    identity: Identity,
    window: InsightsWindow,
    filters: dict[str, Any],
    analysis_version: str,
    assignment_revision: int,
    embedding_profile: str,
    profile_id: uuid.UUID | None,
    profile_state_revision: int,
    source_cutoff: dt.datetime | None,
    counts: dict[str, int | float | None],
    comparable: bool,
    limitations: list[str] | None = None,
    status: str = "ok",
) -> dict[str, Any]:
    return {
        "schema_version": INSIGHTS_SCHEMA_VERSION,
        "metric_version": "demand-insights-metrics-v1",
        "as_of": window.as_of.isoformat(),
        "window": window.meta(),
        "filters": filters,
        "scope": {
            "tenant_id": str(identity.tenant.id) if identity.tenant else None,
            "context_version": identity.context_version,
        },
        "projected_at": None,
        "data_lag_seconds": 0.0,
        "measurement_source": "direct_sql",
        "status": status,
        "limitations": limitations or [],
        "analysis_version": analysis_version,
        "assignment_revision": assignment_revision,
        "embedding_profile": embedding_profile,
        "profile_id": str(profile_id) if profile_id else None,
        "profile_state_revision": profile_state_revision,
        "source_cutoff": source_cutoff.isoformat() if source_cutoff else None,
        **counts,
        "comparable": comparable,
    }
