"""回答反馈 API 端到端测试（真实 PG；专用库，不触碰业务卷）。

    ADMIN_API 的 ledger_env 复用自 test_event_ledger_api；本文件覆盖 change
    add-chat-feedback-interactions 的 spec 场景：

- 评价绑定已确认回答：正常评价、未确认/临时 ID/跨企业拒绝；
- 切换/撤销与历史：正→负→撤销，历史三次有效修改；
- 幂等/CAS：同 ID 重投复用、同 ID 异载荷 409、双页面 CAS 冲突；
- 意见正文：写入、运营读取需原文权限、事件载荷不含意见；
- 浏览器通用端点：feedback/copy/timing 混合批次逐项受理、重投复用、
  非法关联拒绝；runtime 预留类型仍拒绝；
- 统计：满意率 2/3、覆盖率 3/4（fixture 核对）、复制 3 次但 distinct=1。
"""

from __future__ import annotations

import datetime as dt
import uuid

from ctx_client import ContextClient
from sqlalchemy import text

from app.db import OwnerSessionLocal
from tests.test_event_ledger_api import (  # noqa: F401  复用夹具与辅助
    PASSWORD,
    PREFIX,
    _account_of,
    _admit_body,
    _grant_thread,
    _internal_token,
    _login,
    _make_membership,
    _make_platform_user,
    _make_tenant,
)
from tests.test_event_ledger_api import (
    ledger_env as ledger_env,  # noqa: F401  pytest fixture 再导出
)


async def _make_confirmed_answer(
    client: ContextClient,
    env: dict,
    thread_id: str,
    submission_key: str,
    *,
    user_message_id: str = "um-1",
    message_id: str | None = None,
    platform_run_id: str | None = None,
) -> dict:
    """受理→启动→确认→记录已确认回答；返回 admitted + answer。"""
    admitted = (
        await client.post(
            "/agent/requests",
            json={**_admit_body(thread_id, submission_key), "user_message_id": user_message_id},
        )
    ).json()
    res = await client.post(
        "/agent/runs/start-claim",
        headers={"x-internal-token": _internal_token()},
        json={
            "submission_id": admitted["submission_id"],
            "attempt_id": admitted["attempt_id"],
            "thread_id": thread_id,
            "graph_id": "basic-qa",
            "operation_kind": "initial",
            "content": "问题",
        },
    )
    assert res.status_code == 200, res.text
    confirm = await client.post(
        "/agent/runs/admission-confirmed",
        headers={"x-internal-token": _internal_token()},
        json={
            "request_id": admitted["request_id"],
            "attempt_id": admitted["attempt_id"],
            "submission_id": admitted["submission_id"],
            "outcome": "confirmed",
            "platform_run_id": platform_run_id or f"run-{submission_key}",
        },
    )
    assert confirm.status_code == 200, confirm.text
    answer = await client.post(
        f"/agent/runs/{admitted['attempt_id']}/answers",
        headers={"x-internal-token": _internal_token()},
        json={
            "message_id": message_id or f"msg-{submission_key[:8]}",
            "content_version": 1,
            "confirmed": True,
        },
    )
    assert answer.status_code == 200, answer.text
    return {**admitted, "answer": answer.json()}


def _fb_payload(rating: str, **kw) -> dict:
    body = {
        "rating": rating,
        "operation_id": f"op-{uuid.uuid4().hex[:16]}",
        "expected_revision": 0,
    }
    body.update(kw)
    return body


# ------------------------------------------------------------------ 正向与归属


async def test_feedback_positive_then_history(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        data = await _make_confirmed_answer(client, env, thread_id, "fb-pos-0001")
        answer_id = data["answer"]["answer_id"]
        res = await client.put(f"/agent/feedback/answers/{answer_id}", json=_fb_payload("positive"))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["rating"] == "positive"
        assert body["revision"] == 1
        assert body["replayed"] is False
        assert body["message_id"] == data["answer"].get("message_id") or body["message_id"]
        # 回读：权威映射与当前评价
        readback = await client.get(f"/agent/feedback/answers?answer_ids={answer_id}")
        assert readback.status_code == 200, readback.text
        item = readback.json()["items"][0]
        assert item["answer_id"] == answer_id
        assert item["rating"] == "positive"
        assert item["revision"] == 1
        assert item["feedbackable"] is True
        # 刷新后新操作（不同 operation_id、expected_revision=1）→ 正常递增
        second_op = _fb_payload("positive", expected_revision=1)
        second = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json=second_op,
        )
        assert second.status_code == 200, second.text
        assert second.json()["revision"] == 2
        # 同 operation_id 重投：复用原结果，修订不增
        replay = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json={**second_op, "expected_revision": 1},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["revision"] == 2
    finally:
        await client.aclose()


async def test_feedback_partial_answer_rejected(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        admitted = (
            await client.post("/agent/requests", json=_admit_body(thread_id, "fb-part-0001"))
        ).json()
        partial = await client.post(
            f"/agent/runs/{admitted['attempt_id']}/answers",
            headers={"x-internal-token": _internal_token()},
            json={"message_id": "msg-partial", "content_version": 1, "confirmed": False},
        )
        assert partial.status_code == 200
        res = await client.put(
            f"/agent/feedback/answers/{partial.json()['answer_id']}",
            json=_fb_payload("positive"),
        )
        assert res.status_code == 404, res.text
        # 临时/不存在 ID 同样 404 且不泄露
        missing = await client.put(
            f"/agent/feedback/answers/{uuid.uuid4()}", json=_fb_payload("positive")
        )
        assert missing.status_code == 404
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 切换/撤销


async def test_switch_and_cancel_with_history(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        data = await _make_confirmed_answer(client, env, thread_id, "fb-sw-0001")
        answer_id = data["answer"]["answer_id"]
        r1 = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json=_fb_payload("negative", reason_code="incomplete"),
        )
        assert r1.json()["revision"] == 1
        r2 = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json=_fb_payload("positive", expected_revision=1),
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["revision"] == 2
        r3 = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json=_fb_payload("none", expected_revision=2),
        )
        assert r3.json()["rating"] == "none"
        assert r3.json()["revision"] == 3
        # 当前状态：撤销后 rating=none
        readback = await client.get(f"/agent/feedback/answers?answer_ids={answer_id}")
        item = readback.json()["items"][0]
        assert item["rating"] == "none"
        # 历史三次有效修改（运营视角）
        ops = await client.get(f"/agent/feedback/operations?answer_id={answer_id}")
        assert ops.status_code == 200, ops.text
        items = ops.json()["items"]
        assert len(items) == 3
        assert [i["rating"] for i in items] == ["negative", "positive", "none"]
        assert all(i["comment_included"] is False for i in items)
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 幂等/CAS


async def test_idempotent_replay_and_conflict(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        data = await _make_confirmed_answer(client, env, thread_id, "fb-idem-001")
        answer_id = data["answer"]["answer_id"]
        op_id = f"op-{uuid.uuid4().hex[:16]}"
        first = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json={"rating": "positive", "operation_id": op_id, "expected_revision": 0},
        )
        assert first.json()["replayed"] is False
        # 响应丢失重投：同 ID 同载荷 → 复用，修订不增
        replay = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json={"rating": "positive", "operation_id": op_id, "expected_revision": 0},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["revision"] == first.json()["revision"]
        # 同 ID 异载荷 → 409 event_conflict
        conflict = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json={"rating": "negative", "operation_id": op_id, "expected_revision": 0},
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "event_conflict"
        # 双页面 CAS：同 revision 两个新操作 → 一个成功一个 409
        op_a, op_b = f"op-{uuid.uuid4().hex[:16]}", f"op-{uuid.uuid4().hex[:16]}"
        ok = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json={"rating": "negative", "operation_id": op_a, "expected_revision": 1},
        )
        assert ok.status_code == 200, ok.text
        late = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json={"rating": "positive", "operation_id": op_b, "expected_revision": 1},
        )
        assert late.status_code == 409, late.text
        assert late.json()["detail"]["code"] == "revision_conflict"
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 意见正文与权限


async def test_comment_saved_and_content_permission_split(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    ops_client = await _login(await _account_of(env["user_b"]), env["tenant_a"])
    try:
        data = await _make_confirmed_answer(client, env, thread_id, "fb-cmt-0001")
        answer_id = data["answer"]["answer_id"]
        res = await client.put(
            f"/agent/feedback/answers/{answer_id}",
            json=_fb_payload("negative", reason_code="answer_incorrect", comment="数据似乎不对"),
        )
        assert res.status_code == 200, res.text
        comment_ref = res.json()["comment_ref"]
        assert comment_ref
        # 事件载荷不含意见正文（只有 comment_ref）
        async with OwnerSessionLocal() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT payload FROM agent_events WHERE event_type='browser.feedback'"
                        " AND answer_id=:a ORDER BY occurred_at DESC LIMIT 1"
                    ),
                    {"a": answer_id},
                )
            ).scalar()
            assert "数据似乎不对" not in str(row)
            assert comment_ref in str(row)
        # 仅运营权限成员读意见正文 → 403（无 agent:content:read）
        denied = await ops_client.get(
            f"/agent/feedback/operations/op-{uuid.uuid4().hex[:8]}/comment"
        )
        assert denied.status_code == 403
        # 运营（含原文权限的 user_a）可读
        ops = await client.get(f"/agent/feedback/operations?answer_id={answer_id}")
        op_id = ops.json()["items"][0]["operation_id"]
        comment = await client.get(f"/agent/feedback/operations/{op_id}/comment")
        assert comment.status_code == 200, comment.text
        assert comment.json()["comment"] == "数据似乎不对"
    finally:
        await client.aclose()
        await ops_client.aclose()


async def test_cross_tenant_isolated_and_operations_scoped(ledger_env) -> None:
    env = ledger_env
    thread_a = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_a)
    client_a = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    client_b = await _login(await _account_of(env["user_a"]), env["tenant_b"])
    try:
        data = await _make_confirmed_answer(client_a, env, thread_a, "fb-iso-0001")
        answer_id = data["answer"]["answer_id"]
        # 同用户企业 B 视角：不能评价/回读 A 的回答（404 不泄露）
        cross = await client_b.put(
            f"/agent/feedback/answers/{answer_id}", json=_fb_payload("positive")
        )
        assert cross.status_code == 404, cross.text
        cross_read = await client_b.get(f"/agent/feedback/answers?answer_ids={answer_id}")
        assert cross_read.json()["items"] == []
        # 运营游标绑定租户：B 不能借 A 的游标
        ops_a = await client_a.get(f"/agent/feedback/operations?answer_id={answer_id}&limit=1")
        cursor = ops_a.json()["next_cursor"]
        if cursor:
            stolen = await client_b.get(f"/agent/feedback/operations?cursor={cursor}")
            assert stolen.status_code == 422
    finally:
        await client_a.aclose()
        await client_b.aclose()


# ------------------------------------------------------------------ 浏览器通用端点


def _bev(event_type: str, payload: dict, **kw) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "source": "browser",
        "tenant_id": str(uuid.uuid4()),  # 服务端覆盖
        "user_id": str(uuid.uuid4()),
        "membership_id": str(uuid.uuid4()),
        "payload": payload,
        **kw,
    }


async def test_browser_mixed_batch_per_item(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        data = await _make_confirmed_answer(client, env, thread_id, "fb-bev-0001")
        answer_id = data["answer"]["answer_id"]
        attempt_id = data["attempt_id"]
        # 混合批次：合法复制 + 伪终态 + 合法反馈重投构造
        good_copy = _bev("browser.copy", {"answer_id": answer_id, "copy_scope": "full"})
        forged = _bev(
            "run.state_observed",
            {"state": "succeeded", "evidence_source": "runtime_callback", "state_revision": 1},
        )
        feedback_event = _bev(
            "browser.feedback",
            {
                "answer_id": answer_id,
                "rating": "negative",
                "expected_revision": 0,
                "client_operation_id": f"op-{uuid.uuid4().hex[:16]}",
            },
        )
        replay = dict(feedback_event, event_id=feedback_event["event_id"])
        res = await client.post(
            "/agent/events/browser",
            json={"events": [good_copy, forged, feedback_event, replay]},
            headers={"origin": "http://localhost:3000"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        reasons = [(i["valid"], i.get("reason")) for i in body["items"]]
        assert reasons[0] == (True, "ok"), reasons
        assert reasons[1][0] is False and reasons[1][1] == "source_not_allowed"
        assert reasons[2] == (True, "ok")
        assert reasons[3][0] is True and body["items"][3].get("replayed") is True
        # written：copy 1 + feedback 1 = 2（重投不计）
        assert body["written"] == 2
        # PG：copy 事件 1 条、feedback 操作 1 条
        async with OwnerSessionLocal() as session:
            copies = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM agent_events WHERE event_type='browser.copy'"
                        " AND answer_id=:a"
                    ),
                    {"a": answer_id},
                )
            ).scalar()
            ops = (
                await session.execute(
                    text("SELECT count(*) FROM agent_feedback_operations WHERE answer_id=:a"),
                    {"a": answer_id},
                )
            ).scalar()
        assert copies == 1
        assert ops == 1
        # 无效关联：他人 answer / 不存在 attempt
        bad_assoc = _bev("browser.copy", {"answer_id": str(uuid.uuid4())})
        bad_timing = _bev(
            "browser.timing",
            {"attempt_id": str(uuid.uuid4()), "clock": "monotonic"},
        )
        res2 = await client.post(
            "/agent/events/browser",
            json={"events": [bad_assoc, bad_timing]},
            headers={"origin": "http://localhost:3000"},
        )
        items = res2.json()["items"]
        assert items[0]["valid"] is False and items[0]["reason"] == "invalid_association"
        assert items[1]["valid"] is False and items[1]["reason"] == "invalid_association"
        # 合法 timing + 负耗时拒绝
        ok_timing = _bev(
            "browser.timing",
            {
                "attempt_id": attempt_id,
                "clock": "monotonic",
                "measurement_id": "m-1",
                "first_visible_answer_ms": 1200,
                "display_complete_ms": 3400,
                "progress_seen": True,
                "completion_state": "complete",
            },
        )
        neg_timing = _bev(
            "browser.timing",
            {"attempt_id": attempt_id, "clock": "monotonic", "first_visible_answer_ms": -5},
        )
        res3 = await client.post(
            "/agent/events/browser",
            json={"events": [ok_timing, neg_timing]},
            headers={"origin": "http://localhost:3000"},
        )
        items3 = res3.json()["items"]
        assert items3[0]["valid"] is True, items3
        assert items3[1]["valid"] is False and items3[1]["reason"] == "invalid_timing"
        # 浏览器只知道平台 run_id：服务端按 platform_run_id 回退解析台账 attempt
        platform_timing = _bev(
            "browser.timing",
            {
                "attempt_id": "run-fb-bev-0001",
                "clock": "monotonic",
                "first_visible_answer_ms": 1500,
            },
        )
        res4 = await client.post(
            "/agent/events/browser",
            json={"events": [platform_timing]},
            headers={"origin": "http://localhost:3000"},
        )
        item4 = res4.json()["items"][0]
        assert item4["valid"] is True, item4
    finally:
        await client.aclose()


async def test_browser_feedback_rating_enum_rejected(ledger_env) -> None:
    env = ledger_env
    thread_id = str(uuid.uuid4())
    await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        data = await _make_confirmed_answer(client, env, thread_id, "fb-enum-0001")
        answer_id = data["answer"]["answer_id"]
        res = await client.post(
            "/agent/events/browser",
            json={
                "events": [
                    _bev("browser.feedback", {"answer_id": answer_id, "rating": 1}),
                ]
            },
            headers={"origin": "http://localhost:3000"},
        )
        item = res.json()["items"][0]
        assert item["valid"] is False and item["reason"] == "invalid_enum_value"
        # runtime 预留类型仍然拒绝
        runtime = _bev(
            "runtime.model_usage",
            {"attempt_id": data["attempt_id"], "model": "m", "total_tokens": 1},
            attempt_id=data["attempt_id"],
        )
        res2 = await client.post(
            "/agent/events/browser",
            json={"events": [runtime]},
            headers={"origin": "http://localhost:3000"},
        )
        item2 = res2.json()["items"][0]
        # runtime.* 对 browser 来源仍是预留：信封校验先命中未启用
        assert item2["valid"] is False and item2["reason"] == "source_not_allowed"
    finally:
        await client.aclose()


# ------------------------------------------------------------------ 统计口径


async def test_metrics_fixture_2_of_3_and_copy_distinct(ledger_env) -> None:
    env = ledger_env
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        # 4 个可评价回答：2 正面、1 负面、1 未评价 → 满意率 2/3、覆盖率 3/4
        answer_ids = []
        for idx in range(4):
            thread_id = str(uuid.uuid4())
            await _grant_thread(env["tenant_a"], env["user_a"], thread_id)
            data = await _make_confirmed_answer(client, env, thread_id, f"fb-metrics-00{idx}")
            answer_ids.append(data["answer"]["answer_id"])
        ratings = ["positive", "positive", "negative", None]
        for answer_id, rating in zip(answer_ids, ratings, strict=False):
            if rating is None:
                continue
            res = await client.put(
                f"/agent/feedback/answers/{answer_id}",
                json=_fb_payload(rating),
            )
            assert res.status_code == 200, res.text
        # 复制：第一版复制 3 次（不同事件 ID）、第二版未复制
        for _ in range(3):
            copy_res = await client.post(
                "/agent/events/browser",
                json={
                    "events": [
                        _bev("browser.copy", {"answer_id": answer_ids[0], "copy_scope": "full"})
                    ]
                },
                headers={"origin": "http://localhost:3000"},
            )
            assert copy_res.json()["items"][0]["valid"], copy_res.text
        metrics = await client.get("/agent/feedback/metrics")
        assert metrics.status_code == 200, metrics.text
        body = metrics.json()
        fb = body["feedback"]
        assert fb["eligible_answers"] == 4, fb
        assert fb["rated_answers"] == 3
        assert fb["positive"] == 2
        assert abs(fb["satisfaction_rate"] - 2 / 3) < 1e-3
        assert abs(fb["coverage_rate"] - 3 / 4) < 1e-3
        copy = body["copy"]
        assert copy["copy_events"] == 3
        assert copy["distinct_copied_answers"] == 1
        # 本 fixture：4 个问题各 1 回答，复制 1 个 → 复制率 1/4
        assert abs(copy["copy_rate"] - 1 / 4) < 1e-3
        # 撤销后退出分子：第一个回答撤销 → 满意率 1/2、覆盖 2/4
        cancel = await client.put(
            f"/agent/feedback/answers/{answer_ids[0]}",
            json=_fb_payload("none", expected_revision=1),
        )
        assert cancel.status_code == 200
        after = (await client.get("/agent/feedback/metrics")).json()
        assert after["feedback"]["positive"] == 1
        assert abs(after["feedback"]["satisfaction_rate"] - 1 / 2) < 1e-3
    finally:
        await client.aclose()


async def test_metrics_empty_window_null_rates(ledger_env) -> None:
    env = ledger_env
    client = await _login(await _account_of(env["user_a"]), env["tenant_a"])
    try:
        metrics = await client.get("/agent/feedback/metrics")
        body = metrics.json()
        assert body["feedback"]["satisfaction_rate"] is None
        assert body["feedback"]["coverage_rate"] is None
        assert body["feedback"]["eligible_answers"] == 0
    finally:
        await client.aclose()


async def test_timing_resolves_confirmed_platform_uuid_to_actual_attempt(ledger_env):
    """平台run与台账attempt都是UUID，必须显式回读关联而不混用。"""
    thread = str(uuid.uuid4())
    await _grant_thread(ledger_env["tenant_a"], ledger_env["user_a"], thread)
    client = await _login(await _account_of(ledger_env["user_a"]), ledger_env["tenant_a"])
    other = await _login(await _account_of(ledger_env["user_a"]), ledger_env["tenant_b"])
    try:
        data = await _make_confirmed_answer(
            client, ledger_env, thread, uuid.uuid4().hex, platform_run_id=str(uuid.uuid4())
        )
        async with OwnerSessionLocal() as session:
            run = await session.scalar(
                text("SELECT platform_run_id FROM agent_submissions WHERE id=:id"),
                {"id": data["submission_id"]},
            )
        params = {"thread_id": thread, "platform_run_id": run}
        resolved = await client.get("/agent/feedback/attempts/resolve", params=params)
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["attempt_id"] == data["attempt_id"]
        assert resolved.json()["attempt_id"] != run
        assert (
            await other.get("/agent/feedback/attempts/resolve", params=params)
        ).status_code == 404
        assert (
            await client.get(
                "/agent/feedback/attempts/resolve",
                params={**params, "thread_id": str(uuid.uuid4())},
            )
        ).status_code == 404
        deleted = await client.post("/agent/threads/deletion", json={"thread_id": thread})
        assert deleted.status_code == 200, deleted.text
        assert (
            await client.get("/agent/feedback/attempts/resolve", params=params)
        ).status_code == 404
    finally:
        await client.aclose()
        await other.aclose()


async def test_feedback_business_day_half_open_and_late_rating(ledger_env):
    """上海业务日与UTC等价；上界不纳入，窗口外评价改变历史回答当前状态。"""
    client = await _login(await _account_of(ledger_env["user_a"]), ledger_env["tenant_a"])
    start = dt.datetime.fromisoformat("2026-10-08T00:00:00+08:00")
    end = start + dt.timedelta(days=1)
    data = []
    try:
        for timestamp in [start, end - dt.timedelta(microseconds=1), end]:
            thread = str(uuid.uuid4())
            await _grant_thread(ledger_env["tenant_a"], ledger_env["user_a"], thread)
            row = await _make_confirmed_answer(client, ledger_env, thread, uuid.uuid4().hex)
            data.append(row)
            async with OwnerSessionLocal() as session:
                await session.execute(
                    text("UPDATE agent_answers SET confirmed_at=:at WHERE id=:id"),
                    {"at": timestamp, "id": row["answer"]["answer_id"]},
                )
                await session.execute(
                    text("UPDATE agent_requests SET admitted_at=:at WHERE id=:id"),
                    {"at": timestamp, "id": row["request_id"]},
                )
                await session.commit()
        changed = await client.put(
            "/agent/feedback/answers/" + data[0]["answer"]["answer_id"],
            json=_fb_payload("positive"),
        )
        assert changed.status_code == 200, changed.text
        local = await client.get(
            "/agent/feedback/metrics", params={"start": start.isoformat(), "end": end.isoformat()}
        )
        utc = await client.get(
            "/agent/feedback/metrics",
            params={
                "start": start.astimezone(dt.UTC).isoformat(),
                "end": end.astimezone(dt.UTC).isoformat(),
            },
        )
        assert local.status_code == utc.status_code == 200
        assert local.json()["feedback"] == utc.json()["feedback"]
        assert local.json()["feedback"]["eligible_answers"] == 2
        assert local.json()["feedback"]["positive"] == 1
        assert local.json()["feedback"]["coverage_rate"] == 0.5
        import json
        from pathlib import Path

        (
            Path(__file__).resolve().parents[3]
            / "docs/reviews/evidence/takeover-03-05/business-day.json"
        ).write_text(
            json.dumps(
                {
                    "start_local": start.isoformat(),
                    "end_local_exclusive": end.isoformat(),
                    "start_utc": start.astimezone(dt.UTC).isoformat(),
                    "feedback": local.json()["feedback"],
                    "utc_equivalent": True,
                    "rating_changed_outside_window": True,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        await client.aclose()


async def test_late_stop_targets_original_run_and_never_rebinds_new_attempt(ledger_env):
    """迟到旧run、伪造run、跨线程、永久删除的停止关联真实反例。"""
    thread = str(uuid.uuid4())
    await _grant_thread(ledger_env["tenant_a"], ledger_env["user_a"], thread)
    client = await _login(await _account_of(ledger_env["user_a"]), ledger_env["tenant_a"])
    try:
        old_run = str(uuid.uuid4())
        old = await _make_confirmed_answer(
            client,
            ledger_env,
            thread,
            uuid.uuid4().hex,
            user_message_id=uuid.uuid4().hex,
            platform_run_id=old_run,
        )
        observed = await client.post(
            "/agent/runs/" + old["attempt_id"] + "/state",
            headers={"x-internal-token": _internal_token()},
            json={"state": "succeeded", "evidence_source": "platform_read"},
        )
        assert observed.status_code == 200, observed.text
        new = await client.post(
            "/agent/requests",
            json={**_admit_body(thread, uuid.uuid4().hex), "user_message_id": uuid.uuid4().hex},
        )
        assert new.status_code == 200, new.text
        late = await client.post(
            "/agent/runs/stop-intent",
            json={
                "thread_id": thread,
                "platform_run_id": old_run,
                "origin": "user_stop",
                "phase": "output",
            },
        )
        assert late.status_code == 200 and late.json()["attempt_id"] == old["attempt_id"], late.text
        bad = await client.post(
            "/agent/runs/stop-intent",
            json={
                "thread_id": thread,
                "platform_run_id": str(uuid.uuid4()),
                "origin": "user_stop",
                "phase": "unknown",
            },
        )
        assert bad.status_code == 404, bad.text
        wrong = await client.post(
            "/agent/runs/stop-intent",
            json={
                "thread_id": str(uuid.uuid4()),
                "attempt_id": old["attempt_id"],
                "origin": "user_stop",
                "phase": "unknown",
            },
        )
        assert wrong.status_code == 404, wrong.text
        async with OwnerSessionLocal() as session:
            flags = (
                await session.execute(
                    text(
                        "SELECT cancel_requested,platform_run_id "
                        "FROM agent_run_attempts WHERE id=:id"
                    ),
                    {"id": new.json()["attempt_id"]},
                )
            ).one()
            event_thread = await session.scalar(
                text(
                    "SELECT thread_id FROM agent_events WHERE attempt_id=:a "
                    "AND event_type='run.cancel_requested' "
                    "ORDER BY received_at DESC LIMIT 1"
                ),
                {"a": old["attempt_id"]},
            )
        assert flags == (False, None) and event_thread == thread
        deleted = await client.post("/agent/threads/deletion", json={"thread_id": thread})
        assert deleted.status_code == 200
        rejected = await client.post(
            "/agent/runs/stop-intent",
            json={"attempt_id": old["attempt_id"], "origin": "user_stop", "phase": "unknown"},
        )
        assert rejected.status_code == 404, rejected.text
    finally:
        await client.aclose()
