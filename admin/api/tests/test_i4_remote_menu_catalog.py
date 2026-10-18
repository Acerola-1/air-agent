from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.routers import auth


def test_remote_menu_is_display_only_and_uses_trusted_iam_codes(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "remote")

    async def forbidden_local_permission_query(_session) -> list:
        raise AssertionError("remote menu display must not query the removed permissions table")

    monkeypatch.setattr(auth, "load_permissions", forbidden_local_permission_query)
    nodes = asyncio.run(
        auth.menus(
            identity=SimpleNamespace(permissions=frozenset({"user:read", "unknown:future"})),
            session=object(),
        )
    )

    def flatten(items):
        flattened = {}
        for item in items:
            flattened[item.code] = item
            flattened.update(flatten(item.children))
        return flattened

    by_code = flatten(nodes)
    assert "system" in by_code and "system:user" in by_code
    assert "user:read" in by_code
    assert "user:create" not in by_code
    assert "unknown:future" not in by_code
    assert [node.name for node in nodes] == ["系统管理"]
