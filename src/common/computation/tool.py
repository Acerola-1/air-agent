"""当前已授权线程数据的有界计算工具."""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import Field

from common.artifact_scope import runtime_scope
from common.computation.runner import execute_code
from common.config import config
from common.data_manifest import build_manifest
from common.runtime_events import event_payload, merge_processing_status
from common.storage.artifact_db import (
    claim_computation,
    get_tool_artifact,
    save_tool_artifact,
)


@tool
async def compute_artifact(
    artifact_id: str,
    code: Annotated[
        str,
        Field(
            description="JavaScript 函数体；必须 return {result,scope}，同步 helpers.scan 完整记录数组；不含代码围栏。"
        ),
    ],
    runtime: ToolRuntime,
) -> str | Command[str]:
    """对当前会话归档的完整 JSON 只读计算。code 必须为同步 JavaScript 函数体，末尾必须 return {result:聚合/筛选结果,scope:'时间、站点、因子、过滤条件和单位说明'}。data 为完整冻结 JSON；helpers.scan(JSON_pointer,callback) 完整遍历记录；helpers.finite(value) 检查数值；helpers.pollutionLevel(factor,value,period,timestamp,unit) 使用已确认规则。所有统计范围必须经 scan。示例：let n=0; helpers.scan('',()=>n++); return {result:{count:n},scope:'完整数组，无筛选'}; 只有一次错误修正机会."""
    user_id, thread_id, run_id = runtime_scope(runtime.config)
    writer = runtime.stream_writer
    writer(
        event_payload(
            runtime.config,
            "context_status",
            stage="data",
            status="started",
            artifact_id=artifact_id,
        )
    )
    claim: dict[str, Any] | None = None
    try:
        record = await get_tool_artifact(
            artifact_id, user_id=user_id, thread_id=thread_id
        )
        if record is None:
            raise PermissionError("资料不属于当前已授权会话或不存在")
        if record["kind"] != "raw":
            raise ValueError("请使用原始资料进行完整计算")
        claim = await claim_computation(
            artifact_id, run_id, started_at=runtime.state.get("data_model_started_at")
        )
        result = await execute_code(
            record["raw_payload"], code, remaining_seconds=claim["remaining_seconds"]
        )
        result["source_artifact_id"] = artifact_id
        result["complete"] = True
        payload = json.dumps(result, ensure_ascii=False)
        derived_id = await save_tool_artifact(
            tool_call_id=runtime.tool_call_id or "",
            tool_name="compute_artifact",
            raw_payload=payload,
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            kind="derived",
            parent_artifact_id=artifact_id,
            processing_metadata={
                "code": code,
                "helper_version": "v1",
                "attempt": claim["attempt"],
                "coverage": result["coverage"],
            },
        )
        result["derived_artifact_id"] = derived_id
        if len(payload) > config.TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS:
            result = {
                "complete": True,
                "source_artifact_id": artifact_id,
                "derived_artifact_id": derived_id,
                "scope": result["scope"],
                "coverage": result["coverage"],
                "result_manifest": build_manifest(payload, derived_id),
                "message": "全量计算结果已保存，正文只提供引用；请基于原始资料改写代码返回更小的精确聚合，不从样本猜测结果。",
            }
        status = event_payload(
            runtime.config,
            "context_status",
            stage="data",
            status="completed",
            artifact_id=artifact_id,
        )
        writer(merge_processing_status(runtime.state.get("data_status", {}), status))
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps(result, ensure_ascii=False),
                        name="compute_artifact",
                        tool_call_id=runtime.tool_call_id or "",
                        artifact={
                            "storage_status": "stored",
                            "artifact_id": derived_id,
                        },
                    )
                ],
                "data_status": status,
            }
        )
    except Exception as exc:
        terminal = (
            claim is None
            or claim["attempt"] >= 1 + config.QUICKJS_MAX_CORRECTIONS
            or not isinstance(exc, ValueError)
        )
        status = event_payload(
            runtime.config,
            "context_status",
            stage="data",
            status="failed",
            artifact_id=artifact_id,
            message="数据计算未完成，不能据此给出完整统计。",
        )
        writer(merge_processing_status(runtime.state.get("data_status", {}), status))
        message = ToolMessage(
            content=json.dumps(
                {
                    "success": False,
                    "source_artifact_id": artifact_id,
                    "message": "全量计算未完成",
                    "diagnostic": str(exc)[:240]
                    + "；请用同步 JavaScript 完整修正：helpers.scan('实际路径',回调); return {result:统计对象,scope:'完整范围与单位'}; 最后一行变量不会自动返回。"
                    if isinstance(exc, ValueError) and claim is not None
                    else type(exc).__name__,
                    "correction_allowed": not terminal,
                },
                ensure_ascii=False,
            ),
            tool_call_id=runtime.tool_call_id or "",
            name="compute_artifact",
            status="error",
            additional_kwargs={
                "error_message": "数据计算未完成，不能据此给出完整统计。"
                if terminal
                else "首次计算未完成，后续修正结果见本轮工具记录。",
                "processing_terminal": terminal,
            },
        )
        return Command(update={"messages": [message], "data_status": status})
