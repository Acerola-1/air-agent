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

# 逐 Skill 人工判读，只描述材料本身；不能作为运行授权配置。
SKILL_NOTES = {
    "air-quality-basic-query": "主数据：实时空气质量；时间基准：helper。气象需调用但缺失可降级；当日未覆盖未来时段才请求空气预报，缺失标注局限。单事实问题先给结论与数值，分析类问题按专家结构展开；不生成图表或画布。",
    "broadcast-hour": "主数据：broadcastHour；时间基准：helper。小时气象按时段调用；当日小时未覆盖全天才请求空气预报，缺失标注局限。火点/轨迹等补充缺失则剔除，不推定工具；不生成图表或画布。",
    "comparison-composition": "同比：text2sql 与实时工具均在流程中；占比：按需 integratedIndexRatio。两者均列为加载必需，具体业务用途按流程。时间基准：helper；气象缺失可降级；不生成图表或画布。",
    "compliance-feasibility": "主数据：compliance_prediction；时间基准：helper；实时为补充。气象缺失标注局限；仅当日未来未覆盖时请求预报，失败降级；不生成图表或画布。",
    "integrated-index-ratio": "主数据：integratedIndexRatio；时间基准：helper。气象仅用户要求解释时调用；预报仅明确追问未来风险时可调用。遥感/产业等补充缺失剔除，不推定工具；不生成图表或画布。",
    "ranking-assessment": "主数据：cityFactorRankings；时间基准：helper；实时工具可作补充。补充字段缺失只分析已有事实；不生成图表或画布。",
    "regional-benchmark": "按场景分别需要 text2sql 月年对标主数据或 calculations 区域达标主数据；时间基准：helper；日级多城对标与该流程的对应关系未明确；不生成图表或画布。",
    "station-extreme": "必须先有上游或上下文站点主数据，否则直接缺失处理；实时城市、站点历史与预报仅称补充，不能推定其为主数据来源。站点基础工具可在需要基础信息时使用；不生成图表或画布。",
    "trend-analysis": "主数据：oneIndexManyCityLine；时间基准：helper；实时为补充。气象需调用但缺失可降级；仅当日/未来趋势请求预报；不生成图表或画布。",
}

# 将人工判读绑定到输入字节：材料或登记变化必须重新逐项审阅并更新快照。
EXPECTED = {
    "src/basic_qa/skills/air-quality-basic-query/SKILL.md": "c0ca7dcb767a2537e3994a8249d6f3f8582f746e2c753e54fda7b708b7eb6d9e",
    "src/basic_qa/skills/air-quality-basic-query/references/rules.md": "439a989d1951a26f54704c8883c7ad4dd163dd74d0b8ad1922ebc1d6bdb09637",
    "src/basic_qa/skills/broadcast-hour/SKILL.md": "4d34d4e5bbd09cd257a0c7c38e6411cbac403d11861feb13b19671bc04dd21b0",
    "src/basic_qa/skills/broadcast-hour/references/rules.md": "95d8bd7a5283974bba8039d197c3954197a447f9033e9e2bb2e3140e58d7c938",
    "src/basic_qa/skills/comparison-composition/SKILL.md": "bc2388c4e09f27e0f0265b3b4671fcdfc8aeed6e45264d9b3f7ad3b00a57e513",
    "src/basic_qa/skills/comparison-composition/references/rules.md": "2aec845c587582b8782dc206130f0aa23fb92de03e4d668e2c889055f35e28a3",
    "src/basic_qa/skills/compliance-feasibility/SKILL.md": "f622550c5fc8d4e10a412ef02bf83f2c947eaab025761d5ca4bc6b0eda1385f5",
    "src/basic_qa/skills/compliance-feasibility/references/rules.md": "3c3716569b519634310ecbc40f68de83935484d4d76a661b292851c6e0f2fa94",
    "src/basic_qa/skills/integrated-index-ratio/SKILL.md": "2d9b3bd543769146ab257592793140c956ac247ab5cec0cf1472960b95a92b2f",
    "src/basic_qa/skills/integrated-index-ratio/references/rules.md": "3c4c62375356cad9d01ca743b8413f456879d0e7f11490986979fbbdf9209aab",
    "src/basic_qa/skills/ranking-assessment/SKILL.md": "ec5487f6509e06a23054b5b7c223a13498046e39f7902525d623a460bcbe7422",
    "src/basic_qa/skills/ranking-assessment/references/rules.md": "e26d45caa6a4e08a1ad5566af640a99fb374e7e5533767da360e7187cd912095",
    "src/basic_qa/skills/regional-benchmark/SKILL.md": "d36458d6ead1fd671f19e4a66e7236d2b27bf9765c9ddc820cdedc203acb1887",
    "src/basic_qa/skills/regional-benchmark/references/rules.md": "be8b153618c3a24a63fa5e345362ce7bd0df7634f452548dee7cebbf408e1f25",
    "src/basic_qa/skills/station-extreme/SKILL.md": "7209813773cc138d3cf33f70a0468e9e2e023144637c65e10c936e93c341e2e3",
    "src/basic_qa/skills/station-extreme/references/rules.md": "605c39d48285b2157b08f1a4b68275549161a19dedd065068fd4765f68fbc7eb",
    "src/basic_qa/skills/trend-analysis/SKILL.md": "f504d14ae2dbb918222125737fffe1835913acead992c28e11e839c06f7695de",
    "src/basic_qa/skills/trend-analysis/references/rules.md": "07c9e18e4b7509dbb31625648fd72856d19dc91541e2f2674e70cf9ea9367290",
    "src/common/business_registry.py": "2ee81fa0f824ac99485e3c7a8b8ca60e5c8e7caeba35d9b8f6cf49120da87ef0",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_constants(tree: ast.Module) -> dict[str, object]:
    values: dict[str, object] = {}
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target = node.targets[0].id
        if target is None:
            continue
        try:
            values[target] = ast.literal_eval(node.value)
        except ValueError:
            continue
    return values


def _registered_tools() -> set[str]:
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"))
    constants = _module_constants(tree)
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "BUSINESSES"
        ):
            assert isinstance(node.value, ast.Dict)
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                if not (isinstance(key, ast.Constant) and key.value == "basic-qa"):
                    continue
                assert isinstance(value, ast.Call)
                conn = next(k.value for k in value.keywords if k.arg == "connections")
                if isinstance(conn, ast.Call):
                    # 当前形态：_ipp(IPP_APPROVED_QUERY_TOOLS)
                    assert isinstance(conn.func, ast.Name) and conn.args
                    source = conn.args[0]
                    assert isinstance(source, ast.Name)
                    names = constants[source.id]
                else:
                    names = ast.literal_eval(conn)
                assert isinstance(names, tuple)
                return set(names)
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
    if {p.name for p in SKILLS.iterdir() if p.is_dir()} != set(SKILL_NOTES):
        raise SystemExit("技能集合变化，须重新审阅")
    approved = _registered_tools()
    lines = [
        "# Basic QA Skill 材料与工具静态审计",
        "",
        "材料为本次更新后的工作区版本，材料和业务登记字节由脚本快照锁定。只读静态检查，未连接服务、读取部署配置或验证实时发现/授权/参数。",
        "",
        "复跑：`.venv/bin/python scripts/audit_basic_qa_skill_materials.py --check`；查看重新生成内容：`.venv/bin/python scripts/audit_basic_qa_skill_materials.py`。快照变化时先人工重审，再更新脚本内快照和报告。",
        "",
        "材料版本按 `src/common/capabilities.py` 的相对路径排序 SHA-256 算法计算；此脚本读取失败即终止，不使用其读取失败时的空字节回退。每个 Skill 只有 `SKILL.md` 与单一 `references/rules.md`，不代表运行加载已验证。",
        "",
        "静态登记中的短名可构造候选 `mcp:ipp:<名称>`，依据 `src/common/business_registry.py` 和 `src/common/mcp_scoped.py`；实际发现还须经过受保护连接、approved_query_tools、实时发现和权限交集，且短名跨服务重名时会失效。候选不代表当前可用或功能/参数等价。未登记项在当前 Basic QA 静态策略下不可用，不能映射到其他同名或相似工具。",
        "",
        "加载分类：总纲 allowed-tools 全部为必需，optional-tools 为空；必需工具缺失阻止加载。下方说明保留数据调用条件与结果缺失时的领域规则；工具缺失与调用后数据缺失是不同情形。工具名称逐项与引用位置见下表。",
        "",
        "## 九个 Skill",
        "",
    ]
    aliases = {}
    for slug in sorted(SKILL_NOTES):
        d = SKILLS / slug
        files = [d / "SKILL.md", d / "references/rules.md"]
        if not all(p.is_file() for p in files):
            raise SystemExit(f"{slug} 总纲或规则文件缺失")
        extra = sorted(
            p.name
            for p in (d / "references").iterdir()
            if p.is_file() and p.name != "rules.md"
        )
        if extra:
            raise SystemExit(f"{slug} 仍存在旧规则文件：{extra}")
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
        if optional:
            raise SystemExit(f"{slug} 仍声明可选工具：{sorted(optional)}")
        if required != declared:
            raise SystemExit(f"{slug} required-tools 与 allowed-tools 不一致")
        if any(name.startswith("chart_") for name in declared):
            raise SystemExit(f"{slug} 仍声明图表工具")
        lines += [
            f"### {slug}",
            "",
            f"- Skill ID：`skill:basic-qa:{slug}`",
            f"- 材料版本：`{_version(d)}`",
            "- 必需：" + "、".join(f"`{name}`" for name in sorted(required)),
            f"- 规则：{SKILL_NOTES[slug]}",
            "",
        ]
        for path, content in zip(files, texts, strict=True):
            for no, line in enumerate(content.splitlines(), 1):
                names = set(
                    re.findall(
                        r"`((?:mcp_|text2sql_)[A-Za-z0-9_]+|helper_get_latest_time|broadcastHour|integratedIndexRatio|cityFactorRankings|oneIndexManyCityLine)`",
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
            raise SystemExit(f"{slug} 规则引用工具未声明：{undeclared}")
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
        "- 九份材料均声明 `required-tools`；运行时是否按稳定 ID 与实时连接共同校验仍须实现并验收。",
        "- 图表与画布工具已退役，材料不再声明或引用；用户要求图表时用 Markdown 表格表达。",
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
