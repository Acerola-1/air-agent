"""Exercise the actual SDK against only the owned native probe API."""

# ruff: noqa: T201 -- Emit the compact verification summary.
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

from langgraph_sdk import get_client

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/unit_tests/tracing"))
from conversation_fixtures import analysis_question  # noqa: E402


async def main():
    """Verify durable native turns, publication hashes and tenant refusal."""
    out = Path(sys.argv[1])
    if out.exists():
        raise RuntimeError("Refusing to overwrite evidence")
    client = get_client(
        url="http://127.0.0.1:12625",
        headers={"authorization": "Bearer conversation-probe"},
    )
    other = get_client(
        url="http://127.0.0.1:12625",
        headers={"authorization": "Bearer conversation-probe-b"},
    )
    thread = await client.threads.create(
        metadata={
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "graph_id": "intelligent-tracing",
            "title": "SDK固定多轮",
        }
    )
    tid = thread["thread_id"]
    rows = []
    for label, question, expected in [
        ("greet", "你好啊,你能做什么", "conversation"),
        ("incomplete", analysis_question().replace(" 10 ", " "), "clarify"),
        ("supplement", "调查半径十公里", "analysis"),
        ("after_report", "你还能做什么", "conversation"),
        ("revision", "把刚才半径改为二十公里", "analysis"),
    ]:
        events = []
        async for event in client.runs.stream(
            tid,
            "intelligent-tracing",
            input={"messages": [{"role": "human", "content": question}]},
            stream_mode=["custom", "values"],
        ):
            if event.event == "error":
                raise RuntimeError(str(event.data))
            events.append({"event": event.event, "data": event.data})
        state = (await client.threads.get_state(tid))["values"]
        done = [
            e["data"]
            for e in events
            if e["event"] == "custom" and e["data"].get("type") == "final_output_done"
        ]
        decision = state["turn_decision"]
        assert decision["route"] == expected, (label, decision)
        assert len(done) == 1, (label, len(done))
        answer = done[0]["message"]
        visible = [
            m
            for m in state["messages"]
            if m.get("additional_kwargs", {}).get("ui_visible")
        ]
        assert visible[-1]["content"] == answer
        assert visible[-1]["id"] == done[0]["message_id"]
        repeated = (await client.threads.get_state(tid))["values"]
        assert repeated["messages"] == state["messages"]
        rows.append(
            {
                "case": label,
                "route": decision["route"],
                "revision": decision["revision"],
                "run_id": decision["context"]["run_id"],
                "output_id": done[0]["output_id"],
                "message_id": done[0]["message_id"],
                "sha256": hashlib.sha256(answer.encode()).hexdigest(),
                "answer": answer,
                "done": len(done),
                "visible_messages": len(visible),
                "passed": True,
            }
        )
    denied = False
    try:
        await other.threads.get_state(tid)
    except Exception as exc:
        denied = getattr(getattr(exc, "response", None), "status_code", None) in (
            403,
            404,
        )
    assert denied, "other tenant must not read the owned thread"
    result = {
        "fixture": "tracing-conversation-v1",
        "native": "official persistent LangGraph API",
        "identity": "controlled test facade",
        "data": "frozen fixture",
        "thread_id": tid,
        "rows": rows,
        "cross_tenant_read_denied": denied,
        "formal_resources_used": False,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "rows": len(rows),
                "passed": all(row["passed"] for row in rows),
                "cross_tenant_read_denied": denied,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
