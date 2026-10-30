"""运营后台 API 端到端测试（真实 PG；专用临时库）.

覆盖：
- 统计/运营/正文/处理四类权限；
- 用户/运行/反馈列表与游标；
- 正文精确版本分页、no-store、删除优先；
- 反馈处理 CAS/幂等/反馈修订变化/事务审计；
- 不支持过滤、窗口/limit 越界。

运行前需将 DATABASE_URI / ADMIN_RUNTIME_DATABASE_URI 指向已迁移到
0024_console_read_model 的空测试库（本文件不触碰正式 15432）。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from ctx_client import ContextClient
from sqlalchemy import text

from app.db import OwnerSessionLocal
from tests.test_event_ledger_api import (  # noqa: F401  复用夹具与辅助
    PREFIX,
    _account_of,
    _grant_thread,
    _login,
    _make_membership,
    _make_platform_user,
)
from tests.test_event_ledger_api import (
    ledger_env as ledger_env,  # noqa: F401  pytest fixture 再导出
)
from tests.test_feedback_api import _make_confirmed_answer


async def _seed_answer_content(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    data: dict,
    text_value: str,
    *,
    version: int = 1,
) -> None:
    import hashlib

    attempt_id = data["attempt_id"]
    request_id = data["request_id"]
    answer_id = data["answer"]["answer_id"]
    async with OwnerSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO agent_answer_contents"
                " (id, tenant_id, user_id, request_id, attempt_id, answer_id, message_id,"
                "  thread_id, content_version, content_sha256, content_text, summary,"
                "  total_chars, truncated, capture_status, captured_at, created_at)"
                " VALUES (gen_random_uuid(),:t,:u,:r,:a,:ans,NULL,"
                "  (SELECT thread_id FROM agent_requests WHERE id=:r AND tenant_id=:t),"
                "  :v,:h,:txt,:summary,:total,:truncated,'captured',now(),now())"
                " ON CONFLICT (tenant_id,answer_id,content_version) DO NOTHING"
            ),
            {
                "t": tenant_id,
                "u": user_id,
                "r": request_id,
                "a": attempt_id,
                "ans": answer_id,
                "v": version,
                "h": hashlib.sha256(text_value.encode()).hexdigest(),
                "txt": text_value,
                "summary": text_value[:100],
                "total": len(text_value),
                "truncated": len(text_value) > 65536,
            },
        )
        await session.commit()


async def _seed_feedback(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    data: dict,
    *,
    rating: str = "negative",
    revision: int = 1,
    comment: str | None = None,
) -> str | None:
    attempt_id = data["attempt_id"]
    request_id = data["request_id"]
    answer_id = data["answer"]["answer_id"]
    comment_id = None
    async with OwnerSessionLocal() as session:
        thread_id = (
            await session.execute(
                text("SELECT thread_id FROM agent_requests WHERE id=:r AND tenant_id=:t"),
                {"r": request_id, "t": tenant_id},
            )
        ).scalar_one()
        if comment is not None:
            comment_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO agent_feedback_comments"
                    " (id, tenant_id, user_id, answer_id, request_id, attempt_id,"
                    "  thread_id, comment_text, created_at)"
                    " VALUES (:i,:t,:u,:a,:r,:at,:th,:c,now())"
                ),
                {
                    "i": comment_id,
                    "t": tenant_id,
                    "u": user_id,
                    "a": answer_id,
                    "r": request_id,
                    "at": attempt_id,
                    "th": thread_id,
                    "c": comment,
                },
            )
        await session.execute(
            text(
                "INSERT INTO agent_feedback_current"
                " (id, tenant_id, user_id, answer_id, request_id, attempt_id, thread_id,"
                "  rating, reason_code, comment_id, revision, updated_at, created_at)"
                " VALUES (gen_random_uuid(),:t,:u,:a,:r,:at,:th,:rating,'answer_incorrect',"
                "  :c,:rev,now(),now())"
                " ON CONFLICT (tenant_id,user_id,answer_id) DO UPDATE SET"
                "  rating=EXCLUDED.rating, reason_code=EXCLUDED.reason_code,"
                "  comment_id=EXCLUDED.comment_id, revision=EXCLUDED.revision,"
                "  updated_at=now()"
            ),
            {
                "t": tenant_id,
                "u": user_id,
                "a": answer_id,
                "r": request_id,
                "at": attempt_id,
                "th": thread_id,
                "rating": rating,
                "c": comment_id,
                "rev": revision,
            },
        )
        await session.commit()
    return str(comment_id) if comment_id else None


async def _make_member(tenant_id: uuid.UUID, perms: list[str]) -> tuple[uuid.UUID, ContextClient]:
    suffix = uuid.uuid4().hex[:6]
    user_id = await _make_platform_user(f"{PREFIX}c6_{suffix}", [])
    await _make_membership(tenant_id, user_id, ["agent:business:basic-qa:use", *perms])
    client = await _login(await _account_of(user_id), tenant_id)
    return user_id, client


def _action_body(
    *,
    operation_id: str | None = None,
    expected_revision: int = 0,
    expected_feedback_revision: int = 1,
    action: str = "start",
    note: str | None = None,
) -> dict:
    return {
        "operation_id": operation_id or str(uuid.uuid4()),
        "expected_revision": expected_revision,
        "expected_feedback_revision": expected_feedback_revision,
        "action": action,
        "note": note,
    }


# ------------------------------------------------------------------ 权限


async def test_console_run_selection_snapshot_uses_mcp_only(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admission = await client.post(
            "/agent/requests",
            json={
                "thread_id": thread_id,
                "submission_key": "console-selection-v2",
                "graph_id": "basic-qa",
                "operation_kind": "initial",
                "content": "console capability snapshot",
                "capability_selection": {
                    "schema_version": "agent.capability-selection.v2",
                    "mcp": {"mode": "auto", "tool_ids": []},
                },
            },
        )
        assert admission.status_code == 200, admission.text
        response = await client.get(f"/agent/console/runs/{admission.json()['attempt_id']}")
        assert response.status_code == 200, response.text
        selection = response.json()["capability_selection"]
        assert selection["schema_version"] == "agent.capability-selection.v2"
        assert selection["mcp"] == {"mode": "auto", "tool_ids": []}
        assert selection["selection_source"] == "automatic"
        assert selection["digest_version"] == 3
        assert not {"skill", "skill_mode", "skill_id", "material_version"}.intersection(
            selection
        )
    finally:
        await client.aclose()


async def test_console_permission_matrix(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    owner_client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    data = await _make_confirmed_answer(owner_client, env, thread_id, "c6-perm-0001")
    await _seed_answer_content(env["tenant_a"], env["user_a"], data, "回答正文")
    await _seed_feedback(env["tenant_a"], env["user_a"], data, comment="用户意见")

    stats_user, stats_client = await _make_member(env["tenant_a"], ["agent:metrics:read"])
    try:
        assert (await stats_client.get("/agent/console/overview")).status_code == 200
        assert (await stats_client.get("/agent/console/users")).status_code == 403
        assert (await stats_client.get("/agent/console/runs")).status_code == 403
        assert (await stats_client.get("/agent/console/feedback")).status_code == 403
        assert (
            await stats_client.get(
                f"/agent/console/answers/{data['answer']['answer_id']}/content",
                params={"content_version": 1},
            )
        ).status_code == 403
    finally:
        await stats_client.aclose()

    ops_client = await _login(await _account_of(env["user_b"]), env["tenant_a"])
    try:
        # 只有运营、没有统计：聚合端点拒绝，明细可用。
        assert (await ops_client.get("/agent/console/overview")).status_code == 403
        assert (await ops_client.get("/agent/console/runs")).status_code == 200
        assert (await ops_client.get("/agent/console/feedback")).status_code == 200
        assert (
            await ops_client.get(f"/agent/console/requests/{data['request_id']}/content")
        ).status_code == 403
        assert (
            await ops_client.get(
                f"/agent/console/answers/{data['answer']['answer_id']}/content",
                params={"content_version": 1},
            )
        ).status_code == 403
        assert (
            await ops_client.post(
                f"/agent/console/feedback/{data['answer']['answer_id']}/actions",
                json=_action_body(action="start"),
            )
        ).status_code == 403
    finally:
        await ops_client.aclose()

    content_client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        assert (await content_client.get("/agent/console/users")).status_code == 200
        assert (await content_client.get("/agent/console/runs")).status_code == 200
        answer_page = await content_client.get(
            f"/agent/console/answers/{data['answer']['answer_id']}/content",
            params={"content_version": 1},
        )
        assert answer_page.status_code == 200, answer_page.text
        assert answer_page.headers["cache-control"] == "no-store"
        assert answer_page.json()["text"] == "回答正文"
        comment_page = await content_client.get(
            f"/agent/console/feedback/{data['answer']['answer_id']}/content",
            params={"kind": "comment"},
        )
        assert comment_page.status_code == 200, comment_page.text
        assert comment_page.json()["text"] == "用户意见"
        # 有正文但无 manage：写端点拒绝。
        assert (
            await content_client.post(
                f"/agent/console/feedback/{data['answer']['answer_id']}/actions",
                json=_action_body(action="start"),
            )
        ).status_code == 403
    finally:
        await content_client.aclose()
        await owner_client.aclose()


# ------------------------------------------------------------------ 列表与游标


async def test_console_lists_and_cursor_boundaries(ledger_env) -> None:
    env = ledger_env
    threads = [str(uuid.uuid4()), str(uuid.uuid4())]
    data = []
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        for index, thread_id in enumerate(threads):
            await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
            item = await _make_confirmed_answer(client, env, thread_id, f"c6-list-{index:04d}")
            await _seed_answer_content(env["tenant_a"], env["user_a"], item, f"回答{index}")
            await _seed_feedback(
                env["tenant_a"], env["user_a"], item, rating="negative", revision=1
            )
            data.append(item)

        feedback = await client.get("/agent/console/feedback")
        assert feedback.status_code == 200, feedback.text
        body = feedback.json()
        assert body["meta"]["schema_version"] == "admin-console-v1"
        assert any(item["answer_id"] == data[0]["answer"]["answer_id"] for item in body["items"])
        item = next(
            row for row in body["items"] if row["answer_id"] == data[0]["answer"]["answer_id"]
        )
        assert item["needs_review"] is True
        assert item["case_state"] == "pending"

        runs = await client.get("/agent/console/runs", params={"limit": 1})
        assert runs.status_code == 200, runs.text
        first = runs.json()
        assert first["has_more"] is True
        assert first["next_cursor"]
        # 翻页保持稳定数据截止
        second = await client.get(
            "/agent/console/runs",
            params={"limit": 1, "cursor": first["next_cursor"]},
        )
        assert second.status_code == 200, second.text
        assert second.json()["meta"]["as_of"] == first["meta"]["as_of"]
        assert second.json()["items"][0]["attempt_id"] != first["items"][0]["attempt_id"]

        # 坏游标 400
        bad = await client.get("/agent/console/runs", params={"cursor": "not-a-cursor"})
        assert bad.status_code == 400
        assert bad.json()["detail"]["code"] == "bad_cursor"

        # 不支持过滤 / 窗口越界 422
        unsupported = await client.get(
            "/agent/console/overview", params={"user_id": str(env["user_a"])}
        )
        assert unsupported.status_code == 422
        assert unsupported.json()["detail"]["code"] == "unsupported_filter"
        custom_missing = await client.get("/agent/console/overview", params={"window": "custom"})
        assert custom_missing.status_code == 422
        assert custom_missing.json()["detail"]["code"] == "invalid_window"
        too_large = await client.get("/agent/console/runs", params={"limit": 201})
        assert too_large.status_code == 422

        # 跨企业游标：A 签发，切到 B 后重放必须拒绝。
        tenant_b_cursor = first["next_cursor"]
        switched = await client.post(
            "/auth/switch-tenant",
            json={"tenant_id": str(env["tenant_b"]), "expected_ctx_version": int(client.claim[1])},
        )
        assert switched.status_code == 200, switched.text
        cross = await client.get("/agent/console/runs", params={"cursor": tenant_b_cursor})
        assert cross.status_code == 400
        assert cross.json()["detail"]["code"] == "bad_cursor"

        # 同一用户切换企业：B 没有数据，不能看到 A 的运行/提问。
        b_runs = await client.get("/agent/console/runs")
        assert b_runs.status_code == 200
        assert b_runs.json()["items"] == []
        b_overview = await client.get("/agent/console/overview")
        assert b_overview.status_code == 200
        assert b_overview.json()["requests"]["questions"]["value"] == 0
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 正文分页与删除


async def test_console_content_pagination_and_deletion(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        data = await _make_confirmed_answer(client, env, thread_id, "c6-content-0001")
        long_text = "字" * 9000
        await _seed_answer_content(env["tenant_a"], env["user_a"], data, long_text)
        first = await client.get(
            f"/agent/console/answers/{data['answer']['answer_id']}/content",
            params={"content_version": 1, "limit": 8000},
        )
        assert first.status_code == 200, first.text
        page = first.json()
        assert len(page["text"]) == 8000
        assert page["total_chars"] == 9000
        assert page["next_offset"] == 8000
        second = await client.get(
            f"/agent/console/answers/{data['answer']['answer_id']}/content",
            params={"content_version": 1, "limit": 8000, "offset": 8000},
        )
        assert second.status_code == 200
        assert len(second.json()["text"]) == 1000
        assert second.json()["next_offset"] is None

        # 未采集版本：明确状态，不 404，也不回填
        missing = await client.get(
            f"/agent/console/answers/{data['answer']['answer_id']}/content",
            params={"content_version": 2},
        )
        assert missing.status_code == 404  # 回答版本本身不存在

        # 删除优先：下一次读取 404
        delete = await client.post("/agent/threads/deletion", json={"thread_id": thread_id})
        assert delete.status_code == 200, delete.text
        after = await client.get(
            f"/agent/console/answers/{data['answer']['answer_id']}/content",
            params={"content_version": 1},
        )
        assert after.status_code == 404
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 反馈处理


async def test_console_feedback_triage_state_machine(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    owner_client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    manager_user, manager = await _make_member(
        env["tenant_a"],
        [
            "agent:metrics:read",
            "agent:operations:read",
            "agent:content:read",
            "agent:feedback:manage",
        ],
    )
    try:
        data = await _make_confirmed_answer(owner_client, env, thread_id, "c6-case-0001")
        await _seed_answer_content(env["tenant_a"], env["user_a"], data, "回答正文")
        await _seed_feedback(env["tenant_a"], env["user_a"], data, comment="用户意见正文")
        answer_id = data["answer"]["answer_id"]
        op = str(uuid.uuid4())

        # start：创建 case，reviewed_feedback_revision 追上当前评价
        started = await manager.post(
            f"/agent/console/feedback/{answer_id}/actions",
            json=_action_body(operation_id=op, action="start"),
        )
        assert started.status_code == 200, started.text
        start_body = started.json()
        assert start_body["state"] == "in_progress"
        assert start_body["revision"] == 1
        assert start_body["replayed"] is False

        # 同 ID 同载荷：复用
        replay = await manager.post(
            f"/agent/console/feedback/{answer_id}/actions",
            json=_action_body(operation_id=op, action="start"),
        )
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert replay.json()["revision"] == 1

        # 同 ID 异载荷：409
        conflict = await manager.post(
            f"/agent/console/feedback/{answer_id}/actions",
            json=_action_body(operation_id=op, action="start", note="不同载荷"),
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "event_conflict"

        # 旧 case 修订：409 revision_conflict
        stale = await manager.post(
            f"/agent/console/feedback/{answer_id}/actions",
            json=_action_body(expected_revision=0, action="resolve", note="处理完成"),
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "revision_conflict"

        # resolve 必须有说明
        no_note = await manager.post(
            f"/agent/console/feedback/{answer_id}/actions",
            json=_action_body(expected_revision=1, action="resolve"),
        )
        assert no_note.status_code == 422
        assert no_note.json()["detail"]["code"] == "note_required"

        resolved = await manager.post(
            f"/agent/console/feedback/{answer_id}/actions",
            json=_action_body(expected_revision=1, action="resolve", note="已修复并回读"),
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["state"] == "resolved"
        assert resolved.json()["revision"] == 2
        note_ref = resolved.json()["note_ref"]
        assert note_ref

        # 已处理不能再 start / resolve，只能 reopen
        invalid = await manager.post(
            f"/agent/console/feedback/{answer_id}/actions",
            json=_action_body(expected_revision=2, action="start"),
        )
        assert invalid.status_code == 409
        assert invalid.json()["detail"]["code"] == "invalid_transition"
        reopened = await manager.post(
            f"/agent/console/feedback/{answer_id}/actions",
            json=_action_body(expected_revision=2, action="reopen"),
        )
        assert reopened.status_code == 200
        assert reopened.json()["state"] == "in_progress"

        # 用户评价修订变化：必须先回读
        async with OwnerSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE agent_feedback_current SET revision=revision+1,"
                    " rating='negative', updated_at=now()"
                    " WHERE tenant_id=:t AND answer_id=:a"
                ),
                {"t": env["tenant_a"], "a": answer_id},
            )
            await session.commit()
        changed = await manager.post(
            f"/agent/console/feedback/{answer_id}/actions",
            json=_action_body(
                expected_revision=3,
                expected_feedback_revision=1,
                action="resolve",
                note="旧评价修订",
            ),
        )
        assert changed.status_code == 409
        assert changed.json()["detail"]["code"] == "feedback_changed"

        # 处理说明正文：精确引用可读；审计不含正文
        note_page = await manager.get(
            f"/agent/console/feedback/{answer_id}/content",
            params={"kind": "note", "ref": note_ref},
        )
        assert note_page.status_code == 200, note_page.text
        assert note_page.json()["text"] == "已修复并回读"
        async with OwnerSessionLocal() as session:
            detail = (
                (
                    await session.execute(
                        text(
                            "SELECT detail::text FROM audit_logs WHERE tenant_id=:t"
                            " AND action LIKE 'agent.feedback.case.%'"
                        ),
                        {"t": env["tenant_a"]},
                    )
                )
                .scalars()
                .all()
            )
        assert detail
        assert all("已修复并回读" not in item for item in detail)

        # 处理说明引用列表：不预取正文
        refs = await manager.get(
            f"/agent/console/feedback/{answer_id}/content", params={"kind": "note"}
        )
        assert refs.status_code == 200
        assert refs.json()["text"] is None
        assert refs.json()["items"]
        assert all(item["content_included"] is False for item in refs.json()["items"])

        # 反馈列表 needs_review：新负面修订后为 true
        listing = await manager.get("/agent/console/feedback")
        assert listing.status_code == 200
        row = next(item for item in listing.json()["items"] if item["answer_id"] == answer_id)
        assert row["needs_review"] is True
        assert row["case_revision"] == 3
    finally:
        await manager.aclose()
        await owner_client.aclose()


async def test_console_feedback_audit_failure_rolls_back(ledger_env, monkeypatch) -> None:
    """审计写入失败必须整体回滚：不留下半个处理状态/动作/说明."""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    owner_client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    manager_user, manager = await _make_member(
        env["tenant_a"],
        [
            "agent:operations:read",
            "agent:content:read",
            "agent:feedback:manage",
        ],
    )
    try:
        data = await _make_confirmed_answer(owner_client, env, thread_id, "c6-audit-0001")
        await _seed_feedback(env["tenant_a"], env["user_a"], data)
        answer_id = data["answer"]["answer_id"]

        def boom(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr("app.services.audit.record_audit", boom)
        with pytest.raises(RuntimeError):
            await manager.post(
                f"/agent/console/feedback/{answer_id}/actions",
                json=_action_body(action="resolve", note="审计故障下不应落库"),
            )
        async with OwnerSessionLocal() as session:
            case_count = await session.scalar(
                text(
                    "SELECT count(*) FROM agent_feedback_cases WHERE tenant_id=:t AND answer_id=:a"
                ),
                {"t": env["tenant_a"], "a": answer_id},
            )
            action_count = await session.scalar(
                text(
                    "SELECT count(*) FROM agent_feedback_case_actions"
                    " WHERE tenant_id=:t AND answer_id=:a"
                ),
                {"t": env["tenant_a"], "a": answer_id},
            )
            note_count = await session.scalar(
                text(
                    "SELECT count(*) FROM agent_feedback_case_notes"
                    " WHERE tenant_id=:t AND answer_id=:a"
                ),
                {"t": env["tenant_a"], "a": answer_id},
            )
        assert case_count == 0
        assert action_count == 0
        assert note_count == 0
    finally:
        await manager.aclose()
        await owner_client.aclose()


async def test_console_feedback_concurrent_cas(ledger_env) -> None:
    """两个运营使用同一修订并发提交：至多一个成功，另一个 409 revision_conflict."""
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    owner_client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    manager_user, manager_a = await _make_member(
        env["tenant_a"],
        ["agent:operations:read", "agent:content:read", "agent:feedback:manage"],
    )
    manager_b = await _login(await _account_of(manager_user), env["tenant_a"])
    try:
        data = await _make_confirmed_answer(owner_client, env, thread_id, "c6-cas-0001")
        await _seed_feedback(env["tenant_a"], env["user_a"], data)
        answer_id = data["answer"]["answer_id"]

        async def start(client, note):
            return await client.post(
                f"/agent/console/feedback/{answer_id}/actions",
                json=_action_body(action="start", note=note),
            )

        first, second = await asyncio.gather(
            start(manager_a, "并发 A"),
            start(manager_b, "并发 B"),
        )
        statuses = sorted([first.status_code, second.status_code])
        assert statuses == [200, 409], (first.text, second.text)
        conflict = first if first.status_code == 409 else second
        assert conflict.json()["detail"]["code"] == "revision_conflict"

        async with OwnerSessionLocal() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT state, revision FROM agent_feedback_cases"
                            " WHERE tenant_id=:t AND answer_id=:a"
                        ),
                        {"t": env["tenant_a"], "a": answer_id},
                    )
                )
                .mappings()
                .first()
            )
            actions = await session.scalar(
                text(
                    "SELECT count(*) FROM agent_feedback_case_actions"
                    " WHERE tenant_id=:t AND answer_id=:a"
                ),
                {"t": env["tenant_a"], "a": answer_id},
            )
        assert row["state"] == "in_progress"
        assert row["revision"] == 1
        assert actions == 1
    finally:
        await manager_a.aclose()
        await manager_b.aclose()
        await owner_client.aclose()
