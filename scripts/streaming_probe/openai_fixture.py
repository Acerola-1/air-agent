# ruff: noqa: D103, T201
"""Minimal OpenAI-compatible controlled model server for native API probes.

It streams twelve short Chinese fragments, blocks after the second until
``/probe/release`` is called, and exposes only sequence/timing facts.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
FRAGMENTS = [f"第{i}段安全正文。" for i in range(1, 13)]
_state = {
    "started": 0,
    "finished": 0,
    "reached": asyncio.Event(),
    "release": asyncio.Event(),
    "first_chunk_at": None,
    "deltas": 0,
}


def _chunk(text: str, finish: str | None = None) -> str:
    payload = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "controlled-hy3",
        "choices": [
            {
                "index": 0,
                "delta": {"content": text} if text else {},
                "finish_reason": finish,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    if not body.get("stream"):
        return JSONResponse(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "controlled-hy3",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "".join(FRAGMENTS),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 100,
                    "total_tokens": 101,
                },
            }
        )

    async def generate():
        _state["started"] += 1
        for index, text in enumerate(FRAGMENTS):
            if _state["first_chunk_at"] is None:
                _state["first_chunk_at"] = time.perf_counter()
            _state["deltas"] += 1
            yield _chunk(text)
            await asyncio.sleep(0.01)
            if index == 1:
                _state["reached"].set()
                await _state["release"].wait()
        _state["finished"] += 1
        yield _chunk("", "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/probe/state")
async def probe_state():
    return {
        "started": _state["started"],
        "finished": _state["finished"],
        "deltas": _state["deltas"],
        "reached": _state["reached"].is_set(),
        "released": _state["release"].is_set(),
        "first_chunk_at": _state["first_chunk_at"],
    }


@app.post("/probe/reset")
async def probe_reset():
    _state["started"] = 0
    _state["finished"] = 0
    _state["deltas"] = 0
    _state["first_chunk_at"] = None
    _state["reached"] = asyncio.Event()
    _state["release"] = asyncio.Event()
    return {"reset": True}


@app.post("/probe/release")
async def probe_release():
    _state["release"].set()
    return {"released": True}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "controlled-hy3", "object": "model"}]}
