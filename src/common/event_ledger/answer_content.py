"""确认终答的受控正文快照（Change 6 小扩展）.

只保存**用户可见终答文本**：不保存 reasoning、tool call、完整 state、草稿。
由 `lifecycle._on_graph_end` 在 `record_answer` 确认后调用；快照失败不改变
已有回答/终态事实，后台以 `capture_failed`/`not_collected` 如实展示。

表 `agent_answer_contents`（迁移 0024）：
- (tenant, answer, content_version) 唯一；
- 相同版本相同 hash 幂等；相同版本不同 hash 拒绝覆盖（记录安全诊断类别）；
- 删除墓碑优先：已删除会话拒绝晚到快照（不复活内容）；
- 单条最多 65536 字符，超长受控截断并标记，不宣称全文。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from typing import Any

from loguru import logger

from common.event_ledger.store import LedgerBackendUnavailable, LedgerScope, _tx

ANSWER_SNAPSHOT_MAX_CHARS = 65536
SUMMARY_MAX_CHARS = 200
DEFAULT_RETENTION_DAYS = 90


def _retention_days(tenant_id: str) -> int:
    """非法保留配置停止正文采集，不延长内容生命周期."""
    raw = os.environ.get("LEDGER_CONTENT_RETENTION_DAYS", "").strip()
    days = DEFAULT_RETENTION_DAYS
    if raw:
        try:
            candidate = int(raw)
            if not 1 <= candidate <= 3650:
                raise ValueError("retention out of range")
            days = candidate
        except ValueError as exc:
            raise ValueError("invalid content retention policy") from exc
    mapping_raw = os.environ.get("LEDGER_CONTENT_RETENTION_DAYS_BY_TENANT", "").strip()
    if mapping_raw:
        try:
            mapping = json.loads(mapping_raw)
            if not isinstance(mapping, dict):
                raise ValueError("invalid mapping")
            if tenant_id in mapping:
                candidate = mapping[tenant_id]
                if isinstance(candidate, bool) or not isinstance(candidate, int):
                    raise ValueError("invalid tenant retention")
                if not 1 <= candidate <= 3650:
                    raise ValueError("tenant retention out of range")
                days = candidate
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid tenant content retention policy") from exc
    return days


def _summary(text_value: str) -> str:
    compact = " ".join(text_value.split())
    return compact[:SUMMARY_MAX_CHARS]


async def capture_answer_content(
    scope: LedgerScope,
    *,
    attempt_id: str,
    request_id: str,
    answer_id: str,
    message_id: str | None,
    content_version: int,
    text_value: str | None,
    retention_days: int | None = None,
) -> dict[str, Any]:
    """持久化确认回答文本；返回 captured/replayed/conflict/empty 状态."""
    if not isinstance(text_value, str) or not text_value.strip():
        return {"captured": False, "reason": "empty"}
    # PostgreSQL text 不接受 NUL；只清理该控制字符，不改写正文语义。
    full_text = text_value.replace("\x00", "")
    truncated = len(full_text) > ANSWER_SNAPSHOT_MAX_CHARS
    stored = full_text[:ANSWER_SNAPSHOT_MAX_CHARS]
    digest = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    now = dt.datetime.now(dt.UTC)
    days = (
        retention_days
        if retention_days is not None
        else _retention_days(scope.tenant_id)
    )
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
        raise ValueError("invalid content retention policy")
    retention_until = now + dt.timedelta(days=days)
    async with _tx(scope) as conn:
        # 序列化本版本的采集与恢复，避免 ON CONFLICT 的 UPDATE RLS 对
        # 已捕获的不可变行造成重放失败；跨正文版本不共享锁。
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"answer-content:{scope.tenant_id}:{answer_id}:{content_version}",),
        )
        existing = await (
            await conn.execute(
                "SELECT content_sha256,capture_status FROM agent_answer_contents"
                " WHERE tenant_id=%s AND answer_id=%s AND content_version=%s",
                (scope.tenant_id, answer_id, content_version),
            )
        ).fetchone()
        if existing and existing["capture_status"] == "captured":
            same = existing["content_sha256"] == digest
            return {
                "captured": same,
                "replayed": same,
                "reason": "replayed" if same else "content_conflict",
                "answer_id": answer_id,
                "content_version": content_version,
            }
        cur = await conn.execute(
            "INSERT INTO agent_answer_contents"
            " (id, tenant_id, user_id, request_id, attempt_id, answer_id, message_id,"
            "  thread_id, content_version, content_sha256, content_text, summary,"
            "  total_chars, truncated, capture_status, captured_at, retention_until,"
            "  created_at)"
            " SELECT gen_random_uuid(), a.tenant_id, a.user_id, r.id, a.attempt_id, a.id,"
            "        a.message_id, r.thread_id, %(version)s, %(digest)s, %(text)s,"
            "        %(summary)s, %(total)s, %(truncated)s, 'captured', %(now)s,"
            "        LEAST(%(retention)s, a.confirmed_at + %(days)s * interval '1 day'), %(now)s"
            " FROM agent_answers a"
            " JOIN agent_run_attempts t ON t.id = a.attempt_id AND t.tenant_id = a.tenant_id"
            " JOIN agent_requests r ON r.id = t.request_id AND r.tenant_id = t.tenant_id"
            " WHERE a.id = %(answer)s AND a.tenant_id = %(tenant)s"
            "   AND a.confirmed = true AND a.partial = false"
            "   AND r.id = %(request)s"
            "   AND t.id = %(attempt)s AND a.content_version = %(version)s"
            "   AND a.confirmed_at + %(days)s * interval '1 day' > %(now)s"
            "   AND NOT EXISTS (SELECT 1 FROM agent_thread_deletions d"
            "                   WHERE d.tenant_id = r.tenant_id AND d.thread_id = r.thread_id)"
            " ON CONFLICT (tenant_id, answer_id, content_version) DO UPDATE"
            " SET content_sha256=EXCLUDED.content_sha256, content_text=EXCLUDED.content_text,"
            " summary=EXCLUDED.summary, total_chars=EXCLUDED.total_chars,"
            " truncated=EXCLUDED.truncated, capture_status='captured',"
            " captured_at=EXCLUDED.captured_at,"
            " retention_until=LEAST(agent_answer_contents.retention_until,EXCLUDED.retention_until)"
            " WHERE agent_answer_contents.capture_status='capture_failed'"
            " RETURNING id",
            {
                "version": content_version,
                "digest": digest,
                "text": stored,
                "summary": _summary(full_text),
                "total": len(full_text),
                "truncated": truncated,
                "now": now,
                "retention": retention_until,
                "days": days,
                "answer": answer_id,
                "tenant": scope.tenant_id,
                "request": request_id,
                "attempt": attempt_id,
            },
        )
        inserted = await cur.fetchone()
        if inserted is not None:
            return {
                "captured": True,
                "replayed": False,
                "answer_id": answer_id,
                "content_version": content_version,
                "content_sha256": digest,
                "truncated": truncated,
                "total_chars": len(full_text),
            }
        existing = await (
            await conn.execute(
                "SELECT content_sha256, capture_status FROM agent_answer_contents"
                " WHERE tenant_id = %(tenant)s AND answer_id = %(answer)s"
                "   AND content_version = %(version)s",
                {
                    "tenant": scope.tenant_id,
                    "answer": answer_id,
                    "version": content_version,
                },
            )
        ).fetchone()
        if existing is None:
            return {"captured": False, "reason": "answer_not_confirmed_or_deleted"}
        if existing["content_sha256"] == digest:
            return {
                "captured": True,
                "replayed": True,
                "answer_id": answer_id,
                "content_version": content_version,
                "content_sha256": digest,
            }
        # 相同版本不同正文：拒绝覆盖，只记录不含正文的安全诊断。
        logger.bind(event_ledger=True).warning(
            "answer content conflict: attempt={} answer={} version={} existing={} incoming={}",
            attempt_id,
            answer_id,
            content_version,
            str(existing["content_sha256"])[:12],
            digest[:12],
        )
        return {
            "captured": False,
            "reason": "content_conflict",
            "answer_id": answer_id,
            "content_version": content_version,
        }


async def mark_capture_failed(
    scope: LedgerScope,
    *,
    attempt_id: str,
    request_id: str,
    answer_id: str,
    content_version: int,
    reason: str,
) -> dict[str, Any]:
    """记录采集失败（不覆盖已成功快照；不保存失败时的正文）."""
    now = dt.datetime.now(dt.UTC)
    digest = hashlib.sha256(b"").hexdigest()
    async with _tx(scope) as conn:
        cur = await conn.execute(
            "INSERT INTO agent_answer_contents"
            " (id, tenant_id, user_id, request_id, attempt_id, answer_id, message_id,"
            "  thread_id, content_version, content_sha256, content_text, summary,"
            "  total_chars, truncated, capture_status, captured_at, retention_until,"
            "  created_at)"
            " SELECT gen_random_uuid(), a.tenant_id, a.user_id, r.id, a.attempt_id, a.id,"
            "        a.message_id, r.thread_id, %(version)s, %(digest)s, NULL, NULL,"
            "        0, false, 'capture_failed', %(now)s,"
            "        a.confirmed_at + %(days)s * interval '1 day', %(now)s"
            " FROM agent_answers a"
            " JOIN agent_run_attempts t ON t.id = a.attempt_id AND t.tenant_id = a.tenant_id"
            " JOIN agent_requests r ON r.id = t.request_id AND r.tenant_id = t.tenant_id"
            " WHERE a.id = %(answer)s AND a.tenant_id = %(tenant)s"
            "   AND a.confirmed = true AND a.partial = false"
            "   AND r.id = %(request)s AND t.id = %(attempt)s"
            "   AND a.content_version = %(version)s"
            "   AND NOT EXISTS (SELECT 1 FROM agent_thread_deletions d"
            "                   WHERE d.tenant_id = r.tenant_id AND d.thread_id = r.thread_id)"
            " ON CONFLICT (tenant_id, answer_id, content_version) DO NOTHING"
            " RETURNING id",
            {
                "version": content_version,
                "digest": digest,
                "now": now,
                "days": _retention_days(scope.tenant_id),
                "answer": answer_id,
                "tenant": scope.tenant_id,
                "request": request_id,
                "attempt": attempt_id,
            },
        )
        row = await cur.fetchone()
        if row is None:
            return {"recorded": False, "reason": "already_recorded_or_unavailable"}
        logger.bind(event_ledger=True).warning(
            "answer content capture failed: answer={} version={} reason={}",
            answer_id,
            content_version,
            reason,
        )
        return {
            "recorded": True,
            "answer_id": answer_id,
            "capture_status": "capture_failed",
        }


__all__ = [
    "ANSWER_SNAPSHOT_MAX_CHARS",
    "LedgerBackendUnavailable",
    "capture_answer_content",
    "mark_capture_failed",
]
