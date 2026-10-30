"""Evaluate the configured real model on synthetic conversations and controlled tools.

No user Trace, IAM identity or production business response is replayed. The
MCP interfaces are frozen, sanitized fixtures; authentication is controlled.
This does not replace native Agent Server / browser acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch as mock_patch

import httpx
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

ROOT = Path(os.environ.get("BQA_HARNESS_ROOT", Path(__file__).resolve().parents[2]))
SOURCE = Path(os.environ.get("BQA_HARNESS_SRC", ROOT / "src"))
sys.path.insert(0, str(SOURCE))
CHAINS = {
    "A02": ["湖州市今天的空气质量如何？", "那杭州呢？", "那金华呢？"],
    "A03": [
        "湖州市今天空气质量如何？",
        "那杭州呢？",
        "那金华呢？",
        "缩成两句",
        "谢谢",
        "再查湖州",
    ],
    "A05": ["湖州市今天空气质量如何？", "帮我写一首爱情诗", "讲解Python装饰器"],
    "A06": ["查询湖州市今天空气质量，并分析2026年10月21日至27日PM2.5逐日趋势。"],
    "A07": [
        "查询湖州市今天空气质量",
        "比较湖州2026年9月和2025年9月的综合指数及PM2.5",
        "再查杭州市今天空气质量",
    ],
    "B01": ["湖州市今天空气质量怎么样？"],
    "B02": ["查询湖州今天空气质量，并核对坐标（120.15,30.27）所属行政区"],
    "B03": ["核对监测点坐标（120.15,30.27）所属行政区"],
    "A04": ["那杭州呢？"],
    "A08": ["请使用站点极值Skill分析湖州站点PM2.5极值"],
}
CORE_CHAINS = ("A02", "A03", "A05", "A06", "A07", "B01", "B02", "B03")
TARGET = "mcp_city_base_get_district_by_lnglat"


class ModelMeasurements(AsyncCallbackHandler):
    """Capture provider-reported usage and first answer token separately."""

    def __init__(self):
        self.roles = {}
        self.completed = []
        self.first_answer_at = None

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.roles[run_id] = (kwargs.get("metadata") or {}).get("langgraph_node")

    async def on_llm_new_token(self, token, *, run_id, **kwargs):
        if token and self.roles.get(run_id) == "answer_model":
            if self.first_answer_at is None:
                self.first_answer_at = time.monotonic()

    async def on_llm_end(self, response, *, run_id, **kwargs):
        usages = [
            getattr(g.message, "usage_metadata", None)
            for group in response.generations
            for g in group
            if getattr(g, "message", None) is not None
        ]
        self.completed.append({"node": self.roles.get(run_id), "usage": usages or None})


class PatchScope:
    """Restore isolated process fixtures without requiring pytest in runtime images."""

    def __enter__(self):
        self.stack = contextlib.ExitStack()
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def setattr(self, target, name, value=None):
        if isinstance(target, str):
            module, attribute = target.rsplit(".", 1)
            self.stack.enter_context(
                mock_patch.object(importlib.import_module(module), attribute, name)
            )
        else:
            self.stack.enter_context(mock_patch.object(target, name, value))


def fixture_tools(raw):
    """Keep interface types/names; replace vendor descriptions with synthetic prose."""
    descriptions = {
        TARGET: "根据经度longitude、纬度latitude核对行政区，level为0省1市2区县3乡镇。",
        "mcp_city_common_get_air_quality_realtime_stat": "查询城市空气质量，region为城市中文名，type为hourly/daily/daily_count/month/year，sTime/eTime为查询时间。",
    }
    result = []
    for item in raw:
        schema = item["schema"]
        properties = {
            name: {"type": value.get("type", "string")}
            for name, value in schema.get("properties", {}).items()
        }
        sanitized = {
            "type": "object",
            "properties": properties,
            "required": schema.get("required", []),
        }
        name = item["name"]

        async def blocked(**kwargs):
            raise AssertionError("only the controlled counting executor may run")

        result.append(
            StructuredTool.from_function(
                coroutine=blocked,
                name=name,
                description=descriptions.get(name, "合成测试接口：" + name),
                args_schema=sanitized,
                metadata={
                    "is_mcp": True,
                    "stable_id": "mcp:ipp:" + name,
                    "server_key": "ipp",
                },
            )
        )
    return result


def response(name, args):
    """Return deterministic synthetic data; never request a production service."""
    city = args.get("region") or args.get("city") or "湖州市"
    if name == TARGET:
        return {
            "code": 200,
            "data": [{"fullName": "浙江省杭州市", "regionKeyVO": "synthetic-hangzhou"}],
            "synthetic": True,
        }
    if name in {"helper_get_latest_time", "helper_getLatestTime2"}:
        return {
            "code": 200,
            "data": {
                "time": "2026-10-28 15:00:00",
                "airCityH": "2026-10-28 15:00:00",
                "airCityD": "2026-10-27",
                "airCityM": "2026-09",
                "airCityY": "2025",
            },
            "synthetic": True,
        }
    if "weather" in name:
        return {
            "code": 200,
            "data": [
                {
                    "time": f"2026-10-28 {h:02}:00",
                    "windSpeed": 2,
                    "windDirection": "东北风",
                    "temperature": 20,
                    "humidity": 70,
                }
                for h in range(24)
            ],
            "synthetic": True,
        }
    if name == "oneIndexManyCityLine":
        return {
            "code": 200,
            "data": [
                {"region": city, "time": f"2026-10-{21 + i}", "pm25": v, "PM2.5": v}
                for i, v in enumerate([30, 40, 50, 60, 50, 40, 50])
            ],
            "synthetic": True,
        }
    if "month" in name.lower() or "comparison" in name.lower():
        return {
            "code": 200,
            "data": [
                {"region": city, "time": "2025-09", "integratedIndex": 4.0, "pm25": 50},
                {"region": city, "time": "2026-09", "integratedIndex": 3.2, "pm25": 40},
            ],
            "synthetic": True,
        }
    aqi = 95 if "杭州" in city else 60 if "金华" in city else 75
    return {
        "code": 200,
        "data": [
            {
                "region": city,
                "time": args.get("sTime", "2026-10-28 15:00"),
                "AQI": aqi,
                "aqi": aqi,
                "level": "良",
                "primaryPollutant": "PM2.5",
                "pm25": 40,
                "pm10": 60,
                "so2": 8,
                "no2": 24,
                "co": 0.6,
                "o3": 80,
                "o38h": 90,
            }
        ],
        "synthetic": True,
    }


async def evaluate(args):
    """Run the real compiled graph while recording actual HTTP model requests."""
    if (ROOT / ".env.langgraph").exists():
        from dotenv import dotenv_values

        values = dotenv_values(ROOT / ".env.langgraph")
    else:
        values = {
            key: value
            for key, value in os.environ.items()
            if key.startswith("MAIN_LLM_")
            or key in {"MODEL_MAX_CONTEXT_TOKENS", "MODEL_OUTPUT_RESERVED_TOKENS"}
        }
    for key, value in values.items():
        if value is not None and (
            key.startswith("MAIN_LLM_")
            or key in {"MODEL_MAX_CONTEXT_TOKENS", "MODEL_OUTPUT_RESERVED_TOKENS"}
        ):
            os.environ[key] = value
    os.environ.update(
        LANGFUSE_ENABLED="false",
        LANGFUSE_PROMPTS_ENABLED="false",
        AGENT_OBSERVABILITY_ENABLED="false",
    )
    # These required configuration fields are unused by the controlled search
    # paths. Keep accidental auxiliary requests local and immediately failing.
    os.environ.update(
        SILICONFLOW_API_KEY="controlled-unused",
        SERPER_API_KEY="controlled-unused",
        SILICONFLOW_BASE_URL="http://127.0.0.1:1/v1",
    )
    import common.capabilities as capabilities
    import common.identity.runtime as identity_module
    import common.mcp_scoped as mcp
    import common.runtime_tools as runtime_tools
    from basic_qa.capabilities import runtime as basic_qa_runtime
    from basic_qa import builder
    from basic_qa.capabilities import mcp_catalog as mcp_tool_catalog
    from basic_qa.capabilities import mcp_search as mcp_tool_search
    from basic_qa import nodes
    from basic_qa import prompts as prompting
    from common.business_registry import BUSINESS_POLICY_VERSION
    from common.capabilities import list_graph_skills
    from common.config import config
    from common.identity.runtime import RuntimeIdentity
    from common.mcp_scoped import ScopedMCPToolFactory, set_factory
    from common.models import ModelRegistry
    from common.observability.tool_adapter import classify_tool_output
    from common.skill_router import SkillSemanticRouter

    output = Path(args.out).absolute()
    if not output.is_relative_to(ROOT / ".local"):
        raise ValueError("raw evidence must stay in the ignored .local directory")
    output.mkdir(parents=True, exist_ok=False)
    raw = json.loads(Path(args.schemas).read_text())
    remote = fixture_tools(raw)
    os.environ["AGENT_MCP_CONNECTIONS_FILE"] = ""
    os.environ["AGENT_MCP_CONNECTIONS_JSON"] = json.dumps(
        {
            "schema_version": "agent.mcp-connections.v1",
            "revision": 1,
            "servers": [
                {
                    "server_key": "ipp",
                    "url": "https://example.invalid/mcp",
                    "scope": "shared_readonly",
                    "auth_mode": "none",
                    "enabled": True,
                    "approved_query_tools": [t.name for t in remote],
                }
            ],
        }
    )
    documents = tuple(mcp_tool_catalog.document_for("ipp", t) for t in remote)
    by_name = {d.name: d for d in documents}
    (output / "frozen-interfaces.json").write_text(
        json.dumps(
            [
                {"tool_id": d.tool_id, "schema": d.schema, "version": d.schema_version}
                for d in documents
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    identity = RuntimeIdentity(
        user_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        membership_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
        permissions=frozenset({"agent:business:basic-qa:use"}),
        business_code="basic-qa",
        business_graph="basic-qa",
        business_permission="agent:business:basic-qa:use",
        business_policy_version=BUSINESS_POLICY_VERSION,
    )
    cat = mcp_tool_catalog.ToolCatalog(
        documents,
        "sha256:" + "1" * 64,
        mcp_tool_catalog.content_version(
            [(d.tool_id, d.schema_version) for d in documents]
        ),
        identity.tenant_id,
        identity.user_id,
        identity.membership_id,
    )
    requests, executions = [], []

    async def capture(request):
        if request.url.path.endswith("/chat/completions"):
            requests.append(json.loads(request.content))

    client = httpx.AsyncClient(event_hooks={"request": [capture]})
    from langchain_openai import ChatOpenAI

    real_model = ChatOpenAI(
        api_key=values["MAIN_LLM_API_KEY"],
        base_url=values["MAIN_LLM_BASE_URL"],
        model=values["MAIN_LLM_MODEL"],
        temperature=0.1,
        max_completion_tokens=config.MAIN_LLM_MAX_OUTPUT_TOKENS,
        timeout=120,
        max_retries=0,
        disable_streaming="tool_calling",
        http_async_client=client,
    )

    async def execute(server, name, params, run_config):
        original = name.split("__")[-1]
        document = by_name[original]
        missing = [k for k in document.schema.get("required", []) if k not in params]
        if missing:
            return json.dumps(
                {
                    "code": 422,
                    "success": False,
                    "message": "合成接口缺少参数：" + ",".join(missing),
                },
                ensure_ascii=False,
            )
        executions.append({"name": original, "params": params})
        return json.dumps(response(original, params), ensure_ascii=False)

    async def search_index(query, catalog, cfg, **kwargs):
        selected = [
            by_name[TARGET],
            by_name["mcp_city_base_get_child_district_by_parent"],
            by_name["mcp_city_base_get_parent_district_by_child"],
        ]
        return {
            "status": "ok",
            "catalog_version": cat.catalog_version,
            "candidates": [d.candidate(1 - i * 0.1) for i, d in enumerate(selected)],
        }

    async def match(router, query, *, strict=False):
        skills = list_graph_skills("basic-qa")
        preferred = (
            "trend-analysis"
            if "趋势" in query
            else "comparison-composition"
            if "同比" in query or "比较" in query
            else "air-quality-basic-query"
        )
        skills.sort(key=lambda s: (s.name != preferred, s.id))
        return [
            SimpleNamespace(name=s.name, similarity_score=1 - i * 0.1)
            for i, s in enumerate(skills)
        ]

    async def finalize(state, config):
        return {}

    async def time_tool():
        """读取合成测试时钟."""
        return "2026-10-28 16:00:00 Asia/Shanghai"

    general = [
        StructuredTool.from_function(coroutine=time_tool, name="get_beijing_time")
    ]
    factory = ScopedMCPToolFactory()
    factory.register_catalog(remote, server_key="ipp")
    report = {
        "dataset_version": "bqa-session-capabilities-v2",
        "model": values["MAIN_LLM_MODEL"],
        "auth": "controlled identity, not native acceptance",
        "tools": "controlled frozen sanitized schemas and synthetic data",
        "skill_search": "controlled catalog scores",
        "mcp_search": "controlled Top3, not embedding acceptance",
        "clock": "2026-10-28T16:00:00+08:00",
        "runs": [],
        "model_config_fingerprint": hashlib.sha256(
            json.dumps(
                {
                    "base": values["MAIN_LLM_BASE_URL"],
                    "model": values["MAIN_LLM_MODEL"],
                    "output": config.MAIN_LLM_MAX_OUTPUT_TOKENS,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "source_fingerprint": hashlib.sha256(
            b"".join(
                p.read_bytes()
                for p in sorted((SOURCE / "common/business_graph").glob("*.py"))
            )
        ).hexdigest(),
    }
    with PatchScope() as patch:
        set_factory(factory)
        patch.setattr(
            identity_module, "guard_runtime_call", AsyncMock(return_value=identity)
        )
        patch.setattr(
            identity_module, "guard_request_call", AsyncMock(return_value=identity)
        )
        patch.setattr(
            basic_qa_runtime, "guard_runtime_call", AsyncMock(return_value=identity)
        )
        patch.setattr(
            "basic_qa.capabilities.skill_search.guard_runtime_call",
            AsyncMock(return_value=identity),
        )
        patch.setattr(
            capabilities,
            "discover_authorized_mcp_catalog",
            AsyncMock(
                return_value=(
                    [],
                    [
                        SimpleNamespace(id=d.tool_id, name=d.name, status="available")
                        for d in documents
                    ],
                )
            ),
        )
        patch.setattr(
            basic_qa_runtime,
            "discover_authorized_mcp_catalog",
            capabilities.discover_authorized_mcp_catalog,
        )
        patch.setattr(
            mcp_tool_catalog, "resolve_tool_catalog", AsyncMock(return_value=cat)
        )
        patch.setattr(
            mcp_tool_search, "resolve_tool_catalog", AsyncMock(return_value=cat)
        )
        patch.setattr(mcp_tool_search, "search_catalog", search_index)
        patch.setattr(mcp, "execute_mcp_tool", execute)

        # Both lifecycle versions receive the same program-side discovered
        # catalog. Their own disclosure logic chooses what reaches the model.
        # Giving the old node only general tools would be an invalid baseline.
        def fixture_business(**_):
            return [*general, *[d.proxy() for d in documents]]

        patch.setattr(builder, "get_business_tools", fixture_business)
        patch.setattr(nodes, "get_business_tools", fixture_business)
        patch.setattr(
            runtime_tools,
            "get_business_tools",
            lambda **_: [*general, *[d.proxy() for d in documents]],
        )
        patch.setattr(builder, "make_ledger_finalize_node", lambda _: finalize)
        patch.setattr(
            prompting,
            "build_time_context",
            lambda: (
                "【当前真实时间】2026-10-28 16:00:00；今天=2026-10-28，昨天=2026-10-27。"
            ),
        )
        patch.setattr(SkillSemanticRouter, "amatch", match)
        patch.setattr(ModelRegistry, "main_llm", real_model)
        for case in args.cases.split(","):
            for repeat in range(args.repeat):
                graph = builder.build_basic_qa_graph(
                    skills_dir=SOURCE / "basic_qa/skills",
                    name="basic-qa",
                    checkpointer=InMemorySaver(),
                )
                thread = str(uuid.uuid4())
                run = {"case_id": case, "repeat": repeat + 1, "turns": []}
                for index, question in enumerate(CHAINS[case]):
                    before, before_exec = len(requests), len(executions)
                    start = time.monotonic()
                    measurements = ModelMeasurements()
                    cfg = {
                        "callbacks": [measurements],
                        "configurable": {
                            "thread_id": thread,
                            "ledger_graph_id": "basic-qa",
                            "ledger_attempt_id": str(uuid.uuid4()),
                            "ledger_submission_id": str(uuid.uuid4()),
                            "capability_selection_id": str(uuid.uuid4()),
                            "capability_selection_digest": "controlled-selection",
                            "capability_skill_mode": "auto",
                            "capability_mcp_mode": "auto",
                            "chat_mode": "fast",
                            "capability_authorized_servers": ["ipp"],
                            "capability_authorized_tool_ids": [
                                d.tool_id for d in documents
                            ],
                        },
                        "recursion_limit": 80,
                    }
                    try:
                        result = await graph.ainvoke(
                            {"messages": [HumanMessage(content=question)]}, cfg
                        )
                        last = max(
                            i
                            for i, m in enumerate(result["messages"])
                            if isinstance(m, HumanMessage)
                        )
                        turn = result["messages"][last + 1 :]
                        calls = [
                            c
                            for m in turn
                            if isinstance(m, AIMessage)
                            for c in m.tool_calls
                        ]
                        errors = [
                            {"name": m.name, "content": m.content}
                            for m in turn
                            if isinstance(m, ToolMessage)
                            and (
                                m.status == "error"
                                or classify_tool_output(m.content)
                                in {
                                    "permission_denied",
                                    "validation_rejected",
                                    "technical_error",
                                    "timed_out",
                                    "cancelled",
                                }
                            )
                        ]
                        answers = [
                            str(m.content)
                            for m in turn
                            if isinstance(m, AIMessage)
                            and m.additional_kwargs.get("ui_visible")
                        ]
                        controls = [
                            m.additional_kwargs["control_code"]
                            for m in turn
                            if isinstance(m, AIMessage)
                            and m.additional_kwargs.get("control_code")
                        ]
                        output_aborts = [
                            m.additional_kwargs["output_aborted"]
                            for m in turn
                            if isinstance(m, AIMessage)
                            and m.additional_kwargs.get("output_aborted")
                        ]
                        record = {
                            "input": question,
                            "model_requests": len(requests) - before,
                            "calls": calls,
                            "errors": errors,
                            "answer": answers,
                            "controls": controls,
                            "output_aborts": output_aborts,
                            "active_skills": result.get("bqa_capabilities", {}).get(
                                "active_skill_ids", []
                            ),
                            "active_tools": result.get("bqa_capabilities", {}).get(
                                "independent_active_tool_ids", []
                            ),
                            "executions": executions[before_exec:],
                            "search_skills": sum(
                                c["name"] == "search_skills" for c in calls
                            ),
                            "search_mcp": sum(c["name"] == "search_mcp" for c in calls),
                            "load_skill": sum(c["name"] == "load_skill" for c in calls),
                            "load_mcp_tool": sum(
                                c["name"] == "load_mcp_tool" for c in calls
                            ),
                            "embedding_documents": 0,
                            "embedding_queries": 0,
                            "logical_tool_calls": len(calls),
                            "network_attempts": None,
                            "controlled_business_calls": len(executions) - before_exec,
                            "first_answer_ms": (
                                (measurements.first_answer_at - start) * 1000
                                if measurements.first_answer_at is not None
                                else None
                            ),
                            "model_usage": measurements.completed,
                            "elapsed_ms": (time.monotonic() - start) * 1000,
                            "usage": result.get("context_metrics", {}).get(
                                "latest_usage"
                            )
                            or None,
                            "first_pass_completed": bool(answers)
                            and not errors
                            and not controls
                            and not output_aborts,
                            "recovered_completed": bool(answers)
                            and bool(errors)
                            and not controls
                            and not output_aborts,
                            "incomplete": not bool(answers)
                            or bool(controls)
                            or bool(output_aborts),
                            "user_visible_error": bool(errors)
                            or bool(controls)
                            or bool(output_aborts),
                        }
                    except Exception as exc:
                        record = {
                            "input": question,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc)[:300]
                            if isinstance(exc, TypeError)
                            else None,
                            "elapsed_ms": (time.monotonic() - start) * 1000,
                            "incomplete": True,
                            "error_frames": [
                                {
                                    "file": f.filename,
                                    "line": f.lineno,
                                    "function": f.name,
                                }
                                for f in traceback.extract_tb(exc.__traceback__)
                            ],
                        }
                    record["serialized_requests"] = requests[before:]
                    run["turns"].append(record)
                    (output / f"{case}-{repeat + 1}-turn-{index + 1}.json").write_text(
                        json.dumps(record, ensure_ascii=False, indent=2, default=str)
                    )
                    print(  # noqa: T201
                        json.dumps(
                            {
                                "case": case,
                                "repeat": repeat + 1,
                                "turn": index + 1,
                                "calls": [c["name"] for c in record.get("calls", [])],
                                "errors": len(record.get("errors", [])),
                                "incomplete": record["incomplete"],
                            }
                        ),
                        flush=True,
                    )
                report["runs"].append(run)
                (output / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2, default=str)
                )
    set_factory(None)
    await client.aclose()


def main():
    """Require a new private output directory; preserve every raw attempt."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--schemas", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cases", default=",".join(CORE_CHAINS))
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(evaluate(args))


if __name__ == "__main__":
    main()
