"""真实 QuickJS worker 的完整数据与资源边界回归."""

from __future__ import annotations

import asyncio
import json

import pytest

from common.computation.runner import execute_code
from common.config import config
from common.data_manifest import build_manifest


@pytest.mark.anyio
@pytest.mark.parametrize(
    "source",
    [
        "let n=0; Object.getPrototypeOf(new Map().entries()).next=function(){return n++===0?{value:['',{input_records:999,processed_records:999}],done:false}:{done:true}}",
        "Math.floor=()=>0",
        "RegExp.prototype.test=()=>true",
        "return {result:()=>1,scope:'完整'}",
    ],
)
async def test_prototype_tampering_and_silent_json_omission_are_rejected(source):
    with pytest.raises(ValueError):
        await execute_code(
            "[{}]",
            "helpers.scan('',()=>{});" + source + "; return {result:1,scope:'完整'};",
        )


@pytest.mark.anyio
async def test_full_tail_sum_and_sorted_ranking():
    records = [{"id": i, "value": i % 100, "padding": "x" * 20} for i in range(10001)]
    records[-1]["value"] = 777
    raw = json.dumps({"data": records})
    manifest = build_manifest(raw, "art_x")
    assert len(raw) > 120000 and manifest["collections"][0]["record_count"] == 10001
    result = await execute_code(
        raw,
        "let sum=0,top=[]; helpers.scan('/data', r=>{sum+=r.value; top.push([r.id,r.value]);}); top.sort((a,b)=>b[1]-a[1]||a[0]-b[0]); return {result:{sum,top:top.slice(0,3)},scope:'完整输入，全时间，全站点，value 原始单位'};",
    )
    assert result["result"]["sum"] == sum(row["value"] for row in records)
    assert result["result"]["top"][0] == [10000, 777]
    assert result["coverage"] == [
        {"path": "/data", "input_records": 10001, "processed_records": 10001}
    ]


@pytest.mark.anyio
async def test_pollution_boundary_date_and_units():
    raw = "[{}]"
    code = "helpers.scan('',()=>{}); return {result:[helpers.pollutionLevel('CO',2,'daily','2026-01-01','mg/m3'),helpers.pollutionLevel('CO',2.01,'daily','2026-01-01','mg/m3'),helpers.pollutionLevel('PM10',420,'hourly','2026-01-01 00:00:00','ug/m3')],scope:'规则边界验证'};"
    assert (await execute_code(raw, code))["result"] == [1, 2, 5]
    for expression in [
        "helpers.pollutionLevel('PM10',20,'hourly','2026-01-01 01:00:00','ug/m3')",
        "helpers.pollutionLevel('CO',2,'daily','2026-01-01','ug/m3')",
    ]:
        with pytest.raises(ValueError):
            await execute_code(
                raw,
                f"helpers.scan('',()=>{{}}); return {{result:{expression},scope:'规则验证'}};",
            )


@pytest.mark.anyio
async def test_timeout_result_limit_and_followup(monkeypatch):
    monkeypatch.setattr(config, "QUICKJS_TIMEOUT_SECONDS", 1)
    with pytest.raises((ValueError, TimeoutError)):
        await execute_code("[{}]", "while(true){}")
    monkeypatch.setattr(config, "QUICKJS_MAX_RESULT_BYTES", 100)
    with pytest.raises(ValueError):
        await execute_code(
            "[{}]",
            "helpers.scan('',()=>{}); return {result:'x'.repeat(20000),scope:'完整'};",
        )
    monkeypatch.setattr(config, "QUICKJS_MAX_RESULT_BYTES", 1000)
    result = await execute_code(
        "[{}]", "helpers.scan('',()=>{}); return {result:2,scope:'完整'};"
    )
    assert result["result"] == 2


@pytest.mark.anyio
async def test_oom_and_cancel_recover(monkeypatch):
    monkeypatch.setattr(config, "QUICKJS_MEMORY_MIB", 4)
    with pytest.raises(ValueError):
        await execute_code("[{}]", "return new Array(2000000).fill(1);")
    task = asyncio.create_task(execute_code("[{}]", "while(true){}"))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (
        await execute_code(
            "[{}]", "helpers.scan('',()=>{}); return {result:3,scope:'完整'};"
        )
    )["result"] == 3


@pytest.mark.anyio
async def test_readonly_no_external_capability_and_required_scan():
    with pytest.raises(ValueError):
        await execute_code("[{}]", "return {result:1,scope:'声称完整'};")
    result = await execute_code(
        "[{}]",
        "helpers.scan('',()=>{}); return {result:[typeof fetch,typeof require,typeof process],scope:'能力验证'};",
    )
    assert result["result"] == ["undefined", "undefined", "undefined"]


@pytest.mark.anyio
async def test_input_cap_nan_and_so2_ambiguous_boundary(monkeypatch):
    monkeypatch.setattr(config, "QUICKJS_MAX_INPUT_BYTES", 4)
    with pytest.raises(ValueError, match="输入配额"):
        await execute_code("[12345]", "return {};")
    monkeypatch.setattr(config, "QUICKJS_MAX_INPUT_BYTES", 10000)
    with pytest.raises(ValueError):
        await execute_code(
            "[{}]", "helpers.scan('',()=>{}); return {result:NaN,scope:'完整'};"
        )
    result = await execute_code(
        "[{}]",
        "helpers.scan('',()=>{}); return {result:helpers.pollutionLevel('SO2',150,'hourly','2026-01-01 01:00:00','ug/m3'),scope:'规则边界'};",
    )
    assert result["result"] == 1
    with pytest.raises(ValueError, match="重叠"):
        await execute_code(
            "[{}]",
            "helpers.scan('',()=>{}); return {result:helpers.pollutionLevel('SO2',800,'hourly','2026-01-01 01:00:00','ug/m3'),scope:'规则边界'};",
        )
