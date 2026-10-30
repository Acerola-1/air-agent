#!/usr/bin/env python3
"""离线审计 Basic QA Skill 材料，不导入应用代码或发现远程工具."""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "src/basic_qa/skills"
REPORT = ROOT / "docs/testing/basic-qa-conversation-loop/material-audit.md"
REGISTRY = ROOT / "src/common/business_registry.py"

# 逐模式人工判读，只描述材料本身；不能作为运行授权配置。
MODES = {
    "air-quality-basic-query": (
        "主数据：实时空气质量；时间基准：helper。气象需调用但缺失可降级；当日未覆盖未来时段才请求空气预报，缺失标注局限；图表主数据成功后要求调用但失败不影响结论。",
        "主数据：实时空气质量；时间基准：helper。气象需调用但缺失可降级；当日未覆盖未来时段才请求空气预报，缺失标注局限；图表主数据成功后要求调用但失败不影响结论。",
    ),
    "broadcast-hour": (
        "主数据：broadcastHour；时间基准：helper。气象仅用户要求解释时调用；当日小时未覆盖全天才请求空气预报，缺失标注局限；图表要求调用但失败可降级。",
        "主数据：broadcastHour；时间基准：helper。小时气象按时段调用；当日小时未覆盖全天才请求空气预报，缺失标注局限；图表要求调用但失败可降级。火点/轨迹等补充缺失则剔除，不推定工具。",
    ),
    "comparison-composition": (
        "同比：text2sql 与实时工具均在流程中；占比：按需 integratedIndexRatio。两工具均列为加载必需，具体业务用途仍按原流程。时间基准：helper；气象缺失可降级；两类图表各自要求调用但失败不影响结论。",
        "同比：text2sql 与实时工具均在流程中；占比：按需 integratedIndexRatio。两工具均列为加载必需，具体业务用途仍按原流程。时间基准：helper；气象缺失可降级；两类图表各自要求调用但失败不影响结论。",
    ),
    "compliance-feasibility": (
        "主数据：compliance_prediction；时间基准：helper；实时为补充。气象缺失标注局限；仅当日未来未覆盖时请求预报，失败降级。",
        "主数据：compliance_prediction；时间基准：helper；实时为补充。气象缺失标注局限；仅当日未来未覆盖时请求预报，失败降级。专家模式要求的核心字段严于快速模式。",
    ),
    "integrated-index-ratio": (
        "主数据：integratedIndexRatio；时间基准：helper。气象仅用户要求解释时调用；预报仅明确追问未来风险时可调用；图表要求调用但失败不影响结论。",
        "主数据：integratedIndexRatio；时间基准：helper。气象按时段调用；预报仅明确追问未来风险时可调用；图表要求调用但失败不影响结论。遥感/产业等补充缺失剔除，不推定工具。",
    ),
    "ranking-assessment": (
        "主数据：cityFactorRankings；时间基准：helper；实时工具可作补充。图表要求调用但失败不影响结论。",
        "主数据：cityFactorRankings；时间基准：helper；实时工具可作补充。图表要求调用但失败不影响结论；补充字段缺失只分析已有事实。",
    ),
    "regional-benchmark": (
        "按场景分别需要 text2sql 月年对标主数据或 calculations 区域达标主数据；时间基准：helper。对应图表要求调用但失败不影响结论；日级多城对标与该流程的对应关系未明确。",
        "按场景分别需要 text2sql 月年对标主数据或 calculations 区域达标主数据；时间基准：helper。对应图表要求调用但失败不影响结论；日级多城对标与该流程的对应关系未明确。",
    ),
    "station-extreme": (
        "必须先有上游或上下文站点主数据，否则直接缺失处理；实时城市、站点历史与预报仅称补充，不能推定其为主数据来源。站点基础工具可在需要基础信息时使用，材料未表述为主数据来源。",
        "必须先有上游或上下文站点主数据，否则直接缺失处理；实时城市、站点历史与预报仅称补充，不能推定其为主数据来源。站点基础工具可在需要基础信息时使用，材料未表述为主数据来源。",
    ),
    "trend-analysis": (
        "主数据：oneIndexManyCityLine；时间基准：helper；实时为补充。气象需调用但缺失可降级；仅当日/未来趋势请求预报；图表要求调用但失败不影响结论。",
        "主数据：oneIndexManyCityLine；时间基准：helper；实时为补充。气象需调用但缺失可降级；仅当日/未来趋势请求预报；图表要求调用但失败不影响结论。",
    ),
}

# 将人工判读绑定到输入字节：材料或登记变化必须重新逐项审阅并更新快照。
EXPECTED = {
    "src/basic_qa/skills/air-quality-basic-query/SKILL.md": "3f32ecf51f0fbbf784d44952e1f8f5305fe073d6c9cb066c502ddf1c656dbae9",
    "src/basic_qa/skills/air-quality-basic-query/references/expert.md": "0bc7fdb08c720f7c5479004bcbc9a923260af7481999b61a88c16862f95dccd6",
    "src/basic_qa/skills/air-quality-basic-query/references/fast.md": "fb76ff84ff0b23e636cd4256dd782db22728bd973fc4f0e35ff6a3e41807f60c",
    "src/basic_qa/skills/broadcast-hour/SKILL.md": "41c3d94e891772e07278f3a3fe948b170c94c4ed137e55884ca8e31be80f9650",
    "src/basic_qa/skills/broadcast-hour/references/expert.md": "393f6b4a411ed0ad33a509aa0b2e8b94829373b40263ff972ad331f29ec80921",
    "src/basic_qa/skills/broadcast-hour/references/fast.md": "a2652737e3d4976853317ab212fcb4c7f27c3a85713a27ad5f7aceeb7e3a5ca1",
    "src/basic_qa/skills/comparison-composition/SKILL.md": "ed625077799a192aa7f3bfc0e3c3a9617dff15c34729c7dfbc8dba257004b83a",
    "src/basic_qa/skills/comparison-composition/references/expert.md": "b02c2dec8052404f3d46287e67c9755af847c7bccd3f7c2b42f55bc166b800ad",
    "src/basic_qa/skills/comparison-composition/references/fast.md": "cb48a5e723ab19a8a774bf3877d414d404be228e85ae160560c17e83e5f3a2f2",
    "src/basic_qa/skills/compliance-feasibility/SKILL.md": "17ac9b00cc3d8e53e18fc2990880b660736828a587dec42e8516dce922d4ef8b",
    "src/basic_qa/skills/compliance-feasibility/references/expert.md": "d3fef5270e0b89454ae56a1a4f85cbe59e1055599b4801c09e2f6deb5c3efd84",
    "src/basic_qa/skills/compliance-feasibility/references/fast.md": "68ce879b1d1624e3e1df011af5d83c81aec632a59c50263bb0ae92c07a5e8d2a",
    "src/basic_qa/skills/integrated-index-ratio/SKILL.md": "f399e77b570dd1d82c4772c8cd09dfcca044c5a506898b1566d0dc6760968f8c",
    "src/basic_qa/skills/integrated-index-ratio/references/expert.md": "4f5afefaf45a2720f02ff4ba1994a17714bc8fd04af72e15e159f3702b7a4618",
    "src/basic_qa/skills/integrated-index-ratio/references/fast.md": "aacdbfbfbee4c9ca5ea80980b7fd26eb654bf43329c754173629480f2dfe5b28",
    "src/basic_qa/skills/ranking-assessment/SKILL.md": "ef113dfd27e383e6664a8030eabe0aa87ed08bcce18de9a32e4d491bddf8030c",
    "src/basic_qa/skills/ranking-assessment/references/expert.md": "9d5cf9dd5ac143ecb0f5964e15b32b211177241896d6de7015f8c5e422d047bf",
    "src/basic_qa/skills/ranking-assessment/references/fast.md": "46fc61f0e5ac0b71e88d3a2dc575e2ee227100ed23409c4bfc511810c9073130",
    "src/basic_qa/skills/regional-benchmark/SKILL.md": "f0a7f40c0aa990f0a01b3f4ce15aa1178b3b5ae4ad88d0a66ea0b8d072746191",
    "src/basic_qa/skills/regional-benchmark/references/expert.md": "68380f7661c543858b7b103e5032978e0b3086bdc471b8760dce94e22359a693",
    "src/basic_qa/skills/regional-benchmark/references/fast.md": "73dfa6f7d7c11bb4a3ca3c9a9d6b71d5348bec8294423442530052bb1602fbd7",
    "src/basic_qa/skills/station-extreme/SKILL.md": "f9a2c2321320cb9e742adc4ceba241a8d4085de36a6e0a56d177cb6528aa0455",
    "src/basic_qa/skills/station-extreme/references/expert.md": "da78df9bf49a077924a58164f7fba0a06edd8f60b20c904e17bb88b53fde7993",
    "src/basic_qa/skills/station-extreme/references/fast.md": "e168217b414b6552870733a3f8ed7225de0aefce6a329368723579b05fcb1c32",
    "src/basic_qa/skills/trend-analysis/SKILL.md": "d6d1928ddd6a95930851987f33bc1c74cde65ad4a43673eb7bebff765c4de396",
    "src/basic_qa/skills/trend-analysis/references/expert.md": "aa8b18e88c3f7d4dd7e03d57739935305bb85efd4e719459d82129b044de58e6",
    "src/basic_qa/skills/trend-analysis/references/fast.md": "67bab79a1dd494b9742016631248b52f076910a496dda62e1459096aab83531a",
    "src/common/business_registry.py": "310e1bf4311c50caf9589fa4eb484e26e35a654d5ae58e4447ffc4ea341d7817",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registered_tools() -> set[str]:
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "BUSINESSES"
        ):
            assert isinstance(node.value, ast.Dict)
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "basic-qa":
                    assert isinstance(value, ast.Call)
                    conn = next(
                        k.value for k in value.keywords if k.arg == "connections"
                    )
                    assert isinstance(conn, ast.Call) and isinstance(
                        conn.args[0], ast.Tuple
                    )
                    return set(ast.literal_eval(conn.args[0]))
    raise ValueError("未找到 Basic QA 静态登记")


def _version(directory: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(
        (p for p in directory.rglob("*") if p.is_file() and not p.name.startswith(".")),
        key=lambda p: p.relative_to(directory).as_posix(),
    ):
        h.update(f"{path.relative_to(directory).as_posix()}:".encode())
        h.update(path.read_bytes())  # 读取失败直接终止，不用空字节替代
        h.update(b"\n")
    return "sha256:" + h.hexdigest()


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="校验已保存报告是否与当前生成结果一致"
    )
    args = parser.parse_args()
    paths = sorted(
        p for p in SKILLS.rglob("*") if p.is_file() and not p.name.startswith(".")
    ) + [REGISTRY]
    actual = {str(p.relative_to(ROOT)): _sha(p) for p in paths}
    if actual != EXPECTED:
        changed = sorted(
            k for k in set(actual) | set(EXPECTED) if actual.get(k) != EXPECTED.get(k)
        )
        raise SystemExit(
            "材料或业务登记已变化，须重新审阅并更新快照：\n" + "\n".join(changed)
        )
    if {p.name for p in SKILLS.iterdir() if p.is_dir()} != set(MODES):
        raise SystemExit("技能集合变化，须重新审阅")
    approved = _registered_tools()
    lines = [
        "# Basic QA Skill 材料与工具静态审计",
        "",
        "代码基线：`7d79526bb4cdd4c5d04911f62943fd11a50c9153`；材料为本次更新后的工作区版本，材料和业务登记字节由脚本快照锁定。只读静态检查，未连接服务、读取部署配置或验证实时发现/授权/参数。",
        "",
        "复跑：`.venv/bin/python scripts/audit_basic_qa_skill_materials.py --check`；查看重新生成内容：`.venv/bin/python scripts/audit_basic_qa_skill_materials.py`。快照变化时先人工重审，再更新脚本内快照和报告。",
        "",
        "材料版本按 `src/common/capabilities.py:89` 的相对路径排序 SHA-256 算法计算；此脚本读取失败即终止，不使用其读取失败时的空字节回退。`fast`/`expert` 与总纲均存在，不代表运行加载已验证。",
        "",
        "静态登记中的短名可构造候选 `mcp:ipp:<名称>`，依据 `src/common/business_registry.py` 和 `src/common/mcp_scoped.py:170`；实际发现还须经过受保护连接、approved_query_tools、实时发现和权限交集，且短名跨服务重名时会失效。候选不代表当前可用或功能/参数等价。未登记项在当前 Basic QA 静态策略下不可用，不能映射到其他同名或相似工具。",
        "",
        "依照用户确认的加载分类：总纲 allowed-tools 和模式引用的全部非 chart 工具均为必需，chart 工具为可选；必需工具缺失阻止加载，可选图表缺失不阻止正文加载。下方模式说明保留数据调用条件与结果缺失时的领域规则；工具缺失与调用后数据缺失是不同情形。工具名称逐项与引用位置见下表。",
        "",
        "## 九个 Skill 与模式",
        "",
    ]
    aliases = {}
    for slug in sorted(MODES):
        d = SKILLS / slug
        files = [d / "SKILL.md", d / "references/fast.md", d / "references/expert.md"]
        if not all(p.is_file() for p in files):
            raise SystemExit(f"{slug} 总纲或模式文件缺失")
        texts = [p.read_text(encoding="utf-8") for p in files]
        match = re.search(r"^allowed-tools:[ \t]*([^\n]*)$", texts[0], re.M)
        if not match:
            raise SystemExit(f"{slug} 缺 allowed-tools")
        declared = set(match.group(1).split())
        fields = {}
        for field in ("required-tools", "optional-tools"):
            field_match = re.search(rf"^{field}:[ \t]*([^\n]*)$", texts[0], re.M)
            if not field_match:
                raise SystemExit(f"{slug} 缺 {field}")
            fields[field] = set(field_match.group(1).split())
        required, optional = fields["required-tools"], fields["optional-tools"]
        if required & optional or required | optional != declared:
            raise SystemExit(f"{slug} 必需与可选声明有交集或未覆盖 allowed-tools")
        if required != {
            name for name in declared if not name.startswith("chart_")
        } or optional != {name for name in declared if name.startswith("chart_")}:
            raise SystemExit(f"{slug} 分类与已确认规则不符")
        lines += [
            f"### {slug}",
            "",
            f"- Skill ID：`skill:basic-qa:{slug}`",
            f"- 材料版本：`{_version(d)}`",
            "- 必需：" + "、".join(f"`{name}`" for name in sorted(required)),
            "- 可选：" + ("、".join(f"`{name}`" for name in sorted(optional)) or "无"),
            f"- fast：{MODES[slug][0]}",
            f"- expert：{MODES[slug][1]}",
            "",
        ]
        for path, content in zip(files, texts, strict=True):
            for no, line in enumerate(content.splitlines(), 1):
                names = set(
                    re.findall(
                        r"`((?:mcp_|chart_|text2sql_)[A-Za-z0-9_]+|helper_get_latest_time|broadcastHour|integratedIndexRatio|cityFactorRankings|oneIndexManyCityLine)`",
                        line,
                    )
                )
                if path.name == "SKILL.md" and line.startswith("allowed-tools:"):
                    names |= declared
                for name in names:
                    entry = aliases.setdefault(name, [])
                    entry.append(f"{path.relative_to(ROOT)}:{no}")
        referenced = {
            n
            for n in aliases
            if any(x.startswith(f"src/basic_qa/skills/{slug}/") for x in aliases[n])
        }
        undeclared = sorted(referenced - declared)
        if undeclared:
            raise SystemExit(f"{slug} 模式引用工具未声明：{undeclared}")
    lines += [
        "## 工具名称对照（逐项）",
        "",
        "| 材料名称 | 静态稳定 ID / 状态 | 引用位置 |",
        "|---|---|---|",
    ]
    for name, refs in sorted(aliases.items()):
        status = (
            f"`mcp:ipp:{name}`（静态登记；动态未验证）"
            if name in approved
            else "未登记，当前策略不可用；无已验证稳定 ID"
        )
        lines.append(
            f"| `{name}` | {status} | "
            + "、".join(f"`{x}`" for x in sorted(set(refs)))
            + " |"
        )
    lines += [
        "",
        "## 待解决的设计阻断",
        "",
        "- 九份材料现分别声明 `required-tools` 和 `optional-tools`；运行时是否按稳定 ID 与实时连接共同校验仍须实现并验收。",
        "- 模式引用的图表已补入允许声明并列为可选；这里发现的图表均不在 Basic QA 业务工具登记。不能凭相似名称补齐或视为已经发现，缺失不阻止正文加载。",
        "- 站点主数据需要可信上游来源；站点历史/预报在材料里仅作补充。不得未经领域确认将其改判为主数据。",
        "- regional-benchmark 日级多城对标来源仍未明确；任何改变领域语义须先补内容 delta。",
        "- text2sql 名称在 `ipp` 静态登记中，不能据此声称本地旧 Text2SQL 工具存在；当前真实连接、远端 schema 和发现仍未验证。",
        "",
    ]
    output = "\n".join(lines)
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != output:
            raise SystemExit("报告与生成结果不一致")
        sys.stdout.write("材料快照、工具登记与审计报告一致\n")
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    _main()
