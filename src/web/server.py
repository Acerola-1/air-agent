"""Air Agent 本地化 Web 服务.

提供:
- 静态页面 (聊天 UI,位于 src/web/static/index.html)
- LangGraph Server API 子集:
    POST /api/threads                              - 创建会话线程
    POST /api/threads/{id}/runs/stream            - 流式对话 (SSE)
    GET  /api/threads/{id}/history?graph_id=xxx   - 历史消息
    GET  /api/graphs                                - 可用图清单

设计:
- 6 个图在 lifespan 里一次性加载, 共享同一 AsyncSqliteSaver
- checkpointer 用官方 langgraph-checkpoint-sqlite 的 AsyncSqliteSaver (基于 aiosqlite)
- 流式输出走 SSE, 前端 fetch + ReadableStream 消费
- 不依赖 langgraph dev / langgraph-api, 全部直连

运行:
    uvicorn web.server:app --host 0.0.0.0 --port 8125
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from common.config.checkpointing import get_checkpointer
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ==================== 图加载 ====================

# (raw graph 名字 -> import 路径:实例)
_GRAPH_SPECS: dict[str, str] = {
    "basic-qa": "basic_qa.graph:graph",
    "intelligent-analysis": "intelligent_analysis.graph:graph",
    "data-analysis": "data_analysis.graph:graph",
    "intelligent-report": "intelligent_report.graph:graph",
    "deep-research": "deep_research.graph:graph",
    "intelligent-tracing": "intelligent_tracing.graph:graph",
}

# 运行时: graph_id -> 已 compile 的 CompiledStateGraph
_compiled_graphs: dict[str, Any] = {}


def _load_raw_graphs() -> dict[str, Any]:
    """import 所有原始图 (无 checkpointer)."""
    import importlib

    raw: dict[str, Any] = {}
    for gid, spec in _GRAPH_SPECS.items():
        module_name, attr = spec.split(":")
        mod = importlib.import_module(module_name)
        raw[gid] = getattr(mod, attr)
        logger.info("已加载图: %s -> %s", gid, spec)
    return raw


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动时建 checkpointer + 编译所有图; 关闭时释放.

    注: 图模块加载时已调用 `get_checkpointer()` 编译过自身,这里直接拿现成实例
    (都共享同一个 checkpointer, 跨 thread 持久化到 SQLite).
    """
    # 触发各 graph 模块 import, 拿到它们模块级已 compile 的 graph
    raw = _load_raw_graphs()
    for gid, g in raw.items():
        _compiled_graphs[gid] = g  # g 已经是 CompiledStateGraph
    logger.info("已加载 %d 个图, 共享 checkpointer", len(_compiled_graphs))
    try:
        yield
    finally:
        _compiled_graphs.clear()
        logger.info("图缓存清理完成")

# ==================== FastAPI app ====================

app = FastAPI(title="Air Agent", lifespan=lifespan)

# 静态文件: src/web/static
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.get("/api/graphs")
async def list_graphs() -> dict[str, Any]:
    return {"graphs": list(_GRAPH_SPECS.keys())}


# ==================== Thread & History ====================


class CreateThreadRequest(BaseModel):
    graph_id: str = Field(default="basic-qa", description="默认 basic-qa")


@app.post("/api/threads")
async def create_thread(req: CreateThreadRequest | None = None) -> dict[str, Any]:
    """创建一个新会话, 返回 thread_id."""
    if req is not None and req.graph_id not in _GRAPH_SPECS:
        raise HTTPException(404, f"未知图: {req.graph_id}")
    return {
        "thread_id": str(uuid.uuid4()),
        "graph_id": (req.graph_id if req else "basic-qa"),
    }


def _serialize_message(msg: Any) -> dict[str, Any]:
    """把 LangChain Message 序列化为前端友好的 dict."""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        text = content
    else:
        # 多模态内容, 拼成纯文本预览
        try:
            text = " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except Exception:
            text = str(content)

    return {
        "type": getattr(msg, "type", "unknown"),
        "role": "user" if getattr(msg, "type", "") == "human" else "assistant",
        "content": text,
        "id": getattr(msg, "id", None),
    }


@app.get("/api/threads/{thread_id}/history")
async def get_history(
    thread_id: str,
    graph_id: str = Query("basic-qa", description="图 id"),
) -> dict[str, Any]:
    """取一个 thread 的全部历史消息."""
    if graph_id not in _compiled_graphs:
        raise HTTPException(404, f"未知图: {graph_id}")
    graph = _compiled_graphs[graph_id]
    cfg = {"configurable": {"thread_id": thread_id}}
    try:
        state = await graph.aget_state(cfg)
    except Exception as exc:
        logger.exception("get_state 失败: %s", exc)
        raise HTTPException(500, f"读取历史失败: {exc}") from exc

    values = state.values or {}
    messages = values.get("messages", [])
    return {
        "thread_id": thread_id,
        "graph_id": graph_id,
        "messages": [_serialize_message(m) for m in messages],
    }


# ==================== SSE Stream ====================


class RunRequest(BaseModel):
    graph_id: str = Field(default="basic-qa")
    content: str = Field(..., description="用户消息内容")
    config: dict[str, Any] = Field(
        default_factory=dict, description="透传到图的 configurable"
    )


def _serialize_event(event: Any, mode: str) -> dict[str, Any] | None:
    """把 astream 的 event 转 dict; None 表示丢弃."""
    if mode == "messages":
        # (message_chunk, metadata) 元组
        if not isinstance(event, tuple) or len(event) != 2:
            return None
        msg_chunk, meta = event
        # 只放 ai 的内容 token; human / tool 不再 echo (已在 history 里)
        if getattr(msg_chunk, "type", "") != "ai":
            return None
        text = msg_chunk.content or ""
        if isinstance(text, list):
            # 提取 text block
            text = "".join(
                b.get("text", "") for b in text if isinstance(b, dict)
            )
        node = (meta or {}).get("langgraph_node", "")
        return {
            "type": "message",
            "node": node,
            "content": text,
        }
    elif mode == "values":
        # 整个 state 快照; 体积大, 不实时传 (流式不必要)
        return None
    elif mode == "events":
        # 元事件 / custom event
        kind = (event or {}).get("event") if isinstance(event, dict) else None
        if kind == "on_chain_start":
            return {"type": "node_start", "name": (event or {}).get("name")}
        if kind == "on_chain_end":
            return {"type": "node_end", "name": (event or {}).get("name")}
        return None
    return None


@app.post("/api/threads/{thread_id}/runs/stream")
async def stream_run(thread_id: str, req: RunRequest) -> StreamingResponse:
    """流式对话: 把 graph.astream 输出转成 SSE 推到前端."""
    if req.graph_id not in _compiled_graphs:
        raise HTTPException(404, f"未知图: {req.graph_id}")
    graph = _compiled_graphs[req.graph_id]
    cfg: dict[str, Any] = {
        "configurable": {
            "thread_id": thread_id,
            **(req.config or {}),
        }
    }
    input_data = {"messages": [HumanMessage(content=req.content)]}

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            # v3 events API: stream.messages 是 typed projection
            # 每个 message 有 .text (delta 流), .output (最终 message)
            stream = await graph.astream_events(
                input_data,
                config=cfg,
                version="v3",
            )
            try:
                async for msg in stream.messages:
                    node = getattr(msg, "node", "")
                    # 推 text delta
                    try:
                        async for delta in msg.text:
                            if delta:
                                payload = {
                                    "type": "text",
                                    "node": node,
                                    "content": delta,
                                }
                                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                    except Exception:
                        pass
                    # 推 tool_calls
                    try:
                        final = await msg.output
                        if final is not None:
                            tcs = getattr(final, "tool_calls", None) or []
                            for tc in tcs:
                                payload = {
                                    "type": "tool_call",
                                    "node": node,
                                    "name": tc.get("name"),
                                    "args": tc.get("args"),
                                    "id": tc.get("id"),
                                }
                                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                    except Exception:
                        pass
            finally:
                # 关闭 mux (释放后台资源)
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    try:
                        result = aclose()
                        if hasattr(result, "__aiter__") or hasattr(result, "__await__"):
                            await result
                    except Exception:
                        pass
            yield b"data: {\"type\":\"done\"}\n\n"
        except Exception as exc:
            logger.exception("stream_run 失败: %s", exc)
            err = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode("utf-8")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
