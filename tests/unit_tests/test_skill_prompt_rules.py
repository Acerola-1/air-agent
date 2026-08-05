from pathlib import Path

GENERIC_TIME_RULE_FRAGMENTS = (
    "## 时间解析规则",
    "如果工具对时间参数要求不明确",
    "时间解析必须以真实北京时间为准",
    "相对时间包括但不限于",
    "用户询问当天/今天的数据时",
    "单位统一：PM2.5、PM10",
)


INTELLIGENT_ANALYSIS_UNCONNECTED_TOOL_FRAGMENTS = (
    "## 工具接入状态",
    "本技能当前只完成路由与提示词规则整理",
    "本技能当前未配置业务查询工具",
    "allowed-tools 暂留空",
    "业务查询工具尚未接入",
    "数据接口尚未接入",
    "接口尚未接入",
    "接口未接入",
    "当前未接入",
    "暂留空",
    "不得调用其他模块工具替代",
)


def test_skill_main_files_do_not_repeat_global_time_rules() -> None:
    skill_files = sorted(Path("src").glob("*/skills/*/SKILL.md"))

    assert skill_files
    for skill_file in skill_files:
        source = skill_file.read_text(encoding="utf-8")
        for fragment in GENERIC_TIME_RULE_FRAGMENTS:
            assert fragment not in source, skill_file


def test_intelligent_analysis_skills_do_not_include_unconnected_tool_placeholders() -> None:
    skill_docs = sorted(Path("src/intelligent_analysis/skills").rglob("*.md"))

    assert skill_docs
    for skill_doc in skill_docs:
        source = skill_doc.read_text(encoding="utf-8")
        for fragment in INTELLIGENT_ANALYSIS_UNCONNECTED_TOOL_FRAGMENTS:
            assert fragment not in source, skill_doc
