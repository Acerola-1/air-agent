"""Bounded HTTP client for the shared knowledge search tool.

The client only calls the protected Knowledge API at a deployment-fixed base
URL. It never accepts a model/browser supplied URL, tenant, workspace or
credential, performs no automatic retry, and never forwards the delegation to
the model. Every response is normalized against the frozen tool contract; an
unusable or partial upstream response is reported as such instead of being
silently promoted to "complete".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from typing import Any

import httpx

from common.identity.runtime import RuntimeIdentity, revalidate
from common.knowledge.contracts import (
    DEFAULT_DEADLINE_SECONDS,
    DEFAULT_TOP_K,
    MAX_EXCERPT_CHARS,
    MAX_FRAGMENT_CHARS,
    MAX_FRAGMENTS,
    MAX_QUERY_CHARS,
    MAX_REFERENCES,
    MAX_RESPONSE_BYTES,
    MAX_TOP_K,
    MAX_TOTAL_EXCERPT_CHARS,
    KnowledgeReference,
    SearchOutcome,
)
from common.knowledge.delegation import KnowledgeDelegationError, issue_delegation
from common.knowledge.store import register_citations

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LIBRARY_CODE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


def knowledge_api_url() -> str:
    """Deployment-fixed Knowledge API base URL; missing config fails closed."""
    value = (os.environ.get("KNOWLEDGE_API_URL") or "").strip().rstrip("/")
    if not value:
        raise KnowledgeDelegationError("unavailable", "知识服务地址未配置")
    if not value.startswith(("http://", "https://")):
        raise KnowledgeDelegationError("unavailable", "知识服务地址非法")
    return value


def _deadline_seconds() -> float:
    raw = (os.environ.get("KNOWLEDGE_DEADLINE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_DEADLINE_SECONDS
    try:
        value = float(raw)
    except ValueError as exc:
        raise KnowledgeDelegationError("unavailable", "知识检索总预算配置非法") from exc
    if not 1 <= value <= 20:
        raise KnowledgeDelegationError("unavailable", "知识检索总预算超出 1..20 秒")
    return value


def _normalized_libraries(codes: list[str] | None) -> list[str] | None:
    if codes is None:
        return None
    if not isinstance(codes, list) or not 1 <= len(codes) <= 10:
        raise ValueError("library_codes 必须是 1..10 个公开资料 code")
    seen: list[str] = []
    for code in codes:
        if not isinstance(code, str) or not _LIBRARY_CODE.fullmatch(code):
            raise ValueError("library_codes 含非法 code")
        if code not in seen:
            seen.append(code)
    if not seen:
        raise ValueError("library_codes 不能为空")
    return seen


def _outcome_for(status: str | None, reason: str | None, has_refs: bool) -> str:
    if status in ("retrieved", "complete"):
        return "ok" if has_refs else "evidence_unusable"
    if status == "missing":
        return "no_match" if reason == "empty_discovery" else "evidence_unusable"
    if status in ("service_failure",):
        return "unavailable"
    if status in ("partial", "ambiguous", "stale"):
        return "evidence_unusable"
    return "unknown"


def _http_outcome(status_code: int) -> str:
    if status_code in (401, 403):
        return "permission_denied"
    if status_code == 409:
        return "context_changed"
    if status_code == 404:
        return "unavailable"
    if status_code == 504:
        return "timeout"
    return "unavailable"


def _completeness(status: str | None) -> str:
    if status == "complete":
        return "complete"
    if status in ("retrieved", "partial", "ambiguous"):
        return "candidate_only"
    if status == "missing":
        return "incomplete"
    return "unknown"


async def search_knowledge(
    identity: RuntimeIdentity,
    *,
    query: str,
    library_codes: list[str] | None,
    top_k: int,
    thread_id: str = "",
    run_id: str = "",
    deadline_seconds: float | None = None,
) -> dict[str, Any]:
    """Run one logical search; returns the model-visible contract object."""
    started = time.monotonic()
    budget = _deadline_seconds() if deadline_seconds is None else deadline_seconds
    query = (query or "").strip()
    search_id = str(uuid.uuid4())
    receipt_id = str(uuid.uuid4())
    timing: dict[str, Any] = {}
    if not 1 <= len(query) <= MAX_QUERY_CHARS:
        raise ValueError("query 长度必须在 1..2000 字符")
    if (
        isinstance(top_k, bool)
        or not isinstance(top_k, int)
        or not 1 <= top_k <= MAX_TOP_K
    ):
        raise ValueError(f"top_k 必须在 1..{MAX_TOP_K}")
    libraries = _normalized_libraries(library_codes)
    top_k = min(top_k, MAX_TOP_K) if top_k else DEFAULT_TOP_K

    # Every send re-checks the live source identity before acquiring a token.
    await revalidate(identity)
    try:
        delegation = await issue_delegation(
            identity,
            "query",
            timeout_seconds=max(0.1, budget - (time.monotonic() - started)),
        )
    except KnowledgeDelegationError as exc:
        return SearchOutcome(
            search_id=search_id,
            receipt_id=receipt_id,
            outcome=exc.code,
            retrieval_status=None,
            retrieval_reason=None,
            completeness="unknown",
            timing=_timing(started),
        ).as_dict()
    timing["delegation_ms"] = _elapsed(started)

    body: dict[str, Any] = {
        "question": query,
        "route": "pg",
        "top_k": top_k,
        "expand": True,
        "no_record": False,
    }
    if libraries:
        body["workspaces"] = libraries
    else:
        body["workspace"] = "all"
    remaining_ms = max(1, int((budget - (time.monotonic() - started)) * 1000))
    request_started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=knowledge_api_url(),
            timeout=httpx.Timeout(max(0.1, budget - (time.monotonic() - started))),
            trust_env=False,
            headers={"accept": "application/json"},
        ) as client:
            async with client.stream(
                "POST",
                "/api/query",
                json=body,
                headers={
                    "Authorization": f"Bearer {delegation.token}",
                    "X-Knowledge-Remaining-Ms": str(remaining_ms),
                },
            ) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        return _outcome(
                            search_id,
                            receipt_id,
                            "evidence_unusable",
                            None,
                            None,
                            timing,
                            warnings=[("response_limit", "知识服务响应超过上限")],
                        )
    except (TimeoutError, httpx.TimeoutException):
        timing["http_ms"] = _elapsed(request_started)
        return _outcome(
            search_id,
            receipt_id,
            "timeout",
            None,
            None,
            timing,
            warnings=[("timeout", "检索超时")],
        )
    except httpx.HTTPError:
        timing["http_ms"] = _elapsed(request_started)
        return _outcome(
            search_id,
            receipt_id,
            "unavailable",
            None,
            None,
            timing,
            warnings=[("unavailable", "知识服务暂不可用")],
        )
    timing["http_ms"] = _elapsed(request_started)
    if response.status_code != 200:
        return _outcome(
            search_id,
            receipt_id,
            _http_outcome(response.status_code),
            None,
            None,
            timing,
            warnings=[("upstream", "知识服务未返回可用结果")],
        )
    try:
        payload = json.loads(raw)
    except ValueError:
        return _outcome(
            search_id,
            receipt_id,
            "evidence_unusable",
            None,
            None,
            timing,
            warnings=[("invalid_response", "知识服务响应格式非法")],
        )
    if not isinstance(payload, dict):
        return _outcome(
            search_id,
            receipt_id,
            "evidence_unusable",
            None,
            None,
            timing,
            warnings=[("invalid_response", "知识服务响应格式非法")],
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        return _outcome(
            search_id,
            receipt_id,
            "evidence_unusable",
            None,
            None,
            timing,
            warnings=[("invalid_response", "知识服务缺少检索结果")],
        )
    status = result.get("status") if isinstance(result.get("status"), str) else None
    reason = (
        result.get("retrieval_reason")
        if isinstance(result.get("retrieval_reason"), str)
        else None
    )
    # Unusable upstream evidence must never become model context, even when
    # individual rows still carry verified=true from an earlier retrieval.
    if status not in {"retrieved", "complete"}:
        return _outcome(
            search_id,
            receipt_id,
            _outcome_for(status, reason, False),
            status,
            reason,
            timing,
            completeness=_completeness(status),
        )
    workspaces = payload.get("workspaces")
    queried = [str(w) for w in workspaces] if isinstance(workspaces, list) else []
    record_id = payload.get("record_id")
    evidence = result.get("evidence")
    candidates = _select_references(
        evidence if isinstance(evidence, list) else [],
        record_id=str(record_id) if isinstance(record_id, str) else "",
        outcome=_outcome_for(status, reason, True),
    )
    truncated = bool(candidates.pop("truncated", False))
    items = candidates.pop("items", [])
    if not items:
        return _outcome(
            search_id,
            receipt_id,
            _outcome_for(status, reason, False),
            status,
            reason,
            timing,
            queried=queried,
            completeness=_completeness(status),
            truncated=truncated,
            warnings=[("no_verified_reference", "未取得可核验的引用片段")],
        )
    if not record_id:
        return _outcome(
            search_id,
            receipt_id,
            "evidence_unusable",
            status,
            reason,
            timing,
            queried=queried,
            completeness=_completeness(status),
            warnings=[("missing_record", "检索结果缺少可回读的记录标识")],
        )
    try:
        citation_ids = await register_citations(
            identity,
            thread_id=thread_id,
            run_id=run_id,
            record_id=str(record_id),
            query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest(),
            outcome=_outcome_for(status, reason, True),
            items=items,
        )
    except Exception:  # noqa: BLE001 - citation registration failure is fail-closed
        return _outcome(
            search_id,
            receipt_id,
            "unknown",
            status,
            reason,
            timing,
            queried=queried,
            completeness=_completeness(status),
            warnings=[("citation_store_unavailable", "引用登记不可用，未返回来源")],
        )
    await revalidate(identity)
    references = [
        KnowledgeReference(
            citation_id=citation_id,
            doc_name=str(item.get("doc_name") or "未命名文档"),
            verified=bool(item.get("verified")),
            excerpt=str(item.get("excerpt") or ""),
            location=item.get("location")
            if isinstance(item.get("location"), dict)
            else None,
            source_version=item.get("source_version"),
            source_sha256=str(item.get("source_sha256")),
        )
        for citation_id, item in zip(citation_ids, items, strict=False)
    ]
    fragments = [
        {"citation_id": citation_id, "content": item["content"]}
        for citation_id, item in zip(citation_ids, items, strict=True)
    ]
    return SearchOutcome(
        search_id=search_id,
        receipt_id=receipt_id,
        outcome=_outcome_for(status, reason, True),
        retrieval_status=status,
        retrieval_reason=reason,
        completeness=_completeness(status),
        queried_libraries=queried,
        references=references[:MAX_REFERENCES],
        fragments=fragments,
        truncated=truncated,
        limits={
            "top_k": top_k,
            "fragments": len(evidence) if isinstance(evidence, list) else 0,
            "max_references": MAX_REFERENCES,
            "excerpt_chars": MAX_EXCERPT_CHARS,
            "total_excerpt_chars": MAX_TOTAL_EXCERPT_CHARS,
        },
        warnings=[],
        timing={**timing, "total_ms": _elapsed(started)},
    ).as_dict()


def _select_references(
    evidence: list[Any], *, record_id: str, outcome: str
) -> dict[str, Any]:
    """Deduplicate by composite identity and cap excerpt/total lengths."""
    seen: set[tuple[str, str]] = set()
    selected: list[dict[str, Any]] = []
    total_chars = 0
    truncated = False
    for index, raw in enumerate(evidence[:MAX_FRAGMENTS]):
        if not isinstance(raw, dict) or raw.get("verified") is not True:
            continue
        digest = raw.get("source_digest")
        if not isinstance(digest, str) or not _HEX64.match(digest):
            truncated = True
            continue
        workspace = str(raw.get("workspace") or "")
        chunk_id = str(raw.get("chunk_id") or "")
        doc_id = str(raw.get("doc_id") or "")
        identity = (workspace, chunk_id or f"{doc_id}|{raw.get('kind') or ''}")
        if identity in seen:
            continue
        seen.add(identity)
        content = str(raw.get("content") or "")
        fragment = content[:MAX_FRAGMENT_CHARS]
        excerpt = fragment[:MAX_EXCERPT_CHARS]
        if len(content) > MAX_FRAGMENT_CHARS:
            truncated = True
        if total_chars + len(fragment) > MAX_TOTAL_EXCERPT_CHARS:
            truncated = True
            break
        total_chars += len(fragment)
        selected.append(
            {
                "evidence_index": index,
                "workspace": workspace,
                "doc_id": raw.get("doc_id"),
                "doc_name": raw.get("doc_name") or "",
                "chunk_id": raw.get("chunk_id"),
                "kind": raw.get("kind"),
                "source_version": raw.get("source_version"),
                "source_sha256": digest,
                "location": raw.get("location"),
                "verified": True,
                "excerpt": excerpt,
                "content": fragment,
            }
        )
        if len(selected) >= MAX_REFERENCES:
            if len(evidence) > index + 1:
                truncated = True
            break
    return {"items": selected, "truncated": truncated}


def _outcome(
    search_id: str,
    receipt_id: str,
    outcome: str,
    status: str | None,
    reason: str | None,
    timing: dict[str, Any],
    *,
    queried: list[str] | None = None,
    completeness: str = "unknown",
    truncated: bool = False,
    warnings: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    return SearchOutcome(
        search_id=search_id,
        receipt_id=receipt_id,
        outcome=outcome,
        retrieval_status=status,
        retrieval_reason=reason,
        completeness=completeness,
        queried_libraries=queried or [],
        truncated=truncated,
        warnings=[
            {"code": code, "message": message} for code, message in (warnings or [])
        ],
        timing={**timing, "total_ms": _elapsed_from_timing(timing)},
    ).as_dict()


def _timing(started: float) -> dict[str, Any]:
    return {"total_ms": int((time.monotonic() - started) * 1000)}


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _elapsed_from_timing(timing: dict[str, Any]) -> int:
    """Stage timings share one monotonic clock, so their sum is the total."""
    return int(
        sum(
            value
            for key, value in timing.items()
            if key.endswith("_ms") and isinstance(value, (int, float))
        )
    )
