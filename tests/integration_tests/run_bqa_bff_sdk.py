"""Actual SDK through an owned frontend/BFF; no browser acceptance claims."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

import httpx
from langgraph_sdk import get_sync_client


def current_turn(values):
    messages = values["messages"]
    start = max(i for i, m in enumerate(messages) if m["type"] == "human")
    return messages[start + 1 :]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    report = {
        "evidence_kind": "actual_python_sdk_via_owned_frontend_bff",
        "browser": "NOT_RUN",
        "controlled_identity_model_mcp": True,
        "checks": [],
    }
    with httpx.Client(
        base_url="http://127.0.0.1:25590",
        headers={"origin": "http://bqa-cap-v2.localhost:25590"},
        timeout=90,
    ) as http:
        response = http.post(
            "/api/auth/login",
            json={"account": "fixture", "password": "synthetic-password"},
        )
        response.raise_for_status()
        context = response.json()
        headers = {
            "origin": "http://bqa-cap-v2.localhost:25590",
            "cookie": "; ".join(f"{k}={v}" for k, v in http.cookies.items()),
            "x-tenant-id": context["current_tenant"]["id"],
            "x-tenant-context-version": str(context["context_version"]),
        }
        sdk = get_sync_client(
            url="http://127.0.0.1:25590/api", headers=headers, timeout=90
        )
        thread = sdk.threads.create()["thread_id"]
        original_ref = None
        for question, mode, expected_loads in [
            ("湖州市今天空气质量如何？", "fast", 1),
            ("那杭州呢？", "fast", 0),
            ("那金华呢？", "expert", 1),
            ("再查湖州", "expert", 0),
        ]:
            sdk.runs.wait(
                thread,
                "basic-qa",
                input={"messages": [{"role": "user", "content": question}]},
                config={"configurable": {"chat_mode": mode}},
                headers={"x-submission-key": str(uuid.uuid4())},
            )
            snapshot = sdk.threads.get_state(thread)
            values = snapshot["values"]
            turn = current_turn(values)
            names = [c["name"] for m in turn for c in m.get("tool_calls", [])]
            errors = [
                m for m in turn if m["type"] == "tool" and m.get("status") == "error"
            ]
            assert names.count("load_skill") == expected_loads, names
            assert not errors
            caps = values["bqa_capabilities"]
            key = "skill:basic-qa:air-quality-basic-query"
            reference = caps["loaded_skills"][key]
            assert reference["mode"] == mode
            if mode == "fast":
                if original_ref is None:
                    original_ref = reference["load_ref"]
                assert reference["load_ref"] == original_ref
            report["checks"].append(
                {
                    "question": question,
                    "mode": mode,
                    "load_skill": names.count("load_skill"),
                    "tool_calls": names,
                    "paired_errors": len(errors),
                    "checkpoint": snapshot.get("checkpoint"),
                    "reference_mode": reference["mode"],
                }
            )
        # The platform continues a streamed run after this SDK consumer closes;
        # reconnect to the same thread/run instead of submitting the query again.
        stream = sdk.runs.stream(
            thread,
            "basic-qa",
            input={"messages": [{"role": "user", "content": "那杭州呢？"}]},
            config={"configurable": {"chat_mode": "expert"}},
            stream_mode="updates",
            on_disconnect="continue",
            headers={"x-submission-key": str(uuid.uuid4())},
        )
        first = next(stream)
        assert first.event == "metadata"
        run_id = first.data["run_id"]
        stream.close()
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            info = sdk.runs.get(thread, run_id)
            if info["status"] not in {"pending", "running"}:
                break
            time.sleep(0.2)
        assert info["status"] == "success", info["status"]
        state = sdk.threads.get_state(thread)
        names = [
            c["name"]
            for m in current_turn(state["values"])
            for c in m.get("tool_calls", [])
        ]
        assert (
            "load_skill" not in names
            and names.count("mcp_city_common_get_air_quality_realtime_stat") == 1
        )
        report["checks"].append(
            {
                "name": "SDK_stream_disconnect_then_reconnect_same_run",
                "run_status": info["status"],
                "load_skill": 0,
                "business_calls": 1,
            }
        )
        # This altered page context is a negative HTTP control, with a live
        # authorized state read immediately before it as a positive control.
        bad = http.get(
            f"/api/threads/{thread}/state",
            headers={
                **headers,
                "x-tenant-context-version": str(context["context_version"] + 1),
            },
        )
        assert bad.status_code == 409
        report["checks"].append(
            {"name": "stale_frontend_context", "status": bad.status_code}
        )
        report["passed"] = True
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": True, "checks": len(report["checks"])}))  # noqa: T201


if __name__ == "__main__":
    main()
