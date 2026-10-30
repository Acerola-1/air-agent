"""以当前配置模型、官方 API/SDK 和自建 PG 验证合成 BQA 连续会话."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

import baseline_native as native
import httpx
from dotenv import dotenv_values
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph_sdk import get_sync_client

base = native.base
root = Path(__file__).resolve().parents[2]
base.REPO = root
settings = dotenv_values(root / ".env")
provider = settings["MAIN_LLM_BASE_URL"].rstrip("/")
model = settings["MAIN_LLM_MODEL"]
key = settings.get("MAIN_LLM_API_KEY") or ""
sys.argv.append("--summary")
native.STATE["provider_calls"] = []


async def completion(request: Request):
    """只代理合成载荷，凭据留在宿主内存，不写入容器或报告."""
    body = await request.json()
    native.STATE["provider_calls"].append({"stream": bool(body.get("stream"))})
    body["model"] = model
    client = httpx.AsyncClient(timeout=180)
    upstream = await client.send(
        client.build_request(
            "POST",
            provider + "/chat/completions",
            json=body,
            headers={"authorization": "Bearer " + key},
        ),
        stream=True,
    )
    if upstream.status_code != 200:
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        return JSONResponse(
            {"error": {"message": "configured provider rejected synthetic probe"}},
            status_code=status,
        )
    if not body.get("stream"):
        payload = json.loads(await upstream.aread())
        await upstream.aclose()
        await client.aclose()
        return JSONResponse(payload)

    async def stream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream")


def checks(admin, http):
    """身份与受理走合成 IAM，执行和持久化走当前实际入口."""
    check = base.check_response
    check(
        admin.post(
            "/auth/login", json={"account": "fixture", "password": "synthetic-password"}
        )
    )
    admin.headers.update(
        {
            "x-tenant-id": base.IDS["tenant"],
            "x-tenant-context-version": "1",
            "x-internal-token": base.INTERNAL,
        }
    )
    ticket = check(
        admin.post(
            "/auth/runtime-ticket",
            json={"target_tenant_id": base.IDS["tenant"], "expected_ctx_version": 1},
        )
    )["ticket"]
    http.headers["authorization"] = "Bearer " + ticket
    sdk = get_sync_client(
        url=str(http.base_url).rstrip("/"),
        headers={"authorization": "Bearer " + ticket},
        timeout=240,
    )
    thread = str(uuid.uuid4())
    sdk.threads.create(
        thread_id=thread,
        metadata={
            "tenant_id": base.IDS["tenant"],
            "user_id": base.IDS["user"],
            "membership_id": base.IDS["membership"],
            "session_id": base.IDS["session"],
        },
    )
    assert check(
        http.post(
            "/internal/threads/claim-confirmation",
            json={"thread_id": thread},
            headers={"x-internal-token": base.INTERNAL},
        )
    )["confirmed"]
    inputs = [
        "请用一句话解释AQI的含义。这是概念解释，不查询实时数据。",
        "请把刚才的解释缩短为十几个字。",
        *[
            f"以下是合成训练记录第{i}段，仅需记住结尾标记，不要查询工具，也无需复述全文。"
            + "合成记录：甲站与乙站的观测数值只供学习，不能作为现实证据。" * 180
            + f"结尾标记：历史样本{i}。请回复已记住。"
            for i in range(4)
        ],
        "请结合最早的AQI解释，说明为什么上面的合成记录不能作为现实空气质量证据。两句话即可。",
    ]
    records = []

    def fresh_sdk():
        ticket = check(
            admin.post(
                "/auth/runtime-ticket",
                json={
                    "target_tenant_id": base.IDS["tenant"],
                    "expected_ctx_version": 1,
                },
            )
        )["ticket"]
        return get_sync_client(
            url=str(http.base_url).rstrip("/"),
            headers={"authorization": "Bearer " + ticket},
            timeout=240,
        )

    for index, question in enumerate(inputs):
        sdk = fresh_sdk()
        admission = check(
            admin.post(
                "/agent/requests",
                json={
                    "thread_id": thread,
                    "submission_key": uuid.uuid4().hex,
                    "graph_id": "basic-qa",
                    "content": question,
                    "capability_selection": {
                        "schema_version": "agent.capability-selection.v2",
                        "mcp": {"mode": "allowlist", "tool_ids": []},
                    },
                },
            )
        )
        before = len(native.STATE["provider_calls"])
        metadata = {
            "ledger_request_id": admission["request_id"],
            "ledger_attempt_id": admission["attempt_id"],
            "ledger_submission_id": admission["submission_id"],
            "ledger_graph_id": "basic-qa",
            "ledger_business_code": "basic-qa",
            "ledger_operation_kind": "initial",
        }
        events = list(
            sdk.runs.stream(
                thread,
                "basic-qa",
                input={"messages": [{"role": "user", "content": question}]},
                metadata=metadata,
                stream_mode=["custom", "updates"],
            )
        )
        sdk = fresh_sdk()
        runs = sdk.runs.list(thread)
        assert runs[0]["status"] == "success", runs[0]["status"]
        check(
            admin.post(
                "/agent/runs/admission-confirmed",
                json={
                    "request_id": admission["request_id"],
                    "attempt_id": admission["attempt_id"],
                    "submission_id": admission["submission_id"],
                    "outcome": "confirmed",
                    "platform_run_id": runs[0]["run_id"],
                },
            )
        )
        state = fresh_sdk().threads.get_state(thread)
        messages = state["values"]["messages"]
        answer = next(
            m for m in reversed(messages) if m.get("type") == "ai" and m.get("content")
        )
        assert not state["next"] and answer["content"]
        records.append(
            {
                "turn": index + 1,
                "provider_calls": len(native.STATE["provider_calls"]) - before,
                "event_count": len(events),
                "answer": answer["content"],
                "summary": bool(state["values"].get("summary")),
                "watermark": state["values"].get("summary_covered_message_id"),
            }
        )
    assert records[-1]["summary"] and records[-1]["watermark"], (
        "synthetic history did not exercise summarization"
    )
    assert len(native.STATE["executions"]) == 0
    base.EVIDENCE.update(
        checks=records,
        configured_model=model,
        actual_provider=True,
        synthetic_iam_and_data=True,
        tool_requests=0,
        thread_id=thread,
    )


shutil.copytree(
    root / "src", native.STAGE, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
)
base.MOCK.router.routes[:] = [
    r
    for r in base.MOCK.router.routes
    if getattr(r, "path", "") != "/v1/chat/completions"
]
base.MOCK.post("/v1/chat/completions")(completion)
native.NAMES = []
base.start_container = native.start_container
base.run_checks = checks
base.main()
