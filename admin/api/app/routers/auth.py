"""统一认证接口：可撤销会话、企业选择/切换、运行票据签发。

会话协议（0003 起）：
- cookie `air_agent_session` = "<session_id>.<opaque token>"，HttpOnly + SameSite=Lax；
- 服务端只存 token 的 SHA-256 摘要；登出/改密/停用即时撤销（每请求实时查库）；
- 旧 HMAC cookie 不再授予任何访问（401 + 重新登录引导）；
- 企业切换带 `expected_ctx_version`，旧页面并发提交得到 409 context_changed；
- 运行票据只经内部端点（X-Internal-Token）签发，浏览器不可取得。
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, text

from app.config import get_settings
from app.db import TenantContext, apply_tenant_context
from app.deps import (
    IdentityDep,
    SessionDep,
    require_internal_token,
    require_tenant_context,
    verify_write_origin,
)
from app.models import AuthSession, Tenant, TenantMembership, User
from app.routers.permissions import load_permissions
from app.schemas import (
    ChangePasswordIn,
    FollowedRegionsIn,
    LoginIn,
    PermissionNode,
    RegionOut,
    RuntimeTicketIn,
    RuntimeTicketOut,
    SessionContextOut,
    SwitchTenantIn,
    TenantListItem,
)
from app.security import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    RuntimeTicket,
    compose_session_cookie,
    hash_password,
    new_session_token,
    parse_session_cookie,
    sign_runtime_ticket,
    token_hash_of,
    verify_password,
)
from app.services.audit import record_audit
from app.services.followed_regions import (
    FollowedRegionsError,
    list_followed_regions,
    list_membership_followed_regions,
    set_followed_regions,
    set_membership_followed_regions,
)
from app.services.identity import resolve_identity
from app.services.permission_tree import build_tree
from app.services.remote_mode import identity_backend_mode
from app.services.tenant_context import build_session_context, list_active_tenants

router = APIRouter(tags=["auth"], dependencies=[Depends(verify_write_origin)])

# 账号不存在时也做一次 bcrypt 比对，避免用响应时间探测账号是否存在。
# 代价是启动时多一次哈希计算（约 50-100ms），可以接受。
_DUMMY_HASH = hash_password("timing-equalization-dummy")


def set_session_cookie(response: Response, session_id: str, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=compose_session_cookie(session_id, raw_token),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/login", response_model=SessionContextOut, summary="登录（创建可撤销会话）")
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: SessionDep,
) -> SessionContextOut:
    # I2 remote：本地身份写入口关闭（投影 users 行的口令哨兵不可登录只是第一道；
    # 显式拒绝确保 remote 部署绝不建立本地会话——改走 /auth/iam/start SSO）。
    from app.services.remote_mode import identity_backend_mode

    if identity_backend_mode() == "remote":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前部署使用统一身份服务登录，请经 SSO 入口认证",
        )
    # 登录查询需要 users 读旁路：login GUC 仅在本事务内开放 users 可见性
    await apply_tenant_context(session, TenantContext(login=True))
    user = await session.scalar(select(User).where(User.account == payload.account))

    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用")

    # 有效成员企业列表：单企业自动选中为当前企业；多企业不自动落到第一个。
    await apply_tenant_context(session, TenantContext(user_id=str(user.id)))
    tenants = await list_active_tenants(session, user.id)
    initial_tenant_id: uuid.UUID | None = None
    if len(tenants) == 1:
        initial_tenant_id = tenants[0].tenant.id

    session_id, raw_token, token_hash = new_session_token()
    now = dt.datetime.now(dt.UTC)
    session.add(
        AuthSession(
            id=uuid.UUID(session_id),
            user_id=user.id,
            token_hash=token_hash,
            active_tenant_id=initial_tenant_id,
            user_auth_revision=user.auth_revision,
            expires_at=now + dt.timedelta(seconds=SESSION_TTL_SECONDS),
        )
    )
    user.last_login_at = now
    record_audit(
        session,
        actor_id=user.id,
        actor_account=user.account,
        action="auth.login",
        target_type="session",
        target_id=session_id,
        request=request,
    )
    await session.commit()

    set_session_cookie(response, session_id, raw_token)
    # 提交后按新会话完整解析（含平台授权与初始企业上下文）
    identity = await resolve_identity(session, compose_session_cookie(session_id, raw_token))
    return await build_session_context(session, identity, include_tenants=True)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="登出（撤销当前会话）")
async def logout(
    request: Request,
    response: Response,
    session: SessionDep,
) -> None:
    """撤销当前会话并清 cookie。未登录/已撤销都返回 204，不暴露会话状态。"""
    sid, token = parse_session_cookie(request.cookies.get(SESSION_COOKIE))
    if sid and token:
        await apply_tenant_context(session, TenantContext(session_id=sid))
        row = (
            (
                await session.execute(
                    text(
                        "SELECT user_id, token_hash FROM auth_sessions"
                        " WHERE id = :sid AND revoked_at IS NULL"
                    ),
                    {"sid": sid},
                )
            )
            .mappings()
            .first()
        )
        if row is not None and row["token_hash"] == token_hash_of(token):
            # WITH CHECK 要求 user_id=本人：拿到行后把用户 GUC 补上
            await apply_tenant_context(
                session, TenantContext(user_id=str(row["user_id"]), session_id=sid)
            )
            await session.execute(
                text("UPDATE auth_sessions SET revoked_at = now() WHERE id = :sid"),
                {"sid": sid},
            )
            record_audit(
                session,
                actor_id=row["user_id"],
                actor_account=None,
                action="auth.logout",
                target_type="session",
                target_id=sid,
                request=request,
            )
            await session.commit()
    clear_session_cookie(response)


@router.get("/me", response_model=SessionContextOut, summary="当前身份与企业上下文（实时）")
async def me(
    identity: IdentityDep,
    session: SessionDep,
) -> SessionContextOut:
    return await build_session_context(session, identity, include_tenants=True)


@router.get("/tenants", response_model=list[TenantListItem], summary="本人活跃企业列表")
async def my_tenants(
    identity: IdentityDep,
    session: SessionDep,
) -> list[TenantListItem]:
    if identity.remote_tenants is not None:
        from app.services.tenant_context import remote_tenant_items

        return remote_tenant_items(identity)
    return await list_active_tenants(session, identity.user.id)


@router.post(
    "/switch-tenant",
    response_model=SessionContextOut,
    summary="切换当前企业（带上下文版本约束）",
)
async def switch_tenant(
    payload: SwitchTenantIn,
    request: Request,
    response: Response,
    identity: IdentityDep,
    session: SessionDep,
) -> SessionContextOut:
    if identity.session.ctx_version != payload.expected_ctx_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="上下文版本冲突：页面持有旧企业上下文，请刷新后重试",
        )

    # 只允许选择“自己有效成员所属的有效企业”。未加入的企业与不存在一致（404）。
    target = await session.get(Tenant, payload.tenant_id)
    membership = await session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == payload.tenant_id,
            TenantMembership.user_id == identity.user.id,
        )
    )
    if target is None or membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="企业或成员关系不存在（与不可访问对象一致）",
        )
    if target.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="企业已停用")
    if membership.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="成员身份已停用")

    # 原子递增 ctx_version：并发切换下只有一个请求能命中 expected 版本
    result = await session.execute(
        text(
            "UPDATE auth_sessions"
            " SET active_tenant_id = :tid, ctx_version = ctx_version + 1,"
            "     last_seen_at = now()"
            " WHERE id = :sid AND ctx_version = :expected AND revoked_at IS NULL"
        ),
        {
            "tid": str(payload.tenant_id),
            "sid": str(identity.session.id),
            "expected": payload.expected_ctx_version,
        },
    )
    if result.rowcount != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="上下文版本冲突：该会话已被其他请求切换，请刷新后重试",
        )
    record_audit(
        session,
        identity=identity,
        actor_id=identity.user.id,
        actor_account=identity.user.account,
        action="auth.switch_tenant",
        target_type="tenant",
        target_id=str(payload.tenant_id),
        request=request,
    )
    await session.commit()

    # expire_on_commit=False 会保留旧行缓存；先过期再解析，保证读到新版本
    session.expire_all()
    refreshed = await resolve_identity(session, request.cookies.get(SESSION_COOKIE))
    return await build_session_context(session, refreshed, include_tenants=True)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="本人改密（撤销全部既有会话）",
)
async def change_password(
    payload: ChangePasswordIn,
    request: Request,
    response: Response,
    identity: IdentityDep,
    session: SessionDep,
) -> None:
    if not verify_password(payload.old_password, identity.user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="原密码不正确")
    # auth_revision 递增 → 会话表里记录的签发值全部不一致；再显式撤销所有活跃会话：
    # 已登录页面的后续业务请求不再成功。
    identity.user.password_hash = hash_password(payload.new_password)
    identity.user.auth_revision += 1
    session.add(identity.user)
    await session.execute(
        text(
            "UPDATE auth_sessions SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"
        ),
        {"u": str(identity.user.id)},
    )
    record_audit(
        session,
        identity=identity,
        actor_id=identity.user.id,
        actor_account=identity.user.account,
        action="auth.change_password",
        target_type="user",
        target_id=str(identity.user.id),
        request=request,
    )
    await session.commit()
    clear_session_cookie(response)


@router.get("/me/followed-regions", response_model=list[RegionOut], summary="我的关注城市")
async def my_followed_regions(
    identity: IdentityDep,
    session: SessionDep,
) -> list[RegionOut]:
    """有企业上下文时读写成员作用域偏好；无企业（平台用户）保持全局账号偏好。"""
    if identity.membership is not None and identity.membership_status == "active":
        regions = await list_membership_followed_regions(session, identity.membership.id)
    else:
        regions = await list_followed_regions(session, identity.user.id)
    return [RegionOut.of(region) for region in regions]


@router.put(
    "/me/followed-regions",
    response_model=list[RegionOut],
    summary="设置我的关注城市（最多 5 个）",
    # R02：成员作用域偏好按会话当前企业落库，写操作必须声明一致的上下文
    dependencies=[Depends(require_tenant_context)],
)
async def put_my_followed_regions(
    payload: FollowedRegionsIn,
    request: Request,
    identity: IdentityDep,
    session: SessionDep,
) -> list[RegionOut]:
    """本人偏好管理，不需要 followed:manage（那是管理员改他人/企业配置用）。"""
    try:
        if identity.membership is not None and identity.membership_status == "active":
            regions = await set_membership_followed_regions(
                session,
                identity.membership.id,
                identity.membership.tenant_id,
                payload.region_codes,
            )
        else:
            regions = await set_followed_regions(session, identity.user.id, payload.region_codes)
    except FollowedRegionsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    record_audit(
        session,
        identity=identity,
        actor_id=identity.user.id,
        actor_account=identity.user.account,
        action="profile.set_followed_regions",
        target_type="user",
        target_id=str(identity.user.id),
        detail={"region_codes": [region.code for region in regions]},
        request=request,
    )
    await session.commit()
    return [RegionOut.of(region) for region in regions]


@router.get("/menus", response_model=list[PermissionNode], summary="当前身份可见菜单树")
async def menus(
    identity: IdentityDep,
    session: SessionDep,
) -> list[PermissionNode]:
    """按当前权限码过滤的菜单树。任何人有权看到自己的菜单边界。"""
    permissions = await load_permissions(session)
    return build_tree(permissions, allowed=set(identity.permissions))


# ------------------------------------------------------------------ 运行票据（内部）


@router.post(
    "/runtime-ticket",
    response_model=RuntimeTicketOut,
    summary="服务端运行票据（仅内部调用）",
    dependencies=[Depends(require_internal_token)],
)
async def runtime_ticket(
    payload: RuntimeTicketIn,
    identity: IdentityDep,
    request: Request,
    session: SessionDep,
) -> RuntimeTicketOut:
    """基于有效会话与**当前企业上下文**签发短时票据。

    请求携带的目标企业是非授权性提示：与会话上下文不一致一律 409；
    票据含标识与修订号，不含任何业务凭据。
    """
    if payload.target_tenant_id is not None and (
        identity.tenant_id is None or payload.target_tenant_id != identity.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="目标企业与当前会话上下文不一致，请刷新后重试",
        )
    # F05/R02：上下文版本签发窗口核验（A→B→A 后版本前进，旧声明不得换票）
    if (
        payload.expected_ctx_version is not None
        and payload.expected_ctx_version != identity.session.ctx_version
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="企业上下文版本已变更（可能在其他标签页切换），请刷新后重试",
        )
    if identity.tenant is None or identity.membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="尚未选择企业或成员身份无效，无法签发运行票据",
        )
    if identity.tenant_status != "active" or identity.membership_status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="企业或成员已停用")

    if identity_backend_mode() == "remote":
        import asyncio

        from common.identity.ticket_binding import (
            TicketBindingError,
            facade_issue_and_bind,
            register_binding,
        )

        from app.services.remote_identity import (
            load_session_token,
            parse_handle_cookie,
            raise_remote_error,
            request_cookie,
        )

        row = await load_session_token(
            session,
            parse_handle_cookie(request_cookie(request)),
        )
        if row is None or not row.get("subject_token"):
            raise HTTPException(status_code=401, detail="应用会话无效")
        try:
            ticket, ttl, binding = await asyncio.to_thread(
                facade_issue_and_bind,
                row["subject_token"],
                target_tenant_id=str(identity.tenant_id),
                expected_ctx_version=identity.context_version,
            )
            await register_binding(binding, ticket, ttl)
        except TicketBindingError as exc:
            raise HTTPException(status_code=409, detail="签票上下文已变更") from exc
        except Exception as exc:
            raise_remote_error(exc)
        from common.identity.ticket import parse_keys, verify_ticket

        claims = verify_ticket(
            ticket,
            parse_keys(os.environ["RUNTIME_TICKET_KEYS"]),
            expected_audience=binding.audience,
        )
        return RuntimeTicketOut(
            ticket=ticket,
            expires_at=dt.datetime.fromtimestamp(claims.expires_at, dt.UTC),
            audience=binding.audience,
        )

    settings = get_settings()
    keys = settings.runtime_ticket_key_pairs
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="运行票据服务未配置（RUNTIME_TICKET_KEYS 缺失）",
        )
    now = dt.datetime.now(dt.UTC)
    ticket = RuntimeTicket(
        session_id=str(identity.session.id),
        user_id=str(identity.user.id),
        tenant_id=str(identity.tenant.id),
        membership_id=str(identity.membership.id),
        audience=settings.runtime_ticket_audience,
        issued_at=int(now.timestamp()),
        expires_at=int(now.timestamp()) + settings.runtime_ticket_ttl_seconds,
        session_ctx_version=identity.session.ctx_version,
        user_auth_revision=identity.user.auth_revision,
        tenant_auth_revision=identity.tenant.auth_revision,
        membership_revision=identity.membership.revision,
        kid=keys[0][0],
    )
    return RuntimeTicketOut(
        ticket=sign_runtime_ticket(ticket, keys),
        expires_at=now + dt.timedelta(seconds=settings.runtime_ticket_ttl_seconds),
        audience=settings.runtime_ticket_audience,
    )
