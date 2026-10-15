"""admin API 测试会话前置：权限目录/平台角色/区划的项目化幂等 seed。

轮次01 评审要求"94/4 必须按声明迁移和前置目录在清洁专用库复现，不依赖业务库
手工授权"。此前权限目录、super_admin/viewer 平台角色与区划数据靠外部脚本准备
（setup_probe/seed_rls_probe），新库直接跑套件会大面积 setup 失败。本 conftest
在项目内声明这些前置（全部 ON CONFLICT 幂等），并同时准备 API 库
（DATABASE_URI）与 RLS/迁移测试库（TENANT_TEST_OWNER_URI，等价评审的
seed_rls_probe 前置目录）；区划仅在空表时导入。
"""

from __future__ import annotations

import os

for key in (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "MCP_SERVER_URL",
    "SILICONFLOW_API_KEY",
    "DEEPSEEK_API_KEY",
    "XFYUN_MAAS_API_KEY",
    "XIAOMI_MIMO_API_KEY",
    "SERPER_API_KEY",
    "ARK_API_KEY",
    "MAIN_LLM_API_KEY",
):
    os.environ.setdefault(key, "test")

os.environ.setdefault("TENANT_ENFORCE_RUNTIME_IDENTITY", "false")
os.environ.setdefault("TENANT_MCP_FIXTURE_MODE", "true")

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import _normalize_pg_uri
from app.db import OwnerSessionLocal
from scripts.seed import seed_permissions, seed_platform_roles


async def _seed(session) -> None:  # noqa: ANN001
    permissions = await seed_permissions(session)
    await seed_platform_roles(session, permissions)
    # 只读平台角色：旧全局 user_roles 授权路径的用例需要（等价评审 setup_probe）
    viewer_id = await session.scalar(
        text(
            "INSERT INTO roles(code,name,scope,is_system)"
            " VALUES ('viewer','只读角色','platform',true)"
            " ON CONFLICT (code) WHERE scope='platform' DO UPDATE SET name=EXCLUDED.name"
            " RETURNING id"
        )
    )
    for code, perm in permissions.items():
        if code.endswith(":read") or code in {
            "system",
            "business",
            "system:user",
            "system:role",
            "system:permission",
            "system:audit",
            "business:region",
            "business:followed",
        }:
            await session.execute(
                text(
                    "INSERT INTO role_permissions(role_id,permission_id)"
                    " VALUES (:r,:p) ON CONFLICT DO NOTHING"
                ),
                {"r": viewer_id, "p": perm.id},
            )
    existing_regions = await session.scalar(text("SELECT count(*) FROM regions"))
    if not existing_regions:
        from scripts.import_regions import load_dataset

        dataset = load_dataset()
        if isinstance(dataset, tuple):
            dataset = dataset[0]
        for entry in dataset:
            await session.execute(
                text(
                    "INSERT INTO regions(code,name,level,parent_code,sort)"
                    " VALUES (:c,:n,:l,:p,:s) ON CONFLICT (code) DO NOTHING"
                ),
                {
                    "c": entry[0],
                    "n": entry[1],
                    "l": entry[2],
                    "p": entry[3],
                    "s": entry[4],
                },
            )
    await session.commit()


async def _seed_into(uri: str) -> None:
    engine = create_async_engine(uri)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed(session)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def ensure_test_catalog() -> None:
    async with OwnerSessionLocal() as session:
        await _seed(session)
    # RLS/迁移专项库（评审 run_admin_suite 的双库结构）同样需要权限目录前置
    rls_raw = os.environ.get("TENANT_TEST_OWNER_URI", "")
    api_uri = str(OwnerSessionLocal.kw["bind"].url)  # type: ignore[attr-defined]
    if rls_raw:
        rls_uri = _normalize_pg_uri(rls_raw)
        if rls_uri != api_uri:
            await _seed_into(rls_uri)
