# ruff: noqa: D103, T201
"""Probe the configured main model gateway for streaming capabilities.

Reads only MAIN_LLM_* keys from the private dotenv files; prints capability and
timing facts, never credentials or answer text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from dotenv import dotenv_values
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[2]


def _env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for name in (".env", ".env.langgraph"):
        path = ROOT / name
        if path.exists():
            for key, value in dotenv_values(path).items():
                if value is not None and key not in merged:
                    merged[key] = value
    return merged


async def _stream(model, messages, config=None):
    started = time.perf_counter()
    first = None
    chunks = 0
    chars = 0
    async for chunk in model.astream(messages, config=config or {}):
        chunks += 1
        text = getattr(chunk, "content", "")
        if isinstance(text, list):
            text = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in text)
        chars += len(str(text or ""))
        if first is None and str(text or ""):
            first = (time.perf_counter() - started) * 1000
    return {
        "chunks": chunks,
        "chars": chars,
        "first_content_ms": round(first, 1) if first is not None else None,
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
        "incremental": chunks > 1,
    }


def _tool():
    async def echo(text: str) -> str:
        return text

    return StructuredTool.from_function(
        coroutine=echo,
        name="echo",
        description="echo text",
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    env = _env()
    required = ("MAIN_LLM_API_KEY", "MAIN_LLM_BASE_URL", "MAIN_LLM_MODEL")
    missing = [key for key in required if not env.get(key)]
    result: dict = {"mode": "real_gateway", "model": env.get("MAIN_LLM_MODEL")}
    if missing:
        result.update({"status": "blocked", "missing": missing})
    else:
        model = ChatOpenAI(
            api_key=SecretStr(env["MAIN_LLM_API_KEY"]),
            base_url=env["MAIN_LLM_BASE_URL"],
            model=env["MAIN_LLM_MODEL"],
            temperature=0,
            max_completion_tokens=256,
            timeout=60,
            max_retries=0,
            disable_streaming="tool_calling",
        )
        try:
            plain = await _stream(
                model,
                [HumanMessage(content="用一句中文介绍空气质量指数。")],
                {"tags": ["nostream", "probe"]},
            )
            result["plain_no_tools"] = plain
        except Exception as exc:  # noqa: BLE001
            result["plain_no_tools"] = {"status": "error", "class": type(exc).__name__}
        try:
            with_tools = model.bind_tools([_tool()])
            result["tool_bound_stream"] = await _stream(
                with_tools,
                [HumanMessage(content="调用 echo 工具返回 hello。")],
                {"tags": ["nostream", "probe"]},
            )
        except Exception as exc:  # noqa: BLE001
            result["tool_bound_stream"] = {"status": "error", "class": type(exc).__name__}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
