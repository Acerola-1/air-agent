"""Skill 路由分数排行诊断测试.

用于诊断用户问题在 intelligent-analysis 技能目录下对所有 SKILL.md 的 embedding 分数排行，
帮助排查路由错配根因。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

INTELLIGENT_ANALYSIS_SKILLS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "intelligent_analysis"
    / "skills"
)

QUERY = "小时播报:分析下湖州市今天的六参空气质量情况,并出具一份报告"

pytestmark = [
    # 需要真实 embedding 调用；只在 RUN_SKILL_RANKING=1 时跑
    pytest.mark.skipif(
        os.environ.get("RUN_SKILL_RANKING") != "1",
        reason="需要真实 embedding 调用，默认跳过；设置 RUN_SKILL_RANKING=1 执行。",
    ),
]


@dataclass
class SkillScoreRow:
    """单个 skill 的最终得分行."""

    skill_name: str
    max_similarity: float
    mean_similarity: float
    utterance_count: int
    matched_utterance: str


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_skill_ranking_huzhou_hourly_report_print_ranking(capsys: pytest.CaptureFixture[str]) -> None:
    """打印 QUERY 对 intelligent-analysis 所有 skill 的 embedding 分数排行.

    不做阈值裁剪 / max_candidate 限制，真实还原 router 内部构建的 utterances 索引
    与 query 的最大余弦相似度排行。
    """
    from dotenv import dotenv_values

    env_file = Path(__file__).resolve().parents[2] / ".env"
    env_vals = dotenv_values(env_file)

    # conftest.py 里 setdefault 会在 pytest 进程启动时把 SILICONFLOW_API_KEY="test" 写入 os.environ；
    # 这里直接把 .env 里的真实值覆盖到 os.environ，并重建 config 模块级单例
    import importlib
    import os
    import sys

    for k in ("SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL", "SILICONFLOW_EMBEDDING_MODEL"):
        if env_vals.get(k):
            os.environ[k] = env_vals[k]

    import common.config
    from common.config.settings import Config

    common.config.config = Config()
    # 若 skill_router 已提前 import，则其内部 import 的 config 是旧引用；强制从 sys.modules 清掉重导
    for mod_name in list(sys.modules.keys()):
        if (
            mod_name == "common.skill_router"
            or mod_name.startswith("common.skill_router.")
            or mod_name == "common.config"
            or mod_name == "common.config.settings"
        ):
            del sys.modules[mod_name]
    importlib.invalidate_caches()

    from common.skill_router import (
        SkillSemanticRouter,
        normalize_routing_text,
    )

    router = SkillSemanticRouter(INTELLIGENT_ANALYSIS_SKILLS_DIR)
    built = router._build_router()  # noqa: SLF001  —— 测试需要直接访问成员
    assert built is not None, "semantic-router 构建失败，请检查 embedding 配置/网络"

    encoder = built.encoder
    assert encoder is not None, "router encoder 为空"

    skills_by_name = router._skills_by_name  # noqa: SLF001
    assert skills_by_name, "未加载到任何 skill"

    normalized_query = normalize_routing_text(QUERY)
    query_vec = np.asarray(encoder([normalized_query])[0])

    rows: list[SkillScoreRow] = []
    for skill_name, meta in skills_by_name.items():
        utterances = list(meta.utterances)
        if not utterances:
            rows.append(SkillScoreRow(skill_name, -1.0, -1.0, 0, ""))
            continue
        # 每条 utterance 独立 embedding，取 max 作为 route score（与 semantic-router aggregation=max 对齐）
        vecs = np.asarray(encoder(utterances))
        sims = [_cosine(query_vec, v) for v in vecs]
        max_idx = int(np.argmax(sims))
        rows.append(
            SkillScoreRow(
                skill_name=skill_name,
                max_similarity=float(sims[max_idx]),
                mean_similarity=float(np.mean(sims)),
                utterance_count=len(utterances),
                matched_utterance=utterances[max_idx],
            )
        )

    rows.sort(key=lambda r: r.max_similarity, reverse=True)

    # Print 排行榜；用 capsys 确保 pytest -s 能看到
    separator = "=" * 140
    with capsys.disabled():
        print(f"\n\n{separator}")  # noqa: T201
        print(f"QUERY: {QUERY}")  # noqa: T201
        print(f"归一化后: {normalized_query}")  # noqa: T201
        print(f"技能目录: {INTELLIGENT_ANALYSIS_SKILLS_DIR}")  # noqa: T201
        print(f"共计技能: {len(rows)}")  # noqa: T201
        print(separator)  # noqa: T201
        header = f"{'RANK':>4}  {'SKILL':<36}  {'MAX_SIM':>8}  {'MEAN_SIM':>8}  {'UTT_CNT':>7}  MATCHED_UTTERANCE"
        print(header)  # noqa: T201
        print("-" * 140)  # noqa: T201
        for i, row in enumerate(rows, 1):
            mark = " ★ 目标技能" if row.skill_name == "broadcast-hour" else ""
            mark2 = " ✗ 错配技能" if row.skill_name == "today-target-analysis" else ""
            print(  # noqa: T201
                f"{i:>4}  {row.skill_name:<36}  {row.max_similarity:>8.5f}  "
                f"{row.mean_similarity:>8.5f}  {row.utterance_count:>7}  "
                f"{row.matched_utterance!r}{mark}{mark2}"
            )
        print(separator)  # noqa: T201

    # 基础断言：至少加载到了 2 个预期技能
    skill_names = {r.skill_name for r in rows}
    assert "broadcast-hour" in skill_names, "缺少 broadcast-hour 技能"
    assert "today-target-analysis" in skill_names, "缺少 today-target-analysis 技能"


def test_amatch_returns_top3_candidates_for_huzhou_query(capsys: pytest.CaptureFixture[str]) -> None:
    """验证 amatch 现在返回最多 3 个候选（方案 A：top3 让模型选）.

    之前 limit=1 时只返回 top1（today-target-analysis），导致错配。
    改为 limit=3 后应返回多个候选，包含 broadcast-hour。
    """
    from dotenv import dotenv_values

    env_file = Path(__file__).resolve().parents[2] / ".env"
    env_vals = dotenv_values(env_file)

    import asyncio
    import importlib
    import os
    import sys

    for k in ("SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL", "SILICONFLOW_EMBEDDING_MODEL"):
        if env_vals.get(k):
            os.environ[k] = env_vals[k]

    import common.config
    from common.config.settings import Config

    common.config.config = Config()
    for mod_name in list(sys.modules.keys()):
        if (
            mod_name == "common.skill_router"
            or mod_name.startswith("common.skill_router.")
            or mod_name == "common.config"
            or mod_name == "common.config.settings"
        ):
            del sys.modules[mod_name]
    importlib.invalidate_caches()

    from common.skill_router import SkillSemanticRouter

    router = SkillSemanticRouter(INTELLIGENT_ANALYSIS_SKILLS_DIR)
    candidates = asyncio.run(router.amatch(QUERY))

    separator = "=" * 100
    with capsys.disabled():
        print(f"\n\n{separator}")  # noqa: T201
        print(f"amatch 候选结果 (limit={router.max_candidates}):")  # noqa: T201
        print(f"QUERY: {QUERY}")  # noqa: T201
        for i, c in enumerate(candidates, 1):
            mark = " ★ 目标技能" if c.name == "broadcast-hour" else ""
            mark2 = " ✗ 错配技能" if c.name == "today-target-analysis" else ""
            print(f"  {i}. {c.name:<36}  score={c.similarity_score:.5f}{mark}{mark2}")  # noqa: T201
        print(separator)  # noqa: T201

    # 核心断言：应返回 >1 个候选（top3 生效）
    assert len(candidates) > 1, (
        f"amatch 仍只返回 1 个候选，limit=3 未生效；candidates={candidates}"
    )
    assert len(candidates) <= 3, (
        f"amatch 返回了 {len(candidates)} 个候选，应 <=3"
    )

    candidate_names = {c.name for c in candidates}
    assert "broadcast-hour" in candidate_names, (
        f"broadcast-hour 不在 top3 候选中，candidates={candidate_names}"
    )
