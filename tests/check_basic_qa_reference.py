"""离线试检固定题库：真实意图规则、Skill 加载与独立答案算术。

不发送模型/MCP 请求，不连接数据库；不能代替实际问答或性能测试。
每次必须提供新的输出目录，失败同样保存报告并返回非零退出码。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/testing/data"


def _deny_network(*args, **kwargs):
    raise RuntimeError("offline reference check forbids network access")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    records = []

    def check(name, expected, actual, *, case_id=None):
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            passed = abs(expected - actual) < 0.051
        else:
            passed = expected == actual
        records.append(
            {
                "check_id": name,
                "case_id": case_id,
                "expected": expected,
                "actual": actual,
                "status": "PASS" if passed else "FAIL",
            }
        )

    cases_path = DATA / "basic-qa-reference-cases.v1.json"
    fixtures_path = DATA / "basic-qa-reference-fixtures.v1.json"
    dataset = json.loads(cases_path.read_text())
    fixtures = json.loads(fixtures_path.read_text())
    cases = dataset["cases"]
    # 只为加载纯规则模块提供占位配置；不建立身份/服务，不加载私有配置文件。
    for key in ("SILICONFLOW_API_KEY", "SERPER_API_KEY", "MAIN_LLM_API_KEY"):
        os.environ.setdefault(key, "offline-reference-check")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
    sys.path.insert(0, str(ROOT / "src"))
    with (
        patch.object(socket.socket, "connect", _deny_network),
        patch.object(socket, "create_connection", _deny_network),
    ):
        from common.business_graph.intent import classify_intent_rule
        from common.business_graph.skill_content import load_skill_rules
        from common.skill_router import SkillSemanticRouter, load_skill_metadata

        skills_dir = ROOT / "src/basic_qa/skills"
        metadata = load_skill_metadata(skills_dir)
        check("case-count", 40, len(cases))
        check("unique-ids", 40, len({case["case_id"] for case in cases}))
        check(
            "category-count",
            {f"Q{i}": 10 for i in range(1, 5)},
            dict(Counter(c["category"] for c in cases)),
        )
        covered = {skill for case in cases for skill in case["expected_skills"]}
        check("skill-coverage", sorted(s.name for s in metadata), sorted(covered))
        router = SkillSemanticRouter(skills_dir)
        skill_hashes = {}
        for skill in metadata:
            skill_hashes[skill.name] = {}
            for mode in ("fast", "expert"):
                rules = load_skill_rules(router, skill.name, mode)
                reference = skill.path.parent / "references" / f"{mode}.md"
                check(
                    f"rules-loaded:{skill.name}:{mode}",
                    True,
                    bool(rules) and reference.is_file(),
                )
                skill_hashes[skill.name][mode] = hashlib.sha256(
                    rules.encode()
                ).hexdigest()

        for case in cases:
            cid = case["case_id"]
            actual, reason = classify_intent_rule(case["question"])
            check("intent", case["expected_intent"], actual, case_id=cid)
            records[-1]["reason"] = reason
            check(
                "history-pairs",
                10 if case["category"] == "Q4" else 0,
                len(case["history"]),
                case_id=cid,
            )
            check(
                "fixture-refs",
                True,
                all(ref in fixtures for ref in case["fixture_refs"]),
                case_id=cid,
            )

    # 从输入序列独立重算，防止题目中的预期数值随手填错。
    day = fixtures["day"]["cities"]
    for city, values in day.items():
        check(
            f"fixture-consistency:daily:{city}",
            values["pm25"],
            fixtures["daily_series"]["cities"][city][-1],
        )
        hours = fixtures["hourly"]["cities"][city]
        check(
            f"fixture-consistency:hourly:{city}",
            values["pm25"],
            sum(hours) / len(hours),
        )
        ozone = next(
            r["value"] for r in fixtures["regional"]["rows"] if r["city"] == city
        )
        check(f"fixture-consistency:ozone:{city}", values["o3_8h"], ozone)
    monthly = fixtures["monthly"]["cities"]
    luoyang = monthly["洛阳市"]
    numeric = {
        "BQA-011": {"aqi": day["洛阳市"]["aqi"]},
        "BQA-012": {
            k: day["洛阳市"][k] for k in ("pm25", "pm10", "so2", "no2", "o3_8h", "co")
        },
        "BQA-013": {
            "luoyang": day["洛阳市"]["aqi"],
            "zhengzhou": day["郑州市"]["aqi"],
            "difference": day["洛阳市"]["aqi"] - day["郑州市"]["aqi"],
        },
        "BQA-014": {
            "aqi": fixtures["current"]["aqi"],
            "cutoff_hour": fixtures["current"]["cutoff_hour"],
        },
        "BQA-021": {
            "index_change_pct": (luoyang["index"] / luoyang["last_index"] - 1) * 100,
            "pm25_change_pct": (luoyang["pm25"] / luoyang["last_pm25"] - 1) * 100,
        },
        "BQA-022": {
            "good_days_change": luoyang["good_days"] - luoyang["last_good_days"]
        },
        "BQA-027": {"difference": luoyang["index"] - monthly["郑州市"]["index"]},
        "BQA-031": {"aqi": day["郑州市"]["aqi"]},
        "BQA-033": {"change": luoyang["good_days"] - luoyang["last_good_days"]},
        "BQA-034": {"difference": luoyang["index"] - monthly["郑州市"]["index"]},
        "BQA-040": {"difference": day["洛阳市"]["aqi"] - day["郑州市"]["aqi"]},
    }
    for cid, factor in (("BQA-015", "pm25"), ("BQA-032", "pm10")):
        rows = fixtures["rank"][factor]
        row = next(r for r in rows if r["city"] == "洛阳市")
        numeric[cid] = {"rank": row["rank"], "total": len(rows), "value": row["value"]}
    stations = fixtures["stations"]["rows"]
    national_max = max(r["aqi"] for r in stations if r["type"] == "国控")
    numeric["BQA-016"] = numeric["BQA-039"] = {"aqi": national_max}
    numeric["BQA-017"] = {"count": sum(r["aqi"] > 100 for r in stations)}
    numeric["BQA-029"] = {
        "national_max": national_max,
        "provincial_max": max(r["aqi"] for r in stations if r["type"] == "省控"),
    }
    regional = fixtures["regional"]
    count = sum(r["value"] <= regional["threshold"]["value"] for r in regional["rows"])
    numeric["BQA-018"] = {
        "count": count,
        "total": len(regional["rows"]),
        "rate_pct": round(count / len(regional["rows"]) * 100, 1),
    }
    comp = fixtures["compliance"]["cities"]["洛阳市"]
    numeric["BQA-019"] = {
        "cumulative": comp["countValue"],
        "current": comp["value"],
        "remaining": comp["remaining"],
    }
    numeric["BQA-038"] = {"remaining": comp["remaining"]}
    hourly = fixtures["hourly"]
    lh, zh = hourly["cities"]["洛阳市"], hourly["cities"]["郑州市"]
    numeric["BQA-020"] = {
        "peak": max(lh),
        "peak_hour": lh.index(max(lh)),
        "min": min(lh),
        "min_hour": lh.index(min(lh)),
    }
    diffs = [a - b for a, b in zip(lh, zh, strict=True)]
    numeric["BQA-028"] = {"max_difference": max(diffs), "hour": diffs.index(max(diffs))}
    numeric["BQA-036"] = {"peak": max(zh), "hour": zh.index(max(zh))}
    for cid, city in (("BQA-025", "洛阳市"), ("BQA-026", "郑州市")):
        seq = fixtures["daily_series"]["cities"][city]
        numeric[cid] = {
            "max": max(seq),
            "min": min(seq),
            "mean": round(sum(seq) / len(seq), 1),
        }
    numeric["BQA-035"] = {k: numeric["BQA-025"][k] for k in ("mean", "max")}
    ratios = fixtures["ratio"]["cities"]
    sub = ratios["洛阳市"]["subindices"]
    total = sum(sub.values())
    factor_keys = {
        "PM25": "pm25_pct",
        "PM10": "pm10_pct",
        "SO2": "so2_pct",
        "NO2": "no2_pct",
        "CO": "co_pct",
        "O3_8h": "o3_pct",
    }
    numeric["BQA-023"] = {
        "sum": total,
        **{factor_keys[k]: v / total * 100 for k, v in sub.items()},
    }
    zsub = ratios["郑州市"]["subindices"]
    numeric["BQA-024"] = {
        "luoyang_top_pct": max(sub.values()) / total * 100,
        "zhengzhou_top_pct": max(zsub.values()) / sum(zsub.values()) * 100,
    }
    numeric["BQA-037"] = {
        "rank": sorted(sub.values(), reverse=True).index(sub["O3_8h"]) + 1,
        "pct": sub["O3_8h"] / total * 100,
    }
    for case in cases:
        expected = case["numeric_expected"]
        actual = numeric.get(case["case_id"], {})
        check("gold-keys", sorted(expected), sorted(actual), case_id=case["case_id"])
        for key, value in expected.items():
            check(f"gold:{key}", value, actual.get(key), case_id=case["case_id"])

    source_paths = [
        cases_path,
        fixtures_path,
        *sorted((ROOT / "src/basic_qa/skills").rglob("*.md")),
        ROOT / "src/common/business_graph/intent.py",
        ROOT / "src/common/permission/rules.py",
        Path(__file__).resolve(),
    ]
    summary = {
        "schema_version": "basic-qa-reference-check.v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "scope": "offline-intent-skill-loading-and-gold-arithmetic",
        "network": "denied",
        "duration_seconds": round(time.monotonic() - started, 3),
        "dataset_version": dataset["dataset_version"],
        "counts": dict(Counter(r["status"] for r in records)),
        "case_count": len(cases),
        "skill_count": len(skill_hashes),
        "skill_rule_hashes": skill_hashes,
        "source_hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_paths
        },
        "not_executed": [
            "real-embedding-routing",
            "real-model-answers",
            "MCP-tool-execution",
            "IAM/PG/BFF integration",
            "browser",
            "concurrent-performance",
        ],
        "records": records,
    }
    (args.out / "checks.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    failed = [r for r in records if r["status"] == "FAIL"]
    lines = [
        "# basic QA 固定题库离线试检报告",
        "",
        f"执行时间：{summary['recorded_at']}；数据集：{dataset['dataset_version']}。",
        "",
        f"实际检查 {len(records)} 项：通过 {summary['counts'].get('PASS', 0)}，失败 {len(failed)}；40 题，{len(skill_hashes)} 个 Skill。",
        "",
        "范围：实际意图规则、fast/expert 规则装载、题库结构及独立数值重算。网络被禁止，未连接业务库。不是模型实际回答/语义路由/并发性能验收。",
        "",
        "## 失败项",
        "",
        "| 题号 | 检查 | 预期 | 实际 |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r['case_id'] or '-'} | {r['check_id']} | {r['expected']} | {r['actual']} |"
        for r in failed
    ]
    if not failed:
        lines.append("| - | 无失败 | - | - |")
    lines += [
        "",
        "原始结果及源码摘要：[checks.json](checks.json)。",
        "",
        "未执行：" + "、".join(summary["not_executed"]) + "。",
    ]
    (args.out / "report.md").write_text("\n".join(lines) + "\n")
    sys.stdout.write(
        json.dumps(
            {
                "counts": summary["counts"],
                "out": str(args.out),
                "scope": summary["scope"],
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
