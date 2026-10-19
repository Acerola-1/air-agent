#!/usr/bin/env python3
# ruff: noqa: T201
"""Agent 业务授权单一协调发布入口（change simplify-agent-business-authorization 任务 5.3/5.4）.

分阶段、可重入、有检查点：

  preflight -> drain -> stop -> migrate -> start -> verify-switch
  -> [root 证据门禁] -> stop -> iam-cleanup -> start -> verify-final

- 维护拒绝：任何一步失败都让 Agent 服务保持停止（新运行 503/连接拒绝），
  不重启旧鉴权，不回退旧表。
- 无备份：不创建 dump/快照/备份表/影子库/配置.backup；启动器通过
  `AGENT_RELEASE_NO_BACKUP=1` 跳过 native.env 备份。
- 检查点只保存步骤、对象计数、版本和时间，不保存旧行、秘密或正文。
- 清理顺序遵守共同契约：先验证新链路与 root 证据，再删除旧表；IAM 残留由
  第一阶段提供的脚本执行，不在本仓修改 IAM 业务代码。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

AGENT_ROOT = Path(__file__).resolve().parents[1]
IAM_ROOT = AGENT_ROOT.parent.parent / "Java" / "air_agent_iam"
RELEASE_ROOT = AGENT_ROOT / ".local" / "release" / "agent-business-20261019"
CHECKPOINT = RELEASE_ROOT / "checkpoint.json"
SERVICE_DIR = AGENT_ROOT / ".local" / "i4-live" / "services"
AGENT_SERVICES = ("agent-admin-api", "agent-chat-web", "agent-admin-web")
AGENT_PORTS = (8100, 3000, 3100)
GRAPH_CONTAINER = "air-agent-langgraph-api"
ROOT_UUID = "dd5ca3c0-a454-466e-b2f7-a7fc5191867e"
DRAIN_SECONDS = 60
HEALTH = (
    ("agent-admin-api", "http://127.0.0.1:8100/health"),
    ("agent-chat-web", "http://127.0.0.1:3000/"),
    ("agent-admin-web", "http://127.0.0.1:3100/"),
    ("langgraph", "http://127.0.0.1:2024/ok"),
)


class ReleaseError(RuntimeError):
    """可安全展示的发布错误（不含秘密）."""


def _now() -> str:
    """Now."""
    return dt.datetime.now(dt.UTC).isoformat()


def _env() -> dict[str, str]:
    """Env."""
    merged = {key: value for key, value in os.environ.items()}
    merged.update(
        {
            key: value
            for key, value in dotenv_values(AGENT_ROOT / ".env").items()
            if value
        }
    )
    return merged


def _db_uri(env: dict[str, str]) -> str:
    """Db uri."""
    uri = env.get("DATABASE_URI", "")
    if not uri:
        raise ReleaseError("DATABASE_URI 未配置")
    return uri


def _load_checkpoint() -> dict[str, Any]:
    """Load checkpoint."""
    if not CHECKPOINT.exists():
        return {"release": "agent-business-20261019", "created_at": _now(), "steps": {}}
    try:
        return json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError("检查点损坏，拒绝继续") from exc


def _save_checkpoint(data: dict[str, Any]) -> None:
    """Save checkpoint."""
    RELEASE_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(RELEASE_ROOT, 0o700)
    data["updated_at"] = _now()
    temporary = CHECKPOINT.with_suffix(".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, CHECKPOINT)
    os.chmod(CHECKPOINT, 0o600)


def _step(data: dict[str, Any], name: str, **fields: Any) -> None:
    """Step."""
    data["steps"][name] = {"at": _now(), **fields}
    _save_checkpoint(data)
    print(f"[release] {name}: {json.dumps(fields, ensure_ascii=False)}")


def _run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 300):
    """Run."""
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return (
        result.returncode,
        (result.stdout or "")[-4000:],
        (result.stderr or "")[-2000:],
    )


def _query(
    uri: str, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Query helper."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(uri, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute("SET statement_timeout='10s'")
        return [dict(row) for row in conn.execute(sql, params or {}).fetchall()]


def _http_ok(url: str, timeout: float = 5.0) -> bool:
    """Http ok."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 400
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    """Pid alive."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(name: str) -> int | None:
    """Read pid."""
    path = SERVICE_DIR / f"{name}.pid"
    try:
        value = int(path.read_text(encoding="ascii").strip())
        return value if value > 1 else None
    except (OSError, ValueError):
        return None


def preflight(data: dict[str, Any]) -> None:
    """Preflight."""
    env = _env()
    uri = _db_uri(env)
    version = _query(uri, "select version_num from alembic_version")
    old = _query(uri, "select to_regclass('public.mcp_identity_bindings') as r")
    counts = _query(
        uri,
        "select state, count(*)::int as n from agent_run_attempts"
        " where state in ('admitting','admission_unknown','queued','running','waiting_input')"
        " group by state order by state",
    )
    old_count = 0
    if old and old[0]["r"]:
        old_count = int(
            _query(uri, "select count(*)::int as n from mcp_identity_bindings")[0]["n"]
        )
    connections_ok = False
    errors = ""
    try:
        code = (
            "import json, os, sys; sys.path.insert(0, 'src');"
            "from common.mcp_connections import load_connections;"
            "c=load_connections(); print(json.dumps({'revision': c.revision,"
            " 'servers': [s.redacted() for s in c.servers]}, ensure_ascii=False))"
        )
        rc, out, err = _run(
            [sys.executable, "-c", code], cwd=AGENT_ROOT, env=env, timeout=30
        )
        connections_ok = rc == 0
        errors = err[-300:] if rc else ""
    except Exception as exc:  # noqa: BLE001
        errors = type(exc).__name__
    _step(
        data,
        "preflight",
        alembic_version=version[0]["version_num"] if version else None,
        old_table_present=bool(old and old[0]["r"]),
        old_table_rows=old_count,
        active_attempts=counts,
        connections_ok=connections_ok,
        connections_error=errors,
        git_head=_run(["git", "rev-parse", "HEAD"], cwd=AGENT_ROOT, env=env)[1].strip(),
    )


def drain(data: dict[str, Any]) -> None:
    """Drain."""
    env = _env()
    uri = _db_uri(env)
    started = time.monotonic()
    observations: list[dict[str, Any]] = []
    while True:
        rows = _query(
            uri,
            "select state, count(*)::int as n from agent_run_attempts"
            " where state in ('admitting','admission_unknown','queued','running','waiting_input')"
            " group by state order by state",
        )
        observations.append({"at": _now(), "states": rows})
        if not rows or time.monotonic() - started >= DRAIN_SECONDS:
            break
        time.sleep(5)
    _step(
        data,
        "drain",
        waited_seconds=round(time.monotonic() - started, 1),
        final_states=observations[-1]["states"] if observations else [],
        note="未强制改写账本；停止服务后不再产生外部发送",
    )


def _port_pids(port: int) -> set[int]:
    """Return PIDs listening on the given local port."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}


def stop_services(data: dict[str, Any]) -> None:
    """Stop agent services and the native container to enter maintenance rejection."""
    stopped: list[str] = []
    pids: set[int] = set()
    for name in AGENT_SERVICES:
        pid = _read_pid(name)
        if pid and _pid_alive(pid):
            pids.add(pid)
            stopped.append(name)
    for port in AGENT_PORTS:
        pids.update(port for port in _port_pids(port) if port > 1)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not any(
            (pid := _read_pid(name)) and _pid_alive(pid) for name in AGENT_SERVICES
        ):
            break
        time.sleep(0.5)
    for pid in pids:
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    container = subprocess.run(
        ["container", "stop", GRAPH_CONTAINER],
        capture_output=True,
        text=True,
        timeout=60,
    )
    ports_open = {
        "graph": _http_ok(HEALTH[3][1], timeout=1),
        "admin": _http_ok(HEALTH[0][1], timeout=1),
        "chat": _http_ok(HEALTH[1][1], timeout=1),
        "admin_web": _http_ok(HEALTH[2][1], timeout=1),
    }
    if any(ports_open.values()):
        _step(
            data,
            "stop",
            stopped=stopped,
            container_rc=container.returncode,
            ports=ports_open,
        )
        raise ReleaseError("Agent 服务未完全停止，拒绝进入清理")
    _step(
        data,
        "stop",
        stopped=stopped,
        container_rc=container.returncode,
        ports=ports_open,
    )


def migrate(data: dict[str, Any], target: str = "head") -> None:
    """Execute migrations to the requested revision."""
    env = _env()
    rc, out, err = _run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=AGENT_ROOT / "admin" / "api",
        env=env,
        timeout=300,
    )
    if rc != 0:
        _step(data, "migrate", rc=rc, tail=err)
        raise ReleaseError("迁移失败；服务保持停止")
    _step(data, "migrate", target=target, rc=rc, tail=out.splitlines()[-6:])


def start_services(data: dict[str, Any]) -> None:
    """Start services."""
    env = _env()
    env["AGENT_RELEASE_NO_BACKUP"] = "1"
    rc, out, err = _run(
        [sys.executable, "deployment/run_separated_local.py", "start"],
        cwd=AGENT_ROOT,
        env=env,
        timeout=300,
    )
    if rc != 0:
        _step(data, "start", rc=rc, tail=err)
        raise ReleaseError("启动失败；保持维护拒绝")
    _step(data, "start", rc=rc, tail=out.splitlines()[-12:])


def verify_switch(data: dict[str, Any]) -> None:
    """Verify switch."""
    env = _env()
    uri = _db_uri(env)
    health = {name: _http_ok(url) for name, url in HEALTH}
    version = _query(uri, "select version_num from alembic_version")
    new_columns = _query(
        uri,
        "select table_name, column_name from information_schema.columns"
        " where table_schema='public' and"
        " ((table_name='agent_requests' and column_name='business_code') or"
        "  (table_name='agent_run_attempts' and column_name='business_policy_version') or"
        "  (table_name='agent_iam_ticket_bindings' and column_name='permissions'))",
    )
    _step(
        data,
        "verify-switch",
        health=health,
        alembic_version=version[0]["version_num"] if version else None,
        new_columns=sorted(row["column_name"] for row in new_columns),
    )
    if not all(health.values()):
        raise ReleaseError("新链路健康检查未通过；保持维护拒绝")


def root_verify(data: dict[str, Any], evidence: Path) -> bool:
    """Run the IAM root login readback with a protected password source."""
    password = os.environ.get("IAM_ROOT_PASSWORD", "")
    secret_file = AGENT_ROOT / ".local" / "i4-live" / "root-password"
    if not password and secret_file.is_file():
        try:
            password = secret_file.read_text(encoding="utf-8").strip()
        except OSError:
            password = ""
    if not password:
        _step(
            data,
            "root-verify",
            ok=False,
            note="缺少 IAM_ROOT_PASSWORD 或受保护口令文件",
        )
        return False
    env = _env()
    env["IAM_ROOT_PASSWORD"] = password
    evidence.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    rc, out, err = _run(
        [
            sys.executable,
            "scripts/verify-root-entitlements.py",
            "--base-url",
            "http://127.0.0.1:19100",
            "--account",
            "root",
            "--output",
            str(evidence),
        ],
        cwd=IAM_ROOT,
        env=env,
        timeout=120,
    )
    _step(
        data,
        "root-verify",
        ok=rc == 0,
        rc=rc,
        output_tail=out.splitlines()[-6:],
        error_tail=err.splitlines()[-3:],
    )
    return rc == 0


def root_gate(data: dict[str, Any], evidence: Path | None) -> None:
    """Root 正常登录/切企业证据门禁；缺失则拒绝物理清理."""
    candidates: list[Path] = []
    if evidence:
        candidates.append(evidence)
    candidates.append(
        IAM_ROOT
        / "docs"
        / "reviews"
        / "evidence"
        / "run"
        / "agent-business-20261019"
        / "formal-root-readback.json"
    )
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        identity_ok = (
            payload.get("user_id") == ROOT_UUID or payload.get("account") == "root"
        )
        if payload.get("ok") is True and identity_ok:
            _step(data, "root-gate", evidence=str(path), ok=True)
            return
    _step(
        data,
        "root-gate",
        ok=False,
        note="缺少 root 正常登录/切企业读回证据；按共同契约拒绝删除旧授权",
    )
    raise ReleaseError(
        "root 正常登录证据缺失（现有 4 字符口令不满足 IAM 8..256 策略）；"
        "按共同契约保持维护拒绝，不删除旧表/旧授权"
    )


def iam_cleanup(data: dict[str, Any]) -> None:
    """Iam cleanup."""
    env = _env()
    env["IAM_OPS_ROOT_USER_ID"] = ROOT_UUID
    rc, out, err = _run(
        ["bash", "scripts/iam-final-cleanup.sh", "--apply"],
        cwd=IAM_ROOT,
        env=env,
        timeout=300,
    )
    if rc != 0:
        _step(data, "iam-cleanup", rc=rc, tail=err)
        raise ReleaseError("IAM 残留清理失败；保持维护拒绝")
    rc2, out2, err2 = _run(
        ["bash", "scripts/iam-ops.sh", "verify", f"--iam.ops.root-user-id={ROOT_UUID}"],
        cwd=IAM_ROOT,
        env=env,
        timeout=300,
    )
    _step(
        data,
        "iam-cleanup",
        rc=rc,
        verify_rc=rc2,
        tail=out.splitlines()[-6:],
        verify=out2.splitlines()[-6:],
    )
    if rc2 != 0:
        raise ReleaseError("IAM 清理后 root 授权复验失败；保持维护拒绝")


def verify_final(data: dict[str, Any]) -> None:
    """Verify final."""
    env = _env()
    uri = _db_uri(env)
    health = {name: _http_ok(url) for name, url in HEALTH}
    old = _query(uri, "select to_regclass('public.mcp_identity_bindings') as r")
    old_rows = 0
    if old and old[0]["r"]:
        old_rows = int(
            _query(uri, "select count(*)::int as n from mcp_identity_bindings")[0]["n"]
        )
    backup_objects = _query(
        uri,
        "select n.nspname, c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace"
        " where n.nspname not in ('pg_catalog','information_schema') and"
        " (c.relname like '%%_backup%%' or c.relname like '%%_shadow%%' or c.relname like '%%_bak')",
    )
    forbidden_files = [
        str(path)
        for path in RELEASE_ROOT.rglob("*")
        if path.is_file()
        and (
            path.name.endswith((".dump", ".sql", ".backup", ".bak"))
            or ".backup-" in path.name
        )
    ]
    _step(
        data,
        "verify-final",
        health=health,
        old_table_present=bool(old and old[0]["r"]),
        old_table_rows=old_rows,
        backup_objects=backup_objects,
        forbidden_files=forbidden_files,
    )
    if (
        not all(health.values())
        or (old and old[0]["r"])
        or backup_objects
        or forbidden_files
    ):
        raise ReleaseError("最终核验未通过")


def command_status(_: argparse.Namespace) -> None:
    """Command status."""
    data = _load_checkpoint()
    print(json.dumps(data, ensure_ascii=False, indent=2))


def command_preflight(args: argparse.Namespace) -> None:
    """Command preflight."""
    data = _load_checkpoint()
    preflight(data)
    drain(data)
    stop_services(data)
    # 先只加业务快照列；旧表删除必须等 root 证据门禁通过。
    migrate(data, target="0021_business_authorization")
    start_services(data)
    verify_switch(data)


def command_cleanup(args: argparse.Namespace) -> None:
    """Command cleanup."""
    data = _load_checkpoint()
    root_gate(data, args.root_evidence)
    stop_services(data)
    migrate(data, target="head")
    iam_cleanup(data)
    start_services(data)
    verify_final(data)


def command_run_all(args: argparse.Namespace) -> None:
    """Command run all."""
    command_preflight(args)
    command_cleanup(args)
    print("[release] completed")


def command_verify(args: argparse.Namespace) -> None:
    """Command verify."""
    data = _load_checkpoint()
    verify_switch(data)
    verify_final(data)


def command_root_verify(args: argparse.Namespace) -> None:
    """Run only the root login readback."""
    data = _load_checkpoint()
    evidence = args.root_evidence or (
        IAM_ROOT
        / "docs"
        / "reviews"
        / "evidence"
        / "run"
        / "agent-business-20261019"
        / "formal-root-readback.json"
    )
    if not root_verify(data, evidence):
        raise ReleaseError("root 正常登录读回未完成")
    root_gate(data, evidence)


def _parser() -> argparse.ArgumentParser:
    """Parser."""
    parser = argparse.ArgumentParser(description="Agent 业务授权协调发布（无备份）")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "preflight", "cleanup", "run-all", "verify", "root-verify"):
        child = sub.add_parser(name)
        if name in ("cleanup", "run-all", "root-verify"):
            child.add_argument(
                "--root-evidence",
                type=Path,
                default=None,
                help="verify-root-entitlements.py 产出的 root 读回 JSON",
            )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Execute the requested release phase."""
    args = _parser().parse_args(argv)
    handlers = {
        "status": command_status,
        "preflight": command_preflight,
        "cleanup": command_cleanup,
        "run-all": command_run_all,
        "verify": command_verify,
        "root-verify": command_root_verify,
    }
    try:
        handlers[args.command](args)
        return 0
    except ReleaseError as exc:
        print(f"[release] 拒绝/失败：{exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("[release] 子命令超时；保持维护拒绝", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
