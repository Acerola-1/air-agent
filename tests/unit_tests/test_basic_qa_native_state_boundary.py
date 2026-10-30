"""Basic QA 不接受客户端注入运行状态."""

import pytest
from starlette.requests import Request

from basic_qa.input_policy import validate_input as _reject_basic_qa_state_input
from common.identity.langgraph_auth import authenticate


def test_only_message_input_is_allowed():
    _reject_basic_qa_state_input(
        {"input": {"messages": [{"role": "user", "content": "hello"}]}}
    )
    _reject_basic_qa_state_input(
        {"kwargs": {"input": None, "command": {"resume": "ok"}}}
    )
    for key in (
        "active_skill",
        "bqa_capabilities",
        "bqa_disclosure",
        "skill_runtime_version",
        "skill_rules_content",
        "system_prompt",
        "summary",
    ):
        with pytest.raises(Exception) as error:
            _reject_basic_qa_state_input(
                {"kwargs": {"input": {"messages": [], key: {}}}}
            )
        assert getattr(error.value, "status_code", None) == 403


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT"])
@pytest.mark.parametrize("path", ["/threads/t/state", "/threads/t/state/checkpoint"])
async def test_native_state_mutations_rejected_before_auth(method, path):
    with pytest.raises(Exception) as error:
        await authenticate(
            Request({"type": "http", "method": method, "path": path, "headers": []})
        )
    assert getattr(error.value, "status_code", None) == 403
