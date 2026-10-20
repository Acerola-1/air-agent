"""Same-origin source proxy for verified knowledge citations.

The browser never receives a knowledge-service token, record id or URL. The
Agent resolves the citation from its protected store, re-checks the current
user/session/thread and obtains a fresh single-operation delegation for this
read. The fixed knowledge-service base URL comes from deployment config.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from common.identity.runtime import RuntimeIdentity
from common.knowledge.client import knowledge_api_url
from common.knowledge.delegation import KnowledgeDelegationError, issue_delegation

MAX_TEXT_PAGE = 8000
DEFAULT_DEADLINE_SECONDS = 20.0


class CitationSourceError(RuntimeError):
    """Source read failed; ``status`` is the HTTP status to return."""

    def __init__(self, status: int, message: str) -> None:
        """Store the HTTP status and a user-safe message."""
        super().__init__(message)
        self.status = status
        self.message = message


async def read_citation_source(
    identity: RuntimeIdentity,
    citation: dict[str, Any],
    *,
    kind: str,
    offset: int = 0,
    limit: int = MAX_TEXT_PAGE,
    range_header: str | None = None,
) -> tuple[bytes, str, dict[str, str], int]:
    """Read one authorized citation from the knowledge service.

    Returns ``(body, content_type, headers)``. Full text is never cached by the
    Agent: each page/PDF read re-issues a source/document delegation and RAG
    revalidates owner, permission and source snapshot.
    """
    if kind not in ("text", "pdf"):
        raise CitationSourceError(404, "未知的原文格式")
    operation = "document:read" if kind == "pdf" else "source:read"
    record_id = str(citation.get("record_id") or "")
    evidence_index = citation.get("evidence_index")
    if not record_id or not isinstance(evidence_index, int):
        raise CitationSourceError(410, "引用缺少可回读的来源映射")
    from common.knowledge.validation import KnowledgeValidationError, validate_citations

    try:
        await validate_citations(identity, [citation])
    except KnowledgeValidationError as exc:
        raise CitationSourceError(exc.status, str(exc)) from exc
    started = time.monotonic()
    budget = DEFAULT_DEADLINE_SECONDS
    try:
        delegation = await issue_delegation(identity, operation, timeout_seconds=budget)
    except KnowledgeDelegationError as exc:
        status = (
            403
            if exc.code == "permission_denied"
            else (
                409
                if exc.code == "context_changed"
                else (504 if exc.code == "timeout" else 503)
            )
        )
        raise CitationSourceError(status, "来源读取授权失败") from exc
    remaining_ms = max(1, int((budget - (time.monotonic() - started)) * 1000))
    if kind == "text":
        offset = max(int(offset), 0)
        limit = min(max(int(limit), 1), MAX_TEXT_PAGE)
        path = f"/api/source/{record_id}/{evidence_index}/text"
        params = {"offset": str(offset), "limit": str(limit)}
    else:
        path = f"/api/source/{record_id}/{evidence_index}/pdf"
        params = {}
    try:
        async with httpx.AsyncClient(
            base_url=knowledge_api_url(),
            timeout=httpx.Timeout(max(1.0, budget - (time.monotonic() - started))),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            headers = {
                "Authorization": f"Bearer {delegation.token}",
                "X-Knowledge-Remaining-Ms": str(remaining_ms),
            }
            if kind == "pdf" and range_header:
                headers["Range"] = range_header
            response = await client.get(
                path,
                params=params,
                headers=headers,
            )
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise CitationSourceError(504, "来源读取超时") from exc
    except httpx.HTTPError as exc:
        raise CitationSourceError(503, "知识服务暂不可用") from exc
    if response.status_code in (200, 206):
        content_type = (
            "application/pdf" if kind == "pdf" else "text/plain; charset=utf-8"
        )
        headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        }
        for name in (
            "X-Content-Offset",
            "X-Content-Next-Offset",
            "X-Content-Total-Chars",
            "Content-Range",
            "Accept-Ranges",
        ):
            if response.headers.get(name):
                headers[name] = response.headers[name]
        if kind == "pdf":
            headers["Content-Disposition"] = response.headers.get(
                "Content-Disposition", 'attachment; filename="source.pdf"'
            )
        return response.content, content_type, headers, response.status_code
    if response.status_code in (403, 404):
        raise CitationSourceError(410, "来源已失效或权限已变化，请重新检索")
    if response.status_code == 409:
        raise CitationSourceError(409, "企业或会话上下文已变化，请刷新后重试")
    if response.status_code == 504:
        raise CitationSourceError(504, "来源读取超时")
    if response.status_code == 416:
        raise CitationSourceError(416, "请求的文件范围无效")
    raise CitationSourceError(503, "知识服务暂不可用")


async def read_citation_with_budget(
    identity: RuntimeIdentity,
    citation: dict[str, Any],
    *,
    kind: str,
    offset: int,
    limit: int,
    range_header: str | None = None,
) -> tuple[bytes, str, dict[str, str], int]:
    """Total 20s budget wrapper (same bound as the tool)."""
    async with asyncio.timeout(DEFAULT_DEADLINE_SECONDS):
        return await read_citation_source(
            identity,
            citation,
            kind=kind,
            offset=offset,
            limit=limit,
            range_header=range_header,
        )
