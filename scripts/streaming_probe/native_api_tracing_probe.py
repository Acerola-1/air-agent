# ruff: noqa: D103, T201
"""Official langgraph-api SSE probe for the tracing section publication pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/unit_tests/tracing"))
from fixtures import make_request, make_scope  # noqa: E402

NATIVE = "http://127.0.0.1:12024"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    thread = httpx.post(
        f"{NATIVE}/threads",
        json={"metadata": {"graph_id": "intelligent-tracing"}},
        timeout=20,
    )
    thread.raise_for_status()
    thread_id = thread.json()["thread_id"]
    body = {
        "assistant_id": "intelligent-tracing",
        "input": {
            "messages": [{"type": "human", "content": "分析目标站 PM2.5"}],
            "request": make_request().model_dump(mode="json"),
            "scope": make_scope().model_dump(mode="json"),
        },
        "stream_mode": ["custom", "values"],
        "config": {"configurable": {"thread_id": thread_id}},
    }
    events: list[dict] = []
    times: dict[str, float] = {}
    started = time.perf_counter()
    with httpx.stream(
        "POST", f"{NATIVE}/threads/{thread_id}/runs/stream", json=body, timeout=120
    ) as response:
        response.raise_for_status()
        event_name = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
                if event_name != "custom":
                    continue
                try:
                    payload = json.loads(data)
                except ValueError:
                    continue
                if not str(payload.get("type", "")).startswith("final_output_"):
                    continue
                now = time.perf_counter()
                payload["_t"] = now
                events.append(payload)
                if payload["type"] == "final_output_delta":
                    times.setdefault("t_first_safe_delta", now)
                    times["t_last_safe_delta"] = now
            elif line == "":
                event_name = None
    times["t_stream_end"] = time.perf_counter()
    deltas = [event for event in events if event["type"] == "final_output_delta"]
    done = [event for event in events if event["type"] == "final_output_done"]
    text = done[-1]["message"] if done else ""
    result = {
        "graph": "intelligent-tracing",
        "entry": "official-langgraph-api-http-sse",
        "thread_id": thread_id,
        "deltas": len(deltas),
        "sections": [event.get("section_id") for event in deltas],
        "done_count": len(done),
        "first_delta_source": "new_delta" if deltas else None,
        "answer_chars": len(text),
        "answer_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        **{key: round(value - started, 4) for key, value in times.items()},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
