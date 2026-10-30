"""Retire manually selected Skills while retaining immutable legacy evidence.

Revision ID: 0031_retire_skill_selection
Revises: 0030_bqa_mcp_index
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0031_retire_skill_selection"
down_revision = "0030_bqa_mcp_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Move old Skill choices to evidence storage, then remove active columns."""
    op.add_column(
        "agent_capability_selections",
        sa.Column("retired_skill_selection", JSONB(), nullable=True),
    )
    op.execute(
        "UPDATE agent_capability_selections "
        "SET retired_skill_selection = jsonb_build_object("
        "'skill_mode', skill_mode, 'skill_id', skill_id, "
        "'material_version', material_version)"
    )
    op.drop_constraint(
        "ck_agent_selections_skill_mode",
        "agent_capability_selections",
        type_="check",
    )
    op.drop_column("agent_capability_selections", "skill_mode")
    op.drop_column("agent_capability_selections", "skill_id")
    op.drop_column("agent_capability_selections", "material_version")
    op.alter_column(
        "agent_capability_selections",
        "digest_version",
        server_default="3",
    )


def downgrade() -> None:
    """Restore legacy columns only when every row has a legacy digest boundary.

    New v3 selections have no meaningful historical Skill selection. Blocking the
    downgrade avoids presenting a fabricated Skill choice as recovered evidence.
    """
    bind = op.get_bind()
    has_v3 = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM agent_capability_selections WHERE digest_version >= 3)"
        )
    )
    if has_v3:
        raise RuntimeError(
            "cannot downgrade 0031 while v3 capability selections exist; "
            "their retired Skill fields cannot be reconstructed"
        )

    op.add_column(
        "agent_capability_selections",
        sa.Column("skill_mode", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_capability_selections",
        sa.Column("skill_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_capability_selections",
        sa.Column("material_version", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE agent_capability_selections SET "
        "skill_mode = COALESCE(retired_skill_selection->>'skill_mode', 'auto'), "
        "skill_id = retired_skill_selection->>'skill_id', "
        "material_version = COALESCE(retired_skill_selection->>'material_version', 'auto')"
    )
    op.alter_column(
        "agent_capability_selections",
        "skill_mode",
        nullable=False,
        server_default="auto",
    )
    op.alter_column("agent_capability_selections", "material_version", nullable=False)
    op.create_check_constraint(
        "ck_agent_selections_skill_mode",
        "agent_capability_selections",
        "skill_mode IN ('auto', 'explicit')",
    )
    op.drop_column("agent_capability_selections", "retired_skill_selection")
    op.alter_column(
        "agent_capability_selections",
        "digest_version",
        server_default="2",
    )
