from pathlib import Path

GENERIC_TIME_RULE_FRAGMENTS = (
    "## 时间解析规则",
    "如果工具对时间参数要求不明确",
    "时间解析必须以真实北京时间为准",
    "相对时间包括但不限于",
    "用户询问当天/今天的数据时",
    "单位统一：PM2.5、PM10",
)


def test_skill_main_files_do_not_repeat_global_time_rules() -> None:
    skill_files = sorted(Path("src").glob("*/skills/*/SKILL.md"))

    assert skill_files
    for skill_file in skill_files:
        source = skill_file.read_text(encoding="utf-8")
        for fragment in GENERIC_TIME_RULE_FRAGMENTS:
            assert fragment not in source, skill_file
