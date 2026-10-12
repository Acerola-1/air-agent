"""本地真实前端代理、LangGraph API 与 PG 验收；显式启用才执行.

AIR_AGENT_LIVE_TESTS=1 .venv/bin/python -m pytest tests/integration_tests/test_governance_live.py -q
只创建带专用标题的新测试线程；不读取或修改已有用户会话。凭据只从本地配置读取。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("AIR_AGENT_LIVE_TESTS") != "1", reason="须显式启用本地真实验收"
)
ROOT = Path(__file__).resolve().parents[2]


class _SessionToken(str):
    def __repr__(self):
        return "<redacted local test session>"


def _request(path, body=None, *, method=None, token="", backend=False, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    url = ("http://127.0.0.1:2024/" if backend else "http://127.0.0.1:3000/api/") + path
    headers = {"content-type": "application/json"}
    if token:
        headers["Cookie"] = "air_agent_session=" + token
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method or ("POST" if body is not None else "GET"),
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as failure:
        return failure.code, failure.read().decode()
    with response:
        content = response.read().decode()
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return response.status, content
        return response.status, json.loads(content) if content else None


@pytest.fixture(scope="module")
def account():
    from dotenv import dotenv_values

    frontend = dotenv_values(ROOT / "frontend/.env.local")
    backend = dotenv_values(ROOT / ".env")
    secret = frontend.get("AUTH_SECRET")
    assert secret and secret == backend.get("AUTH_SECRET"), (
        "前端与后端会话签名配置必须一致"
    )
    users = json.loads((ROOT / "frontend/data/users.json").read_text())
    user = next(value for value in users.values() if value.get("username") == "admin")
    raw = f"{user['id']}.admin.{int(time.time() + 3600)}"
    body = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
        )
        .decode()
        .rstrip("=")
    )
    # 此值仅驻留本次测试进程，不落盘或打印。
    token = _SessionToken(body + "." + signature)
    return user["id"], token


def new_thread(account, title, graph="basic-qa"):
    user, token = account
    thread_id = str(uuid.uuid4())
    status, response = _request(
        "threads",
        {
            "thread_id": thread_id,
            "metadata": {
                "graph_id": graph,
                "user_id": user,
                "title": "验收专用/" + title,
            },
        },
        token=token,
    )
    assert status == 200, response
    return thread_id


def run(thread_id, account, messages, graph="basic-qa", chat_mode="fast"):
    _, token = account
    status, text = _request(
        f"threads/{thread_id}/runs/stream",
        {
            "assistant_id": graph,
            "input": {"messages": messages} if messages is not None else None,
            "stream_mode": ["values", "custom"],
            "config": {"configurable": {"chat_mode": chat_mode}},
        },
        token=token,
        timeout=240,
    )
    assert status == 200, str(text)[:200]
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = block.splitlines()
        event = next((line[7:] for line in lines if line.startswith("event: ")), "")
        data = "\n".join(line[6:] for line in lines if line.startswith("data: "))
        if data:
            events.append((event, json.loads(data)))
    assert not any(event == "error" for event, _ in events), "真实运行出现流内错误"
    status, state = _request(f"threads/{thread_id}/state", token=token)
    assert status == 200
    (Path("/tmp") / "air-agent-live-last-state.json").write_text(
        json.dumps(
            {
                "thread_id": thread_id,
                "metrics": state["values"].get("context_metrics"),
                "system_prompt_characters": len(
                    state["values"].get("system_prompt", "")
                ),
                "messages": [
                    {
                        k: m.get(k)
                        for k in ["type", "name", "content", "additional_kwargs"]
                    }
                    for m in state["values"].get("messages", [])
                    if m.get("type") == "ai" and not m.get("tool_calls")
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return state["values"], events


def test_live_expert_greeting_then_real_city_query(account):
    """复现用户两句提问，必须实际调用 MCP，不能只绕过容量提示."""
    thread = new_thread(account, "专家问候转查询")
    values, _ = run(
        thread, account, [{"type": "human", "content": "你好"}], chat_mode="expert"
    )
    assert values["intent"] == "chitchat"
    values, _ = run(
        thread,
        account,
        [{"type": "human", "content": "请问湖州市今天的 PM2.5 是多少？"}],
        chat_mode="expert",
    )
    assert values["intent"] == "data_query"
    metrics = values["context_metrics"]
    assert metrics["input_tokens"] < metrics["input_budget_tokens"]
    assert metrics["measurement_source"].startswith("estimate:multilingual:")
    assert any(
        m.get("type") == "tool" and m.get("name", "").startswith("mcp_")
        for m in values["messages"]
    )
    final = [
        m
        for m in values["messages"]
        if m.get("type") == "ai" and m.get("additional_kwargs", {}).get("ui_visible")
    ]
    assert len(final) == 2
    assert "超出模型可用输入容量" not in final[-1]["content"]
    assert "服务暂不可用" not in final[-1]["content"]
    Path("/tmp/air-agent-live-expert-budget.json").write_text(
        json.dumps(
            {
                "thread_id": thread,
                "metrics": metrics,
                "tools": [
                    {"name": m.get("name"), "status": m.get("status")}
                    for m in values["messages"]
                    if m.get("type") == "tool"
                ],
                "final": final[-1]["content"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def test_live_pg_archive_scope_expiry_and_delete(account, monkeypatch):
    from common.storage import artifact_db

    os.environ["DATABASE_URI"] = (
        "postgresql://air_agent:air_agent_dev@127.0.0.1:15432/air_agent"
    )
    artifact_db._initialized = False
    first = new_thread(account, "资料回读与删除")
    second = new_thread(account, "跨会话拒绝")
    user, token = account
    foreign = str(uuid.uuid4())
    status, _ = _request(
        "threads",
        {
            "thread_id": foreign,
            "metadata": {
                "user_id": "fixture-other-user",
                "graph_id": "basic-qa",
                "title": "验收专用/他人会话拒绝",
            },
        },
        backend=True,
    )
    assert status == 200
    assert _request(f"threads/{foreign}/state", token=token)[0] == 403
    assert (
        _request(
            "threads",
            {
                "thread_id": foreign,
                "if_exists": "do_nothing",
                "metadata": {"user_id": user},
            },
            token=token,
        )[0]
        == 403
    )
    assert _request(f"threads/{foreign}", method="DELETE", backend=True)[0] in (
        200,
        204,
    )

    async def save():
        return await artifact_db.save_tool_artifact(
            tool_call_id="fixture",
            tool_name="test_fixture",
            raw_payload="FULL_RAW_777" * 2000,
            user_id=user,
            thread_id=first,
            run_id="fixture",
        )

    artifact = asyncio.run(save())
    status, page = _request(f"archives/{first}/{artifact}", token=token)
    assert (
        status == 200
        and page["content"].startswith("FULL_RAW_777")
        and page["next_offset"] == 8000
    )
    assert _request(f"archives/{second}/{artifact}", token=token)[0] == 404
    assert _request(f"archives/{first}/{artifact}")[0] == 401

    async def expire():
        await artifact_db._query(
            "UPDATE tool_artifact_storage SET expires_at=? WHERE artifact_id=?",
            ("2000-01-01T00:00:00+00:00", artifact),
        )

    asyncio.run(expire())
    assert _request(f"archives/{first}/{artifact}", token=token)[0] == 410
    assert _request(f"threads/{first}", method="DELETE", token=token)[0] in (200, 204)
    assert (
        asyncio.run(
            artifact_db.get_tool_artifact(artifact, user_id=user, thread_id=first)
        )
        is None
    )
    assert _request(f"threads/{second}", method="DELETE", token=token)[0] in (200, 204)


def test_live_full_tail_computation(account):
    from common.data_manifest import build_manifest
    from common.storage import artifact_db

    os.environ["DATABASE_URI"] = (
        "postgresql://air_agent:air_agent_dev@127.0.0.1:15432/air_agent"
    )
    artifact_db._initialized = False
    user, token = account
    thread = new_thread(account, "完整尾部计算")
    rows = [{"id": i, "value": i % 100, "padding": "x" * 30} for i in range(10001)]
    rows[-1]["value"] = 777
    raw = json.dumps(rows)

    async def save():
        return await artifact_db.save_tool_artifact(
            tool_call_id="fixture",
            tool_name="test_fixture",
            raw_payload=raw,
            user_id=user,
            thread_id=thread,
            run_id="fixture",
        )

    artifact = asyncio.run(save())
    manifest = build_manifest(raw, artifact)
    messages = [
        {
            "type": "human",
            "id": "fixture-h",
            "content": "验收资料已经获取，请保留该引用。",
        },
        {
            "type": "ai",
            "id": "fixture-a",
            "content": "",
            "tool_calls": [{"name": "test_fixture", "args": {}, "id": "fixture-call"}],
            "additional_kwargs": {"ui_visible": False},
        },
        {
            "type": "tool",
            "tool_call_id": "fixture-call",
            "name": "test_fixture",
            "content": json.dumps(manifest, ensure_ascii=False),
            "artifact": {"storage_status": "stored", "artifact_id": artifact},
        },
        {
            "type": "human",
            "id": "request-h",
            "content": f"这是独立验收数据，请只用 compute_artifact 对归档 {artifact} 的全部记录进行计算：统计记录数、value 总和以及最大值对应 id。原始 JSON 为数组，有 id、value、padding 字段。必须调用计算工具，不调用远端业务工具。",
        },
    ]
    values, events = run(thread, account, messages)
    computed = [
        message
        for message in values["messages"]
        if message.get("type") == "tool" and message.get("name") == "compute_artifact"
    ]
    assert computed, "模型没有实际调用全量计算工具"
    payload = json.loads(computed[-1]["content"])
    assert payload.get("complete") is True, payload
    assert payload["coverage"][0]["input_records"] == 10001
    derived = asyncio.run(
        artifact_db.get_tool_artifact(
            payload["derived_artifact_id"], user_id=user, thread_id=thread
        )
    )
    assert derived and derived["parent_artifact_id"] == artifact
    result = json.dumps(payload["result"])
    assert (
        "495777" in result
        and "10001" in result
        and "777" in result
        and "10000" in result
    ), result
    assert values["data_status"]["status"] == "completed"
    final = [
        message
        for message in values["messages"]
        if message.get("type") == "ai"
        and message.get("additional_kwargs", {}).get("ui_visible") is True
    ]
    assert len(final) == 1
    # 留下此专用线程供真实浏览器回读验收；报告只记录公开身份与结果，不写授权 token。
    (Path("/tmp") / "air-agent-live-computation.json").write_text(
        json.dumps(
            {
                "thread_id": thread,
                "artifact_id": artifact,
                "derived_artifact_id": payload["derived_artifact_id"],
                "coverage": payload["coverage"],
                "result": payload["result"],
                "final_message": final[0]["content"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def test_live_history_compression_preserves_history_and_public_state(account):
    thread = new_thread(account, "历史自动整理")
    messages = []
    for index in range(10):
        messages += [
            {
                "type": "human",
                "id": f"h{index}",
                "content": f"测试背景 {index}：验收关键词是星河站，年份为 2025。"
                + ("测试记录" * 700),
            },
            {
                "type": "ai",
                "id": f"a{index}",
                "content": "此前已经确认验收关键词为星河站，年份为 2025。"
                + ("占位资料" * 2500),
            },
        ]
    messages.append({"type": "human", "id": "latest-h", "content": "你好"})
    values, events = run(thread, account, messages)
    assert values.get("summary_covered_message_id") == "a7", values.get(
        "context_status"
    )

    assert "星河站" in values["summary"] and "2025" in values["summary"]
    assert len(values["messages"]) >= 22
    assert {f"h{i}" for i in range(10)} <= {
        message["id"] for message in values["messages"]
    }
    status = values["context_status"]
    assert status["status"] == "completed" and status["kept_turns"] == 3
    assert status["input_after"] < status["input_before"]
    assert status["input_after"] == values["context_metrics"]["input_tokens"]
    public = [
        data
        for event, data in events
        if event == "custom" and data.get("type") == "context_status"
    ]
    assert any(event["status"] == "started" for event in public) and any(
        event["status"] == "completed" for event in public
    )
    assert all("summary" not in event for event in public)
    (Path("/tmp") / "air-agent-live-compression.json").write_text(
        json.dumps(
            {
                "thread_id": thread,
                "status": status,
                "metrics": values["context_metrics"],
                "message_count": len(values["messages"]),
                "summary_characters": len(values["summary"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def test_live_mcp_parameter_error_is_readable(account):
    """从真实 checkpoint 的工具边恢复，执行现有 MCP 工具参数校验."""
    from common.data_manifest import parse_payload

    thread = new_thread(account, "MCP 参数错误")
    _, token = account
    status, response = _request(
        f"threads/{thread}/state",
        {
            "as_node": "call_model",
            "values": {
                "intent": "knowledge",
                "system_prompt": "这是参数校验验收。请用中文说明调用失败，不补齐参数，不调用其他工具。",
                "messages": [
                    {
                        "type": "human",
                        "id": "error-h",
                        "content": "请验证工具必填参数错误，仅说明失败。",
                    },
                    {
                        "type": "ai",
                        "id": "error-a",
                        "content": "",
                        "tool_calls": [
                            {
                                "name": "mcp_city_common_city_area_ranking",
                                "args": {},
                                "id": "error-call",
                            }
                        ],
                        "additional_kwargs": {"ui_visible": False},
                    },
                ],
            },
        },
        token=token,
    )
    assert status == 200, response
    values, events = run(thread, account, None)
    result = next(
        m
        for m in values["messages"]
        if m.get("type") == "tool" and m.get("tool_call_id") == "error-call"
    )
    raw = result["content"]
    payload = parse_payload(raw if isinstance(raw, str) else json.dumps(raw))
    assert result["status"] == "error" and payload["code"] == 400
    assert "参数" in result["additional_kwargs"]["error_message"]
    final = [
        m
        for m in values["messages"]
        if m.get("type") == "ai"
        and m.get("additional_kwargs", {}).get("ui_visible") is True
    ]
    assert len(final) == 1 and not final[0]["content"].lstrip().startswith("{")
    assert any(
        event == "custom"
        and data.get("type") == "tool_status"
        and data.get("status") == "failed"
        for event, data in events
    )
    Path("/tmp/air-agent-live-error.json").write_text(
        json.dumps(
            {
                "thread_id": thread,
                "tool_status": result["status"],
                "code": 400,
                "readable_error": result["additional_kwargs"]["error_message"],
                "final": final[0]["content"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@pytest.mark.parametrize("graph", ["intelligent-analysis", "data-analysis"])
def test_live_other_explicit_graphs_unique_answer(account, graph):
    thread = new_thread(account, graph + " 单一终答", graph)
    values, _ = run(
        thread,
        account,
        [
            {
                "type": "human",
                "id": "smoke-h",
                "content": "你好，请简短回复，不查询业务数据。",
            }
        ],
        graph,
    )
    final = [
        m
        for m in values["messages"]
        if m.get("type") == "ai"
        and m.get("additional_kwargs", {}).get("ui_visible") is True
    ]
    assert len(final) == 1 and final[0]["content"]
    assert (
        "暂时不可用" not in final[0]["content"]
        and "超出模型" not in final[0]["content"]
    )
    assert (
        values["context_metrics"]["input_tokens"]
        <= values["context_metrics"]["input_budget_tokens"]
    )
