"""数据分析输出护栏测试."""

from common.prompts import (
    ADMINISTRATIVE_REGION_NORMALIZATION_GUIDE,
    AIR_QUALITY_UNIT_NORMALIZATION_GUIDE,
    DATA_ANALYSIS_OUTPUT_GUARD,
    EXPERT_MODE_SYSTEM_PROMPT,
    MAIN_AGENT_TOOL_USE_OUTPUT_GUARD,
    TIME_PARAMETER_NORMALIZATION_GUIDE,
    with_data_analysis_output_guard,
    with_main_agent_tool_use_output_guard,
)


def test_output_guards_no_tool_silence_protocol() -> None:
    """确认静默协议段落已从两个护栏中删除."""
    for guard in (MAIN_AGENT_TOOL_USE_OUTPUT_GUARD, DATA_ANALYSIS_OUTPUT_GUARD):
        assert "工具调用静默协议" not in guard
        assert "AIMessage.content 必须为空" not in guard
        assert "保持静默" not in guard
        assert "只能发起 tool_calls" not in guard


def test_output_guards_no_internal_exposure_ban() -> None:
    """确认'不暴露内部'类约束已从两个护栏中删除."""
    for guard in (MAIN_AGENT_TOOL_USE_OUTPUT_GUARD, DATA_ANALYSIS_OUTPUT_GUARD):
        assert "严禁泄露内部信息" not in guard
        assert "严禁过程化自述" not in guard
        assert "严禁进度提示" not in guard
        assert "一律不要展示" not in guard
        assert "不得输出推理步骤" not in guard
        assert "不得输出或提及 skill" not in guard


def test_main_agent_guard_no_process_first_person() -> None:
    """确认 MAIN_AGENT 护栏已删除过程化第一人称和工具步骤提及约束."""
    assert "不得提及前面执行过哪些工具或内部步骤" not in MAIN_AGENT_TOOL_USE_OUTPUT_GUARD
    assert "不得使用第一人称解释执行过程" not in MAIN_AGENT_TOOL_USE_OUTPUT_GUARD
    assert "我正在/我需要/我先/我再/我已" not in MAIN_AGENT_TOOL_USE_OUTPUT_GUARD


def test_tool_failure_silence_protocol_removed() -> None:
    """确认 TOOL_FAILURE_SILENCE_PROTOCOL 常量已删除."""
    import common.prompts as prompts_module

    assert not hasattr(prompts_module, "TOOL_FAILURE_SILENCE_PROTOCOL")
    assert "TOOL_FAILURE_SILENCE_PROTOCOL" not in prompts_module.__all__


def test_main_agent_guard_preserves_internal_question_handling() -> None:
    """确认'内部机制追问处理'段落被保留."""
    assert "内部机制追问处理" in MAIN_AGENT_TOOL_USE_OUTPUT_GUARD
    assert "业务能力和结果口径" in MAIN_AGENT_TOOL_USE_OUTPUT_GUARD


def test_output_guards_preserve_business_rules() -> None:
    """确认业务规范段落被保留."""
    assert "输出格式要求" in MAIN_AGENT_TOOL_USE_OUTPUT_GUARD
    assert "通用规范要求" in MAIN_AGENT_TOOL_USE_OUTPUT_GUARD


def test_expert_mode_prompt_limits_tables_to_small_tables() -> None:
    assert "支持国标表格" not in EXPERT_MODE_SYSTEM_PROMPT
    assert "表格最多 4 列、6 行数据" in EXPERT_MODE_SYSTEM_PROMPT
    assert "不得输出长表格、大明细表或完整原始数据表" in EXPERT_MODE_SYSTEM_PROMPT


def test_with_data_analysis_output_guard_keeps_pollutant_boundaries() -> None:
    prompt = with_data_analysis_output_guard("业务提示词")

    assert "业务提示词" in prompt
    assert ADMINISTRATIVE_REGION_NORMALIZATION_GUIDE in prompt
    assert TIME_PARAMETER_NORMALIZATION_GUIDE in prompt
    assert AIR_QUALITY_UNIT_NORMALIZATION_GUIDE in prompt
    assert "## 各因子污染等级说明" in prompt


def test_shared_prompts_normalize_municipality_query_names() -> None:
    main_prompt = with_main_agent_tool_use_output_guard("业务提示词")
    data_analysis_prompt = with_data_analysis_output_guard("业务提示词")

    for prompt in (main_prompt, data_analysis_prompt):
        assert "北京市" in prompt
        assert "北京城区" in prompt
        assert "上海城区" in prompt
        assert "天津城区" in prompt
        assert "重庆城区" in prompt
        # 直辖市规则改为逐条列举格式
        assert "直辖市城区查询规则" in prompt


def test_shared_prompts_define_time_parameter_formats_without_tool_names() -> None:
    main_prompt = with_main_agent_tool_use_output_guard("业务提示词")
    data_analysis_prompt = with_data_analysis_output_guard("业务提示词")

    for prompt in (main_prompt, data_analysis_prompt):
        assert TIME_PARAMETER_NORMALIZATION_GUIDE in prompt
        assert "小时 | `yyyy-MM-dd HH:00`" in prompt
        assert "日累计 | `yyyy-MM-dd HH:00`" in prompt
        assert "日 | `yyyy-MM-dd`" in prompt
        assert "月 | `yyyy-MM-dd`" in prompt
        assert "年 | `yyyy-MM-dd`" in prompt
        assert "helper_get_latest_time" not in TIME_PARAMETER_NORMALIZATION_GUIDE


def test_shared_prompts_define_air_quality_units() -> None:
    main_prompt = with_main_agent_tool_use_output_guard("业务提示词")
    data_analysis_prompt = with_data_analysis_output_guard("业务提示词")

    for prompt in (main_prompt, data_analysis_prompt):
        assert AIR_QUALITY_UNIT_NORMALIZATION_GUIDE in prompt
        assert 'PM2.5、PM10、SO2、NO2、O3-8h 使用"微克/立方米"' in prompt
        assert 'CO 使用"毫克/立方米"' in prompt
