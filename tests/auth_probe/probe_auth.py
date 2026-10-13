"""任务 1.2 探针：验证当前自托管镜像的原生 authenticate/resource hooks/自定义应用鉴权。

刻意用**静态探针密钥**而非真实运行票据，只回答“镜像支持什么”：
- @auth.authenticate 对 Bearer 的处理（放行/拒绝）
- 资源 handler（threads/search、runs/create 等）是否被真实调用、能否改写 metadata
- 自定义 HTTP app（probe_http.py）是否要求认证（默认应 401）
"""

from __future__ import annotations

from langgraph_sdk import Auth

auth = Auth()

PROBE_VALID = "probe-valid-token"
PROBE_USER_ID = "probe-user-a"


@auth.authenticate
async def authenticate(request):
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if token != PROBE_VALID:
        raise Auth.exceptions.HTTPException(
            status_code=401, detail="invalid probe token"
        )
    return {
        "identity": PROBE_USER_ID,
        "display_name": "Probe",
        "permissions": ["probe:read"],
        "tenant_id": "probe-tenant",  # 自定义字段应进入 langgraph_auth_user
        "is_authenticated": True,
    }


@auth.on.threads.create
async def on_thread_create(
    ctx: Auth.types.AuthContext, value: Auth.types.threads.create.Value
):
    # 强制注入可信 metadata，模拟生产行为
    meta = dict(value.get("metadata") or {})
    meta["tenant_id"] = getattr(ctx.user, "tenant_id", "missing")
    meta["user_id"] = ctx.user.identity
    value["metadata"] = meta
    return


@auth.on.threads.search
async def on_thread_search(
    ctx: Auth.types.AuthContext, value: Auth.types.threads.search.Value
):
    return {"meta": {"tenant_id": getattr(ctx.user, "tenant_id", None)}}


@auth.on.threads.read
async def on_thread_read(ctx: Auth.types.AuthContext, value):
    # 探针：只有 thread_id 以 probe-ok 开头才放行
    tid = str(value.get("thread_id") or "")
    if not tid.startswith("probe-ok"):
        raise Auth.exceptions.HTTPException(status_code=403, detail="not probe thread")
    return
