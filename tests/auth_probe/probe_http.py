"""任务 1.2 探针：自定义 HTTP app，验证平台是否为自定义路由执行认证。"""

from __future__ import annotations

from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/probe-archive")
async def probe_archive(request: Request):
    user = getattr(request.state, "user", None)
    return {
        "authenticated": bool(
            user is not None and getattr(user, "is_authenticated", False)
        ),
        "identity": getattr(user, "identity", None),
    }
