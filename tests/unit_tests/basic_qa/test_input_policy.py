"""Preserve the public input boundary after ownership migration."""

import pytest
from langgraph_sdk import Auth

from basic_qa.input_policy import validate_input


def test_input_policy_rejects_internal_state_in_both_payload_shapes():
    validate_input({"input": {"messages": []}})
    validate_input({"kwargs": {"input": {"messages": []}}})
    for value in (
        {"input": {"messages": [], "bqa_disclosure": {}}},
        {"kwargs": {"input": {"bqa_capabilities": {}}}},
    ):
        with pytest.raises(Auth.exceptions.HTTPException) as exc:
            validate_input(value)
        assert exc.value.status_code == 403
