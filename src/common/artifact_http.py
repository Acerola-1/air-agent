"""LangGraph API 自定义归档接口，授权来自同源服务签名."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request

from common.artifact_scope import verify_scope
from common.storage.artifact_db import (
    ArtifactExpired,
    cleanup_expired_artifacts,
    delete_thread_artifacts,
    get_tool_artifact,
    list_tool_artifacts,
)


async def _sweep() -> None:
    while True:
        try:
            await cleanup_expired_artifacts()
        except Exception:
            # 存储故障不切换后端；读写接口仍返回明确故障。
            pass
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理有界过期清理任务."""
    task = asyncio.create_task(_sweep())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan)


def _owner(request: Request, thread_id: str) -> str:
    try:
        return str(
            verify_scope(request.headers.get("x-artifact-scope", ""), thread_id)[
                "userId"
            ]
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="无权读取此会话资料") from exc


@app.get("/archives/{thread_id}")
async def list_archives(
    request: Request, thread_id: str, offset: int = 0, limit: int = 50
):
    """列出当前授权线程归档的元数据."""
    return await list_tool_artifacts(
        user_id=_owner(request, thread_id),
        thread_id=thread_id,
        offset=offset,
        limit=limit,
    )


@app.get("/archives/{thread_id}/{artifact_id}")
async def read_archive(
    request: Request,
    thread_id: str,
    artifact_id: str,
    offset: int = 0,
    limit: int = 8000,
):
    """按字符分页读取归档，错误不透露其他线程数据."""
    owner = _owner(request, thread_id)
    try:
        record = await get_tool_artifact(
            artifact_id, user_id=owner, thread_id=thread_id
        )
    except ArtifactExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="资料不存在或不属于此会话")
    start, size = max(offset, 0), min(max(limit, 1), 16000)
    raw = record.pop("raw_payload")
    record.pop("processing_metadata", None)
    return {
        **record,
        "offset": start,
        "content": raw[start : start + size],
        "next_offset": start + size if start + size < len(raw) else None,
    }


@app.delete("/archives/{thread_id}")
async def delete_archives(request: Request, thread_id: str):
    """清理已授权线程资料，重复请求幂等."""
    await delete_thread_artifacts(
        user_id=_owner(request, thread_id), thread_id=thread_id
    )
    return {"ok": True}
