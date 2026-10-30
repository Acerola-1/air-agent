# ruff: noqa: D103, T201
"""Run basic-qa with the configured real model (no external DB/IAM).

This is a small real-configuration functional check: the gateway is the actual
MAIN_LLM_* endpoint, the graph is the real compiled graph, and the answer must
produce multiple safe deltas before its done event.  It does not claim generic
model quality or a 40-question benchmark.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from dotenv import dotenv_values
from langchain_core.messages import HumanMessage

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _load_env() -> None:
    values: dict[str, str] = {}
    for name in (".env", ".env.langgraph"):
        path = ROOT / name
        if not path.exists():
            continue
        for key, value in dotenv_values(path).items():
            if value is not None and key not in values:
                values[key] = value
    for key, value in values.items():
        if key.startswith("MAIN_LLM_") or key in {
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "MCP_SERVER_URL",
            "SILICONFLOW_API_KEY",
            "DEEPSEEK_API_KEY",
            "XFYUN_MAAS_API_KEY",
            "XIAOMI_MIMO_API_KEY",
            "SERPER_API_KEY",
            "ARK_API_KEY",
        }:
            os.environ.setdefault(key, value)
    os.environ.setdefault("MAIN_LLM_API_KEY", values.get("MAIN_LLM_API_KEY", ""))
    os.environ.setdefault("TENANT_ENFORCE_RUNTIME_IDENTITY", "false")
    os.environ.setdefault("TENANT_BUSINESS_SNAPSHOT_ENFORCED", "false")
    os.environ.setdefault("AGENT_IDENTITY_BACKEND", "local")
    os.environ.setdefault(
        "AGENT_MCP_CONNECTIONS_JSON",
        '{"schema_version":"agent.mcp-connections.v1","revision":1,"servers":[]}',
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--question", default="你好，请用一段话介绍你能做什么。")
    parser.add_argument("--graph", choices=["basic-qa", "data-analysis"], default="basic-qa")
    args = parser.parse_args()
    _load_env()
    from basic_qa.builder import build_basic_qa_graph
    from common.output_streaming.contract import OutputStreamValidator

    if args.graph == "data-analysis":
        from data_analysis.graph import build_graph as build_data_graph

        graph = build_data_graph()
        config = {
            "configurable": {
                "thread_id": "real-config-thread",
                "menu_name": "",
                "chat_mode": "fast",
            }
        }
    else:
        graph = build_basic_qa_graph(
            skills_dir=str(ROOT / "src/basic_qa/skills"), name="basic-qa"
        )
        config = {"configurable": {"thread_id": "real-config-thread"}}
    events: list[dict] = []
    started = time.perf_counter()
    times: dict[str, float] = {"t_submit": started}
    last_values = None
    async for mode, chunk in graph.astream(
        {"messages": [HumanMessage(content=args.question)]},
        config=config,
        stream_mode=["custom", "values"],
    ):
        if mode == "custom" and str(chunk.get("type", "")).startswith("final_output_"):
            now = time.perf_counter()
            events.append({**chunk, "_t": now})
            if chunk["type"] == "final_output_delta":
                times.setdefault("t_first_safe_delta", now)
                times["t_last_safe_delta"] = now
        elif mode == "values":
            last_values = chunk
    times["t_stream_end"] = time.perf_counter()
    times["t_confirmation_end"] = times["t_stream_end"]
    validator = OutputStreamValidator()
    for event in events:
        validator.accept(event)
    visible = [
        message
        for message in (last_values or {}).get("messages", [])
        if getattr(message, "additional_kwargs", {}).get("ui_visible") is True
    ]
    result = {
        "graph": args.graph,
        "mode": "real_configuration",
        "model": os.environ.get("MAIN_LLM_MODEL"),
        "deltas": len([e for e in events if e["type"] == "final_output_delta"]),
        "done": len([e for e in events if e["type"] == "final_output_done"]),
        "first_delta_source": "new_delta"
        if any(e["type"] == "final_output_delta" for e in events)
        else None,
        "answer_chars": len(validator.state.text),
        "answer_sha256": hashlib.sha256(
            validator.state.text.encode("utf-8")
        ).hexdigest(),
        "visible_answer_chars": len(visible[-1].content) if visible else 0,
        "validation_errors": validator.state.errors,
        **{key: round(value - started, 3) for key, value in times.items()},
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
