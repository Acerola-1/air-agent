"""轮次01 修复：归属 pending 状态、受限列授权、归档/计算 RLS 与维护角色（R01/R03/R08）

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12

对已按旧版 0003/0004 建库的实例做**幂等修复**（GRANT 天然幂等；约束/策略
先删后建）。声明链上的全新库执行本迁移同样安全。内容：

1. R01：`thread_ownership.source` 允许 `native_pending`——认领协议在平台
   确认"真实创建成功"前保持不可读；未知历史归属不能靠一次创建吸收。
2. R08：运行角色补上被遗漏的**最小**授权：`users.is_active`（平台停用/启用
   账号）与 `user_followed_regions` 的 UPDATE/DELETE（关注替换、带关注账号
   删除）；RLS 的 user_update/followed_user_scoped 策略继续限定作用域。
   不使用 GRANT ALL，不改 owner 语义。
3. R03：`tool_artifact_storage`/`artifact_computations` 启用 RLS 并只对
   `air_agent_runtime` 授予表权限；Agent 请求路径改走运行角色（非 owner、
   非 BYPASSRLS），计算表补租户/用户维度并以"同租户父归档"策略关联。
   过期清理/回填改由受限维护角色 `air_agent_maintenance`（bootstrap 可选
   创建）：只允许读元数据列、只允许把到期行改写为 expired 状态列，不能
   读取原文载荷，成为任意对话读取入口的可能被策略与列权限双重排除。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "air_agent_runtime"
MAINTENANCE_ROLE = "air_agent_maintenance"


def _role_exists(name: str) -> bool:
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": name}
        ).scalar()
    )


def _tenant_guc(column: str) -> str:
    return (
        f"{column} = NULLIF(current_setting('air_agent.tenant_id', true), '')"
        f" AND {column.replace('tenant_id', 'user_id')}"
        " = NULLIF(current_setting('air_agent.user_id', true), '')"
    )


def upgrade() -> None:
    # ---------------------------------------------------------------- R01
    op.execute(
        "ALTER TABLE thread_ownership DROP CONSTRAINT IF EXISTS thread_ownership_source_valid"
    )
    op.execute(
        "ALTER TABLE thread_ownership ADD CONSTRAINT thread_ownership_source_valid "
        "CHECK (source IN ('native', 'native_pending', 'migrated'))"
    )

    # ---------------------------------------------------------------- R08
    # 停用/启用是平台动作；列级授权 + user_update（本人或平台）策略双重限定。
    op.execute(f"GRANT UPDATE (is_active) ON users TO {RUNTIME_ROLE}")
    # 关注城市替换 = DELETE + INSERT；账号删除会级联清理关注行。
    op.execute(f"GRANT UPDATE, DELETE ON user_followed_regions TO {RUNTIME_ROLE}")

    # ---------------------------------------------------------------- R03
    op.execute("ALTER TABLE artifact_computations ADD COLUMN IF NOT EXISTS tenant_id TEXT")
    op.execute("ALTER TABLE artifact_computations ADD COLUMN IF NOT EXISTS user_id TEXT")
    # 同租户关联回填：能从父归档取到归属的行补齐；取不到的保持 NULL（不可读）。
    op.execute(
        """
        UPDATE artifact_computations c
        SET tenant_id = a.tenant_id, user_id = a.user_id
        FROM tool_artifact_storage a
        WHERE c.artifact_id = a.artifact_id
          AND (c.tenant_id IS NULL OR c.tenant_id = '')
        """
    )

    for table in ("tool_artifact_storage", "artifact_computations"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {RUNTIME_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS artifact_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY artifact_tenant_isolation ON {table} FOR ALL TO {RUNTIME_ROLE}"
            f" USING ({_tenant_guc('tenant_id')})"
            f" WITH CHECK ({_tenant_guc('tenant_id')})"
        )
    # 计算行的父归档绑定必须与 GUC 条件同一条策略：PostgreSQL 多条 permissive
    # 策略是 **OR** 合并——分开写会让"租户匹配"单独放行跨企业引用（轮次01 修复
    # 自检中复现，故此处合并为单一策略）。
    op.execute("DROP POLICY IF EXISTS artifact_tenant_isolation ON artifact_computations")
    g_tenant = "NULLIF(current_setting('air_agent.tenant_id', true), '')"
    g_user = "NULLIF(current_setting('air_agent.user_id', true), '')"
    parent_bind = (
        "EXISTS (SELECT 1 FROM tool_artifact_storage a"
        " WHERE a.artifact_id = artifact_computations.artifact_id"
        " AND a.tenant_id = artifact_computations.tenant_id"
        " AND a.user_id = artifact_computations.user_id)"
    )
    combined = f"tenant_id = {g_tenant} AND user_id = {g_user} AND {parent_bind}"
    op.execute(
        "CREATE POLICY artifact_computations_scope ON artifact_computations"
        f" FOR ALL TO {RUNTIME_ROLE}"
        f" USING ({combined}) WITH CHECK ({combined})"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifact_computations_tenant"
        " ON artifact_computations(tenant_id, user_id)"
    )

    # 受限维护角色（bootstrap 可选创建）：只能标记到期，看不到原始载荷。
    if _role_exists(MAINTENANCE_ROLE):
        op.execute(
            f"GRANT SELECT (artifact_id, tenant_id, user_id, thread_id, char_length,"
            f" kind, status, expires_at, created_at) ON tool_artifact_storage"
            f" TO {MAINTENANCE_ROLE}"
        )
        op.execute(
            f"GRANT UPDATE (raw_payload, summary, status, expires_at)"
            f" ON tool_artifact_storage TO {MAINTENANCE_ROLE}"
        )
        op.execute("DROP POLICY IF EXISTS artifact_maintenance ON tool_artifact_storage")
        op.execute(
            "CREATE POLICY artifact_maintenance ON tool_artifact_storage FOR ALL"
            f" TO {MAINTENANCE_ROLE}"
            " USING (current_setting('air_agent.artifact_maintenance', true) = 'on')"
            " WITH CHECK (current_setting('air_agent.artifact_maintenance', true) = 'on')"
        )


def downgrade() -> None:
    raise NotImplementedError(
        "0005 不提供 downgrade：授权/RLS 修复回滚等同重新引入验收缺陷，"
        "回退遵循受控备份恢复流程（0003/0004 同策略）"
    )
