"""DDL guardrails for retiring manual Skill selection without rewriting evidence."""

from __future__ import annotations

import datetime as dt
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0031_retire_skill_selection.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("retire_skill_selection", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_copies_historical_fields_before_dropping_active_columns(monkeypatch):
    migration = _load_migration()

    class RecordingOperations:
        calls: list[tuple[str, tuple[object, ...]]] = []

        def __getattr__(self, name):
            def record(*args, **kwargs):
                if kwargs:
                    args = (*args, kwargs)
                self.calls.append((name, args))

            return record

    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()

    update_index = next(
        i
        for i, (name, args) in enumerate(operations.calls)
        if name == "execute" and "jsonb_build_object" in str(args[0])
    )
    dropped_columns = [
        i
        for i, (name, args) in enumerate(operations.calls)
        if name == "drop_column" and args[1] in {"skill_mode", "skill_id", "material_version"}
    ]
    assert dropped_columns and update_index < min(dropped_columns)
    update_sql = str(operations.calls[update_index][1][0])
    assert all(field in update_sql for field in ("skill_mode", "skill_id", "material_version"))
    assert migration.revision == "0031_retire_skill_selection"
    assert migration.down_revision == "0030_bqa_mcp_index"


def test_downgrade_blocks_when_v3_selection_cannot_restore_skill_fields(monkeypatch):
    migration = _load_migration()
    operations = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(scalar=lambda _query: True),
    )
    monkeypatch.setattr(migration, "op", operations)

    with pytest.raises(RuntimeError, match="v3 capability selections exist"):
        migration.downgrade()


def test_orm_keeps_retired_evidence_but_exposes_no_active_skill_columns():
    from app.models_ledger import AgentCapabilitySelection

    columns = AgentCapabilitySelection.__table__.columns
    assert "retired_skill_selection" in columns
    assert {"skill_mode", "skill_id", "material_version"}.isdisjoint(columns.keys())
    assert str(columns["digest_version"].server_default.arg) == "3"


def test_readback_does_not_project_retired_skill_evidence_or_legacy_source():
    from app.services.ledger import capability_selection_snapshot

    selection = SimpleNamespace(
        id=uuid.uuid4(),
        selection_digest="old-digest",
        digest_version=2,
        mcp_mode="auto",
        tool_ids=[],
        catalog_version=None,
        selection_source="user_explicit",
        validation_time=dt.datetime(2026, 10, 30, tzinfo=dt.UTC),
        retired_skill_selection={
            "skill_mode": "explicit",
            "skill_id": "skill:basic-qa:retired",
            "material_version": "sha256:historical",
        },
    )

    snapshot = capability_selection_snapshot(selection)
    assert snapshot["selection_source"] == "automatic"
    assert snapshot["digest_version"] == 2
    assert snapshot["mcp"] == {"mode": "auto", "tool_ids": []}
    assert not {"skill", "skill_mode", "skill_id", "material_version"}.intersection(snapshot)
