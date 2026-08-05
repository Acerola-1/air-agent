"""PermissionClassifyMiddleware 测试."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from common.middleware import permission_classify_middleware as module
from common.middleware.permission_classify_middleware import (
    PermissionClassifyMiddleware,
)
from common.permission import AllowedRegion, PermissionResult

pytestmark = pytest.mark.anyio


def _days_ago_str(days: int) -> str:
    """返回北京时间 N 天前的日期字符串，避免用例中的硬编码日期随时间失效."""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    return (now - timedelta(days=days)).strftime("%Y-%m-%d")


class _MockTool:
    """用于模拟 MCP 工具."""

    def __init__(self, name: str, result: object) -> None:
        self.name = name
        self.ainvoke = AsyncMock(return_value=result)


def _ensure_mcp_patch():
    """返回 mcp_client.ensure_mcp_tools 的 async mock patch."""
    return patch("common.mcp_client.ensure_mcp_tools", new_callable=AsyncMock)


# ── MCP 返回规范化测试 ─────────────────────────────────────


def test_coerce_jsonish_unwraps_mcp_text_output() -> None:
    wrapped = {
        "output": [
            {
                "type": "text",
                "text": '{"found":true,"bound_region":{"level":"district","code":"37e1574b2","name":"博山区"}}',
            }
        ]
    }

    result = module._coerce_jsonish(wrapped)

    assert isinstance(result, dict)
    assert result["found"] is True
    assert result["bound_region"]["level"] == "district"


def test_coerce_jsonish_unwraps_langsmith_outputs_shape() -> None:
    wrapped = {
        "outputs": {
            "output": [
                {
                    "type": "text",
                    "text": '{"found":true,"bound_region":{"level":"district","code":"37e1574b2","name":"博山区"}}',
                }
            ]
        }
    }

    result = module._coerce_jsonish(wrapped)

    assert isinstance(result, dict)
    assert result["found"] is True
    assert result["bound_region"]["name"] == "博山区"


# ── no_check 场景测试 ──────────────────────────────────────


async def test_classifies_knowledge_question_as_no_check() -> None:
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="PM2.5是什么？")],
    }

    with _ensure_mcp_patch() as mock_ensure:
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert result["permission_need"] == "no_check"
    mock_ensure.assert_not_awaited()


async def test_no_check_does_not_call_agent() -> None:
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="PM2.5是什么？")],
    }

    with _ensure_mcp_patch() as mock_ensure:
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert result["permission_need"] == "no_check"
    mock_ensure.assert_not_awaited()


async def test_missing_user_id_degrades_to_no_check() -> None:
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="昨天全国PM2.5是多少？")],
    }

    with _ensure_mcp_patch() as mock_ensure:
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert result["permission_need"] == "no_check"
    mock_ensure.assert_not_awaited()


async def test_classification_exception_defaults_to_no_check(monkeypatch) -> None:
    def fail(_question: str) -> str:
        raise RuntimeError("classification failed")

    monkeypatch.setattr(module, "classify_permission_need", fail)
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="昨天全国PM2.5是多少？")],
        "user_id": "test-user",
    }

    with _ensure_mcp_patch() as mock_ensure:
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert result["permission_need"] == "no_check"
    mock_ensure.assert_not_awaited()


# ── 规则引擎路径测试（新路径）───────────────────────────────


async def test_need_check_uses_rule_engine() -> None:
    """need_check 场景应使用规则引擎（非旧 Agent）."""
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="昨天杭州市PM2.5是多少？")],
        "user_id": "test-user-1",
    }

    profile_tool = _MockTool(
        "get_user_profile",
        {
            "output": [
                {
                    "type": "text",
                    "text": (
                        '{"found":true,"province":{"code":"330000","name":"浙江省"},'
                        '"city":{"code":"330100","name":"杭州市"},'
                        '"bound_region":{"code":"330100","name":"杭州市","level":"city"}}'
                    ),
                }
            ]
        },
    )
    region_tool = _MockTool(
        "resolve_region_scope",
        {
            "found": True,
            "code": "330100",
            "name": "杭州市",
            "level": "city",
            "province_code": "330000",
            "city_code": "330100",
        },
    )
    events: list[dict[str, object]] = []

    with (
        _ensure_mcp_patch(),
        patch.object(
            module.mcp_client, "get_profile_mcp_tools", return_value=[profile_tool]
        ),
        patch.object(
            module.mcp_client, "get_region_mcp_tools", return_value=[region_tool]
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "city": "杭州市",
                    "target_level": "city",
                    "time_granularity": "daily",
                    "original_time_span": ["2026-05-27", "2026-05-27"],
                }
            ),
        ),
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=events.append),
        )

    assert {"type": "progress", "message": "正在验证权限信息..."} in events
    assert result is not None
    assert result["permission_need"] in ("need_check", "uncertain")
    assert "permission_result" in result
    # 规则引擎应返回确定性结果
    pr = result["permission_result"]
    assert "permitted" in pr
    profile_tool.ainvoke.assert_awaited_once_with({"userId": "test-user-1"})


@pytest.mark.parametrize(
    "slots",
    [
        {"province": None, "city": None, "district": None},
        {"province": "None", "city": "null", "district": ""},
        {"province": "NULL", "city": "none", "district": "  "},
    ],
)
async def test_empty_region_slots_skip_region_resolution(
    slots: dict[str, object],
) -> None:
    mw = PermissionClassifyMiddleware()

    with patch(
        "common.permission.mcp_tools.resolve_region", new_callable=AsyncMock
    ) as mock_resolve:
        result = await mw._resolve_requested_region(
            slots,
            {
                "bound_region": {"name": "新郑市", "level": "district"},
                "province": {"name": "河南省"},
                "city": {"name": "郑州市"},
            },
            "查一下昨天的空气质量",
        )

    assert result is None
    mock_resolve.assert_not_awaited()


async def test_province_query_does_not_pass_user_city_context() -> None:
    mw = PermissionClassifyMiddleware()

    with patch(
        "common.permission.mcp_tools.resolve_region",
        new=AsyncMock(
            return_value={
                "found": True,
                "ambiguous": False,
                "region": {"name": "河南省", "level": "province"},
                "province": {"name": "河南省"},
                "city": None,
            }
        ),
    ) as mock_resolve:
        result = await mw._resolve_requested_region(
            {
                "province": "河南省",
                "city": "None",
                "district": "None",
                "target_level": "province",
            },
            {
                "bound_region": {"name": "新郑市", "level": "district"},
                "province": {"name": "河南省"},
                "city": {"name": "郑州市"},
            },
            "2024年河南省空气质量怎么样？",
        )

    assert result is not None
    mock_resolve.assert_awaited_once_with(
        "河南省",
        city="",
        province="",
    )


async def test_auto_fills_bound_region_into_query_overrides() -> None:
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="查一下昨天的空气质量")],
        "user_id": "test-user-1",
    }
    profile = {
        "found": True,
        "province": {"name": "河南省"},
        "city": {"name": "郑州市"},
        "bound_region": {"name": "新郑市", "level": "district"},
        "frequent_regions": [],
    }

    with (
        _ensure_mcp_patch(),
        patch.object(
            module, "classify_permission_need", new=AsyncMock(return_value="need_check")
        ),
        patch.object(
            mw,
            "_prefetch_deterministic_context",
            new=AsyncMock(
                return_value={
                    "beijing_time": "2026-05-28 15:00:00",
                    "user_profile": profile,
                }
            ),
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "province": "None",
                    "city": "null",
                    "district": "",
                    "target_level": None,
                    "time_granularity": "year",
                    "original_time_span": ["2024-01-01", "2024-12-31"],
                }
            ),
        ),
        patch(
            "common.permission.mcp_tools.resolve_region", new_callable=AsyncMock
        ) as mock_resolve,
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    mock_resolve.assert_not_awaited()
    pr = result["permission_result"]
    assert pr["permitted"] is True
    assert pr["fix_strategy"] == ""
    assert pr["auto_filled"] is True
    assert pr["allowed_region"] == {
        "name": "新郑市",
        "level": "district",
        "data_type": "detail",
    }
    assert "已自动补全查询区域" in pr["correction_text"]
    assert "新郑市" in pr["correction_text"]
    assert result["permission_query_overrides"]["auto_filled"] is True
    assert result["permission_query_overrides"]["region"]["name"] == "新郑市"


async def test_unsupported_region_slot_is_ignored_and_bound_region_is_used() -> None:
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="昨天空气质量如何")],
        "user_id": "test-user-1",
    }
    profile = {
        "found": True,
        "province": {"name": "河南省"},
        "city": {"name": "商丘市"},
        "bound_region": {"name": "商丘市", "level": "city"},
        "frequent_regions": [],
    }

    with (
        _ensure_mcp_patch(),
        patch.object(
            module, "classify_permission_need", new=AsyncMock(return_value="need_check")
        ),
        patch.object(
            mw,
            "_prefetch_deterministic_context",
            new=AsyncMock(
                return_value={
                    "beijing_time": "2026-05-28 15:00:00",
                    "user_profile": profile,
                }
            ),
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "province": "全国",
                    "city": None,
                    "district": None,
                    "target_level": "province",
                    "time_granularity": "daily",
                    "original_time_span": ["2026-05-27", "2026-05-27"],
                }
            ),
        ),
        patch(
            "common.permission.mcp_tools.resolve_region", new_callable=AsyncMock
        ) as mock_resolve,
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    mock_resolve.assert_not_awaited()
    pr = result["permission_result"]
    assert pr["auto_filled"] is True
    assert pr["fix_strategy"] == ""
    assert pr["allowed_region"] == {
        "name": "商丘市",
        "level": "city",
        "data_type": "summary",
    }
    assert result["permission_query_overrides"]["region"]["name"] == "商丘市"


# ── wrap_model_call 测试 ──────────────────────────────────


async def test_injects_permission_context_before_model_call() -> None:
    permission_result = PermissionResult(
        permitted=False,
        fix_strategy="replace_region",
        allowed_region=AllowedRegion(name="杭州市", level="city", data_type="summary"),
        correction_text="修正说明",
    ).model_dump()
    state = {
        "permission_need": "need_check",
        "permission_result": permission_result,
        "permission_query_overrides": {
            "fix_strategy": "replace_region",
            "correction_text": "修正说明",
            "region": {
                "code": "330100",
                "name": "杭州市",
                "level": "city",
                "data_type": "summary",
            },
        },
    }

    class Request:
        system_message = SystemMessage(content="base prompt")

        def __init__(self, state):
            self.state = state

        def override(self, **kwargs):
            modified = Request(self.state)
            modified.system_message = kwargs.get("system_message", self.system_message)
            return modified

    mw = PermissionClassifyMiddleware()
    result = mw.wrap_model_call(
        Request(state),
        lambda request: request.system_message.content,
    )
    rendered = str(result)

    assert "<permission_context>" in rendered
    assert '"permission_need": "need_check"' in rendered
    assert '"fix_strategy": "replace_region"' in rendered
    assert '"permission_query_overrides"' in rendered
    assert '"region": {"code": "330100"' in rendered


async def test_replace_region_false_does_not_short_circuit_model_call() -> None:
    permission_result = PermissionResult(
        permitted=False,
        fix_strategy="replace_region",
        allowed_region=AllowedRegion(
            name="西湖区", level="district", data_type="detail"
        ),
        correction_text="已为您展示您所在西湖区的明细数据",
    ).model_dump()
    state = {
        "permission_need": "need_check",
        "permission_result": permission_result,
        "permission_query_overrides": {
            "fix_strategy": "replace_region",
            "correction_text": "已为您展示您所在西湖区的明细数据",
            "region": {
                "code": "330106",
                "name": "西湖区",
                "level": "district",
                "data_type": "detail",
            },
        },
    }

    class Request:
        system_message = SystemMessage(content="base prompt")

        def __init__(self, state):
            self.state = state

        def override(self, **kwargs):
            modified = Request(self.state)
            modified.system_message = kwargs.get("system_message", self.system_message)
            return modified

    handler = MagicMock(side_effect=lambda request: request.system_message.content)
    mw = PermissionClassifyMiddleware()
    result = mw.wrap_model_call(Request(state), handler)
    rendered = str(result)

    assert "<permission_context>" in rendered
    assert '"permission_query_overrides"' in rendered
    assert "西湖区" in rendered
    handler.assert_called_once()


async def test_reject_strategy_short_circuits_model_call() -> None:
    permission_result = PermissionResult(
        permitted=False,
        fix_strategy="reject",
        correction_text="无法获取用户权限画像，拒绝访问",
    ).model_dump()
    state = {
        "permission_need": "need_check",
        "permission_result": permission_result,
    }

    class Request:
        system_message = SystemMessage(content="base prompt")

        def __init__(self, state):
            self.state = state

        def override(self, **kwargs):
            modified = Request(self.state)
            modified.system_message = kwargs.get("system_message", self.system_message)
            return modified

    handler = MagicMock()
    mw = PermissionClassifyMiddleware()
    result = mw.wrap_model_call(Request(state), handler)

    assert isinstance(result, AIMessage)
    assert result.content == "无法获取用户权限画像，拒绝访问"
    handler.assert_not_called()


async def test_permitted_false_empty_strategy_short_circuits_model_call() -> None:
    permission_result = PermissionResult(
        permitted=False,
        fix_strategy="",
        correction_text="您无权访问该数据",
    ).model_dump()
    state = {
        "permission_need": "need_check",
        "permission_result": permission_result,
    }

    class Request:
        system_message = SystemMessage(content="base prompt")

        def __init__(self, state):
            self.state = state

        def override(self, **kwargs):
            modified = Request(self.state)
            modified.system_message = kwargs.get("system_message", self.system_message)
            return modified

    handler = MagicMock()
    mw = PermissionClassifyMiddleware()
    result = mw.wrap_model_call(Request(state), handler)

    assert isinstance(result, AIMessage)
    assert result.content == "您无权访问该数据"
    handler.assert_not_called()


async def test_auto_filled_empty_strategy_does_not_short_circuit_model_call() -> None:
    permission_result = PermissionResult(
        permitted=True,
        fix_strategy="",
        allowed_region=AllowedRegion(
            name="新郑市", level="district", data_type="detail"
        ),
        correction_text="根据您的关联区域，已自动补全查询区域为新郑市。",
        matched_rules=["region_auto_filled_from_bound_region"],
        debug_context={"auto_filled": True},
        auto_filled=True,
    ).model_dump()
    state = {
        "permission_need": "need_check",
        "permission_result": permission_result,
        "permission_query_overrides": {
            "fix_strategy": "",
            "correction_text": "根据您的关联区域，已自动补全查询区域为新郑市。",
            "auto_filled": True,
            "region": {
                "name": "新郑市",
                "level": "district",
                "data_type": "detail",
            },
        },
    }

    class Request:
        system_message = SystemMessage(content="base prompt")

        def __init__(self, state):
            self.state = state

        def override(self, **kwargs):
            modified = Request(self.state)
            modified.system_message = kwargs.get("system_message", self.system_message)
            return modified

    handler = MagicMock(side_effect=lambda request: request.system_message.content)
    mw = PermissionClassifyMiddleware()
    result = mw.wrap_model_call(Request(state), handler)
    rendered = str(result)

    assert "<permission_context>" in rendered
    assert '"auto_filled": true' in rendered
    assert '"matched_rules": ["region_auto_filled_from_bound_region"]' in rendered
    assert '"debug_context": {"auto_filled": true}' in rendered
    assert "permission_query_overrides.region 存在时" in rendered
    assert "新郑市" in rendered
    handler.assert_called_once()


async def test_no_check_does_not_inject_context() -> None:
    state = {
        "permission_need": "no_check",
    }

    class Request:
        system_message = SystemMessage(content="base prompt")

        def __init__(self, state):
            self.state = state

        def override(self, **kwargs):
            modified = Request(self.state)
            modified.system_message = kwargs.get("system_message", self.system_message)
            return modified

    mw = PermissionClassifyMiddleware()
    result = mw.wrap_model_call(
        Request(state),
        lambda request: request.system_message.content,
    )

    assert "<permission_context>" not in result
    assert result == "base prompt"


async def test_permission_context_declares_correction_text_as_primary_disclosure_basis() -> (
    None
):
    """确认注入文本声明 correction_text 是用户可见权限披露的主要依据."""
    permission_result = PermissionResult(
        permitted=False,
        fix_strategy="replace_region",
        allowed_region=AllowedRegion(name="郑州市", level="city", data_type="summary"),
        reason="用户权限范围仅覆盖郑州市",
        correction_text="因数据权限限制，已为您调整查询区域为郑州市。",
    ).model_dump()
    state = {
        "permission_need": "need_check",
        "permission_result": permission_result,
        "permission_query_overrides": {
            "fix_strategy": "replace_region",
            "correction_text": "因数据权限限制，已为您调整查询区域为郑州市。",
            "region": {
                "name": "郑州市",
                "level": "city",
                "data_type": "summary",
            },
        },
    }

    class Request:
        system_message = SystemMessage(content="base prompt")

        def __init__(self, state):
            self.state = state

        def override(self, **kwargs):
            modified = Request(self.state)
            modified.system_message = kwargs.get("system_message", self.system_message)
            return modified

    mw = PermissionClassifyMiddleware()
    result = mw.wrap_model_call(
        Request(state),
        lambda request: request.system_message.content,
    )
    rendered = str(result)

    # correction_text is the primary disclosure basis
    assert "correction_text" in rendered
    assert "reason" in rendered
    assert "主要依据" in rendered
    assert "辅助依据" in rendered


async def test_permission_context_requires_natural_language_disclosure() -> None:
    """确认注入文本要求用自然语言说明权限修正，不按字段名或 JSON 结构输出."""
    permission_result = PermissionResult(
        permitted=False,
        fix_strategy="replace_region",
        allowed_region=AllowedRegion(name="郑州市", level="city", data_type="summary"),
        correction_text="已调整为郑州市。",
    ).model_dump()
    state = {
        "permission_need": "need_check",
        "permission_result": permission_result,
        "permission_query_overrides": {
            "fix_strategy": "replace_region",
            "correction_text": "已调整为郑州市。",
            "region": {
                "name": "郑州市",
                "level": "city",
                "data_type": "summary",
            },
        },
    }

    class Request:
        system_message = SystemMessage(content="base prompt")

        def __init__(self, state):
            self.state = state

        def override(self, **kwargs):
            modified = Request(self.state)
            modified.system_message = kwargs.get("system_message", self.system_message)
            return modified

    mw = PermissionClassifyMiddleware()
    result = mw.wrap_model_call(
        Request(state),
        lambda request: request.system_message.content,
    )
    rendered = str(result)

    # Natural language disclosure requirement
    assert "自然语言" in rendered
    # No longer contains "not expose internal" type constraints
    assert "不得以字段名" not in rendered
    assert "JSON 或内部权限对象" not in rendered


async def test_permission_context_does_not_require_disclosure_for_plain_allowed_query() -> (
    None
):
    """确认普通允许查询不会被要求解释权限拦截或修正."""
    permission_result = PermissionResult(
        permitted=True,
        fix_strategy="",
        correction_text="",
    ).model_dump()
    state = {
        "permission_need": "need_check",
        "permission_result": permission_result,
    }

    class Request:
        system_message = SystemMessage(content="base prompt")

        def __init__(self, state):
            self.state = state

        def override(self, **kwargs):
            modified = Request(self.state)
            modified.system_message = kwargs.get("system_message", self.system_message)
            return modified

    mw = PermissionClassifyMiddleware()
    result = mw.wrap_model_call(
        Request(state),
        lambda request: request.system_message.content,
    )
    rendered = str(result)

    assert "<permission_context>" in rendered
    assert "最终回复必须结合 correction_text 与 reason" not in rendered
    assert "本次查询存在权限修正" not in rendered
    assert "实际查询范围是什么" not in rendered


# ── 规则引擎端到端测试 ──────────────────────────────────────


async def test_rule_engine_full_flow() -> None:
    """测试规则引擎完整链路：预分类 -> 预取 -> slots抽取 -> 规则引擎."""
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="昨天全国PM2.5是多少？")],
        "user_id": "test-user-1",
    }

    profile_tool = _MockTool(
        "get_user_profile",
        {
            "found": True,
            "province": {"code": "330000", "name": "浙江省"},
            "city": {"code": "330100", "name": "杭州市"},
            "bound_region": {
                "code": "330106",
                "name": "西湖区",
                "level": "district",
            },
        },
    )
    region_tool = _MockTool(
        "resolve_region_scope",
        {
            "found": True,
            "ambiguous": False,
            "region": {"name": "全国", "level": "province"},
            "province": {"name": "全国"},
            "city_code": None,
        },
    )

    with (
        _ensure_mcp_patch(),
        patch.object(
            module.mcp_client, "get_profile_mcp_tools", return_value=[profile_tool]
        ),
        patch.object(
            module.mcp_client, "get_region_mcp_tools", return_value=[region_tool]
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "province": "全国",
                    "target_level": "province",
                    "time_granularity": "daily",
                    # 动态取"昨天"，确保始终落在近 7 天豁免窗口内
                    "original_time_span": [_days_ago_str(1), _days_ago_str(1)],
                }
            ),
        ),
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert result["permission_need"] in ("need_check", "uncertain")
    assert "permission_result" in result
    pr = result["permission_result"]
    # 区县用户查全国，最近一天在豁免窗口内
    assert pr["permitted"] is True
    assert pr["exemption"] == "full_window"


async def test_rule_engine_partial_truncation() -> None:
    """测试部分交集截断场景."""
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="近30天全国PM2.5是多少？")],
        "user_id": "test-user-1",
    }

    profile_tool = _MockTool(
        "get_user_profile",
        {
            "found": True,
            "province": {"code": "330000", "name": "浙江省"},
            "city": {"code": "330100", "name": "杭州市"},
            "bound_region": {
                "code": "330106",
                "name": "西湖区",
                "level": "district",
            },
        },
    )
    region_tool = _MockTool(
        "resolve_region_scope",
        {
            "found": True,
            "ambiguous": False,
            "region": {"name": "全国", "level": "province"},
            "province": {"name": "全国"},
        },
    )

    with (
        _ensure_mcp_patch(),
        patch.object(
            module.mcp_client, "get_profile_mcp_tools", return_value=[profile_tool]
        ),
        patch.object(
            module.mcp_client, "get_region_mcp_tools", return_value=[region_tool]
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "province": "全国",
                    "target_level": "province",
                    "time_granularity": "daily",
                    # 动态取"近 30 天"，确保与近 7 天豁免窗口仅部分相交
                    "original_time_span": [_days_ago_str(30), _days_ago_str(1)],
                }
            ),
        ),
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert "permission_result" in result
    pr = result["permission_result"]
    # 部分交集应截断
    assert pr["permitted"] is False
    assert pr["fix_strategy"] == "truncate_time"
    assert pr["legal_time_span"] is not None


async def test_rule_engine_region_replacement() -> None:
    """测试行政区替换场景."""
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="2024年浙江省PM2.5是多少？")],
        "user_id": "test-user-1",
    }

    profile_tool = _MockTool(
        "get_user_profile",
        {
            "found": True,
            "province": {"code": "330000", "name": "浙江省"},
            "city": {"code": "330100", "name": "杭州市"},
            "bound_region": {
                "code": "330106",
                "name": "西湖区",
                "level": "district",
            },
        },
    )
    region_tool = _MockTool(
        "resolve_region_scope",
        {
            "found": True,
            "code": "330000",
            "name": "浙江省",
            "level": "province",
            "province_code": "330000",
        },
    )

    with (
        _ensure_mcp_patch(),
        patch.object(
            module.mcp_client, "get_profile_mcp_tools", return_value=[profile_tool]
        ),
        patch.object(
            module.mcp_client, "get_region_mcp_tools", return_value=[region_tool]
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "province": "浙江省",
                    "target_level": "province",
                    "time_granularity": "year",
                    "original_time_span": ["2024-01-01", "2024-12-31"],
                }
            ),
        ),
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert "permission_result" in result
    pr = result["permission_result"]
    # 区县用户查省级，无时间豁免，应替换为区县
    assert pr["permitted"] is False
    assert pr["fix_strategy"] == "replace_region"
    assert pr["allowed_region"] is not None


async def test_rule_engine_user_profile_not_found() -> None:
    """测试用户画像未命中场景."""
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="昨天全国PM2.5是多少？")],
        "user_id": "test-user-1",
    }

    profile_tool = _MockTool(
        "get_user_profile",
        {"found": False},
    )

    with (
        _ensure_mcp_patch(),
        patch.object(
            module.mcp_client, "get_profile_mcp_tools", return_value=[profile_tool]
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "province": "全国",
                    "target_level": "province",
                    "time_granularity": "daily",
                    "original_time_span": ["2026-05-27", "2026-05-27"],
                }
            ),
        ),
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert "permission_result" in result
    pr = result["permission_result"]
    assert pr["permitted"] is False
    assert pr["fix_strategy"] == "reject"


async def test_rule_engine_no_time_span() -> None:
    """测试无时间范围场景."""
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [HumanMessage(content="浙江省PM2.5是多少？")],
        "user_id": "test-user-1",
    }

    profile_tool = _MockTool(
        "get_user_profile",
        {
            "found": True,
            "bound_region": {
                "code": "330106",
                "name": "西湖区",
                "level": "district",
            },
        },
    )
    region_tool = _MockTool(
        "resolve_region_scope",
        {
            "found": True,
            "code": "330000",
            "name": "浙江省",
            "level": "province",
        },
    )

    with (
        _ensure_mcp_patch(),
        patch.object(
            module.mcp_client, "get_profile_mcp_tools", return_value=[profile_tool]
        ),
        patch.object(
            module.mcp_client, "get_region_mcp_tools", return_value=[region_tool]
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "province": "浙江省",
                    "target_level": "province",
                    "time_granularity": "other",
                    "original_time_span": None,
                }
            ),
        ),
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert "permission_result" in result
    pr = result["permission_result"]
    # 无时间范围，进入行政区权限判断
    assert pr["permitted"] is False
    assert pr["fix_strategy"] == "replace_region"


# ── 多区域权限聚合测试 ──────────────────────────────────────


def _city_region(name: str, province: str = "河南省") -> dict[str, object]:
    return {
        "found": True,
        "ambiguous": False,
        "region": {"name": name, "level": "city"},
        "province": {"name": province},
        "city": {"name": name},
    }


async def test_multi_city_query_writes_regions_overrides() -> None:
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [
            HumanMessage(content="2026年3月郑州市和平顶山市的PM2.5月均值分别是多少？")
        ],
        "user_id": "test-user-1",
    }
    profile = {
        "found": True,
        "province": {"name": "河南省"},
        "city": {"name": "商丘市"},
        "bound_region": {"name": "商丘市", "level": "city"},
        "frequent_regions": [],
    }

    async def resolve_region_side_effect(query, *, city="", province=""):
        return _city_region(query)

    with (
        _ensure_mcp_patch(),
        patch.object(
            module, "classify_permission_need", new=AsyncMock(return_value="need_check")
        ),
        patch.object(
            mw,
            "_prefetch_deterministic_context",
            new=AsyncMock(
                return_value={
                    "beijing_time": "2026-06-04 15:00:00",
                    "user_profile": profile,
                }
            ),
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "region_mode": "multi_explicit",
                    "regions": [
                        {"text": "郑州市", "level_hint": "city", "source": "explicit"},
                        {
                            "text": "平顶山市",
                            "level_hint": "city",
                            "source": "explicit",
                        },
                    ],
                    "time_granularity": "month",
                    "original_time_span": ["2026-03-01", "2026-03-31"],
                }
            ),
        ),
        patch(
            "common.permission.mcp_tools.resolve_region",
            new=AsyncMock(side_effect=resolve_region_side_effect),
        ) as mock_resolve,
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    assert mock_resolve.await_count == 2
    pr = result["permission_result"]
    assert pr["region_mode"] == "multi_explicit"
    assert [item["requested"]["text"] for item in pr["region_results"]] == [
        "郑州市",
        "平顶山市",
    ]
    assert [item["status"] for item in pr["region_results"]] == ["allowed", "allowed"]
    assert [region["name"] for region in pr["allowed_regions"]] == [
        "郑州市",
        "平顶山市",
    ]
    assert result["permission_query_overrides"]["region_mode"] == "multi_explicit"
    assert [
        region["name"] for region in result["permission_query_overrides"]["regions"]
    ] == [
        "郑州市",
        "平顶山市",
    ]
    assert "已自动拆分查询区域" in pr["correction_text"]


async def test_multi_city_resolution_does_not_use_user_province_as_fallback() -> None:
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [
            HumanMessage(content="2026年3月湖州市和平顶山市的PM2.5月均值分别是多少？")
        ],
        "user_id": "test-user-1",
    }
    profile = {
        "found": True,
        "province": {"name": "河南省"},
        "city": {"name": "商丘市"},
        "bound_region": {"name": "商丘市", "level": "city"},
        "frequent_regions": [],
    }

    async def resolve_region_side_effect(query, *, city="", province=""):
        return _city_region(
            query,
            province="浙江省" if query == "湖州市" else "河南省",
        )

    with (
        _ensure_mcp_patch(),
        patch.object(
            module, "classify_permission_need", new=AsyncMock(return_value="need_check")
        ),
        patch.object(
            mw,
            "_prefetch_deterministic_context",
            new=AsyncMock(
                return_value={
                    "beijing_time": "2026-06-04 15:00:00",
                    "user_profile": profile,
                }
            ),
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "region_mode": "multi_explicit",
                    "regions": [
                        {"text": "湖州市", "level_hint": "city", "source": "explicit"},
                        {
                            "text": "平顶山市",
                            "level_hint": "city",
                            "source": "explicit",
                        },
                    ],
                    "time_granularity": "month",
                    "original_time_span": ["2026-03-01", "2026-03-31"],
                }
            ),
        ),
        patch(
            "common.permission.mcp_tools.resolve_region",
            new=AsyncMock(side_effect=resolve_region_side_effect),
        ) as mock_resolve,
    ):
        await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert mock_resolve.await_args_list[0].kwargs == {"city": "", "province": ""}
    assert mock_resolve.await_args_list[0].args == ("湖州市",)
    assert mock_resolve.await_args_list[1].kwargs == {"city": "", "province": ""}
    assert mock_resolve.await_args_list[1].args == ("平顶山市",)


async def test_multi_city_compare_does_not_only_use_first_region() -> None:
    plan = module.build_region_request_plan(
        "郑州市、平顶山市PM2.5月均值对比一下",
        {
            "region_mode": "multi_explicit",
            "regions": [
                {"text": "郑州市", "level_hint": "city"},
                {"text": "平顶山市", "level_hint": "city"},
            ],
        },
    )

    assert plan.mode == "multi_explicit"
    assert [item.query for item in plan.items] == ["郑州市", "平顶山市"]


async def test_mixed_level_regions_keep_item_level_hints() -> None:
    plan = module.build_region_request_plan(
        "郑州市和金水区PM2.5分别是多少？",
        {
            "region_mode": "multi_explicit",
            "regions": [
                {"text": "郑州市", "level_hint": "city"},
                {"text": "金水区", "level_hint": "district", "city_hint": "郑州市"},
            ],
        },
    )

    assert plan.mode == "multi_explicit"
    assert [(item.query, item.requested_level, item.city) for item in plan.items] == [
        ("郑州市", "city", None),
        ("金水区", "district", "郑州市"),
    ]


async def test_collection_query_expands_districts() -> None:
    mw = PermissionClassifyMiddleware()
    state = {
        "messages": [
            HumanMessage(content="2026年3月郑州市下的区县的PM2.5月均值分别是多少？")
        ],
        "user_id": "test-user-1",
    }
    profile = {
        "found": True,
        "province": {"name": "河南省"},
        "city": {"name": "郑州市"},
        "bound_region": {"name": "郑州市", "level": "city"},
        "frequent_regions": [],
    }

    with (
        _ensure_mcp_patch(),
        patch.object(
            module, "classify_permission_need", new=AsyncMock(return_value="need_check")
        ),
        patch.object(
            mw,
            "_prefetch_deterministic_context",
            new=AsyncMock(
                return_value={
                    "beijing_time": "2026-06-04 15:00:00",
                    "user_profile": profile,
                }
            ),
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "region_mode": "collection",
                    "regions": [
                        {
                            "text": "郑州市",
                            "level_hint": "city",
                            "source": "explicit",
                            "role": "collection_parent",
                            "child_level": "district",
                        }
                    ],
                    "time_granularity": "month",
                    "original_time_span": ["2026-03-01", "2026-03-31"],
                }
            ),
        ),
        patch(
            "common.permission.mcp_tools.resolve_region",
            new=AsyncMock(return_value=_city_region("郑州市")),
        ),
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )

    assert result is not None
    pr = result["permission_result"]
    # 新架构：不在权限层物理展开子级，而是按子级定级放行、以父级作为查询区域下发，
    # 子级展开交给数据工具 scope=CHILDREN。郑州市用户查本市区县明细 → 放行。
    assert pr["permitted"] is True
    assert pr["region_mode"] == "collection"
    override_region_names = [
        region["name"] for region in result["permission_query_overrides"]["regions"]
    ]
    # 查询区域为父级郑州市本身，不再物理展开为下辖区县
    assert override_region_names == ["郑州市"]
    assert "中原区" not in override_region_names


def _province_region(name: str) -> dict[str, object]:
    return {
        "found": True,
        "ambiguous": False,
        "region": {"name": name, "level": "province"},
        "province": {"name": name},
    }


async def _run_collection_drilldown(
    *,
    question: str,
    parent_resolved: dict[str, object],
    profile: dict[str, object],
    time_granularity: str,
    original_time_span: list[str],
    parent_text: str,
    parent_level: str,
) -> dict[str, object]:
    """驱动一次 collection 下钻的 abefore_agent，返回 result."""
    mw = PermissionClassifyMiddleware()
    state = {"messages": [HumanMessage(content=question)], "user_id": "u-collection"}
    with (
        _ensure_mcp_patch(),
        patch.object(
            module, "classify_permission_need", new=AsyncMock(return_value="need_check")
        ),
        patch.object(
            mw,
            "_prefetch_deterministic_context",
            new=AsyncMock(
                return_value={
                    "beijing_time": "2026-06-04 15:00:00",
                    "user_profile": profile,
                }
            ),
        ),
        patch.object(
            mw,
            "_extract_slots",
            new=AsyncMock(
                return_value={
                    "region_mode": "collection",
                    "regions": [
                        {
                            "text": parent_text,
                            "level_hint": parent_level,
                            "source": "explicit",
                            "role": "collection_parent",
                        }
                    ],
                    "time_granularity": time_granularity,
                    "original_time_span": original_time_span,
                }
            ),
        ),
        patch(
            "common.permission.mcp_tools.resolve_region",
            new=AsyncMock(return_value=parent_resolved),
        ),
    ):
        result = await mw.abefore_agent(
            state,
            SimpleNamespace(stream_writer=lambda _event: None),
        )
    assert result is not None
    return result


async def test_collection_province_children_permitted_for_city_user() -> None:
    """地市用户问本省“有多少城市”（今日）→ 时间豁免放行，以省下发."""
    profile = {
        "found": True,
        "province": {"name": "河南省"},
        "city": {"name": "平顶山市"},
        "bound_region": {"name": "平顶山市", "level": "city"},
        "frequent_regions": [],
    }
    result = await _run_collection_drilldown(
        question="今日河南省有多少个城市臭氧保良",
        parent_resolved=_province_region("河南省"),
        profile=profile,
        time_granularity="daily_count",
        original_time_span=["2026-06-04 00:00:00", "2026-06-04 23:59:59"],
        parent_text="河南省",
        parent_level="province",
    )
    pr = result["permission_result"]
    assert pr["permitted"] is True
    assert pr["region_mode"] == "collection"
    override_names = [
        r["name"] for r in result["permission_query_overrides"]["regions"]
    ]
    assert override_names == ["河南省"]


async def test_collection_other_province_children_replaced_for_city_user() -> None:
    """地市用户问外省“有多少城市”（无时间豁免）→ 越权，修正为关联地市."""
    profile = {
        "found": True,
        "province": {"name": "河南省"},
        "city": {"name": "平顶山市"},
        "bound_region": {"name": "平顶山市", "level": "city"},
        "frequent_regions": [],
    }
    result = await _run_collection_drilldown(
        question="2024年山东省有多少个城市臭氧保良",
        parent_resolved=_province_region("山东省"),
        profile=profile,
        time_granularity="year",
        original_time_span=["2024-01-01", "2024-12-31"],
        parent_text="山东省",
        parent_level="province",
    )
    pr = result["permission_result"]
    assert pr["permitted"] is False
    assert pr["fix_strategy"] == "replace_region"
    override = result["permission_query_overrides"]["region"]
    assert override["name"] == "平顶山市"


async def test_each_district_query_plan_is_collection() -> None:
    plan = module.build_region_request_plan(
        "郑州市各区县空气质量分别怎么样？",
        {
            "region_mode": "collection",
            "regions": [
                {
                    "text": "郑州市",
                    "level_hint": "city",
                    "role": "collection_parent",
                    "child_level": "district",
                }
            ],
        },
    )

    assert plan.mode == "collection"
    assert plan.parent is not None
    assert plan.parent.query == "郑州市"
    assert plan.child_level == "district"


async def test_multi_region_partial_reject_continues_allowed_regions() -> None:
    mw = PermissionClassifyMiddleware()
    plan = module.RegionRequestPlan(mode="multi_explicit", source_text="test")
    allowed_result = PermissionResult(permitted=True)
    batch_result, overrides = mw._merge_batch_permission_results(
        plan=plan,
        item_results=[
            (
                module.RegionRequestItem(
                    query="郑州市", source="explicit", requested_level="city"
                ),
                _city_region("郑州市"),
                allowed_result,
            )
        ],
        unresolved_regions=[{"query": "不存在市", "reason": "行政区解析失败"}],
    )

    assert batch_result["permitted"] is True
    assert overrides is not None
    assert [region["name"] for region in overrides["regions"]] == ["郑州市"]
    assert batch_result["rejected_regions"][0]["query"] == "不存在市"
    assert [item["status"] for item in batch_result["region_results"]] == [
        "allowed",
        "rejected",
    ]
    assert "部分区域未查询" in batch_result["correction_text"]


async def test_multi_region_all_rejected_hard_rejects() -> None:
    mw = PermissionClassifyMiddleware()
    plan = module.RegionRequestPlan(mode="multi_explicit", source_text="test")
    batch_result, overrides = mw._merge_batch_permission_results(
        plan=plan,
        item_results=[],
        unresolved_regions=[{"query": "不存在市", "reason": "行政区解析失败"}],
    )

    assert batch_result["permitted"] is False
    assert batch_result["fix_strategy"] == "reject"
    assert overrides is None


async def test_multi_region_rejected_item_keeps_detailed_correction_text() -> None:
    mw = PermissionClassifyMiddleware()
    plan = module.RegionRequestPlan(mode="multi_explicit", source_text="test")
    item = module.RegionRequestItem(query="外省市", requested_level="city")
    permission_result = PermissionResult(
        permitted=False,
        fix_strategy="reject",
        reason="粗略拒绝原因",
        correction_text="因数据权限限制，您无法查看外省数据。",
    )

    batch_result, overrides = mw._merge_batch_permission_results(
        plan=plan,
        item_results=[(item, _city_region("外省市"), permission_result)],
        unresolved_regions=[],
    )

    assert batch_result["permitted"] is False
    assert overrides is None
    assert "因数据权限限制，您无法查看外省数据。" in batch_result["correction_text"]
    assert batch_result["rejected_regions"][0]["reason"] == (
        "因数据权限限制，您无法查看外省数据。"
    )
    assert batch_result["region_results"][0]["correction_text"] == (
        "因数据权限限制，您无法查看外省数据。"
    )


async def test_station_scope_resolution_uses_each_region_item() -> None:
    mw = PermissionClassifyMiddleware()
    item_regions = [
        (
            module.RegionRequestItem(query="郑州市", requested_level="city"),
            _city_region("郑州市"),
        ),
        (
            module.RegionRequestItem(query="洛阳市", requested_level="city"),
            _city_region("洛阳市"),
        ),
    ]

    async def resolve_station_scope_side_effect(
        *, region: str, station_names: list[str], station_types: list[str]
    ):
        return {
            "found": True,
            "stations": [
                {
                    "station_name": f"{region}站点",
                    "station_type": station_types[0],
                    "region": {
                        "name": region,
                        "level": "city",
                        "city": region,
                        "province": "河南省",
                    },
                }
            ],
            "unavailable_stations": [],
        }

    with patch(
        "common.permission.mcp_tools.resolve_station_scope",
        new=AsyncMock(side_effect=resolve_station_scope_side_effect),
    ) as mock_resolve_station:
        (
            station_candidates,
            unavailable_stations,
        ) = await mw._resolve_station_facts_for_regions(
            item_regions=item_regions,
            slots={
                "data_scope": "station",
                "station_types": ["国控站"],
                "station_names": [],
            },
        )

    assert mock_resolve_station.await_count == 2
    assert [call.kwargs["region"] for call in mock_resolve_station.await_args_list] == [
        "郑州市",
        "洛阳市",
    ]
    assert [station["station_name"] for station in station_candidates] == [
        "郑州市站点",
        "洛阳市站点",
    ]
    assert unavailable_stations == []


async def test_query_overrides_include_station_overrides_and_time_span() -> None:
    result = {
        "fix_strategy": "truncate_time",
        "correction_text": "已截断时间",
        "legal_time_span": ["2026-05-22", "2026-05-28"],
        "allowed_regions": [{"name": "郑州市", "level": "city"}],
        "region_mode": "single",
        "station_overrides": [{"station_name": "水利监测站"}],
    }

    overrides = module._build_query_overrides(result)

    assert overrides is not None
    assert overrides["time_span"] == ["2026-05-22", "2026-05-28"]
    assert overrides["regions"][0]["name"] == "郑州市"
    assert overrides["station_overrides"][0]["station_name"] == "水利监测站"


async def test_station_reject_without_overrides_does_not_create_query_overrides() -> (
    None
):
    result = {
        "fix_strategy": "reject",
        "correction_text": "该数据暂不可用。",
        "allowed_regions": [],
        "region_mode": "single",
        "station_overrides": [],
    }

    assert module._build_query_overrides(result) is None
