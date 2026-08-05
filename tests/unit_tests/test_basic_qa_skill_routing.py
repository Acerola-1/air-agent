"""basic-qa Skill 语义路由测试.  # noqa: T201

使用真实 SkillSemanticRouter 对 basic_qa skills 目录进行语义匹配，
验证用户问题能否正确路由到预期 Skill。

需要真实 SiliconFlow embedding API，通过环境变量 SILICONFLOW_API_KEY 配置。
无有效 key 时跳过。

运行方式:
    SILICONFLOW_API_KEY=xxx .venv/bin/python -m pytest tests/unit_tests/test_basic_qa_skill_routing.py -v
    # 仅输出摘要，不逐条断言:
    SILICONFLOW_API_KEY=xxx .venv/bin/python -m pytest tests/unit_tests/test_basic_qa_skill_routing.py::test_basic_qa_skill_routing_summary -v -s
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from common.skill_router import SkillSemanticRouter

BASIC_QA_SKILLS_DIR = Path(__file__).resolve().parents[2] / "src" / "basic_qa" / "skills"

requires_real_api = pytest.mark.skipif(
    os.environ.get("SILICONFLOW_API_KEY", "test") == "test",
    reason="需要真实 SILICONFLOW_API_KEY，当前为占位值",
)


@pytest.fixture(scope="module")
def router() -> SkillSemanticRouter:
    """构建 basic_qa 的真实语义路由器（模块级复用，避免重复建索引）."""
    return SkillSemanticRouter(BASIC_QA_SKILLS_DIR)


# ── 测试用例：(问题, 期望匹配的 skill name) ──────────────────────────

ROUTING_CASES: list[tuple[str, str]] = [
    # air-quality-basic-query
    ("今天洛阳市空气质量怎么样？", "air-quality-basic-query"),
    ("昨天郑州市空气质量等级是多少？", "air-quality-basic-query"),
    ("今日截至15时北京市空气质量数据？", "air-quality-basic-query"),
    ("实时查询广州市当前AQI及首要污染物？", "air-quality-basic-query"),
    # comparison-composition
    ("本月洛阳市空气质量同比变化情况？", "comparison-composition"),
    ("今年以来郑州市PM2.5和O3同比改善了吗？", "comparison-composition"),
    ("本月洛阳市综合指数六因子的占比如何？", "comparison-composition"),
    ("去年全年合肥市各污染物占综指比例？", "comparison-composition"),
    # compliance-feasibility
    ("今天洛阳市能保良吗？", "compliance-feasibility"),
    ("今日石家庄市能否避免PM2.5重污染？", "compliance-feasibility"),
    ("实时研判北京市O3-8h今日达标可能性？", "compliance-feasibility"),
    # ranking-assessment
    ("今日洛阳市PM2.5在河南省排名第几？", "ranking-assessment"),
    ("本月济南市PM10在山东省排名？", "ranking-assessment"),
    ("这个月虞城县考核咋样，第几名？", "ranking-assessment"),
    # regional-benchmark
    ("本月洛阳市和平顶山市哪个城市空气质量好？", "regional-benchmark"),
    ("今日河南省有多少个城市臭氧保良？", "regional-benchmark"),
    ("昨日江苏省O3-8h达标的城市有哪些？", "regional-benchmark"),
    # station-extreme
    ("昨日洛阳市空气质量最差的站点的是哪个站点？", "station-extreme"),
    ("今日北京市空气质量最差的国控站点及综指？", "station-extreme"),
    ("昨日洛阳市哪个站点轻度污染了？", "station-extreme"),
    # trend-analysis
    ("过去一周洛阳市PM2.5浓度变化趋势如何？", "trend-analysis"),
    ("最近7天郑州市综合指数走势如何？", "trend-analysis"),
    ("近三天成都市臭氧峰值和低值分别是多少？", "trend-analysis"),
]


@requires_real_api
@pytest.mark.parametrize(
    "question, expected_skill",
    ROUTING_CASES,
    ids=[f"{q[:20]}→{s}" for q, s in ROUTING_CASES],
)
def test_basic_qa_skill_routing(router: SkillSemanticRouter, question: str, expected_skill: str) -> None:
    """验证用户问题能正确路由到预期 Skill."""
    candidates = router.match(question)

    assert candidates, f"未匹配到任何 Skill，问题: {question}"
    matched = candidates[0]
    assert matched.name == expected_skill, (
        f"问题: {question}\n"
        f"  期望: {expected_skill}\n"
        f"  实际: {matched.name} (score={matched.similarity_score})"
    )


@requires_real_api
def test_basic_qa_skill_routing_summary(router: SkillSemanticRouter) -> None:
    """批量输出所有测试问题的匹配结果摘要，便于人工审查.

    运行: SILICONFLOW_API_KEY=xxx pytest ...::test_basic_qa_skill_routing_summary -v -s
    """
    results: list[dict[str, str | float | bool | None]] = []
    for question, expected in ROUTING_CASES:
        candidates = router.match(question)
        if candidates:
            matched = candidates[0]
            results.append({
                "question": question,
                "expected": expected,
                "matched": matched.name,
                "score": matched.similarity_score,
                "correct": matched.name == expected,
            })
        else:
            results.append({
                "question": question,
                "expected": expected,
                "matched": None,
                "score": None,
                "correct": False,
            })

    correct = sum(1 for r in results if r["correct"])
    total = len(results)
    print(f"\n{'='*80}")
    print(f"basic-qa Skill 路由测试摘要: {correct}/{total} 通过")
    print(f"{'='*80}")
    for r in results:
        mark = "✓" if r["correct"] else "✗"
        score_str = f"{r['score']:.3f}" if r["score"] is not None else "N/A"
        matched_str = r["matched"] or "无匹配"
        print(
            f"  {mark} [{score_str}] "
            f"{str(r['question'])[:30]:<30} → "
            f"{str(matched_str):<30} "
            f"(期望: {r['expected']})"
        )
    print(f"{'='*80}")

    assert correct == total, f"有 {total - correct} 条路由结果不符合预期，详见上方摘要"
