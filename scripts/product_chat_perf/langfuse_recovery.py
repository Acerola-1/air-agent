"""Recover only retained, missing round traces through official OTLP ingestion.

Preparation is read-only. Raw original spans remain in the ignored private
directory. Replay requires --apply and records a new receipt; original round
results and trace coverage are never rewritten.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]

_EXPORT = r"""
const req=require('module').createRequire('/app/worker/package.json');
const s=req('@langfuse/shared/src/server'),crypto=require('crypto');
const plan=JSON.parse(process.argv[1]),wanted=new Set(plan.trace_ids),seen=new Map();
const norm=(x,bytes=16)=>typeof x==='string'&&new RegExp('^[a-f0-9]{'+(bytes*2)+'}$','i').test(x)?x.toLowerCase():Buffer.from(x,typeof x==='string'?'base64':undefined).toString('hex');
(async()=>{
 const client=s.getS3EventStorageClient(process.env.LANGFUSE_S3_EVENT_UPLOAD_BUCKET);
 const root=process.env.LANGFUSE_S3_EVENT_UPLOAD_PREFIX||'';
 const payloads=[];let objectsRead=0,duplicates=0;
 for(const minute of plan.minutes){
  const prefix=root+'otel/'+plan.project_id+'/'+minute+'/';
  const files=await client.listFiles(prefix);
  if(files.length>=200)throw new Error('Minute listing may be truncated; refuse partial recovery');
  for(const file of files){
   const raw=await client.download(file.file);objectsRead++;
   const resources=JSON.parse(raw);if(!Array.isArray(resources))throw new Error('Unexpected OTLP storage shape');
   const selected=[];let n=0;
   for(const resource of resources){
    const scopes=[];
    for(const scope of resource.scopeSpans||[]){
     const spans=[];
     for(const span of scope.spans||[]){
      if(!wanted.has(norm(span.traceId)))continue;
      const key=norm(span.traceId)+':'+norm(span.spanId,8);
      const digest=crypto.createHash('sha256').update(JSON.stringify(span)).digest('hex');
      if(seen.has(key)){
       if(seen.get(key)!==digest)throw new Error('Conflicting span versions require review');
       duplicates++;continue;
      }
      seen.set(key,digest);spans.push(span);n++;
     }
     if(spans.length)scopes.push({...scope,spans});
    }
    if(scopes.length)selected.push({...resource,scopeSpans:scopes});
   }
   if(n)payloads.push({source_file:file.file,source_sha256:crypto.createHash('sha256').update(raw).digest('hex'),selected_spans:n,resourceSpans:selected});
  }
 }
 await s.redis.quit();
 process.stdout.write('RECOVERY_JSON:'+JSON.stringify({objects_read:objectsRead,duplicate_spans:duplicates,payloads})+'\n',()=>process.exit(0));
})().catch(e=>{console.log('RECOVERY_ERROR:'+e.message);process.exit(1)});
"""


def _id_hex(value: Any, size: int, *, empty: bool = False) -> str:
    if isinstance(value, str) and re.fullmatch(
        r"[0-9a-fA-F]{" + str(size * 2) + "}", value
    ):
        return value.lower()
    if isinstance(value, dict) and value.get("type") == "Buffer":
        raw = bytes(value["data"])
    elif isinstance(value, str):
        raw = base64.b64decode(value, validate=True)
    else:
        raise ValueError("原始 OTLP 标识表示法不支持")
    if len(raw) != size and not (empty and not raw):
        raise ValueError("原始 OTLP 标识长度不符")
    return raw.hex()


def span_trace_id(value: Any) -> str:
    """Normalize retained SDK Buffer JSON or wire-format IDs to identical bytes."""
    return _id_hex(value, 16)


def _wire_otlp(value: Any, key: str = "") -> Any:
    if key in {"traceId", "spanId", "parentSpanId"}:
        return _id_hex(
            value, 16 if key == "traceId" else 8, empty=key == "parentSpanId"
        )
    if (
        key == "bytesValue"
        and isinstance(value, dict)
        and value.get("type") == "Buffer"
    ):
        return base64.b64encode(bytes(value["data"])).decode()
    if isinstance(value, dict):
        return {k: _wire_otlp(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_wire_otlp(v) for v in value]
    return value


def span_batches(resources: list[dict], allowed: set[str], size: int = 50):
    """Preserve each original resource/scope and bound the ingestion batch."""
    if size < 1:
        raise ValueError("批量必须为正数")
    for resource in _wire_otlp(resources):
        for scope in resource.get("scopeSpans", []):
            spans = scope.get("spans", [])
            for span in spans:
                if span_trace_id(span["traceId"]) not in allowed:
                    raise ValueError("恢复载荷包含本轮目标之外的 trace")
            for offset in range(0, len(spans), size):
                yield {
                    "resourceSpans": [
                        {
                            **resource,
                            "scopeSpans": [
                                {**scope, "spans": spans[offset : offset + size]}
                            ],
                        }
                    ]
                }


def existing_span_ids(allowed: set[str]) -> set[tuple[str, str]]:
    """Check live stable identities before replay, including earlier precision imports."""
    ids = [span_trace_id(t) for t in allowed]
    quoted = ",".join("'" + t + "'" for t in ids)
    query = (
        "SELECT DISTINCT trace_id,span_id FROM events_full FINAL WHERE trace_id IN ("
        + quoted
        + ") FORMAT JSONEachRow"
    )
    result = subprocess.run(
        [
            "container",
            "exec",
            "air-agent-langfuse-clickhouse",
            "clickhouse-client",
            "--query",
            query,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise ValueError("恢复前无法核验已有 span，停止补录")
    return {
        (r["trace_id"], r["span_id"])
        for r in (json.loads(s) for s in result.stdout.splitlines() if s.strip())
    }


def protobuf_otlp(body: dict) -> bytes:
    """Use exact uint64 nanos and bytes; OTLP JSON ingestion truncates sub-ms time."""
    from google.protobuf.json_format import ParseDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    def convert(value: Any, key: str = "") -> Any:
        if key in {"traceId", "spanId", "parentSpanId"}:
            return base64.b64encode(bytes.fromhex(value)).decode()
        if isinstance(value, dict):
            return {k: convert(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(v) for v in value]
        return value

    return ParseDict(convert(body), ExportTraceServiceRequest()).SerializeToString()


def _write_private(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)


def extract_original_json(stdout: str) -> str:
    """Keep Unicode line separators inside original span content intact."""
    marker = "RECOVERY_JSON:"
    start = stdout.index(marker) + len(marker)
    raw = stdout[start:]
    _, end = json.JSONDecoder().raw_decode(raw)
    return raw[:end]


def prepare(source: Path, private: Path, out: Path, project_id: str) -> dict:
    """Extract original stored spans for missing attempts, never answer-derived data."""
    if out.exists() or private.exists():
        raise ValueError("输出或私有目录已存在，不得覆盖")
    if not re.fullmatch(r"[\w-]+", project_id):
        raise ValueError("项目标识非法")
    if not private.resolve().is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("原始 OTLP 必须存入本仓 .local 受控目录")
    reconciliation = json.loads((source / "langfuse-reconcile.json").read_text())
    missing = [r for r in reconciliation["association"] if not r["trace_found"]]
    wanted = {span_trace_id(r["expected_trace_id"]) for r in missing}
    if not wanted:
        raise ValueError("没有缺失 trace，不执行原始事件扫描")
    attempts = {r["attempt_id"] for r in missing}
    rows = [
        json.loads(s)
        for f in source.glob("requests*.jsonl")
        for s in f.read_text().splitlines()
        if s.strip()
    ]
    rows = [r for r in rows if r.get("attempt_id") in attempts]
    if len(rows) != len(attempts):
        raise ValueError("缺失运行无法逐请求对账")
    ends = [
        datetime.fromisoformat(r.get("ended_at") or r["planned_at"]).astimezone(UTC)
        for r in rows
    ]
    begins = [
        end - timedelta(milliseconds=r.get("client_complete_ms") or 180000)
        for r, end in zip(rows, ends, strict=True)
    ]
    begin = min(begins).replace(second=0, microsecond=0) - timedelta(minutes=1)
    end = max(ends) + timedelta(minutes=1)
    if end - begin > timedelta(hours=1):
        raise ValueError("恢复窗口超过一小时，需拆分审核")
    minutes = []
    while begin <= end:
        minutes.append(begin.strftime("%Y/%m/%d/%H/%M"))
        begin += timedelta(minutes=1)
    plan = {"project_id": project_id, "trace_ids": sorted(wanted), "minutes": minutes}
    result = subprocess.run(
        [
            "container",
            "exec",
            "air-agent-langfuse-worker",
            "node",
            "-e",
            _EXPORT,
            json.dumps(plan),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        # Worker logs and raw SDK payloads must not be propagated to ordinary output.
        error = next(
            (s for s in result.stdout.splitlines() if s.startswith("RECOVERY_ERROR:")),
            "原事件读取失败",
        )
        raise ValueError(error)
    raw = extract_original_json(result.stdout)
    data = json.loads(raw)
    found = set()
    for payload in data["payloads"]:
        for batch in span_batches(payload["resourceSpans"], wanted):
            for resource in batch["resourceSpans"]:
                for scope in resource["scopeSpans"]:
                    found.update(span_trace_id(s["traceId"]) for s in scope["spans"])
    private.mkdir(parents=True, mode=0o700)
    payload_file = private / "original-otel.json"
    _write_private(payload_file, raw)
    manifest = {
        "source_run": source.name,
        "project_id": project_id,
        "trace_ids": sorted(wanted),
        "traces_found_in_originals": sorted(found),
        "missing_originals": sorted(wanted - found),
        "objects_scanned": data["objects_read"],
        "duplicate_original_spans": data["duplicate_spans"],
        "selected_spans": sum(p["selected_spans"] for p in data["payloads"]),
        "payload_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "original_objects": [
            {k: p[k] for k in ["source_file", "source_sha256", "selected_spans"]}
            for p in data["payloads"]
        ],
        "network_model_calls": 0,
        "ingestion_writes": 0,
    }
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    return manifest


def replay(
    manifest_path: Path, payload_path: Path, receipt: Path, *, apply: bool
) -> dict:
    """Reingest attested original spans with stable IDs and original timestamps."""
    if not apply:
        raise ValueError("真实补录需要显式 --apply")
    if receipt.exists():
        raise ValueError("恢复回执已存在，不能覆盖")
    manifest = json.loads(manifest_path.read_text())
    raw = payload_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["payload_sha256"]:
        raise ValueError("恢复原事件摘要不符")
    data = json.loads(raw)
    wanted = set(manifest["trace_ids"])
    batches = [
        batch
        for p in data["payloads"]
        for batch in span_batches(p["resourceSpans"], wanted)
    ]
    if not batches:
        raise ValueError("没有有来源的原始 span，不执行空补录")
    existing = existing_span_ids(wanted)
    skipped = 0
    pending = []
    for body in batches:
        for resource in body["resourceSpans"]:
            for scope in resource["scopeSpans"]:
                fresh = [
                    s
                    for s in scope["spans"]
                    if (s["traceId"], s["spanId"]) not in existing
                ]
                skipped += len(scope["spans"]) - len(fresh)
                if fresh:
                    pending.append(
                        {
                            "resourceSpans": [
                                {**resource, "scopeSpans": [{**scope, "spans": fresh}]}
                            ]
                        }
                    )
    batches = pending
    config = dotenv_values(ROOT / ".env")
    base = str(config["LANGFUSE_BASE_URL"]).rstrip("/")
    if urlparse(base).hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("本轮恢复仅允许已授权本机 Langfuse")
    outcome: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "model_calls": 0,
        "wire_format": "protobuf",
        "already_present_spans_skipped": skipped,
        "batches": [],
    }
    with httpx.Client(
        auth=(config["LANGFUSE_PUBLIC_KEY"], config["LANGFUSE_SECRET_KEY"]), timeout=30
    ) as client:
        for i, body in enumerate(batches):
            try:
                response = client.post(
                    base + "/api/public/otel/v1/traces",
                    content=protobuf_otlp(body),
                    headers={
                        "x-langfuse-ingestion-version": "4",
                        "content-type": "application/x-protobuf",
                    },
                )
            except httpx.HTTPError as exc:
                outcome["batches"].append(
                    {"index": i, "outcome": "UNKNOWN", "error_type": type(exc).__name__}
                )
                outcome["stopped_reason"] = (
                    "ingestion_outcome_unknown_no_automatic_retry"
                )
                break
            rejected = None
            if response.status_code == 200:
                try:
                    rejected = (
                        response.json()
                        .get("partialSuccess", {})
                        .get("rejectedSpans", 0)
                    )
                except ValueError:
                    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                        ExportTraceServiceResponse,
                    )

                    rejected = ExportTraceServiceResponse.FromString(
                        response.content
                    ).partial_success.rejected_spans
            outcome["batches"].append(
                {
                    "index": i,
                    "http_status": response.status_code,
                    "rejected_spans": rejected,
                }
            )
            if response.status_code != 200 or rejected not in {0, None, "0"}:
                outcome["stopped_reason"] = "ingestion_failed_or_partial"
                break
    outcome["ended_at"] = datetime.now(UTC).isoformat()
    outcome["planned_batches"] = len(batches)
    outcome["note"] = (
        "HTTP success is acceptance, not proof of ClickHouse persistence; reconcile separately"
    )
    receipt.write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
    return outcome


def main() -> None:
    """Prepare a bounded read-only recovery or explicitly replay original spans."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--source", type=Path, required=True)
    prep.add_argument("--private", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--project-id", required=True)
    rep = commands.add_parser("replay")
    rep.add_argument("--manifest", type=Path, required=True)
    rep.add_argument("--payload", type=Path, required=True)
    rep.add_argument("--receipt", type=Path, required=True)
    rep.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.source, args.private, args.out, args.project_id)
        print(  # noqa: T201 - aggregate recovery preparation status
            json.dumps(
                {
                    k: result[k]
                    for k in ["objects_scanned", "selected_spans", "missing_originals"]
                }
            )
        )  # noqa: T201
    else:
        result = replay(args.manifest, args.payload, args.receipt, apply=args.apply)
        print(json.dumps(result))  # noqa: T201


if __name__ == "__main__":
    main()
