"""历史身份迁移工具回归（任务 8.1–8.3；隔离测试库）.

    export TENANT_TEST_OWNER_URI=... TENANT_TEST_RUNTIME_URI=...
    .venv/bin/python -m pytest tests/test_tenant_migration.py -q

测试库缺少平台侧 `thread` 与归档表时按运行同构最小化创建；数据带 mig_ 前缀
并自清理，不触业务库。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import bcrypt
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.security import verify_migration_ticket

OWNER_URI = os.environ.get("TENANT_TEST_OWNER_URI", "")
RUNTIME_URI = os.environ.get("TENANT_TEST_RUNTIME_URI", "")

pytestmark = pytest.mark.skipif(not OWNER_URI or not RUNTIME_URI, reason="需要隔离测试库环境变量")

PASSWORD = "mig-original-pass"

# F03：stub 故障计划（thread_id → 依次弹出的强制状态码；None=走真实可见性检查）
STUB_PLAN: dict[str, list[int | None]] = {}


def _sync(uri: str) -> str:
    return uri if uri.startswith("postgresql") else uri


HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()

DDL_THREAD = (
    "CREATE TABLE IF NOT EXISTS thread ("
    "thread_id UUID PRIMARY KEY,"
    "metadata JSONB NOT NULL DEFAULT '{}'::jsonb)"
)
DDL_ARTIFACT = (
    "CREATE TABLE IF NOT EXISTS tool_artifact_storage ("
    "artifact_id TEXT PRIMARY KEY,"
    "tool_call_id TEXT NOT NULL,"
    "tool_name TEXT NOT NULL,"
    "char_length INTEGER NOT NULL,"
    "raw_payload TEXT NOT NULL,"
    "summary TEXT,"
    "created_at TEXT NOT NULL,"
    "tenant_id TEXT,"
    "user_id TEXT,"
    "thread_id TEXT,"
    "run_id TEXT,"
    "parent_artifact_id TEXT,"
    "kind TEXT DEFAULT 'raw',"
    "status TEXT DEFAULT 'stored',"
    "expires_at TEXT,"
    "processing_metadata TEXT DEFAULT '{}')"
)


def _async(uri: str) -> str:
    if uri.startswith("postgresql://"):
        return "postgresql+psycopg://" + uri[len("postgresql://") :]
    return uri


@pytest.fixture
async def owner_session():
    engine = create_async_engine(_async(OWNER_URI))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def runtime_factory():
    engine = create_async_engine(_async(RUNTIME_URI))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _purge(engine) -> None:
    async with engine.connect() as conn:
        stmts = [
            "DELETE FROM membership_roles WHERE tenant_id IN"
            " (SELECT id FROM tenants WHERE code LIKE 'mig-%')",
            "DELETE FROM membership_followed_regions WHERE tenant_id IN"
            " (SELECT id FROM tenants WHERE code LIKE 'mig-%')",
            "DELETE FROM tenant_memberships WHERE tenant_id IN"
            " (SELECT id FROM tenants WHERE code LIKE 'mig-%')",
            "DELETE FROM thread_ownership WHERE thread_id IN"
            " (SELECT thread_id::text FROM thread WHERE metadata->>'mig' = '1')",
            "DELETE FROM tool_artifact_storage WHERE raw_payload = 'mig'",
            "DELETE FROM thread WHERE metadata->>'mig' = '1'",
            "DELETE FROM roles WHERE tenant_id IN (SELECT id FROM tenants WHERE code LIKE 'mig-%')",
            "DELETE FROM tenants WHERE code LIKE 'mig-%'",
            "DELETE FROM users WHERE account LIKE 'mig_%'",
            "DELETE FROM audit_logs WHERE actor_account = 'legacy_actor'",
            "DELETE FROM migration_ledger",
        ]
        for stmt in stmts:
            await conn.execute(text(stmt))
        await conn.commit()


@pytest.fixture
async def schema():
    engine = create_async_engine(_async(OWNER_URI))
    async with engine.connect() as conn:
        await conn.execute(text(DDL_THREAD))
        await conn.execute(text(DDL_ARTIFACT))
        await conn.commit()
    await _purge(engine)
    yield
    await _purge(engine)
    await engine.dispose()


def _find_free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getnameinfo()[1]) if False else int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def platform_stub():
    """R06：受限受支持 API 的本地替身——校验 mt-v1 票据后按平台语义合并 metadata。

    脚本侧走真实 httpx PATCH + 真实签名；替身验证票据受众/时效/thread 绑定，
    模拟平台只接受可信主体的窄更新。业务逻辑不在此处，验证的是"迁移必须经
    该 API"这一契约（真实镜像内的钩子拒读/拒改路径由原生侧套件覆盖）。
    """
    calls: list[tuple[str, dict]] = []
    app = FastAPI()

    @app.patch("/threads/{thread_id}")
    async def patch_thread(thread_id: str, request: Request):
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        payload = verify_migration_ticket(token, get_settings().runtime_ticket_key_pairs)
        if payload is None:
            return JSONResponse({"detail": "invalid migration ticket"}, status_code=401)
        if str(payload.get("thread")) != thread_id:
            return JSONResponse({"detail": "ticket not bound to thread"}, status_code=403)
        calls.append((thread_id, dict()))
        # F03：stub 必须反映真实事务可见性——像原生 auth 一样用**独立的运行角色
        # 连接**读取归属；未提交即 403。STUB_PLAN 可脚本化 503/403 故障注入。
        import psycopg as _pg

        plan = STUB_PLAN.get(thread_id)
        if plan:
            forced = plan.pop(0)
            if forced is not None:
                return JSONResponse({"detail": "scripted"}, status_code=forced)
        body = await request.json()
        meta = body.get("metadata") or {}
        with _pg.connect(_sync(RUNTIME_URI)) as rconn:
            with rconn.transaction():
                # 与真实 auth 相同：作用域取自已验签票据 claims，而非请求体自报
                rconn.execute(
                    "SELECT set_config('air_agent.tenant_id', %s, true)",
                    (str(payload.get("tenant") or ""),),
                )
                rconn.execute(
                    "SELECT set_config('air_agent.user_id', %s, true)",
                    (str(payload.get("user") or ""),),
                )
                visible = rconn.execute(
                    "SELECT source, tenant_id::text, user_id::text"
                    " FROM thread_ownership WHERE thread_id = %s",
                    (thread_id,),
                ).fetchone()
        if (
            visible is None
            or visible[0] != "migrated"
            or visible[1] != str(payload.get("tenant"))
            or visible[2] != str(payload.get("user"))
        ):
            return JSONResponse({"detail": "ownership not visible/committed"}, status_code=403)
        body_meta = meta
        engine = create_async_engine(_async(OWNER_URI))
        try:
            async with async_sessionmaker(engine)() as session:
                await session.execute(
                    text(
                        "UPDATE thread SET metadata = jsonb_set("
                        " jsonb_set(COALESCE(metadata, CAST('{}' AS jsonb)),"
                        " CAST('{user_id}' AS text[]), to_jsonb(CAST(:uid AS text)), true),"
                        " CAST('{tenant_id}' AS text[]), to_jsonb(CAST(:tid AS text)), true)"
                        " WHERE thread_id = CAST(:t AS uuid)"
                    ),
                    {
                        "uid": body_meta.get("user_id"),
                        "tid": body_meta.get("tenant_id"),
                        "t": thread_id,
                    },
                )
                await session.commit()
        finally:
            await engine.dispose()
        return JSONResponse({"thread_id": thread_id, "metadata": body_meta}, status_code=200)

    port = _find_free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import time as _time

    deadline = _time.time() + 15
    while not server.started and _time.time() < deadline:
        _time.sleep(0.05)
    assert server.started, "platform stub 未启动"
    yield f"http://127.0.0.1:{port}", calls
    server.should_exit = True
    thread.join(timeout=10)


def _write_fixtures(tmp_path: Path) -> tuple[Path, str]:
    json_user_id = str(uuid.uuid4())
    users = {
        json_user_id: {
            "id": json_user_id,
            "username": "mig_carol",
            "passwordHash": HASH,
            "createdAt": 1,
        },
        str(uuid.uuid4()): {
            "id": "orphan-json",
            "username": "mig_orphan",
            "passwordHash": HASH,
            "createdAt": 2,
        },
    }
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps(users), encoding="utf-8")
    return users_file, json_user_id


async def _seed_pg(owner, *, account: str, thread_id: uuid.UUID, meta_user: str) -> str:
    user_id = str(uuid.uuid4())
    await owner.execute(
        text(
            "INSERT INTO users (id, account, display_name, password_hash)"
            " VALUES (:id, :a, 'mig pg', 'x')"
        ),
        {"id": user_id, "a": account},
    )
    await owner.execute(
        text("INSERT INTO thread (thread_id, metadata) VALUES (:t, CAST(:m AS jsonb))"),
        {
            "t": str(thread_id),
            "m": json.dumps({"user_id": meta_user, "graph_id": "basic-qa", "mig": "1"}),
        },
    )
    await owner.execute(
        text(
            "INSERT INTO tool_artifact_storage (artifact_id, tool_call_id, tool_name,"
            " char_length, raw_payload, created_at, user_id, thread_id, run_id)"
            " VALUES (:a, 'c', 't', 1, 'mig', '2026-01-01', :u, :th, 'r')"
        ),
        {"a": str(uuid.uuid4()), "u": meta_user, "th": str(thread_id)},
    )
    await owner.commit()
    return user_id


def _manifest(
    tmp_path: Path,
    *,
    pg_user_id: str,
    thread_id: uuid.UUID,
    meta_user: str,
    json_user_id: str,
) -> Path:
    manifest = {
        "version": 1,
        "tenants": [{"code": "mig-a", "name": "迁移企业A", "create_if_missing": True}],
        "users": [
            {"account": "mig_bob", "source": "pg", "pg_user_id": pg_user_id},
            {
                "account": "mig_carol",
                "source": "json",
                "json_id": json_user_id,
                "display_name": "迁移Carol",
            },
        ],
        "memberships": [
            {
                "account": "mig_bob",
                "tenant_code": "mig-a",
                "role_codes": ["tenant_admin"],
            },
            {"account": "mig_carol", "tenant_code": "mig-a", "role_codes": []},
        ],
        "followed_regions": [{"account": "mig_carol", "tenant_code": "mig-a", "region_codes": []}],
        "threads": [
            {
                "thread_id": str(thread_id),
                "account": "mig_bob",
                "tenant_code": "mig-a",
                "metadata_user_id": meta_user,
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _run_tool(
    command: str,
    manifest: Path,
    users_file: Path,
    base_url: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["DATABASE_URI"] = OWNER_URI
    if base_url:
        env["LANGGRAPH_MIGRATION_BASE_URL"] = base_url
    return subprocess.run(
        [
            sys.executable,
            "scripts/migrate_identity.py",
            command,
            "--manifest",
            str(manifest),
            "--json-users",
            str(users_file),
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
    )


async def test_preflight_reports_without_writes(owner_session, schema, tmp_path):
    meta_user = str(uuid.uuid4())
    thread_id = uuid.uuid4()
    pg_user_id = await _seed_pg(
        owner_session, account="mig_bob", thread_id=thread_id, meta_user=meta_user
    )
    users_file, json_user_id = _write_fixtures(tmp_path)
    manifest = _manifest(
        tmp_path,
        pg_user_id=pg_user_id,
        thread_id=thread_id,
        meta_user=meta_user,
        json_user_id=json_user_id,
    )

    ledger_before = await owner_session.scalar(text("SELECT count(*) FROM migration_ledger"))
    res = _run_tool("preflight", manifest, users_file)
    assert res.returncode == 0, res.stderr
    report = json.loads(res.stdout)
    assert report["clean"] is True, report["conflicts"]
    assert report["counts"]["json_users_unmapped"] >= 1
    ledger_after = await owner_session.scalar(text("SELECT count(*) FROM migration_ledger"))
    assert ledger_after == ledger_before
    assert "$2" not in res.stdout
    assert ledger_before is not None


async def test_apply_is_idempotent_and_preserves_login(
    owner_session, schema, tmp_path, runtime_factory, platform_stub
):
    meta_user = str(uuid.uuid4())
    thread_id = uuid.uuid4()
    pg_user_id = await _seed_pg(
        owner_session, account="mig_bob", thread_id=thread_id, meta_user=meta_user
    )
    users_file, json_user_id = _write_fixtures(tmp_path)
    manifest = _manifest(
        tmp_path,
        pg_user_id=pg_user_id,
        thread_id=thread_id,
        meta_user=meta_user,
        json_user_id=json_user_id,
    )

    base_url, stub_calls = platform_stub
    first = _run_tool("apply", manifest, users_file, base_url=base_url)
    assert first.returncode == 0, first.stderr
    summary = json.loads(first.stdout)
    assert summary["applied"]["threads"] == 1
    # users 计数为登记的规范映射数（bob 复用 PG 账号、carol 新建导入哈希）
    assert summary["applied"]["users"] == 2

    stored_hash = await owner_session.scalar(
        text("SELECT password_hash FROM users WHERE account = 'mig_carol'")
    )
    assert bcrypt.checkpw(PASSWORD.encode(), str(stored_hash).encode())

    meta = (
        (
            await owner_session.execute(
                text("SELECT metadata->>'tenant_id' AS tid FROM thread WHERE thread_id = :t"),
                {"t": str(thread_id)},
            )
        )
        .mappings()
        .first()
    )
    tenant_id = await owner_session.scalar(text("SELECT id FROM tenants WHERE code='mig-a'"))
    assert meta["tid"] == str(tenant_id)
    owned = await owner_session.scalar(
        text("SELECT 1 FROM thread_ownership WHERE thread_id = :t"),
        {"t": str(thread_id)},
    )
    assert owned

    artifact = (
        (
            await owner_session.execute(
                text("SELECT tenant_id FROM tool_artifact_storage WHERE thread_id = :t"),
                {"t": str(thread_id)},
            )
        )
        .mappings()
        .first()
    )
    assert artifact["tenant_id"] == str(tenant_id)

    quarantines = (
        (
            await owner_session.execute(
                text("SELECT conflict_code FROM migration_ledger WHERE entity_type='quarantine'")
            )
        )
        .scalars()
        .all()
    )
    assert any("unmapped" in q for q in quarantines)

    # R06 幂等复跑：台账指纹命中 → 已应用短路，不再产生新 metadata PATCH
    patch_calls_before = len(stub_calls)
    second = _run_tool("apply", manifest, users_file, base_url=base_url)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout).get("already_applied") is True
    assert len(stub_calls) == patch_calls_before
    users_total = await owner_session.scalar(
        text("SELECT count(*) FROM users WHERE account IN ('mig_bob','mig_carol')")
    )
    assert users_total == 2
    # 运行角色下：迁移后的线程归属行对本人可见
    async with runtime_factory() as session:
        await session.execute(
            text("SELECT set_config('air_agent.tenant_id', :t, true)"),
            {"t": str(tenant_id)},
        )
        await session.execute(
            text("SELECT set_config('air_agent.user_id', :u, true)"),
            {"u": str(pg_user_id)},
        )
        res = await session.execute(
            text("SELECT count(*) FROM thread_ownership WHERE thread_id = :t"),
            {"t": str(thread_id)},
        )
        assert res.scalar() == 1


async def test_apply_stops_on_hard_conflict(owner_session, schema, tmp_path):
    """manifest 引用不存在的 PG 用户 → 预检冲突 → apply 拒绝且不写入。"""
    users_file, json_user_id = _write_fixtures(tmp_path)
    manifest = _manifest(
        tmp_path,
        pg_user_id=str(uuid.uuid4()),
        thread_id=uuid.uuid4(),
        meta_user=json_user_id,
        json_user_id=json_user_id,
    )
    before = await owner_session.scalar(
        text("SELECT count(*) FROM tenants WHERE code LIKE 'mig-%'")
    )
    res = _run_tool("apply", manifest, users_file)
    assert res.returncode != 0
    combined = res.stdout + res.stderr
    assert "冲突" in combined or "停止" in combined
    after = await owner_session.scalar(text("SELECT count(*) FROM tenants WHERE code LIKE 'mig-%'"))
    assert after == before


async def test_metadata_migration_uses_supported_api_not_private_table(
    owner_session, schema, tmp_path, platform_stub
):
    """R06：metadata 更新必须经受限 API；平台不可达时进入 metadata_pending，
    绝不回退为脚本直接 UPDATE 平台私有表。"""
    base_url, stub_calls = platform_stub
    meta_user = str(uuid.uuid4())
    thread_id = uuid.uuid4()
    pg_user_id = await _seed_pg(
        owner_session, account="mig_eve", thread_id=thread_id, meta_user=meta_user
    )
    users_file, _ = _write_fixtures(tmp_path)
    manifest = {
        "version": 1,
        "tenants": [{"code": "mig-c", "name": "迁移企业C", "create_if_missing": True}],
        "users": [{"account": "mig_eve", "source": "pg", "pg_user_id": pg_user_id}],
        "memberships": [
            {"account": "mig_eve", "tenant_code": "mig-c", "role_codes": ["tenant_admin"]}
        ],
        "threads": [
            {
                "thread_id": str(thread_id),
                "account": "mig_eve",
                "tenant_code": "mig-c",
                "metadata_user_id": meta_user,
            }
        ],
    }
    mpath = tmp_path / "m3.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    res = _run_tool("apply", mpath, users_file, base_url=base_url)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["applied"]["threads"] == 1
    assert any(call[0] == str(thread_id) for call in stub_calls)

    # 平台不可达（错误端口）：归属照登记，metadata 记 pending，不写私有表
    closed = "http://127.0.0.1:1"
    thread_id2 = uuid.uuid4()
    await owner_session.execute(
        text("INSERT INTO thread (thread_id, metadata) VALUES (:t, CAST(:m AS jsonb))"),
        {"t": str(thread_id2), "m": json.dumps({"user_id": meta_user, "mig": "1"})},
    )
    await owner_session.commit()
    manifest["threads"] = [
        {
            "thread_id": str(thread_id2),
            "account": "mig_eve",
            "tenant_code": "mig-c",
            "metadata_user_id": meta_user,
        }
    ]
    mpath2 = tmp_path / "m4.json"
    mpath2.write_text(json.dumps(manifest), encoding="utf-8")
    res2 = _run_tool("apply", mpath2, users_file, base_url=closed)
    assert res2.returncode == 0, res2.stderr
    summary2 = json.loads(res2.stdout)
    assert summary2["applied"]["threads"] == 0
    assert summary2["applied"]["metadata_pending"] == 1
    meta2 = (
        (
            await owner_session.execute(
                text("SELECT metadata->>'tenant_id' AS tid FROM thread WHERE thread_id = :t"),
                {"t": str(thread_id2)},
            )
        )
        .mappings()
        .first()
    )
    assert meta2["tid"] is None  # 未发生任何直接私有表写入
    owned2 = await owner_session.scalar(
        text("SELECT source FROM thread_ownership WHERE thread_id = :t"),
        {"t": str(thread_id2)},
    )
    assert owned2 == "migrated"


async def test_same_name_identity_requires_explicit_canonical(owner_session, schema, tmp_path):
    """R06 评审复现：同名不同 UUID、未提供 canonical 时 preflight 不得 clean，
    apply 不得静默复用 PG 账号。"""
    pg_uid = str(uuid.uuid4())
    await owner_session.execute(
        text(
            "INSERT INTO users (id, account, display_name, password_hash)"
            " VALUES (:i, 'mig_collide', 'pg原主', :h)"
        ),
        {"i": pg_uid, "h": bcrypt.hashpw(b"pg-original", bcrypt.gensalt(rounds=4)).decode()},
    )
    await owner_session.commit()
    json_uid = str(uuid.uuid4())
    users = {
        json_uid: {
            "id": json_uid,
            "username": "mig_collide",
            "passwordHash": HASH,
            "createdAt": 1,
        }
    }
    users_file = tmp_path / "collide.json"
    users_file.write_text(json.dumps(users), encoding="utf-8")
    manifest = {
        "version": 1,
        "tenants": [],
        "users": [{"account": "mig_collide", "source": "json", "json_id": json_uid}],
        "memberships": [],
        "threads": [],
    }
    mpath = tmp_path / "collide_manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")

    pre = _run_tool("preflight", mpath, users_file)
    assert pre.returncode == 0, pre.stderr
    report = json.loads(pre.stdout)
    assert report["clean"] is False
    assert any(
        c["type"] == "same_account_conflict" and c["key"] == "mig_collide"
        for c in report["conflicts"]
    )

    applied = _run_tool("apply", mpath, users_file)
    assert applied.returncode != 0  # 硬冲突停止
    hash_after = await owner_session.scalar(
        text("SELECT password_hash FROM users WHERE id = :i"), {"i": pg_uid}
    )
    # 原哈希未被 JSON 侧哈希覆盖：pg 密码可验、JSON 密码不可验
    assert bcrypt.checkpw(b"pg-original", str(hash_after).encode())
    assert not bcrypt.checkpw(PASSWORD.encode(), str(hash_after).encode())
    still_one = await owner_session.scalar(
        text("SELECT count(*) FROM users WHERE account = 'mig_collide'")
    )
    assert still_one == 1


async def test_canonical_pg_resolution_reuses_identity_preserves_hash(
    owner_session, schema, tmp_path
):
    """显式 canonical=pg + resolve_pg_user_id：复用 PG 身份，原哈希保全，成员绑定规范账号。"""
    pg_uid = str(uuid.uuid4())
    await owner_session.execute(
        text(
            "INSERT INTO users (id, account, display_name, password_hash)"
            " VALUES (:i, 'mig_resolved', 'pg原主', :h)"
        ),
        {"i": pg_uid, "h": bcrypt.hashpw(b"pg-keep-me", bcrypt.gensalt(rounds=4)).decode()},
    )
    await owner_session.commit()
    json_uid = str(uuid.uuid4())
    users = {
        json_uid: {
            "id": json_uid,
            "username": "mig_resolved",
            "passwordHash": HASH,
            "createdAt": 1,
        }
    }
    users_file = tmp_path / "resolved.json"
    users_file.write_text(json.dumps(users), encoding="utf-8")
    manifest = {
        "version": 1,
        "tenants": [{"code": "mig-d", "name": "迁移企业D", "create_if_missing": True}],
        "users": [
            {
                "account": "mig_resolved",
                "source": "json",
                "json_id": json_uid,
                "canonical": "pg",
                "resolve_pg_user_id": pg_uid,
            }
        ],
        "memberships": [{"account": "mig_resolved", "tenant_code": "mig-d", "role_codes": []}],
        "threads": [],
    }
    mpath = tmp_path / "resolved_manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    res = _run_tool("apply", mpath, users_file)
    assert res.returncode == 0, res.stderr
    hash_after = await owner_session.scalar(
        text("SELECT password_hash FROM users WHERE id = :i"), {"i": pg_uid}
    )
    assert bcrypt.checkpw(b"pg-keep-me", str(hash_after).encode())
    count = await owner_session.scalar(
        text("SELECT count(*) FROM users WHERE account = 'mig_resolved'")
    )
    assert count == 1
    membership = await owner_session.scalar(
        text(
            "SELECT m.user_id FROM tenant_memberships m JOIN tenants t ON t.id = m.tenant_id"
            " WHERE t.code = 'mig-d'"
        )
    )
    assert str(membership) == pg_uid
    mapped = await owner_session.scalar(
        text(
            "SELECT target_key FROM migration_ledger WHERE entity_type='user' AND source_key = :s"
        ),
        {"s": json_uid},
    )
    assert str(mapped) == pg_uid


async def test_old_audit_stays_in_history_domain(owner_session, schema, tmp_path, runtime_factory):
    """旧审计（tenant NULL）迁移后不进入企业审计列表，也不被改写。"""
    await owner_session.execute(
        text(
            "INSERT INTO audit_logs (actor_account, action, tenant_id, platform_scope)"
            " VALUES ('legacy_actor', 'legacy.action', NULL, false)"
        )
    )
    await owner_session.commit()
    meta_user = str(uuid.uuid4())
    thread_id = uuid.uuid4()
    pg_user_id = await _seed_pg(
        owner_session, account="mig_dan", thread_id=thread_id, meta_user=meta_user
    )
    users_file, _ = _write_fixtures(tmp_path)
    manifest = {
        "version": 1,
        "tenants": [{"code": "mig-b", "name": "迁移企业B", "create_if_missing": True}],
        "users": [{"account": "mig_dan", "source": "pg", "pg_user_id": pg_user_id}],
        "memberships": [
            {
                "account": "mig_dan",
                "tenant_code": "mig-b",
                "role_codes": ["tenant_admin"],
            }
        ],
        "threads": [],
    }
    mpath = tmp_path / "m2.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    res = _run_tool("apply", mpath, users_file)
    assert res.returncode == 0, res.stderr

    tenant_b = await owner_session.scalar(text("SELECT id FROM tenants WHERE code='mig-b'"))
    async with runtime_factory() as session:
        await session.execute(
            text("SELECT set_config('air_agent.tenant_id', :t, true)"),
            {"t": str(tenant_b)},
        )
        await session.execute(
            text("SELECT set_config('air_agent.user_id', :u, true)"),
            {"u": pg_user_id},
        )
        rows = await session.scalar(
            text("SELECT count(*) FROM audit_logs WHERE actor_account = 'legacy_actor'")
        )
        assert rows == 0
    still_null = await owner_session.scalar(
        text(
            "SELECT count(*) FROM audit_logs WHERE actor_account = 'legacy_actor'"
            " AND tenant_id IS NULL"
        )
    )
    assert still_null == 1


# ------------------------------------------------------- F03 分阶段/续跑回归（复验修复）


def _resume_manifest(tmp_path: Path, *, account: str, pg_user_id: str, thread_id: str) -> Path:
    manifest = {
        "version": 1,
        "tenants": [{"code": "mig-r", "name": "续跑企业R", "create_if_missing": True}],
        "users": [{"account": account, "source": "pg", "pg_user_id": pg_user_id}],
        "memberships": [
            {"account": account, "tenant_code": "mig-r", "role_codes": ["tenant_admin"]}
        ],
        "threads": [{"thread_id": thread_id, "account": account, "tenant_code": "mig-r"}],
    }
    path = tmp_path / "resume.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


async def test_migration_patch_happens_after_commit_visible(
    owner_session, schema, tmp_path, platform_stub
) -> None:
    """F03：阶段 A 先提交 → 平台 stub（独立运行角色连接）首调即可见归属、返回 200。"""
    base_url, stub_calls = platform_stub
    uid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    account = f"mig_vis_{uuid.uuid4().hex[:6]}"
    async with owner_session.begin():
        await owner_session.execute(
            text(
                "INSERT INTO users (id, account, display_name, password_hash)"
                " VALUES (:u, :a, :a, 'x')"
            ),
            {"u": uid, "a": account},
        )
        await owner_session.execute(
            text("INSERT INTO thread (thread_id, metadata) VALUES (:t, CAST(:m AS jsonb))"),
            {"t": tid, "m": json.dumps({"mig": "1"})},
        )
    users_file, _ = _write_fixtures(tmp_path)
    mpath = _resume_manifest(tmp_path, account=account, pg_user_id=uid, thread_id=tid)
    res = _run_tool("apply", mpath, users_file, base_url=base_url)
    assert res.returncode == 0, res.stderr
    summary = json.loads(res.stdout)
    assert summary["completed"] is True
    assert summary["applied"]["threads"] == 1
    assert summary["applied"].get("metadata_pending", 0) == 0
    assert sum(1 for c in stub_calls if c[0] == tid) == 1


async def test_migration_resumes_after_platform_failure(
    owner_session, schema, tmp_path, platform_stub
) -> None:
    """F03：503/中断后复跑续传；未完成期间 manifest 不登记 applied。"""
    base_url, stub_calls = platform_stub
    uid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    account = f"mig_rsu_{uuid.uuid4().hex[:6]}"
    async with owner_session.begin():
        await owner_session.execute(
            text(
                "INSERT INTO users (id, account, display_name, password_hash)"
                " VALUES (:u, :a, :a, 'x')"
            ),
            {"u": uid, "a": account},
        )
        await owner_session.execute(
            text("INSERT INTO thread (thread_id, metadata) VALUES (:t, CAST(:m AS jsonb))"),
            {"t": tid, "m": json.dumps({"mig": "1"})},
        )
    users_file, _ = _write_fixtures(tmp_path)
    mpath = _resume_manifest(tmp_path, account=account, pg_user_id=uid, thread_id=tid)
    STUB_PLAN[tid] = [503]
    try:
        first = _run_tool("apply", mpath, users_file, base_url=base_url)
        assert first.returncode == 0, first.stderr
        summary1 = json.loads(first.stdout)
        assert summary1["completed"] is False
        assert summary1["applied"]["threads"] == 0
        assert summary1["applied"]["metadata_pending"] == 1
        applied_manifest = await owner_session.scalar(
            text(
                "SELECT count(*) FROM migration_ledger WHERE entity_type='manifest'"
                " AND status='applied' AND source_key = ("
                "  SELECT DISTINCT manifest_fingerprint FROM migration_ledger"
                "  WHERE entity_type='thread' AND source_key=:s)"
            ),
            {"s": tid},
        )
        assert int(applied_manifest or 0) == 0, "失败阶段不得登记 manifest applied"
        # 归属已持久提交（阶段 A）
        owned = await owner_session.scalar(
            text("SELECT source FROM thread_ownership WHERE thread_id = :t"), {"t": tid}
        )
        assert owned == "migrated"
        # 复跑续传：仅未完成实体再 PATCH
        second = _run_tool("apply", mpath, users_file, base_url=base_url)
        assert second.returncode == 0, second.stderr
        summary2 = json.loads(second.stdout)
        assert summary2["completed"] is True
        assert summary2["applied"]["threads"] == 1
        assert sum(1 for c in stub_calls if c[0] == tid) == 2
        # 第三次跑：指纹 completed → already_applied 短路，不再外呼
        third = _run_tool("apply", mpath, users_file, base_url=base_url)
        assert third.returncode == 0, third.stderr
        assert json.loads(third.stdout).get("already_applied") is True
        assert sum(1 for c in stub_calls if c[0] == tid) == 2
    finally:
        STUB_PLAN.pop(tid, None)


async def test_migration_stops_on_foreign_ownership(
    owner_session, schema, tmp_path, platform_stub
) -> None:
    """F03：线程已被他人/他企业归属（native）→ apply 停止，不改归属、不写 metadata。"""
    base_url, stub_calls = platform_stub
    uid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    account = f"mig_own_{uuid.uuid4().hex[:6]}"
    other_tenant = str(uuid.uuid4())
    async with owner_session.begin():
        await owner_session.execute(
            text("INSERT INTO tenants (id, code, name) VALUES (:i, 'mig-forg', 'Foreign原主租户')"),
            {"i": other_tenant},
        )
        await owner_session.execute(
            text(
                "INSERT INTO users (id, account, display_name, password_hash)"
                " VALUES (:u, :a, :a, 'x')"
            ),
            {"u": uid, "a": account},
        )
        await owner_session.execute(
            text("INSERT INTO thread (thread_id, metadata) VALUES (:t, CAST(:m AS jsonb))"),
            {"t": tid, "m": json.dumps({"user_id": str(uuid.uuid4()), "mig": "1"})},
        )
        await owner_session.execute(
            text(
                "INSERT INTO thread_ownership (thread_id, tenant_id, user_id, source)"
                " VALUES (:t, :ot, :ou, 'native')"
            ),
            {"t": tid, "ot": other_tenant, "ou": uid},
        )
    users_file, _ = _write_fixtures(tmp_path)
    mpath = _resume_manifest(tmp_path, account=account, pg_user_id=uid, thread_id=tid)
    res = _run_tool("apply", mpath, users_file, base_url=base_url)
    assert res.returncode != 0
    assert "归属" in (res.stderr + res.stdout)
    assert sum(1 for c in stub_calls if c[0] == tid) == 0, "冲突线程不得触发平台窄更新"
    async with owner_session.begin():
        src = await owner_session.scalar(
            text("SELECT source FROM thread_ownership WHERE thread_id = :t"), {"t": tid}
        )
        assert src == "native"


async def test_json_migration_resumes_after_platform_failure(
    owner_session, schema, tmp_path, platform_stub
) -> None:
    """JSON 账号已落库后续跑，必须凭本清单台账认出本人映射。"""
    base_url, calls = platform_stub
    users_file, json_id = _write_fixtures(tmp_path)
    thread_id = str(uuid.uuid4())
    await owner_session.execute(
        text('INSERT INTO thread(thread_id, metadata) VALUES (:t, \'{"mig":"1"}\')'),
        {"t": thread_id},
    )
    await owner_session.commit()
    manifest = {
        "version": 1,
        "tenants": [{"code": "mig-json-r", "name": "JSON续跑", "create_if_missing": True}],
        "users": [{"account": "mig_carol", "source": "json", "json_id": json_id}],
        "memberships": [{"account": "mig_carol", "tenant_code": "mig-json-r", "role_codes": []}],
        "threads": [{"thread_id": thread_id, "account": "mig_carol", "tenant_code": "mig-json-r"}],
    }
    path = tmp_path / "json-resume.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    STUB_PLAN[thread_id] = [503]
    try:
        first = _run_tool("apply", path, users_file, base_url=base_url)
        assert first.returncode == 0, first.stderr
        assert json.loads(first.stdout)["completed"] is False
        uid = str(
            await owner_session.scalar(text("SELECT id FROM users WHERE account='mig_carol'"))
        )
        second = _run_tool("apply", path, users_file, base_url=base_url)
        assert second.returncode == 0, second.stderr
        assert json.loads(second.stdout)["completed"] is True
        assert (
            str(await owner_session.scalar(text("SELECT id FROM users WHERE account='mig_carol'")))
            == uid
        )
        assert sum(call[0] == thread_id for call in calls) == 2
        third = _run_tool("apply", path, users_file, base_url=base_url)
        assert json.loads(third.stdout)["already_applied"] is True
    finally:
        STUB_PLAN.pop(thread_id, None)


async def test_migration_resume_preserves_post_commit_role_revocation(
    owner_session, schema, tmp_path, platform_stub
) -> None:
    """平台更新待续传时，重跑不得重新授予已被管理员撤销的角色。"""
    base_url, _ = platform_stub
    tid = str(uuid.uuid4())
    uid = await _seed_pg(
        owner_session, account="mig_revoked", thread_id=uuid.UUID(tid), meta_user=str(uuid.uuid4())
    )
    users_file, _ = _write_fixtures(tmp_path)
    path = _resume_manifest(tmp_path, account="mig_revoked", pg_user_id=uid, thread_id=tid)
    STUB_PLAN[tid] = [503]
    try:
        first = _run_tool("apply", path, users_file, base_url=base_url)
        assert json.loads(first.stdout)["completed"] is False
        await owner_session.execute(
            text(
                "DELETE FROM membership_roles WHERE membership_id IN (SELECT id FROM "
                "tenant_memberships WHERE user_id=:u)"
            ),
            {"u": uid},
        )
        await owner_session.commit()
        second = _run_tool("apply", path, users_file, base_url=base_url)
        assert second.returncode == 0, second.stderr
        assert json.loads(second.stdout)["completed"] is True
        assert (
            await owner_session.scalar(
                text(
                    "SELECT count(*) FROM membership_roles mr JOIN tenant_memberships m ON "
                    "m.id=mr.membership_id WHERE m.user_id=:u"
                ),
                {"u": uid},
            )
            == 0
        )
    finally:
        STUB_PLAN.pop(tid, None)


async def test_migration_backfills_computation_scope_without_resetting_quota(
    owner_session, schema, tmp_path, platform_stub
) -> None:
    """归档可信迁移同时恢复旧计算记录作用域，次数与开始时间不可重置。"""
    base_url, _ = platform_stub
    tid = str(uuid.uuid4())
    old_user = str(uuid.uuid4())
    uid = await _seed_pg(
        owner_session, account="mig_compute", thread_id=uuid.UUID(tid), meta_user=old_user
    )
    aid = await owner_session.scalar(
        text("SELECT artifact_id FROM tool_artifact_storage WHERE thread_id=:t"), {"t": tid}
    )
    await owner_session.execute(
        text(
            "INSERT INTO artifact_computations(artifact_id,run_id,started_at,attempts) VALUES "
            "(:a,'old-run','2026-01-01T00:00:00+00:00',2)"
        ),
        {"a": aid},
    )
    await owner_session.commit()
    users_file, _ = _write_fixtures(tmp_path)
    path = _resume_manifest(tmp_path, account="mig_compute", pg_user_id=uid, thread_id=tid)
    manifest = json.loads(path.read_text())
    manifest["threads"][0]["metadata_user_id"] = old_user
    path.write_text(json.dumps(manifest))
    try:
        r = _run_tool("apply", path, users_file, base_url=base_url)
        assert r.returncode == 0, r.stderr
        row = (
            await owner_session.execute(
                text(
                    "SELECT c.tenant_id,c.user_id,c.attempts,c.started_at,a.tenant_id FROM "
                    "artifact_computations c JOIN tool_artifact_storage a USING(artifact_id) "
                    "WHERE c.artifact_id=:a"
                ),
                {"a": aid},
            )
        ).one()
        assert row[0] == row[4] and row[1] == uid
        assert row[2] == 2 and row[3] == "2026-01-01T00:00:00+00:00"
    finally:
        await owner_session.execute(
            text("DELETE FROM artifact_computations WHERE artifact_id=:a"), {"a": aid}
        )
        await owner_session.commit()


async def test_pg_manifest_account_must_match_explicit_uuid(
    owner_session, schema, tmp_path
) -> None:
    """不得用另一个存在用户的 UUID 替换清单所命名的 PG 主体。"""
    tid = uuid.uuid4()
    uid = await _seed_pg(
        owner_session, account="mig_expected", thread_id=tid, meta_user=str(uuid.uuid4())
    )
    other = str(uuid.uuid4())
    await owner_session.execute(
        text(
            "INSERT INTO users(id,account,display_name,password_hash) VALUES "
            "(:i,'mig_other','other','x')"
        ),
        {"i": other},
    )
    await owner_session.commit()
    path = _resume_manifest(tmp_path, account="mig_expected", pg_user_id=other, thread_id=str(tid))
    users_file, _ = _write_fixtures(tmp_path)
    res = _run_tool("preflight", path, users_file)
    assert json.loads(res.stdout)["clean"] is False
    assert uid != other
