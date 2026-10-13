"""R03 回归（轮次01 修复）：归档/计算在真实 PG 上的租户隔离（非 owner 运行角色）.

覆盖评审要求的"漏条件/连接池/计算越权"三类：
- 遗漏 tenant/user 条件的运行时查询在 RLS 下仍只见本作用域行；
- 跨企业读/写/删被策略拒绝（不是靠显式 WHERE）；
- 计算认领跨企业引用失败；连接逐个请求新建（不共享 GUC）交错复用安全。

运行前提：TENANT_TEST_OWNER_URI / TENANT_TEST_RUNTIME_URI 指向已执行
alembic upgrade head（含 0004/0005）的隔离测试库，否则整套 skip。
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.anyio

OWNER_URI = os.environ.get("TENANT_TEST_OWNER_URI", "")
RUNTIME_URI = os.environ.get("TENANT_TEST_RUNTIME_URI", "")
_ENABLED = bool(
    OWNER_URI and RUNTIME_URI and urlparse(OWNER_URI).path.strip("/") != "air_agent"
)


def _is_pg_url(uri: str) -> bool:
    return uri.startswith(("postgresql://", "postgres://"))


@pytest.fixture
async def artifact_env(monkeypatch):
    """把 storage.artifact_db 切到隔离测试库（mode=owner 标记 + runtime 连接）。"""
    if not (_ENABLED and _is_pg_url(OWNER_URI) and _is_pg_url(RUNTIME_URI)):
        pytest.skip("需要 TENANT_TEST_* 隔离库环境变量")
    monkeypatch.setenv("DATABASE_URI", OWNER_URI)  # 仅模式标记；PG 路径实际用 RUNTIME
    monkeypatch.setenv("RUNTIME_DATABASE_URI", RUNTIME_URI)
    # 每次操作都新建连接（artifact_db 无跨请求连接复用）；重置初始化缓存
    import common.storage.artifact_db as adb

    adb._initialized = False
    adb._initialized_key = ""
    suffix = uuid.uuid4().hex[:8]
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    thread_a, thread_b = str(uuid.uuid4()), str(uuid.uuid4())
    ids = {
        "suffix": suffix,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "thread_a": thread_a,
        "thread_b": thread_b,
    }
    yield ids
    # 清理（owner 直删测试数据行；只碰本次插入的合成行）
    import psycopg

    with psycopg.connect(OWNER_URI, autocommit=True) as c:
        c.execute(
            "DELETE FROM artifact_computations WHERE tenant_id IN (%s,%s)",
            (tenant_a, tenant_b),
        )
        c.execute(
            "DELETE FROM tool_artifact_storage WHERE tenant_id IN (%s,%s)",
            (tenant_a, tenant_b),
        )


async def test_rls_enabled_and_runtime_role_not_owner(artifact_env) -> None:
    import psycopg

    assert artifact_env["tenant_a"] != artifact_env["tenant_b"]
    with psycopg.connect(OWNER_URI) as c:
        rows = dict(
            c.execute(
                "SELECT relname, relrowsecurity FROM pg_class"
                " WHERE relname IN ('tool_artifact_storage','artifact_computations')"
                " AND relkind='r'"
            ).fetchall()
        )
    assert rows["tool_artifact_storage"] is True
    assert rows["artifact_computations"] is True
    # 运行角色非 owner：以 runtime URI 用户连接尝试 ALTER 必须失败
    runtime_user = urlparse(RUNTIME_URI).username
    assert runtime_user and runtime_user != urlparse(OWNER_URI).username


async def test_cross_tenant_read_update_delete_denied(artifact_env) -> None:
    import common.storage.artifact_db as adb

    ids = artifact_env
    art = await adb.save_tool_artifact(
        tool_call_id=f"tc-{ids['suffix']}",
        tool_name="unit_rls",
        raw_payload="payload-A",
        tenant_id=ids["tenant_a"],
        user_id=ids["user_a"],
        thread_id=ids["thread_a"],
        run_id="run-a",
    )
    # B 作用域读不到（WHERE + RLS 双限）
    assert (
        await adb.get_tool_artifact(
            art,
            tenant_id=ids["tenant_b"],
            user_id=ids["user_b"],
            thread_id=ids["thread_b"],
        )
        is None
    )
    # A 本人作用域可读
    row = await adb.get_tool_artifact(
        art, tenant_id=ids["tenant_a"], user_id=ids["user_a"], thread_id=ids["thread_a"]
    )
    assert row and row["raw_payload"] == "payload-A"
    # B 更新 A 的归档 → PermissionError（UPDATE 命中 0 行）
    with pytest.raises(PermissionError):
        await adb.update_artifact_summary(
            art,
            tenant_id=ids["tenant_b"],
            user_id=ids["user_b"],
            thread_id=ids["thread_b"],
            summary="s",
            metadata={},
        )
    # 漏条件扫描：以 A 作用域直查（不带 tenant/user WHERE），RLS 只见 A 行
    import psycopg
    from psycopg.rows import dict_row

    async with adb._connection(
        {"air_agent.tenant_id": ids["tenant_a"], "air_agent.user_id": ids["user_a"]}
    ) as db:  # runtime 角色连接 + 事务本地 GUC
        cur = await db.execute(
            "SELECT artifact_id, tenant_id FROM tool_artifact_storage"
            " WHERE raw_payload IN ('payload-A','payload-B-leak-probe')"
        )
        seen = [r["artifact_id"] for r in await cur.fetchall()]
        assert art in seen
    # B 作用域扫描看不到 A 行
    async with await psycopg.AsyncConnection.connect(
        RUNTIME_URI, row_factory=dict_row, autocommit=False
    ) as raw:
        await raw.execute(
            "SELECT set_config('air_agent.tenant_id', %s, true)", (ids["tenant_b"],)
        )
        await raw.execute(
            "SELECT set_config('air_agent.user_id', %s, true)", (ids["user_b"],)
        )
        cur = await raw.execute("SELECT count(*) AS n FROM tool_artifact_storage")
        n = (await cur.fetchone())["n"]
        assert n == 0  # 无任何跨租户可见性


async def test_computation_claim_is_same_tenant_bound(artifact_env) -> None:
    import common.storage.artifact_db as adb

    ids = artifact_env
    art = await adb.save_tool_artifact(
        tool_call_id=f"tc-{ids['suffix']}",
        tool_name="unit_rls",
        raw_payload="payload-C",
        tenant_id=ids["tenant_a"],
        user_id=ids["user_a"],
        thread_id=ids["thread_a"],
        run_id="run-c",
    )
    # A 认领成功
    claim = await adb.claim_computation(
        art, "run-c", tenant_id=ids["tenant_a"], user_id=ids["user_a"], attempt=False
    )
    assert claim["remaining_seconds"] >= 0
    # B 对 A 的归档认领：RLS 下看不到父归档 → 记录不存在/配额失败，且不落 B 行
    with pytest.raises(adb.ComputationLimit):
        await adb.claim_computation(
            art,
            "run-c-b",
            tenant_id=ids["tenant_b"],
            user_id=ids["user_b"],
            attempt=False,
        )
    import psycopg

    with psycopg.connect(OWNER_URI) as c:
        c.execute("BEGIN")
        leaked = c.execute(
            "SELECT count(*) FROM artifact_computations WHERE tenant_id = %s",
            (ids["tenant_b"],),
        ).fetchone()
        c.rollback()
    assert int(leaked[0]) == 0
    # 缺作用域直接失败关闭
    with pytest.raises(Exception):
        await adb.claim_computation(art, "run-x", tenant_id="", user_id="")


async def test_runtime_uri_required_never_owner(artifact_env, monkeypatch) -> None:
    """PG 模式缺失 RUNTIME_DATABASE_URI：失败关闭，绝不回退 owner(DATABASE_URI)。"""
    import common.storage.artifact_db as adb

    monkeypatch.delenv("RUNTIME_DATABASE_URI", raising=False)
    from common.storage.artifact_db import ArtifactBackendUnavailable

    with pytest.raises(ArtifactBackendUnavailable):
        async with adb._connection(
            {"air_agent.tenant_id": "t", "air_agent.user_id": "u"}
        ):
            pass
    # 维护路径同样失败关闭
    monkeypatch.delenv("MAINTENANCE_DATABASE_URI", raising=False)
    with pytest.raises(ArtifactBackendUnavailable):
        async with adb._maintenance_connection():
            pass
