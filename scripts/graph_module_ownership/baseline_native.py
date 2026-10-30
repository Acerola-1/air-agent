# ruff: noqa: D103, D401, D415
"""Owned native Agent Server / SDK / persistent checkpoint probe.

Uses the existing remote-IAM fixture and new empty task-owned PG/Redis/API,
controlled OpenAI-compatible model, and an actual counting MCP transport.
It neither reads production data nor changes formal runtime configuration.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.responses import StreamingResponse
from langgraph_sdk import get_sync_client
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "owned_resume", HERE / "business_resume_probe.py"
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
if "--current" in sys.argv:
    base.REPO = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(base.REPO / "src"), str(base.REPO / "admin/api")]
base.PREFIX = "graph-ownership-20261029-apply"
base.PORTS = {"pg": 27631, "redis": 27632, "api": 27633, "mock": 27634, "admin": 27635}
base.BRIDGE = json.loads(base.command(["container", "network", "inspect", "default"]))[
    0
]["status"]["ipv4Gateway"]
MCP_PORT = 27636
EVIDENCE = Path(sys.argv[1]).resolve()
STAGE = EVIDENCE.parent / (EVIDENCE.name + "-source")
STATE = {"executions": [], "model_requests": []}
API_ARGS = []
LEGACY_UPGRADE = "--legacy-upgrade" in sys.argv
SNAPSHOT = (
    base.REPO
    / ".local/bqa-session-capabilities-v2/preimplementation/src/common/business_graph"
)


def lifecycle_source(legacy):
    """Swap only an owned staged mount while its API process is stopped."""
    source = SNAPSHOT if legacy else base.REPO / "src/common/business_graph"
    for original in SNAPSHOT.glob("*.py"):
        shutil.copy2(
            source / original.name, STAGE / "common/business_graph" / original.name
        )


def model_message(body):
    """A fixed deterministic policy, not an assessment of model intelligence."""
    messages = body["messages"]
    tools = {t["function"]["name"] for t in body.get("tools", [])}
    user_index = max(i for i, m in enumerate(messages) if m["role"] == "user")
    question = messages[user_index]["content"]
    if "submit_tracing_turn" in tools:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tracing-plan",
                    "type": "function",
                    "function": {
                        "name": "submit_tracing_turn",
                        "arguments": json.dumps(
                            {"plan": {"intent": "conversation", "blocks": ["greeting"]}}
                        ),
                    },
                }
            ],
        }
    if "固定数据分析验收" in question:
        turn = messages[user_index + 1 :]
        forecast = "mcp_city_common_get_air_quality_forecast"
        if (
            "--data-matrix" in sys.argv
            and forecast in tools
            and not any(m.get("role") == "tool" for m in turn)
            and not any(row["name"] == forecast for row in STATE["executions"])
        ):
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_" + uuid.uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": forecast,
                            "arguments": json.dumps(
                                {
                                    "region": "湖州市",
                                    "type": "hourly",
                                    "sTime": "2026-10-28 15:00:00",
                                    "force": "1",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "受控分析完成。"}
    if any(word in question for word in ("爱情诗", "Python")):
        return {
            "role": "assistant",
            "content": "本助手服务空气质量与环境监测问题，可以继续讨论空气质量。",
        }
    if question in {"谢谢", "缩成两句"}:
        return {"role": "assistant", "content": "空气质量为良。欢迎继续咨询。"}
    if not tools:
        return {"role": "assistant", "content": "合成验收：查询完成，空气质量良。"}
    turn = messages[user_index + 1 :]
    query_ids = {
        call["id"]
        for m in turn
        for call in m.get("tool_calls", [])
        if call.get("function", {}).get("name", "").startswith("mcp_city_common")
    }
    if any(m["role"] == "tool" and m.get("tool_call_id") in query_ids for m in turn):
        return {"role": "assistant", "content": "合成数据齐备。"}
    domain = "mcp_city_common_get_air_quality_realtime_stat"
    if domain in tools:
        city = (
            "杭州市"
            if "杭州" in question
            else "金华市"
            if "金华" in question
            else "湖州市"
        )
        name, args = (
            domain,
            {"region": city, "sTime": "2026-10-28 15:00", "type": "daily_count"},
        )
    else:
        text = "\n".join(
            str(m.get("content", "")) for m in messages if m["role"] == "system"
        )
        match = re.search(
            r'\{"skill_id":\s*"([^"]*air-quality-basic-query)"[^}]*"material_version":\s*"([^"]+)"',
            text,
        )
        if not match:
            raise AssertionError(
                "the actual system prompt must contain the trusted skill catalog"
            )
        name, args = "load_skill", {"skill_id": match[1], "expected_version": match[2]}
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_" + uuid.uuid4().hex,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        ],
    }


async def completion(request: Request):
    body = await request.json()
    if any(
        "取消测试" in str(m.get("content", ""))
        for m in body["messages"]
        if m["role"] == "user"
    ):
        import asyncio

        await asyncio.sleep(6)
    STATE["model_requests"].append(body)
    message = model_message(body)
    base.STATE["model_calls"] += 1
    result = {
        "id": "controlled-" + uuid.uuid4().hex,
        "created": int(time.time()),
        "model": "controlled",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
            }
        ],
        "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
    }
    if not body.get("stream"):
        return result

    async def stream():
        chunk = {k: v for k, v in result.items() if k not in {"choices", "usage"}}
        chunk.update(
            object="chat.completion.chunk",
            choices=[{"index": 0, "delta": message, "finish_reason": None}],
        )
        yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
        chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        chunk["usage"] = result["usage"]
        yield "data: " + json.dumps(chunk) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


def make_mcp():
    import socket

    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", MCP_PORT))
    server = FastMCP(
        "owned-bqa-counting",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    raw = json.loads(
        (
            Path(
                "/Users/acerola/Dev/Python/air_agent/.local/graph-module-ownership/20261029-apply/tool-documents.json"
            )
        ).read_text()
    )
    for row in raw:
        name, schema = row["name"], row["schema"]

        def create(tool_name, definition):
            async def execute(**kwargs):
                STATE["executions"].append({"name": tool_name, "params": kwargs})
                return {
                    "code": 200,
                    "data": [{"aqi": 75, "pm25": 40}],
                    "synthetic": True,
                }

            params = []
            required = definition.get("required", [])
            for key, prop in definition.get("properties", {}).items():
                if not key.isidentifier():
                    raise ValueError("fixture schema has an invalid parameter")
                annotation = {
                    "number": float,
                    "integer": int,
                    "boolean": bool,
                    "object": dict,
                    "array": list,
                }.get(prop.get("type"), str)
                params.append(
                    inspect.Parameter(
                        key,
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=annotation,
                        default=inspect.Parameter.empty if key in required else None,
                    )
                )
            execute.__signature__ = inspect.Signature(params)
            return execute

        server.add_tool(
            create(name, schema), name=name, description="受控合成接口 " + name
        )
    base.serve(server.streamable_http_app(), MCP_PORT, base.BRIDGE)
    return [r["name"] for r in raw]


ORIGINAL_START = base.start_container


def start_container(kind, args):
    global NAMES
    if kind == "api":
        if not NAMES:
            NAMES = make_mcp()
        args = list(args)
        if (STAGE / "application").is_dir():
            args = [
                v.replace(
                    "src/common/identity/langgraph_auth.py:auth",
                    "src/application/langgraph_auth.py:auth",
                ).replace(
                    "src/common/artifact_http.py:app", "src/application/http.py:app"
                )
                for v in args
            ]
        mount = f"{base.REPO / 'src'}:/deps/air_agent/src"
        args[args.index(mount)] = f"{STAGE}:/deps/air_agent/src"
        source = {
            "schema_version": "agent.mcp-connections.v1",
            "revision": 1,
            "servers": [
                {
                    "server_key": "ipp",
                    "url": f"http://{base.BRIDGE}:{MCP_PORT}/mcp",
                    "scope": "shared_readonly",
                    "auth_mode": "none",
                    "enabled": True,
                    "approved_query_tools": NAMES,
                }
            ],
        }
        for i, value in enumerate(args):
            if value.startswith("AGENT_MCP_CONNECTIONS_JSON="):
                args[i] = "AGENT_MCP_CONNECTIONS_JSON=" + json.dumps(source)
        args[-1:-1] = [
            "-e",
            "LANGFUSE_PROMPTS_ENABLED=false",
            "-e",
            "AGENT_OBSERVABILITY_ENABLED=false",
            "-e",
            "ARCHIVE_SCOPE_KEYS=" + json.dumps([base.KEY]),
        ]
        if "--browser-hold" in sys.argv:
            args[-1:-1] = [
                "-e",
                f"KNOWLEDGE_API_URL=http://{base.BRIDGE}:{base.PORTS['mock']}",
            ]
        if "--summary" in sys.argv:
            args[-1:-1] = [
                "-e",
                "MODEL_MAX_CONTEXT_TOKENS=96000",
                "-e",
                "MAIN_LLM_MAX_OUTPUT_TOKENS=1024",
                "-e",
                "MODEL_CONTEXT_SAFETY_TOKENS=512",
                "-e",
                "CONVERSATION_SUMMARY_TRIGGER_RATIO=0.05",
                "-e",
                "SUMMARY_MODEL_CONTEXT_TOKENS=96000",
                "-e",
                "SUMMARY_MODEL_OUTPUT_TOKENS=1024",
                "-e",
                "SUMMARY_MODEL_SAFETY_TOKENS=512",
            ]
        API_ARGS[:] = args
    name = ORIGINAL_START(kind, args)
    if kind == "pg":
        base.BRIDGE = json.loads(
            base.command(["container", "network", "inspect", "default"])
        )[0]["status"]["ipv4Gateway"]
    return name


def restart_api(label, *, legacy=None):
    name = base.PREFIX + "-api"
    logs = base.command(["container", "logs", "-n", "200", name])
    (EVIDENCE / (label + "-api.log")).write_text(logs)
    base.command(["container", "delete", "--force", name])
    base.CREATED.remove(name)
    if legacy is not None:
        lifecycle_source(legacy)
    ORIGINAL_START("api", API_ARGS)
    base.wait_http(f"http://127.0.0.1:{base.PORTS['api']}/ok")


def run_checks(admin, native):
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

    def client():
        ticket = check(
            admin.post(
                "/auth/runtime-ticket",
                json={
                    "target_tenant_id": base.IDS["tenant"],
                    "expected_ctx_version": 1,
                },
            )
        )["ticket"]
        native.headers["authorization"] = "Bearer " + ticket
        return get_sync_client(
            url=str(native.base_url).rstrip("/"),
            headers={"authorization": "Bearer " + ticket},
            timeout=120,
        )

    sdk = client()
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
        native.post(
            "/internal/threads/claim-confirmation",
            json={"thread_id": thread},
            headers={"x-internal-token": base.INTERNAL},
        )
    )["confirmed"]

    def submit(question, *, interrupt=None, empty=False):
        nonlocal sdk
        sdk = client()
        catalog = check(native.get("/capabilities", params={"graph_id": "basic-qa"}))
        selection = {
            "skill": {"mode": "auto"},
            "mcp": {"mode": "allowlist", "tool_ids": []} if empty else {"mode": "auto"},
            "catalog_version": catalog["catalog_version"],
        }
        admission = check(
            admin.post(
                "/agent/requests",
                json={
                    "thread_id": thread,
                    "submission_key": str(uuid.uuid4()),
                    "graph_id": "basic-qa",
                    "content": question,
                    "capability_selection": selection,
                },
            )
        )
        metadata = {
            "ledger_request_id": admission["request_id"],
            "ledger_attempt_id": admission["attempt_id"],
            "ledger_submission_id": admission["submission_id"],
            "ledger_graph_id": "basic-qa",
            "ledger_business_code": "basic-qa",
            "ledger_operation_kind": "initial",
        }
        before = len(STATE["executions"])
        sdk.runs.wait(
            thread,
            "basic-qa",
            input={"messages": [{"role": "user", "content": question}]},
            metadata=metadata,
            interrupt_before=interrupt,
        )
        runs = sdk.runs.list(thread)
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
        state = sdk.threads.get_state(thread)
        record = {
            "question": question,
            "run_status": runs[0]["status"],
            "next": state["next"],
            "checkpoint": state.get("checkpoint"),
            "executions": STATE["executions"][before:],
            "capabilities": state["values"].get("bqa_capabilities"),
            "legacy": state["values"].get("bqa_active_skill"),
        }
        base.EVIDENCE["checks"].append(record)
        if interrupt:
            check(
                admin.post(
                    f"/agent/runs/{admission['attempt_id']}/state",
                    json={
                        "state": "waiting_input",
                        "evidence_source": "platform_read",
                        "platform_status": "interrupted",
                    },
                )
            )
        return admission, state

    first, initial = submit("湖州市今天空气质量如何？")
    if LEGACY_UPGRADE:
        assert initial["values"]["bqa_active_skill"]["skill_id"]
        assert not initial["values"].get("bqa_capabilities")
    else:
        assert initial["values"]["bqa_capabilities"]["active_skill_ids"]
    assert len(STATE["executions"]) == 1
    restart_api("before-restart", legacy=False if LEGACY_UPGRADE else None)
    _, second = submit("那杭州呢？")
    assert base.EVIDENCE["checks"][-1]["executions"][0]["params"]["region"] == "杭州市"
    last_human = max(
        i for i, m in enumerate(second["values"]["messages"]) if m["type"] == "human"
    )
    calls = [
        c["name"]
        for m in second["values"]["messages"][last_human + 1 :]
        for c in m.get("tool_calls", [])
    ]
    assert calls == ["mcp_city_common_get_air_quality_realtime_stat"], calls
    assert second["values"]["bqa_capabilities"]["active_skill_ids"]
    if LEGACY_UPGRADE:
        base.EVIDENCE["checks"].append(
            {"name": "native_persisted_v1_to_v2_without_reload", "passed": True}
        )
    paused, state = submit("那金华呢？", interrupt=["call_model"])
    assert state["next"] == ["call_model"]
    restart_api("paused-restart")
    sdk = client()
    resumed = check(
        admin.post(
            "/agent/requests",
            json={
                "thread_id": thread,
                "submission_key": str(uuid.uuid4()),
                "graph_id": "basic-qa",
                "operation_kind": "resume",
                "source_attempt_id": paused["attempt_id"],
                "content": "",
            },
        )
    )
    sdk.runs.wait(
        thread,
        "basic-qa",
        input=None,
        metadata={
            "ledger_request_id": resumed["request_id"],
            "ledger_attempt_id": resumed["attempt_id"],
            "ledger_submission_id": resumed["submission_id"],
            "ledger_graph_id": "basic-qa",
            "ledger_business_code": "basic-qa",
            "ledger_operation_kind": "resume",
        },
    )
    final = sdk.threads.get_state(thread)
    assert not final["next"]
    assert STATE["executions"][-1]["params"]["region"] == "金华市"
    assert final["values"]["bqa_capabilities"]["active_skill_ids"]
    base.EVIDENCE["checks"].append(
        {"name": "SDK_resume_after_process_restart", "passed": True}
    )
    if LEGACY_UPGRADE:
        restart_api("before-lifecycle-rollback", legacy=True)
        _, rolled = submit("再查湖州市今天空气质量")
        assert rolled["values"]["bqa_active_skill"]["skill_id"]
        last = max(
            i
            for i, m in enumerate(rolled["values"]["messages"])
            if m["type"] == "human"
        )
        names = [
            c["name"]
            for m in rolled["values"]["messages"][last + 1 :]
            for c in m.get("tool_calls", [])
        ]
        assert names == [
            "load_skill",
            "mcp_city_common_get_air_quality_realtime_stat",
        ], names
        base.EVIDENCE["checks"].append(
            {
                "name": "owned_lifecycle_rollback_preserves_history_and_requires_legacy_load",
                "passed": True,
            }
        )
    if "--summary" in sys.argv:
        for index in range(4):
            _, summary_state = submit(
                "湖州空气质量历史资料" + str(index) + "污染物变化分析。" * 240
            )
        summary_values = summary_state["values"]
        base.EVIDENCE["checks"].append(
            {
                "name": "controlled_summary_watermark",
                "summary_present": bool(summary_values.get("summary")),
                "watermark": summary_values.get("summary_covered_message_id"),
            }
        )
        assert summary_values.get("summary") and summary_values.get(
            "summary_covered_message_id"
        )
    base.EVIDENCE["thread_id"] = thread
    # The archived data-analysis graph is exercised as its actual public SDK entry.
    data_thread = str(uuid.uuid4())
    sdk = client()
    sdk.threads.create(
        thread_id=data_thread,
        metadata={
            "tenant_id": base.IDS["tenant"],
            "user_id": base.IDS["user"],
            "membership_id": base.IDS["membership"],
            "session_id": base.IDS["session"],
        },
    )
    assert check(
        native.post(
            "/internal/threads/claim-confirmation",
            json={"thread_id": data_thread},
            headers={"x-internal-token": base.INTERNAL},
        )
    )["confirmed"]
    catalog = check(
        native.get(
            "/capabilities",
            params={"graph_id": "data-analysis", "menu_name": "CityHourBroadcast"},
        )
    )
    admission = check(
        admin.post(
            "/agent/requests",
            json={
                "thread_id": data_thread,
                "submission_key": str(uuid.uuid4()),
                "graph_id": "data-analysis",
                "content": "固定数据分析验收：按菜单分析合成数据。",
                "menu_name": "CityHourBroadcast",
                "capability_selection": {
                    "skill": {"mode": "auto"},
                    "mcp": {"mode": "auto"},
                    "catalog_version": catalog["catalog_version"],
                },
            },
        )
    )
    metadata = {
        "ledger_request_id": admission["request_id"],
        "ledger_attempt_id": admission["attempt_id"],
        "ledger_submission_id": admission["submission_id"],
        "ledger_graph_id": "data-analysis",
        "ledger_business_code": "data-analysis",
        "ledger_operation_kind": "initial",
    }
    model_before, tool_before = len(STATE["model_requests"]), len(STATE["executions"])
    sdk.runs.wait(
        data_thread,
        "data-analysis",
        input={
            "messages": [
                {"role": "user", "content": "固定数据分析验收：按菜单分析合成数据。"}
            ]
        },
        config={"configurable": {"menu_name": "CityHourBroadcast"}},
        metadata=metadata,
    )
    runs = sdk.runs.list(data_thread)
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
    data_state = sdk.threads.get_state(data_thread)
    base.EVIDENCE["checks"].append(
        {
            "name": "data_analysis_public_sdk_menu",
            "status": runs[0]["status"],
            "selected_skill": data_state["values"].get("selected_skill"),
            "next": data_state["next"],
            "model_requests": len(STATE["model_requests"]) - model_before,
            "tool_requests": len(STATE["executions"]) - tool_before,
        }
    )
    assert (
        runs[0]["status"] == "success"
        and data_state["values"].get("selected_skill") == "broadcast-hour"
    )
    restart_api("data-before-restart")
    data_state = client().threads.get_state(data_thread)
    assert data_state["values"].get("selected_skill") == "broadcast-hour"
    base.EVIDENCE["data_thread_id"] = data_thread
    if "--data-matrix" in sys.argv:

        def submit_data(content, pause=False):
            sdk = client()
            admission = check(
                admin.post(
                    "/agent/requests",
                    json={
                        "thread_id": data_thread,
                        "submission_key": str(uuid.uuid4()),
                        "graph_id": "data-analysis",
                        "content": content,
                        "menu_name": "CityHourBroadcast",
                    },
                )
            )
            sdk.runs.wait(
                data_thread,
                "data-analysis",
                input={"messages": [{"role": "user", "content": content}]},
                config={"configurable": {"menu_name": "CityHourBroadcast"}},
                metadata={
                    "ledger_request_id": admission["request_id"],
                    "ledger_attempt_id": admission["attempt_id"],
                    "ledger_submission_id": admission["submission_id"],
                    "ledger_graph_id": "data-analysis",
                    "ledger_business_code": "data-analysis",
                    "ledger_operation_kind": "initial",
                },
                **({"interrupt_before": ["call_model"]} if pause else {}),
            )
            return admission, sdk.threads.get_state(data_thread)

        page_data = '页面小时播报合成数据：{"broadcastHour":{"region":"湖州市","aqi":75,"pm25":40}}。'
        for index in range(4):
            _, data_state = submit_data(
                "固定数据分析验收：" + page_data + str(index) + "污染物变化分析。" * 600
            )
        assert data_state["values"].get("summary") and data_state["values"].get(
            "summary_covered_message_id"
        )
        assert any(
            "mcp_city_common_get_air_quality_forecast" == row["name"]
            for row in STATE["executions"]
        )
        paused_data, state_data = submit_data("固定数据分析验收：继续同一菜单。", True)
        assert state_data["next"] == ["call_model"]
        data_runs = client().runs.list(data_thread)
        check(
            admin.post(
                "/agent/runs/admission-confirmed",
                json={
                    "request_id": paused_data["request_id"],
                    "attempt_id": paused_data["attempt_id"],
                    "submission_id": paused_data["submission_id"],
                    "outcome": "confirmed",
                    "platform_run_id": data_runs[0]["run_id"],
                },
            )
        )
        check(
            admin.post(
                f"/agent/runs/{paused_data['attempt_id']}/state",
                json={
                    "state": "waiting_input",
                    "evidence_source": "platform_read",
                    "platform_status": "interrupted",
                },
            )
        )
        base.EVIDENCE.update(
            data_paused_thread_id=data_thread,
            data_paused_attempt_id=paused_data["attempt_id"],
        )
        tool_results = sum(
            m.get("type") == "tool" for m in state_data["values"]["messages"]
        )
        assert tool_results > 0
        base.EVIDENCE["checks"].append(
            {
                "name": "data_summary_and_pending_checkpoint",
                "summary": True,
                "watermark": data_state["values"]["summary_covered_message_id"],
                "next": state_data["next"],
                "tool_results": tool_results,
                "synthetic_page_broadcast_hour": True,
                "allowed_forecast_executed": True,
            }
        )
    # Preserve an independently paused old-version thread for upgrade/resume.
    paused_thread = str(uuid.uuid4())
    sdk = client()
    sdk.threads.create(
        thread_id=paused_thread,
        metadata={
            "tenant_id": base.IDS["tenant"],
            "user_id": base.IDS["user"],
            "membership_id": base.IDS["membership"],
            "session_id": base.IDS["session"],
        },
    )
    assert check(
        native.post(
            "/internal/threads/claim-confirmation",
            json={"thread_id": paused_thread},
            headers={"x-internal-token": base.INTERNAL},
        )
    )["confirmed"]
    paused_admission = check(
        admin.post(
            "/agent/requests",
            json={
                "thread_id": paused_thread,
                "submission_key": str(uuid.uuid4()),
                "graph_id": "basic-qa",
                "content": "湖州市今天空气质量如何？",
            },
        )
    )
    paused_metadata = {
        "ledger_request_id": paused_admission["request_id"],
        "ledger_attempt_id": paused_admission["attempt_id"],
        "ledger_submission_id": paused_admission["submission_id"],
        "ledger_graph_id": "basic-qa",
        "ledger_business_code": "basic-qa",
        "ledger_operation_kind": "initial",
    }
    sdk.runs.wait(
        paused_thread,
        "basic-qa",
        input={"messages": [{"role": "user", "content": "湖州市今天空气质量如何？"}]},
        interrupt_before=["call_model"],
        metadata=paused_metadata,
    )
    paused_runs = sdk.runs.list(paused_thread)
    check(
        admin.post(
            "/agent/runs/admission-confirmed",
            json={
                "request_id": paused_admission["request_id"],
                "attempt_id": paused_admission["attempt_id"],
                "submission_id": paused_admission["submission_id"],
                "outcome": "confirmed",
                "platform_run_id": paused_runs[0]["run_id"],
            },
        )
    )
    paused_snapshot = sdk.threads.get_state(paused_thread)
    assert paused_snapshot["next"] == ["call_model"]
    check(
        admin.post(
            f"/agent/runs/{paused_admission['attempt_id']}/state",
            json={
                "state": "waiting_input",
                "evidence_source": "platform_read",
                "platform_status": "interrupted",
            },
        )
    )
    base.EVIDENCE["paused_thread_id"] = paused_thread
    base.EVIDENCE["paused_attempt_id"] = paused_admission["attempt_id"]
    base.EVIDENCE["checks"].append(
        {"name": "paused_old_checkpoint_preserved", "next": paused_snapshot["next"]}
    )
    base.EVIDENCE["thread_id"] = thread
    base.EVIDENCE["model_request_count"] = len(STATE["model_requests"])
    base.EVIDENCE["tool_request_count"] = len(STATE["executions"])
    base.EVIDENCE["sdk"] = (
        "langgraph_sdk.get_sync_client: threads.create/get_state, runs.wait/list"
    )
    base.EVIDENCE["limitations"] = [
        "controlled model and remote IAM; not real-model/browser acceptance",
        "schema/connection change and summary restart remain separate pending checks",
        "lifecycle-only upgrade/rollback keeps current shared business policy; not a full deployment drill",
    ]


def browser_hold(admin, native):
    """Serve an owned frontend copy until the controlling test writes stop."""
    frontend = EVIDENCE.parent / (EVIDENCE.name + "-frontend")
    shutil.copytree(
        base.REPO / "frontend",
        frontend,
        ignore=shutil.ignore_patterns(
            "node_modules", ".next", ".env*", "tsconfig.tsbuildinfo"
        ),
    )
    (frontend / "node_modules").symlink_to(
        base.REPO / "frontend/node_modules", target_is_directory=True
    )
    env = dict(
        os.environ,
        ADMIN_API_URL=f"http://127.0.0.1:{base.PORTS['admin']}",
        LANGGRAPH_API_URL=f"http://127.0.0.1:{base.PORTS['api']}",
        INTERNAL_API_TOKEN=base.INTERNAL,
        AGENT_IDENTITY_BACKEND="remote",
        ARCHIVE_SCOPE_KEYS=json.dumps([base.KEY]),
    )
    with (EVIDENCE / "frontend.log").open("w") as log:
        proc = subprocess.Popen(
            [
                "node",
                str(frontend / "node_modules/next/dist/bin/next"),
                "dev",
                "--webpack",
                "--hostname",
                "127.0.0.1",
                "--port",
                "25590",
            ],
            cwd=frontend,
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            base.wait_http("http://127.0.0.1:25590/login")
            (EVIDENCE / "browser-ready.json").write_text(
                json.dumps(
                    {
                        "url": "http://bqa-cap-v2.localhost:25590",
                        "controlled": True,
                        "frontend_source": str(frontend),
                    }
                )
            )
            deadline = time.monotonic() + 1200
            while not (EVIDENCE / "browser-stop").exists():
                if time.monotonic() > deadline or proc.poll() is not None:
                    raise TimeoutError("owned browser environment deadline")
                time.sleep(1)
            base.EVIDENCE["limitations"] = [
                "controlled model, IAM and MCP; browser assertions are recorded separately"
            ]
        finally:
            (EVIDENCE / "browser-counts.json").write_text(
                json.dumps(
                    {
                        "executions": STATE["executions"],
                        "model_requests": len(STATE["model_requests"]),
                    }
                )
            )
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=15)


if __name__ == "__main__":
    if not EVIDENCE.is_relative_to(
        Path(
            "/Users/acerola/Dev/Python/air_agent/.local/graph-module-ownership/20261029-apply"
        )
    ):
        raise ValueError("private owned evidence must remain ignored")
    if STAGE.exists():
        raise FileExistsError(STAGE)
    shutil.copytree(
        base.REPO / "src", STAGE, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    if LEGACY_UPGRADE:
        lifecycle_source(True)
    if "--browser-hold" in sys.argv:
        from bqa_browser_fixture_services import setup

        setup(base, sys.modules[__name__])
        os.environ["CSRF_ALLOWED_ORIGINS"] = json.dumps(
            [
                "http://bqa-cap-v2.localhost:25590",
                "http://127.0.0.1:25590",
                "http://127.0.0.1:3000",
            ]
        )
    base.MOCK.router.routes[:] = [
        r
        for r in base.MOCK.router.routes
        if getattr(r, "path", "") != "/v1/chat/completions"
    ]
    base.MOCK.post("/v1/chat/completions")(completion)
    NAMES = []
    base.start_container = start_container
    base.run_checks = browser_hold if "--browser-hold" in sys.argv else run_checks
    base.main()
