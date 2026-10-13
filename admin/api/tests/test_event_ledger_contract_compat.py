"""跨实现契约一致性测试：admin 侧与 Agent 侧校验器必须给出相同判定。

两侧独立 venv 不能互相 import，规则各实现一份；本测试用**同一组样本**同时驱动
两边的实现（admin 通过在 app 进程内调用；Agent 侧通过子进程调用，因为依赖树不同），
任一侧漂移即失败。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.event_contract import validate_event as admin_validate

REPO = Path(__file__).resolve().parents[3]
SAMPLES = REPO / "docs" / "contracts" / "agent-events-v1" / "samples" / "samples.json"


def _agent_verdicts() -> list[dict[str, object]]:
    """在 Agent 侧 venv 语义下运行同一组样本（子进程，避免依赖树混用）。"""
    script = (
        "import json,sys\n"
        "sys.path.insert(0,'src')\n"
        "from common.event_ledger.contract import validate_event\n"
        "samples=json.load(open('docs/contracts/agent-events-v1/samples/samples.json'))\n"
        "out=[]\n"
        "for c in samples['cases']:\n"
        "    r=validate_event(c['event'])\n"
        "    out.append({'valid':r.valid,'reason':r.reason})\n"
        "print(json.dumps(out))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(
    not (REPO / ".venv" / "bin" / "python").exists(),
    reason="需要仓库根 .venv 运行 Agent 侧实现",
)
def test_admin_and_agent_validators_agree_on_all_samples() -> None:
    samples = json.loads(SAMPLES.read_text("utf-8"))
    agent_verdicts = _agent_verdicts()
    assert len(agent_verdicts) == len(samples["cases"])
    mismatches = []
    for case, agent_verdict in zip(samples["cases"], agent_verdicts, strict=True):
        # 按事件声明的来源校验；浏览器入口会强制 source=browser（单独断言）
        valid, reason, _detail = admin_validate(case["event"])
        admin_verdict = {"valid": valid, "reason": reason}
        if admin_verdict != agent_verdict:
            mismatches.append(
                f"{case['name']}: admin={admin_verdict} agent={agent_verdict}"
            )
    assert not mismatches, "\n".join(mismatches)


@pytest.mark.skipif(
    not (REPO / ".venv" / "bin" / "python").exists(),
    reason="需要仓库根 .venv",
)
def test_admin_matches_declared_expectations() -> None:
    samples = json.loads(SAMPLES.read_text("utf-8"))
    failures = []
    for case in samples["cases"]:
        valid, reason, _ = admin_validate(case["event"])
        if valid != case["expect"]["valid"] or (
            not valid and reason != case["expect"]["reason"]
        ):
            failures.append(f"{case['name']}: got=({valid},{reason}) want={case['expect']}")
    assert not failures, "\n".join(failures)


def test_browser_endpoint_forces_source_over_declared() -> None:
    """浏览器入口不接受自行声明 runtime：服务端强制 source=browser 后仍拒绝终态声明。"""
    samples = json.loads(SAMPLES.read_text("utf-8"))
    case = next(c for c in samples["cases"] if c["name"] == "browser-terminal-claim-invalid")
    smuggled = dict(case["event"], source="runtime")
    valid, reason, _ = admin_validate(smuggled, trusted_source="browser")
    assert valid is False and reason == "source_not_allowed"


def test_canonical_digest_matches_agent_definition_doc() -> None:
    """规范摘要定义与契约 README 一致（键序无关）。"""
    from app.services.ledger import canonical_digest

    assert canonical_digest({"b": 1, "a": 2}) == canonical_digest({"a": 2, "b": 1})
    assert canonical_digest({"a": 1}) != canonical_digest({"a": 2})
