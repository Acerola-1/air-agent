"""会话模板使用独立版本，旧 top1 路由模板不会被读取."""

from unittest.mock import Mock

from basic_qa.prompts import build_basic_qa_system_prompt


def test_conversation_prompt_uses_new_managed_version(monkeypatch):
    import common.langfuse_prompts as prompts

    managed = Mock(return_value="受管理的会话角色说明")
    monkeypatch.setattr(prompts, "get_managed_prompt", managed)
    text = build_basic_qa_system_prompt({"mode": "fast"})
    assert "受管理的会话角色说明" in text
    assert "当前真实时间" in text
    assert managed.call_args.args[0] == "air-agent/basic-qa/conversation-system-v2"
    assert "不得把现状推断为已证实污染来源" in managed.call_args.args[1]
