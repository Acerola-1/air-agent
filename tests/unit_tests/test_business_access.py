"""公共业务守卫：精确求交、状态语义与运行快照失败关闭."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from common.business_registry import BUSINESS_POLICY_VERSION  # noqa: E402
from common.identity.business_access import (  # noqa: E402
    BusinessAccessError,
    grant_for_graph,
    permissions_of,
)
from common.identity.runtime import (  # noqa: E402
    RuntimeIdentity,
    RuntimeIdentityRevoked,
    _validate_business_snapshot,
)


def _admin_identity(permissions: set[str]) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id="u-1", auth_revision=3),
        session=SimpleNamespace(ctx_version=2),
        tenant=SimpleNamespace(id="t-1", auth_revision=5),
        membership=SimpleNamespace(id="m-1", revision=7),
        permissions=frozenset(permissions),
        remote_verification_ref="ref-1",
    )


def test_valid_business_grant_freezes_exact_snapshot() -> None:
    grant = grant_for_graph(
        graph_id="basic-qa",
        identity=_admin_identity({"agent:business:basic-qa:use"}),
    )
    assert grant.business_code == "basic-qa"
    assert grant.permission == "agent:business:basic-qa:use"
    assert grant.policy_version == BUSINESS_POLICY_VERSION
    assert grant.ledger_fields()["business_graph_id"] == "basic-qa"
    assert grant.pinned_config()["ledger_business_permission"] == grant.permission


def test_missing_permission_is_403_and_does_not_leak_other_permissions() -> None:
    with pytest.raises(BusinessAccessError) as err:
        grant_for_graph(
            graph_id="data-analysis",
            identity=_admin_identity({"agent:business:basic-qa:use"}),
        )
    assert err.value.status_code == 403
    assert err.value.code == "business_not_permitted"
    assert "数据分析" in err.value.message


def test_unknown_graph_is_denied() -> None:
    with pytest.raises(BusinessAccessError) as err:
        grant_for_graph(
            graph_id="deep-research",
            identity=_admin_identity({"agent:business:basic-qa:use"}),
        )
    assert err.value.status_code == 403
    assert err.value.code == "business_unregistered"


def test_missing_tenant_context_is_401_and_platform_flag_does_not_bypass() -> None:
    identity = _admin_identity({"agent:business:basic-qa:use"})
    identity.tenant = None
    with pytest.raises(BusinessAccessError) as err:
        grant_for_graph(graph_id="basic-qa", identity=identity)
    assert err.value.status_code == 401
    # 仅有平台管理员/通配符权限不会获得业务使用权
    with pytest.raises(BusinessAccessError):
        grant_for_graph(
            graph_id="basic-qa",
            identity=_admin_identity({"*"}),
        )


def test_permissions_of_supports_runtime_identity_and_dict_actor() -> None:
    runtime = RuntimeIdentity(
        user_id="u",
        tenant_id="t",
        membership_id="m",
        session_id="s",
        permissions=frozenset({"agent:business:basic-qa:use"}),
    )
    assert permissions_of(runtime) == frozenset({"agent:business:basic-qa:use"})
    assert permissions_of({"permissions": ["a", "b"]}) == frozenset({"a", "b"})
    assert permissions_of(object()) == frozenset()


def _identity(**overrides) -> RuntimeIdentity:
    values = {
        "user_id": "u",
        "tenant_id": "t",
        "membership_id": "m",
        "session_id": "s",
        "permissions": frozenset({"agent:business:basic-qa:use"}),
        "business_code": "basic-qa",
        "business_policy_version": BUSINESS_POLICY_VERSION,
        "business_permission": "agent:business:basic-qa:use",
        "business_graph": "basic-qa",
    }
    values.update(overrides)
    return RuntimeIdentity(**values)


def test_runtime_snapshot_accepts_frozen_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setenv("TENANT_BUSINESS_SNAPSHOT_ENFORCED", "true")
    _validate_business_snapshot(_identity())


@pytest.mark.parametrize(
    "overrides",
    [
        {"business_code": None},
        {"business_policy_version": "old-policy"},
        {"business_permission": "agent:business:data-analysis:use"},
        {"business_graph": "data-analysis"},
        {"permissions": frozenset()},
    ],
)
def test_runtime_snapshot_rejects_missing_or_tampered_values(
    monkeypatch: pytest.MonkeyPatch, overrides: dict
) -> None:
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    monkeypatch.setenv("TENANT_BUSINESS_SNAPSHOT_ENFORCED", "true")
    with pytest.raises(RuntimeIdentityRevoked):
        _validate_business_snapshot(_identity(**overrides))


def test_runtime_snapshot_is_relaxed_only_outside_enforced_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "false")
    _validate_business_snapshot(_identity(business_code=None))
