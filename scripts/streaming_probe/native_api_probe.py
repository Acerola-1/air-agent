# ruff: noqa: D103, T201
"""Official `langgraph-api` HTTP/SSE probe against the controlled model server.

The server container is external to this script (see implementation notes).
This script creates a real thread, streams a real run, and records the first
public deltas while the controlled source is still blocked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx

NATIVE = "http://127.0.0.1:12024"
FAKE = "http://127.0.0.1:17863"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--graph", choices=["basic-qa", "data-analysis"], default="basic-qa")
    args = parser.parse_args()
    thread = httpx.post(f"{NATIVE}/threads", json={}, timeout=20)
    thread.raise_for_status()
    thread_id = thread.json()["thread_id"]
    body = {
        "assistant_id": args.graph,
        "input": {
            "messages": [
                {
                    "type": "human",
                    "content": "你好"
                    if args.graph == "basic-qa"
                    else "分析北京空气质量",
                }
            ]
        },
        "stream_mode": ["custom", "values"],
        "config": {"configurable": {"thread_id": thread_id}},
    }
    events: list[dict] = []
    times: dict[str, float] = {}
    started = time.perf_counter()
    barrier: dict = {}
    with httpx.stream(
        "POST",
        f"{NATIVE}/threads/{thread_id}/runs/stream",
        json=body,
        timeout=120,
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
                    state = httpx.get(f"{FAKE}/probe/state", timeout=5).json()
                    native_delta_count = len(
                        [
                            event
                            for event in events
                            if event["type"] == "final_output_delta"
                        ]
                    )
                    if (
                        state.get("reached")
                        and not state.get("released")
                        and native_delta_count >= 2
                    ):
                        barrier = {
                            "native_deltas_at_barrier": len(
                                [
                                    event
                                    for event in events
                                    if event["type"] == "final_output_delta"
                                ]
                            ),
                            "source_finished": state.get("finished", 0) > 0,
                            "source_deltas": state.get("deltas"),
                        }
                        httpx.post(f"{FAKE}/probe/release", timeout=5)
            elif line == "":
                event_name = None
    times["t_stream_end"] = time.perf_counter()
    deltas = [event for event in events if event["type"] == "final_output_delta"]
    done = [event for event in events if event["type"] == "final_output_done"]
    text = done[-1]["message"] if done else ""
    result = {
        "entry": "official-langgraph-api-http-sse",
        "thread_id": thread_id,
        "events": len(events),
        "deltas": len(deltas),
        "delta_seqs": [event.get("seq") for event in deltas],
        "delta_lengths": [len(event.get("message", "")) for event in deltas],
        "done_count": len(done),
        "first_delta_source": "new_delta" if deltas else None,
        "answer_chars": len(text),
        "answer_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "barrier": barrier,
        **{
            key: round(value - started, 4)
            for key, value in times.items()
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
