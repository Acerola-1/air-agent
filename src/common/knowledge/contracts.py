"""Serializable contracts for the shared knowledge retrieval tool.

Only non-secret identifiers and already-published excerpts live here. The
delegation token, raw RAG record identifiers and upstream URLs never cross this
boundary: they stay inside the server-side client and citation store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "agent.knowledge-search.v1"
MAX_QUERY_CHARS = 2000
DEFAULT_TOP_K = 8
MAX_TOP_K = 20
MAX_REFERENCES = 8
MAX_EXCERPT_CHARS = 500
MAX_TOTAL_EXCERPT_CHARS = 12000
MAX_FRAGMENT_CHARS = 2000
MAX_FRAGMENTS = 20
MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_DEADLINE_SECONDS = 20.0

OUTCOMES = (
    "ok",
    "no_match",
    "permission_denied",
    "context_changed",
    "evidence_unusable",
    "unavailable",
    "timeout",
    "cancelled",
    "unknown",
)
COMPLETENESS = ("candidate_only", "complete", "incomplete", "unknown")


@dataclass(frozen=True)
class KnowledgeReference:
    """One published citation, already registered in the protected store."""

    citation_id: str
    doc_name: str
    verified: bool
    excerpt: str
    location: dict[str, Any] | None
    source_version: str | None
    source_sha256: str

    def as_dict(self) -> dict[str, Any]:
        """Return one model-visible reference; never includes the raw token."""
        return {
            "citation_id": self.citation_id,
            "doc_name": self.doc_name,
            "verified": self.verified,
            "excerpt": self.excerpt,
            "location": self.location,
            "location_status": "provided" if self.location else "not_provided",
            "source_version": self.source_version,
            "source_sha256": self.source_sha256,
        }


@dataclass
class SearchOutcome:
    """Model-visible result of one logical knowledge search call."""

    search_id: str
    receipt_id: str
    outcome: str
    retrieval_status: str | None
    retrieval_reason: str | None
    completeness: str
    queried_libraries: list[str] = field(default_factory=list)
    references: list[KnowledgeReference] = field(default_factory=list)
    fragments: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    limits: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)
    timing: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the model-visible JSON object (no secrets or raw upstream body)."""
        return {
            "schema_version": SCHEMA_VERSION,
            "search_id": self.search_id,
            "receipt_id": self.receipt_id,
            "outcome": self.outcome,
            "retrieval_status": self.retrieval_status,
            "retrieval_reason": self.retrieval_reason,
            "completeness": self.completeness,
            "queried_libraries": self.queried_libraries,
            "references": [ref.as_dict() for ref in self.references],
            "fragments": self.fragments,
            "truncated": self.truncated,
            "limits": self.limits,
            "warnings": self.warnings,
            "timing": self.timing,
        }


@dataclass(frozen=True)
class Delegation:
    """Short-lived single-operation user delegation (secret, never logged)."""

    token: str = field(repr=False)
    operation: str
    expires_at: str | None
    user_id: str
    session_id: str
    tenant_id: str
    membership_id: str
    context_version: int
    revisions: dict[str, int]
