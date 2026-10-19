"""物理退役旧 MCP 人员授权（change simplify-agent-business-authorization 任务 5.1）.

明确删除：
- `mcp_identity_bindings` 及其专属索引/策略/约束/ACL（逐项显式移除，不使用
  `DROP CASCADE`，不扩大删除范围）；
- 如果本库历史上存在同名旧权限定义，删除其 `role_permissions` 关联与
  `permissions` 行（正式分库后本库通常没有这些表，存在时也按精确 code 删除）。

保留：通用身份/角色表、业务台账/选择/事件/审计、`iam_reference_*`、历史迁移。
不创建 dump/快照/备份表/旧表改名；本迁移提交后只允许向前修复。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_retire_mcp_bindings"
down_revision = "0021_business_authorization"
branch_labels = None
depends_on = None

OLD_PERMISSION_CODES = ("enterprise:mcp", "mcp:binding:read", "mcp:binding:manage")


def _relation_exists(bind: sa.engine.Connection, name: str) -> bool:
    return bind.execute(sa.text("SELECT to_regclass(:name)"), {"name": name}).scalar() is not None


def upgrade() -> None:
    bind = op.get_bind()
    if _relation_exists(bind, "public.mcp_identity_bindings"):
        # 1) 专属策略（显式移除；DROP TABLE 不会报错但审计清单要求逐项）
        bind.execute(
            sa.text("DROP POLICY IF EXISTS tenant_isolation ON public.mcp_identity_bindings")
        )
        # 2) 专属索引（含唯一部分索引）
        for index in (
            "idx_bindings_tenant",
            "ux_bindings_tenant_server_default",
            "ux_bindings_tenant_server_member",
        ):
            bind.execute(sa.text(f"DROP INDEX IF EXISTS public.{index}"))
        # 3) 外键/检查约束
        for constraint in (
            "fk_i4_bindings_membership_same_tenant",
            "fk_i4_bindings_tenant",
            "bindings_status_valid",
        ):
            bind.execute(
                sa.text(
                    f"ALTER TABLE public.mcp_identity_bindings "
                    f"DROP CONSTRAINT IF EXISTS {constraint}"
                )
            )
        # 4) 运行角色 ACL（角色存在才撤销；不存在时零项）
        bind.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'air_agent_runtime') THEN
                        EXECUTE 'REVOKE ALL ON TABLE public.mcp_identity_bindings'
                                ' FROM air_agent_runtime';
                    END IF;
                END
                $$;
                """
            )
        )
        # 5) 删除表本身（不带 CASCADE；未知依赖会让事务失败并要求人工处理）
        bind.execute(sa.text("DROP TABLE public.mcp_identity_bindings"))

    # 6) 旧权限定义（仅当本库确实存在通用权限表时；精确 code，不做前缀删除）
    if _relation_exists(bind, "public.permissions"):
        codes = list(OLD_PERMISSION_CODES)
        if _relation_exists(bind, "public.role_permissions"):
            bind.execute(
                sa.text(
                    "DELETE FROM public.role_permissions WHERE permission_id IN "
                    "(SELECT id FROM public.permissions WHERE code = ANY(:codes))"
                ),
                {"codes": codes},
            )
        bind.execute(
            sa.text("DELETE FROM public.permissions WHERE code = ANY(:codes)"),
            {"codes": codes},
        )


def downgrade() -> None:
    raise RuntimeError(
        "旧 MCP 人员授权已物理退役；按本 change 约束不提供旧表/旧授权回退，"
        "请使用新业务授权与受保护连接配置，或从新版本向前修复。"
    )
