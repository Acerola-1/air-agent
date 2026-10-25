"""Versioned, fail-closed execution plans for model and product capacity tests."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from scripts.unified_campaign.guard import assert_targets_allowed

PLAN_SCHEMA = "model-capacity-plan.v2"
BUNDLE_SCHEMA = "model-capacity-perf.v2"
CAPABILITIES = {
    "run_baseline": {("E", "S")},
    "load": {("P", "R")},
    "capacity": {(layer, profile) for layer in "UPE" for profile in "SRF"},
}
PHASES = {
    "smoke",
    "warmup",
    "explore",
    "confirm",
    "open",
    "stability",
    "burst",
    "recovery",
    "fault",
}


class PlanError(ValueError):
    """A plan cannot safely or truthfully be executed by the selected runner."""


def digest(value: Any) -> str:
    """Hash a JSON-compatible frozen plan or input without exposing it."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def load_plan(path: Path) -> dict[str, Any]:
    """Read a JSON plan; validation is deliberately separate and network-free."""
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise PlanError("运行计划必须是 JSON 对象")
    return value


def _positive(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise PlanError(f"{name} 必须是正数")
    return float(value)


def validate_plan(
    plan: dict[str, Any],
    *,
    runner: str = "capacity",
    enable_real_models: bool = False,
    enable_load: bool = False,
    require_execution: bool = False,
) -> dict[str, Any]:
    """Validate profile, budgets, targets and complete-capacity promises before I/O.

    A plan is not proof of successful preflight or load. Real dependency probes,
    samples, elapsed time and SLOs are checked again against actual evidence.
    """
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise PlanError(f"需要 {PLAN_SCHEMA}")

    def reject_secrets(value: Any) -> None:
        if isinstance(value, dict):
            if any(
                key.lower()
                in {"api_key", "cookie", "password", "authorization", "secret"}
                for key in value
            ):
                raise PlanError("计划不得内嵌凭据，仅允许受保护配置引用")
            for item in value.values():
                reject_secrets(item)
        elif isinstance(value, list):
            for item in value:
                reject_secrets(item)

    reject_secrets(plan)
    if not isinstance(plan.get("run_id"), str) or not plan["run_id"].strip():
        raise PlanError("缺少独立 run_id")
    if plan.get("objective") not in {"exploration", "capacity"}:
        raise PlanError("objective 必须是 exploration 或 capacity")
    budgets = plan.get("budgets") or {}
    for name in ("max_seconds", "max_requests", "max_tokens"):
        _positive(budgets.get(name), f"budgets.{name}")
    for name in ("max_requests", "max_tokens"):
        if not isinstance(budgets[name], int):
            raise PlanError(f"budgets.{name} 必须是整数")
    if plan.get("retention") not in {"retain_for_review", "remove_owned_resources"}:
        raise PlanError("必须冻结 retain_for_review 或 remove_owned_resources")
    stages = plan.get("stages")
    if not isinstance(stages, list) or not stages:
        raise PlanError("缺少 stages")
    if runner not in CAPABILITIES:
        raise PlanError(f"未知执行器 {runner}")
    stages_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    ids: set[str] = set()
    targets: list[str] = []
    for stage in stages:
        if not isinstance(stage, dict):
            raise PlanError("stage 必须是对象")
        sid = stage.get("stage_id")
        if (
            not isinstance(sid, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", sid)
            or sid in ids
        ):
            raise PlanError("stage_id 必须非空且不重复")
        ids.add(sid)
        layer, profile = stage.get("layer"), stage.get("profile")
        if (layer, profile) not in CAPABILITIES[runner]:
            raise PlanError(f"执行器 {runner} 不支持 {layer}/{profile}；发送前拒绝")
        if plan["objective"] == "capacity" and profile != "F":
            raise PlanError("capacity 目标需要 F 档，不能由 S/R 冒充")
        if stage.get("phase") not in PHASES:
            raise PlanError("未知 phase")
        if stage.get("arrival") not in {"closed", "open"}:
            raise PlanError("arrival 必须为 closed 或 open")
        for field in (
            "duration_seconds",
            "min_samples",
            "concurrency",
            "request_deadline_seconds",
            "token_ceiling_per_request",
        ):
            _positive(stage.get(field), f"{sid}.{field}")
        for field in ("concurrency", "min_samples", "token_ceiling_per_request"):
            if not isinstance(stage[field], int):
                raise PlanError(f"{sid}.{field} 必须是整数")
        if stage["arrival"] == "open":
            _positive(
                stage.get("arrival_rate_per_second"), f"{sid}.arrival_rate_per_second"
            )
        thresholds = stage.get("thresholds") or {}
        for field in (
            "first_answer_p95_ms",
            "complete_p95_ms",
            "min_success_rate",
            "min_quality_rate",
        ):
            _positive(thresholds.get(field), f"{sid}.thresholds.{field}")
        if any(thresholds[x] > 1 for x in ("min_success_rate", "min_quality_rate")):
            raise PlanError("成功率和质量率必须介于 0 与 1")
        model = stage.get("model")
        if not isinstance(model, str) or not model:
            raise PlanError("每档必须冻结 model（P 层也须标明受控服务版本）")
        workloads = stage.get("workloads")
        if not isinstance(workloads, list) or not workloads:
            raise PlanError("必须冻结 workload 类别及输入摘要")
        if not all(
            isinstance(workload, dict) and isinstance(workload.get("workload_id"), str)
            for workload in workloads
        ):
            raise PlanError("每个 workload 需要稳定 workload_id")
        if len({workload["workload_id"] for workload in workloads}) != len(workloads):
            raise PlanError("workload_id 必须唯一")
        key = (str(layer), model, str(stage.get("graph_profile") or "upstream"))
        stages_by_key.setdefault(key, []).append(stage)
        if layer == "U":
            gateway = stage.get("gateway") or {}
            if not gateway.get("base_url") or not gateway.get("api_key_env"):
                raise PlanError("U 层需明确网关和 api_key_env，仅允许凭据引用")
            if "api_key" in gateway:
                raise PlanError("运行计划不得内嵌模型密钥")
            targets.append(str(gateway["base_url"]))
            for workload in workloads:
                if (
                    not isinstance(workload.get("messages"), list)
                    or not workload["messages"]
                ):
                    raise PlanError("U workload 需要冻结 messages")
                if workload.get("mode", "text") not in {"text", "tool"}:
                    raise PlanError("U workload mode 必须为 text 或 tool")
        else:
            target = stage.get("target") or {}
            ownership = target.get("ownership") or {}
            if (
                ownership.get("run_id") != plan["run_id"]
                or ownership.get("formal") is not False
            ):
                raise PlanError("产品目标必须声明本任务自有隔离资源")
            for field in ("bff_base_url", "admin_base_url"):
                if not target.get(field):
                    raise PlanError(f"缺少 {field}")
                targets.append(str(target[field]))
            targets.extend(str(x) for x in target.get("database_targets", []))
            if not stage.get("graph_profile"):
                raise PlanError("产品层必须冻结图范围，不得外推全产品容量")
            if stage["graph_profile"] != "basic-qa":
                raise PlanError("当前产品执行适配器仅支持 basic-qa，其他图不能仅改标签")
            if stage.get("history_policy") not in {"generated", "fixed_replay"}:
                raise PlanError("产品层必须声明固定/生成历史策略")
            observations = stage.get("observation_source") or {}
            if (
                observations.get("run_id") != plan["run_id"]
                or not observations.get("container")
                or not observations.get("database")
            ):
                raise PlanError("产品层需要本任务自有的实际模型/工具观测来源")
            targets.append(str(observations["database"]))
            if stage["history_policy"] == "fixed_replay":
                raise PlanError(
                    "当前执行器未实现获准 fixed_replay 导入适配；发送前拒绝，不伪造历史"
                )
            _positive(stage.get("max_samples"), f"{sid}.max_samples")
            if stage["max_samples"] < stage["min_samples"]:
                raise PlanError("max_samples 不得低于 min_samples")
            if layer == "E":
                probes = stage.get("dependency_probes") or {}
                if not all(
                    isinstance(probes.get(x), dict)
                    and probes[x].get("status") == "PASS"
                    and probes[x].get("evidence_ref")
                    for x in ("identity", "knowledge", "mcp")
                ):
                    raise PlanError(
                        "E 层缺有效身份/知识/MCP 正向探针证据，完整产品档受阻"
                    )
                if not stage.get("model_budget_meter"):
                    raise PlanError(
                        "E 层需要物理模型调用预算计量器，不能仅按逻辑问题估算 Token 硬上限"
                    )
            for workload in workloads:
                if not workload.get("case_id"):
                    raise PlanError("产品 workload 缺少固定 case_id")
                if stage.get("data_source", "real") == "real" and not (
                    workload.get("oracle") or {}
                ).get("evidence_ref"):
                    raise PlanError(
                        "真实资料质量缺少实际结果 oracle，发送前受阻而非套用 fixture"
                    )
                if stage.get("data_source") == "fixture" and not workload.get(
                    "quality_v2"
                ):
                    raise PlanError("合成 workload 缺少冻结质量规则")
        if require_execution:
            if profile != "S" and not (enable_load and plan.get("allow_load") is True):
                raise PlanError("负载需计划和进程双重显式开启")
            if layer in {"U", "E"} and not (
                enable_real_models and plan.get("allow_real_models") is True
            ):
                raise PlanError("真实模型需计划和进程双重显式开启")
    assert_targets_allowed(targets)
    product_stages = [stage for stage in stages if stage["layer"] == "E"]
    if product_stages:
        if len(product_stages) != len(stages):
            raise PlanError("E 的物理预算需独立运行计划，不与 U/P 消耗混算")
        if len({digest(stage["model_budget_meter"]) for stage in product_stages}) != 1:
            raise PlanError("同一 E 计划必须共用一个持续计量器，禁止逐阶段重置预算")
    if plan["objective"] == "capacity":
        for key, group in stages_by_key.items():
            phases = {stage["phase"] for stage in group}
            if (
                not {"explore", "confirm", "open", "stability", "burst", "recovery"}
                <= phases
            ):
                raise PlanError(f"{key} 缺完整 F 阶段，仅可登记 exploration 计划")
            confirmations = [stage for stage in group if stage["phase"] == "confirm"]
            if len(confirmations) < 3:
                raise PlanError("容量候选档需至少三轮独立确认")
            candidates: dict[int, int] = {}
            for candidate in confirmations:
                if candidate["arrival"] != "closed":
                    raise PlanError("候选并发确认必须使用闭环到达")
                level = candidate["concurrency"]
                candidates[level] = candidates.get(level, 0) + 1
            if not any(count >= 3 for count in candidates.values()):
                raise PlanError("同一候选并发档必须三轮确认，不能拼接不同档位")
            if len({digest(stage["workloads"]) for stage in group}) != 1:
                raise PlanError("同一容量系列的 workload 必须固定，变化需建立新系列")
            for stage in group:
                phase = stage["phase"]
                minimum_time = {
                    "explore": 300,
                    "confirm": 600,
                    "open": 900,
                    "stability": 3600,
                }.get(phase)
                if (
                    minimum_time is not None
                    and stage["duration_seconds"] < minimum_time
                ):
                    raise PlanError(f"{phase} 时长低于既有 F 门槛")
                if (
                    phase in {"confirm", "open"}
                    and stage.get("min_samples_per_workload", 0) < 200
                ):
                    raise PlanError("确认/开放档每类至少 200 个发送样本")
                if phase == "stability" and stage["arrival"] != "open":
                    raise PlanError("可持续稳定性必须是开放到达")
    return {
        "plan_sha256": digest(plan),
        "stages": len(stages),
        "runner": runner,
        "validated": True,
        "network_requests": 0,
    }


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Strip input bodies and DSNs from a public manifest; retain their hashes."""

    def clean(value: Any, key: str = "") -> Any:
        if key in {
            "messages",
            "database_targets",
            "database",
            "oracle",
            "dependency_probes",
        }:
            return {"sha256": digest(value), "retained_in": "restricted:run-plan.json"}
        if isinstance(value, dict):
            return {name: clean(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(plan)


def workload_hashes(workloads: list[dict[str, Any]]) -> list[str]:
    """Return workload hashes used by plan/report reproducibility checks."""
    return [digest(workload) for workload in workloads]
