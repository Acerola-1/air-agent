"""Synthetic admin facade for an isolated copy of the real Next.js frontend."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
ACTIVE_TENANT = "tenant-a"
CONTEXT_VERSION = 1


def context():
    """Build the controlled session with two test-only tenants."""
    result = {
        "user": {
            "id": "user-a",
            "account": "probe",
            "display_name": "Conversation Probe",
            "is_active": True,
            "last_login_at": None,
            "created_at": "2026-01-01T00:00:00Z",
            "roles": [],
            "followed_region_codes": [],
        },
        "session": {
            "session_id": "probe-session",
            "active_tenant_id": "tenant-a",
            "ctx_version": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
        },
        "current_tenant": {
            "id": "tenant-a",
            "code": "conversation-probe",
            "name": "会话路由隔离测试",
            "status": "active",
        },
        "membership": {
            "id": "membership-a",
            "tenant_id": "tenant-a",
            "department_id": None,
            "status": "active",
            "revision": 1,
            "role_codes": ["member"],
        },
        "permissions": ["agent:business:intelligent-tracing:use"],
        "platform_admin": False,
        "context_version": 1,
        "tenants": [],
        "needs_tenant_selection": False,
    }
    items = []
    for tenant in ("tenant-a", "tenant-b"):
        items.append(
            {
                "tenant": {
                    "id": tenant,
                    "code": tenant,
                    "name": "隔离企业" + tenant[-1].upper(),
                    "status": "active",
                },
                "membership": {
                    **result["membership"],
                    "id": "membership-" + tenant[-1],
                    "tenant_id": tenant,
                },
            }
        )
    result["tenants"] = items
    current = next(item for item in items if item["tenant"]["id"] == ACTIVE_TENANT)
    result.update(
        current_tenant=current["tenant"],
        membership=current["membership"],
        context_version=CONTEXT_VERSION,
    )
    result["session"].update(
        active_tenant_id=ACTIVE_TENANT, ctx_version=CONTEXT_VERSION
    )
    return result


@app.get("/auth/me")
async def me():
    """Return the controlled session context."""
    return context()


@app.post("/auth/login")
async def login():
    """Set the synthetic test cookie."""
    response = JSONResponse(context())
    response.set_cookie(
        "air_agent_session",
        "00000000-0000-4000-8000-000000000000.conversation",
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/auth/runtime-ticket")
async def ticket():
    """Issue a test-only native runtime token."""
    return {
        "ticket": "conversation-probe"
        if ACTIVE_TENANT == "tenant-a"
        else "conversation-probe-b",
        "expires_at": "2099-01-01T00:00:00Z",
    }


@app.post("/agent/requests")
async def admit(request: Request):
    """Allocate synthetic admission identifiers."""
    body = await request.json()
    return {
        "request_id": str(uuid.uuid4()),
        "attempt_id": str(uuid.uuid4()),
        "submission_id": str(uuid.uuid4()),
        "admission_status": "admitted",
        "operation_kind": body.get("operation_kind", "initial"),
        "reused": False,
        "thread_id": body.get("thread_id"),
        "graph_id": body.get("graph_id"),
        "business_code": "intelligent-tracing",
        "business_policy_version": "2026-10-19.1",
        "ledger_marker": "conversation-probe",
        "capability_selection": None,
    }


@app.post("/agent/runs/start-claim")
async def claim():
    """Acknowledge the controlled start claim."""
    return {"status": "claimed", "start_claim_token": "probe-claim"}


@app.post("/internal/threads/claim-confirmation")
@app.post("/agent/runs/admission-confirmed")
@app.post("/agent/events/browser")
@app.post("/agent/runs/cancel-intent")
async def acknowledge():
    """Acknowledge test ledger events without a formal database."""
    return {"confirmed": True, "accepted": True}


@app.get("/agent/feedback/policy")
async def feedback_policy():
    """Disable feedback in the isolated probe."""
    return {"enabled": False, "allow_comments": False}


@app.get("/agent/feedback/answers")
async def feedback():
    """Return no feedback records."""
    return {"items": []}


@app.get("/ok")
async def ok():
    """Report probe readiness."""
    return {"status": "ok", "isolation": "synthetic admin only"}


@app.post("/auth/switch-tenant")
async def switch(request: Request):
    """Switch only between the two owned synthetic contexts."""
    global ACTIVE_TENANT, CONTEXT_VERSION
    body = await request.json()
    if (
        body.get("tenant_id") not in ("tenant-a", "tenant-b")
        or body.get("expected_ctx_version") != CONTEXT_VERSION
    ):
        return JSONResponse({"detail": "test context conflict"}, status_code=409)
    ACTIVE_TENANT = body["tenant_id"]
    CONTEXT_VERSION += 1
    return context()
