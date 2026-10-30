"""Synthetic knowledge/canvas services for the owned browser probe only."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse

SOURCE = "合成臭氧资料：臭氧可以在阳光作用下，经由氮氧化物与挥发性有机物参与的光化学反应形成。"
DIGEST = hashlib.sha256(SOURCE.encode()).hexdigest()
TOKEN = "owned-synthetic-knowledge-delegation"


def setup(base, native):
    """Expose only task-owned fixed content; no production RAG or credentials."""

    @base.MOCK.post("/v1/internal/knowledge-delegations")
    async def delegation(request: Request):
        body = await request.json()
        return {
            "schema_version": "iam.knowledge-delegation.v1",
            "delegation_token": TOKEN,
            "token_type": "Bearer",
            "audience": "knowledge",
            "operation": body["operation"],
            "expires_at": (datetime.now(UTC) + timedelta(seconds=45)).isoformat(),
            "source": {
                "user_id": base.IDS["user"],
                "session_id": base.IDS["session"],
                "tenant_id": base.IDS["tenant"],
                "membership_id": base.IDS["membership"],
                "context_version": 1,
                "revisions": base.revisions(),
            },
        }

    def authorized(request):
        if request.headers.get("authorization") != "Bearer " + TOKEN:
            raise HTTPException(403, "owned fixture requires its synthetic delegation")

    @base.MOCK.post("/api/query")
    async def query(request: Request):
        authorized(request)
        return {
            "record_id": "owned-ozone",
            "workspaces": ["air"],
            "result": {
                "status": "retrieved",
                "evidence": [
                    {
                        "workspace": "air",
                        "chunk_id": "owned-ozone-chunk",
                        "doc_id": "owned-ozone-doc",
                        "doc_name": "受控臭氧资料",
                        "kind": "chunk",
                        "content": SOURCE,
                        "verified": True,
                        "source_digest": DIGEST,
                        "source_version": "v1",
                        "location": None,
                    }
                ],
                "workspace_snapshots": {},
                "per_workspace": [],
            },
        }

    @base.MOCK.post("/api/knowledge-references/validate")
    async def validate(request: Request):
        authorized(request)
        body = await request.json()
        return {
            "items": [
                {
                    "index": i,
                    "status": "valid"
                    if row["record_id"] == "owned-ozone"
                    and row["evidence_index"] == 0
                    and row["source_digest"] == DIGEST
                    else "forbidden",
                    "source_digest": DIGEST,
                }
                for i, row in enumerate(body["items"])
            ]
        }

    @base.MOCK.get("/api/source/owned-ozone/0/text")
    async def text(request: Request):
        authorized(request)
        return PlainTextResponse(SOURCE)

    original = native.model_message

    def message(body):
        messages = body["messages"]
        last = max(i for i, m in enumerate(messages) if m["role"] == "user")
        question = messages[last]["content"]
        if "画布" not in question and "知识库" not in question:
            return original(body)
        target = "create_artifact" if "画布" in question else "knowledge_search"
        turn = messages[last + 1 :]
        ids = {
            c["id"]
            for m in turn
            for c in m.get("tool_calls", [])
            if c.get("function", {}).get("name") == target
        }
        results = [
            m for m in turn if m["role"] == "tool" and m.get("tool_call_id") in ids
        ]
        if not results and body.get("tools"):
            args = (
                {
                    "title": "空气质量合成结果",
                    "content_type": "html",
                    "content": "<html><body><h1>空气质量合成结果</h1><p>空气质量为良，AQI为75。</p></body></html>",
                    "open_in": "canvas_window",
                }
                if target == "create_artifact"
                else {"query": "臭氧形成机制"}
            )
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_" + uuid.uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": target,
                            "arguments": json.dumps(args, ensure_ascii=False),
                        },
                    }
                ],
            }
        content = "合成画布已生成，可以在画布中查看。"
        if target == "knowledge_search":
            try:
                payload = json.loads(results[-1]["content"])
                citation = payload["references"][0]["citation_id"]
                content = f"臭氧可以由光化学反应形成。[[cite:{citation}]]"
            except (ValueError, KeyError, IndexError, TypeError):
                content = "合成知识服务本次没有返回有效引用。"
        return {"role": "assistant", "content": content}

    native.model_message = message
