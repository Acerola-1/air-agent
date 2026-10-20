"""Revalidate every protected source, including all batches after item 50."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from common.identity.runtime import RuntimeIdentity, RuntimeIdentityError
from common.knowledge.client import knowledge_api_url
from common.knowledge.delegation import KnowledgeDelegationError, issue_delegation


class KnowledgeValidationError(RuntimeIdentityError):
    """Protected knowledge cannot be safely reused under the current identity."""

    def __init__(self, message: str, status: int = 410) -> None:
        """Carry an explicit safe HTTP status without an upstream error body."""
        super().__init__(message)
        self.status = status


async def validate_citations(
    identity: RuntimeIdentity, citations: list[dict[str, Any]]
) -> None:
    """Require exact positive receipts for every source; no positive cache."""
    if not citations:
        return
    started = time.monotonic()
    try:
        async with asyncio.timeout(20):
            async with httpx.AsyncClient(
                base_url=knowledge_api_url(),
                timeout=20,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                for offset in range(0, len(citations), 50):
                    batch = citations[offset : offset + 50]
                    delegation = await issue_delegation(
                        identity,
                        "record:read",
                        timeout_seconds=20 - (time.monotonic() - started),
                    )
                    items = [
                        {
                            "record_id": row["record_id"],
                            "evidence_index": row["evidence_index"],
                            "source_digest": row["source_sha256"],
                        }
                        for row in batch
                    ]
                    response = await client.post(
                        "/api/knowledge-references/validate",
                        json={"items": items},
                        headers={
                            "Authorization": f"Bearer {delegation.token}",
                            "X-Knowledge-Remaining-Ms": str(
                                max(1, int((20 - (time.monotonic() - started)) * 1000))
                            ),
                        },
                    )
                    if response.status_code != 200:
                        raise KnowledgeValidationError(
                            "来源已失效或当前会话无权回读，请重新检索。",
                            503 if response.status_code >= 500 else 410,
                        )
                    payload = response.json()
                    results = (
                        payload.get("items") if isinstance(payload, dict) else None
                    )
                    if not isinstance(results, list) or len(results) != len(items):
                        raise KnowledgeValidationError("来源核验响应不完整。", 503)
                    for index, (expected, result) in enumerate(
                        zip(items, results, strict=True)
                    ):
                        if not isinstance(result, dict) or (
                            type(result.get("index")) is not int
                            or result["index"] != index
                            or result.get("status") != "valid"
                            or result.get("source_digest") != expected["source_digest"]
                        ):
                            raise KnowledgeValidationError(
                                "来源已失效或当前会话无权回读，请重新检索。"
                            )
    except KnowledgeValidationError:
        raise
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise KnowledgeValidationError("来源核验超时，请稍后重试。", 504) from exc
    except KnowledgeDelegationError as exc:
        raise KnowledgeValidationError(
            "来源读取授权已变化，请重新检索。",
            503 if exc.code == "unavailable" else 410,
        ) from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise KnowledgeValidationError("来源核验暂不可用。", 503) from exc


async def validate_thread_knowledge(identity: RuntimeIdentity, thread_id: str) -> None:
    """Conservative dependency closure survives summaries that omit cite IDs.

    All registered sources remain dependencies of the protected thread. This
    deliberately blocks its replay when safe per-message removal is unavailable.
    A fresh thread without those dependencies remains usable.
    """
    if not thread_id:
        return
    from common.knowledge.store import list_thread_citations

    await validate_citations(identity, await list_thread_citations(identity, thread_id))
