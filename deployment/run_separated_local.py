"""Start or inspect the already-separated local IAM, Agent, RAG, and LangGraph stack.

The launcher validates the committed runtime boundaries before starting processes,
never performs database migration, and records only sanitized lifecycle metadata.
"""

from __future__ import annotations

import argparse
import ast
import copy
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from dotenv import dotenv_values

BRIDGE_HOST = (
    os.environ.get("AIR_AGENT_BRIDGE_HOST", "192.168.64.1").strip() or "192.168.64.1"
)
BRIDGE_PORT = 19110
GRAPH_PORT = 2024
GRAPH_CONTAINER = "air-agent-langgraph-api"
HTTP_TIMEOUT_SECONDS = 20.0
PORT_WAIT_SECONDS = 20.0
GRAPH_HEALTH_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.25


class LaunchError(Exception):
    """A safe-to-display launcher error that contains no configuration values."""


@dataclass(frozen=True)
class Service:
    """A local process definition with its port, health route, and injected environment."""

    key: str
    port: int
    health_path: str
    command: tuple[str, ...]
    cwd: Path
    env: dict[str, str]


def _agent_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _project_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    agent = _agent_root()
    dev = agent.parent.parent
    iam = (
        Path(args.iam_project).expanduser()
        if args.iam_project
        else dev / "Java" / "air_agent_iam"
    )
    rag = (
        Path(args.rag_project).expanduser()
        if args.rag_project
        else agent.parent / "RAG" / "env-rag-final"
    )
    return agent, iam.resolve(), rag.resolve()


def _safe_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise LaunchError(f"缺少{label}文件。")


def _read_dotenv(path: Path, *, required: bool = True) -> dict[str, str]:
    if not path.exists():
        if required:
            raise LaunchError("缺少所需 dotenv 文件。")
        return {}
    if not path.is_file():
        raise LaunchError("dotenv 路径不是普通文件。")
    try:
        parsed = dotenv_values(path, encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise LaunchError("dotenv 文件无法读取。") from exc
    return {key: value for key, value in parsed.items() if key and value is not None}


def _merged_env(*dotenv_files: Path) -> dict[str, str]:
    merged = os.environ.copy()
    for path in dotenv_files:
        merged.update(_read_dotenv(path, required=False))
    return merged


def _required(env: dict[str, str], name: str, label: str) -> str:
    value = env.get(name)
    if not value or not value.strip():
        raise LaunchError(f"{label}缺少必需配置项。")
    return value


def _out(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def _err(message: str) -> None:
    sys.stderr.write(f"{message}\n")


def _business_database_uri(value: str, label: str) -> None:
    if "'" in value or '"' in value:
        raise LaunchError(f"{label}包含引号污染。")
    parsed = urlparse(value)
    database = unquote(parsed.path.lstrip("/"))
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or database != "air_agent_business"
    ):
        raise LaunchError(f"{label}没有指向 air_agent_business。")


def _parse_literal_env(path: Path) -> dict[str, str]:
    _safe_file(path, ".env.langgraph")
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise LaunchError("LangGraph dotenv 文件无法读取。") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise LaunchError("LangGraph dotenv 必须为完整的 literal key=value 行。")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise LaunchError("LangGraph dotenv 存在无效 key=value 行。")
        if key in values:
            raise LaunchError("LangGraph dotenv 存在重复配置项。")
        values[key] = value
    return values


def _validate_host_env(
    iam_root: Path, agent_root: Path, rag_root: Path
) -> tuple[dict[str, str], ...]:
    iam_dotenv = iam_root / ".env.local"
    agent_dotenv = agent_root / ".env"
    rag_dotenv = rag_root / ".env"
    for path, label in (
        (iam_dotenv, "IAM .env.local"),
        (agent_dotenv, "Agent .env"),
        (rag_dotenv, "RAG .env"),
    ):
        _safe_file(path, label)

    iam_file_env = _read_dotenv(iam_dotenv)
    if iam_file_env.get("IAM_PG_DB") != "iam_identity":
        raise LaunchError("IAM 数据库必须配置为 iam_identity。")
    iam_env = _merged_env(iam_dotenv, iam_root / "server" / ".env")
    if iam_env.get("IAM_PG_DB") != "iam_identity":
        raise LaunchError("IAM 有效运行环境必须使用 iam_identity。")

    agent_file_env = _read_dotenv(agent_dotenv)
    agent_env = _merged_env(agent_dotenv, agent_root / "admin" / "api" / ".env")
    for config, label in (
        (agent_file_env, "Agent host .env"),
        (agent_env, "Agent 有效运行环境"),
    ):
        database_uri = _required(config, "DATABASE_URI", label)
        admin_uri = _required(config, "ADMIN_RUNTIME_DATABASE_URI", label)
        runtime_uri = _required(config, "RUNTIME_DATABASE_URI", label)
        _business_database_uri(database_uri, f"{label} DATABASE_URI")
        _business_database_uri(admin_uri, f"{label} ADMIN_RUNTIME_DATABASE_URI")
        _business_database_uri(runtime_uri, f"{label} RUNTIME_DATABASE_URI")
        if admin_uri != runtime_uri:
            raise LaunchError(
                f"{label} ADMIN_RUNTIME_DATABASE_URI 与 RUNTIME_DATABASE_URI 必须相同。"
            )
    if agent_file_env.get("AGENT_IDENTITY_BACKEND", "").lower() != "remote":
        raise LaunchError("Agent 必须使用 remote IAM 模式。")
    if agent_env.get("AGENT_IDENTITY_BACKEND", "").lower() != "remote":
        raise LaunchError("Agent 有效运行环境必须使用 remote IAM 模式。")
    for key in (
        "IAM_BASE_URL",
        "IAM_ISSUER",
        "IAM_INTERNAL_CLIENT_ID",
        "IAM_INTERNAL_CLIENT_SECRET",
    ):
        _required(agent_env, key, "Agent remote IAM 配置")
    if agent_file_env.get("IAM_BASE_URL", "").rstrip("/") != "http://127.0.0.1:19100":
        raise LaunchError("Agent host IAM_BASE_URL 必须指向本机 IAM 19100。")

    rag_file_env = _read_dotenv(rag_dotenv)
    rag_env = _merged_env(rag_dotenv)
    if rag_env.get("ENV_RAG_AUTH_MODE", "").lower() != "remote":
        raise LaunchError("RAG 必须使用 remote IAM 模式。")
    for key in (
        "ENV_RAG_IAM_URL",
        "ENV_RAG_IAM_ISSUER",
        "ENV_RAG_IAM_CLIENT_ID",
        "ENV_RAG_IAM_CLIENT_SECRET",
        "ENV_RAG_IAM_MAPPING",
    ):
        _required(rag_file_env, key, "RAG .env remote IAM 配置")
        _required(rag_env, key, "RAG 有效 remote IAM 配置")
    mapping = Path(rag_file_env["ENV_RAG_IAM_MAPPING"]).expanduser()
    if not mapping.is_absolute():
        mapping = rag_root / mapping
    _safe_file(mapping, "RAG IAM mapping")
    return iam_env, agent_env, rag_env


def _validate_graph_env(path: Path) -> dict[str, str]:
    values = _parse_literal_env(path)
    if values.get("AGENT_IDENTITY_BACKEND") != "remote":
        raise LaunchError("LangGraph 必须使用 remote IAM 模式。")
    for key in ("DATABASE_URI", "POSTGRES_URI", "RUNTIME_DATABASE_URI"):
        _business_database_uri(
            _required(values, key, "LangGraph dotenv"), f"LangGraph {key}"
        )
    store = _langgraph_store_config(
        _required(values, "LANGGRAPH_STORE", "LangGraph dotenv")
    )
    _business_database_uri(store["uri"], "LangGraph LANGGRAPH_STORE uri")
    if (
        values.get("IAM_BASE_URL", "").rstrip("/")
        != f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"
    ):
        raise LaunchError("LangGraph IAM_BASE_URL 必须指向本机私有桥 19110。")
    model_base = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}/v1"
    if values.get("MAIN_LLM_BASE_URL", "").rstrip("/") != model_base:
        raise LaunchError("LangGraph MAIN_LLM_BASE_URL 必须指向本机私有桥 /v1。")
    summary_base = values.get("SUMMARY_LLM_BASE_URL")
    if summary_base and summary_base.rstrip("/") != model_base:
        raise LaunchError("LangGraph SUMMARY_LLM_BASE_URL 必须指向本机私有桥 /v1。")
    return values


def _langgraph_store_config(value: str) -> dict[str, Any]:
    try:
        config = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LaunchError("LangGraph LANGGRAPH_STORE 必须是有效 JSON 对象。") from exc
    if (
        not isinstance(config, dict)
        or config.get("kind") != "postgres"
        or not isinstance(config.get("uri"), str)
    ):
        raise LaunchError("LangGraph LANGGRAPH_STORE 必须包含 postgres kind 和 uri。")
    return config


def _check_bridge_config(path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    _safe_file(path, "私有桥配置")
    if path.is_symlink():
        raise LaunchError("私有桥配置不能是符号链接。")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LaunchError("私有桥配置不可读取。") from exc
    if mode != 0o600:
        raise LaunchError("私有桥配置权限必须为 0600。")
    if not isinstance(config, dict):
        raise LaunchError("私有桥配置格式无效。")
    if config.get("bind_host") not in {"127.0.0.1", BRIDGE_HOST}:
        raise LaunchError("私有桥绑定地址不在允许范围内。")
    if config.get("bind_port") != BRIDGE_PORT:
        raise LaunchError("私有桥端口必须为 19110。")
    allowed = config.get("allowed_source_ips")
    if not isinstance(allowed, list) or not all(
        isinstance(item, str) for item in allowed
    ):
        raise LaunchError("私有桥来源 IP 配置格式无效。")
    return config, tuple(allowed)


def _is_listening(port: int, hosts: tuple[str, ...] = ("127.0.0.1",)) -> bool:
    for host in hosts:
        try:
            with socket.create_connection((host, port), timeout=0.35):
                return True
        except OSError:
            continue
    return False


def _wait_for_port(port: int, hosts: tuple[str, ...] = ("127.0.0.1",)) -> bool:
    deadline = time.monotonic() + PORT_WAIT_SECONDS
    while True:
        if _is_listening(port, hosts):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))


def _http_status(port: int, path: str, timeout: float) -> int | None:
    request = Request(f"http://127.0.0.1:{port}{path}", method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except (URLError, TimeoutError, OSError, ValueError):
        return None


def _wait_for_http(
    service: str,
    port: int,
    path: str,
    timeout_seconds: float = HTTP_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status: int | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = (
                f"最后 HTTP 状态 {last_status}"
                if last_status is not None
                else "服务无 HTTP 响应"
            )
            raise LaunchError(
                f"{service} 健康检查未在 {int(timeout_seconds)} 秒内返回 200（{detail}）。"
            )
        last_status = _http_status(port, path, min(2.0, remaining))
        if last_status == 200:
            return
        time.sleep(min(POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))


def _private_services_dir(agent_root: Path) -> Path:
    path = _prepare_private_i4_dir(agent_root) / "services"
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise LaunchError("服务状态目录必须是普通目录。")
    os.chmod(path, 0o700)
    return path


def _append_lifecycle(log_dir: Path, key: str, event: str, **fields: Any) -> None:
    path = log_dir / f"{key}.log"
    if path.is_symlink():
        raise LaunchError("服务日志不能是符号链接。")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        item = {
            "time": datetime.now(UTC).isoformat(),
            "service": key,
            "event": event,
            **fields,
        }
        os.write(
            descriptor, (json.dumps(item, separators=(",", ":")) + "\n").encode("utf-8")
        )
    finally:
        os.close(descriptor)


def _write_pid(log_dir: Path, key: str, pid: int) -> None:
    path = log_dir / f"{key}.pid"
    if path.is_symlink():
        raise LaunchError("服务 PID 文件不能是符号链接。")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{key}.", dir=log_dir)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(f"{pid}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _read_pid(log_dir: Path, key: str) -> int | None:
    path = log_dir / f"{key}.pid"
    try:
        if path.is_symlink():
            return None
        value = int(path.read_text(encoding="ascii").strip())
        return value if value > 1 else None
    except (OSError, UnicodeError, ValueError):
        return None


def _spawn_service(service: Service, log_dir: Path) -> None:
    try:
        process = subprocess.Popen(
            service.command,
            cwd=service.cwd,
            env=service.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise LaunchError(f"{service.key} 无法启动。") from exc
    _write_pid(log_dir, service.key, process.pid)
    _append_lifecycle(
        log_dir, service.key, "started", pid=process.pid, port=service.port
    )


def _start_http_service(service: Service, log_dir: Path) -> None:
    already_listening = _is_listening(service.port)
    if already_listening:
        _out(f"跳过 {service.key}：端口已监听。")
    else:
        _spawn_service(service, log_dir)
        _out(f"已启动 {service.key}，等待健康检查。")
    _wait_for_http(service.key, service.port, service.health_path)
    if already_listening:
        _append_lifecycle(log_dir, service.key, "skipped_existing", port=service.port)
    _append_lifecycle(
        log_dir, service.key, "healthy", port=service.port, http_status=200
    )
    _out(f"就绪 {service.key}：HTTP 200。")


def _detect_bridge_host(container_cli: str) -> str:
    """Return the current container-network gateway used by the runtime container.

    Apple container recreates its vmnet bridge after a runtime restart, so the
    host address seen by containers can move (e.g. 192.168.64.1 → 192.168.65.1).
    The private bridge must follow the current gateway; falling back keeps the
    historical address for older installations.
    """

    def _gateway(payload: Any) -> str:
        if isinstance(payload, list):
            if len(payload) != 1:
                return ""
            payload = payload[0]
        if not isinstance(payload, dict):
            return ""
        status = payload.get("status")
        if not isinstance(status, dict):
            return ""
        # `container network inspect` puts the gateway directly on status,
        # while a running container exposes it under status.networks[*].
        direct = str(status.get("ipv4Gateway") or "").strip()
        if direct:
            return direct
        networks = status.get("networks")
        records = (
            list(networks.values())
            if isinstance(networks, dict)
            else networks
            if isinstance(networks, list)
            else []
        )
        for record in records:
            if isinstance(record, dict):
                gateway = str(record.get("ipv4Gateway") or "").strip()
                if gateway:
                    return gateway
        return ""

    commands = (
        ("inspect", GRAPH_CONTAINER),
        ("network", "inspect", "default"),
    )
    for command in commands:
        try:
            result = subprocess.run(
                [container_cli, *command],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                continue
            gateway = _gateway(json.loads(result.stdout))
            if gateway:
                return gateway
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            continue
    return BRIDGE_HOST


def _container_ip(container_cli: str, container_name: str) -> str:
    try:
        result = subprocess.run(
            [container_cli, "inspect", container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise LaunchError("无法检查容器状态。")
        payload: Any = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise LaunchError("无法读取容器状态。") from exc
    if isinstance(payload, list):
        if len(payload) != 1:
            raise LaunchError("容器检查结果不唯一。")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise LaunchError("容器检查结果格式无效。")
    status = payload.get("status")
    if not isinstance(status, dict):
        raise LaunchError("容器没有可用的运行状态。")
    status_text = str(status.get("state", status.get("status", ""))).lower()
    if status_text != "running" and status.get("running") is not True:
        raise LaunchError("容器当前未运行。")
    networks = status.get("networks")
    if isinstance(networks, dict):
        records = list(networks.values())
    elif isinstance(networks, list):
        records = networks
    else:
        raise LaunchError("容器网络信息不可用。")
    addresses: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        raw = record.get("ipv4Address")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            address = (
                ipaddress.ip_interface(raw).ip
                if "/" in raw
                else ipaddress.ip_address(raw)
            )
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv4Address) and address.is_global is False:
            if (
                not address.is_loopback
                and not address.is_unspecified
                and not address.is_multicast
            ):
                addresses.add(str(address))
    if len(addresses) != 1:
        raise LaunchError("容器必须恰有一个可用私有 IPv4 地址。")
    return addresses.pop()


def _inspect_existing_dependencies(container_cli: str) -> tuple[str, str]:
    addresses: list[str] = []
    for name in ("air-agent-postgres", "air-agent-redis"):
        try:
            addresses.append(_container_ip(container_cli, name))
        except LaunchError as exc:
            raise LaunchError(
                "既有 PostgreSQL/Redis 容器缺失、未运行或无有效 IPv4；请先启动既有依赖，启动器不会重建它们。"
            ) from exc
    return addresses[0], addresses[1]


def _rewrite_uri_host(value: str, host: str, port: int = 5432) -> str:
    parsed = urlparse(value)
    if not parsed.hostname:
        raise LaunchError("数据库 URI 缺少主机名。")
    userinfo, separator, _old_host = parsed.netloc.rpartition("@")
    prefix = f"{userinfo}@" if separator else ""
    return parsed._replace(netloc=f"{prefix}{host}:{port}").geturl()


def _native_env_bytes(source: bytes, postgres_ip: str, redis_ip: str) -> bytes:
    try:
        text = source.decode("utf-8")
    except UnicodeError as exc:
        raise LaunchError("LangGraph dotenv 不是有效 UTF-8。") from exc
    values = _parse_literal_env_from_text(text)
    store = _langgraph_store_config(
        _required(values, "LANGGRAPH_STORE", "LangGraph dotenv")
    )
    replacements = {
        "DATABASE_URI": _rewrite_uri_host(values["DATABASE_URI"], postgres_ip),
        "POSTGRES_URI": _rewrite_uri_host(values["POSTGRES_URI"], postgres_ip),
        "RUNTIME_DATABASE_URI": _rewrite_uri_host(
            values["RUNTIME_DATABASE_URI"], postgres_ip
        ),
        "REDIS_URI": f"redis://{redis_ip}:6379",
    }
    store["uri"] = replacements["DATABASE_URI"]
    replacements["LANGGRAPH_STORE"] = json.dumps(store, ensure_ascii=False)
    for key in ("DATABASE_URI", "POSTGRES_URI", "RUNTIME_DATABASE_URI"):
        _business_database_uri(replacements[key], f"原生 LangGraph {key}")
    _business_database_uri(store["uri"], "原生 LangGraph LANGGRAPH_STORE uri")

    seen: set[str] = set()
    rendered: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if not body.strip() or body.lstrip().startswith("#") or "=" not in body:
            rendered.append(line)
            continue
        raw_key, _raw_value = body.split("=", 1)
        key = raw_key.strip()
        if key in replacements:
            newline = line[len(body) :]
            rendered.append(f"{raw_key}={replacements[key]}{newline}")
            seen.add(key)
        else:
            rendered.append(line)
    required_keys = {
        "DATABASE_URI",
        "POSTGRES_URI",
        "RUNTIME_DATABASE_URI",
        "LANGGRAPH_STORE",
    }
    if not required_keys.issubset(seen):
        raise LaunchError("LangGraph dotenv 缺少需要定向更新的数据库项。")
    if "REDIS_URI" not in seen:
        if rendered and not rendered[-1].endswith(("\n", "\r")):
            rendered.append("\n")
        rendered.append(f"REDIS_URI={replacements['REDIS_URI']}\n")
    return "".join(rendered).encode("utf-8")


def _parse_literal_env_from_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise LaunchError("LangGraph dotenv 必须为完整的 literal key=value 行。")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise LaunchError("LangGraph dotenv 存在无效 key=value 行。")
        if key in values:
            raise LaunchError("LangGraph dotenv 存在重复配置项。")
        values[key] = value
    return values


def _prepare_private_i4_dir(agent_root: Path) -> Path:
    local_dir = agent_root / ".local"
    i4_dir = local_dir / "i4-live"
    for directory in (local_dir, i4_dir):
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise LaunchError("本机部署目录必须是普通目录。")
        os.chmod(directory, 0o700)
    return i4_dir


def _atomic_private_bytes(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise LaunchError("部署输出文件不能是符号链接。")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _backup_native_env(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise LaunchError("既有原生环境文件必须是普通文件。")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise LaunchError("既有原生环境文件无法备份。") from exc
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    for suffix in range(1000):
        extra = f"-{suffix}" if suffix else ""
        backup = path.with_name(f"{path.name}.backup-{stamp}{extra}")
        try:
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return
    raise LaunchError("无法为既有原生环境文件分配唯一备份名。")


def _expected_alembic_head(agent_root: Path) -> str:
    """Return the single Alembic head declared by admin API migration files."""
    versions_dir = agent_root / "admin" / "api" / "migrations" / "versions"
    revisions: set[str] = set()
    down_revisions: set[str] = set()
    for path in versions_dir.glob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LaunchError("无法读取 admin API 迁移文件。") from exc

        def literal(name: str) -> Any:
            match = re.search(rf"^{name}\s*=\s*(.+)$", text, re.M)
            if not match:
                return None
            try:
                return ast.literal_eval(match.group(1).strip())
            except (ValueError, SyntaxError) as exc:
                raise LaunchError(
                    f"admin API 迁移文件 {path.name} 的 {name} 无法解析。"
                ) from exc

        revision = literal("revision")
        parent = literal("down_revision")
        if isinstance(revision, str):
            revisions.add(revision)
        if isinstance(parent, str):
            down_revisions.add(parent)
        elif isinstance(parent, tuple):
            down_revisions.update(item for item in parent if isinstance(item, str))
    heads = revisions - down_revisions
    if len(heads) != 1:
        raise LaunchError("admin API 迁移链不存在唯一 head。")
    return next(iter(heads))


def _verify_admin_schema(agent_root: Path, agent_env: dict[str, str]) -> None:
    """Fail before starting if the business database is not at the code head."""
    uri = agent_env.get("DATABASE_URI", "").strip()
    if not uri:
        raise LaunchError("缺少 DATABASE_URI，无法校验业务库迁移版本。")
    expected = _expected_alembic_head(agent_root)
    try:
        import psycopg

        with psycopg.connect(uri, connect_timeout=5) as connection:
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            current = str(row[0]) if row else ""
    except Exception as exc:  # noqa: BLE001 - deployment preflight
        raise LaunchError(
            "无法读取业务库迁移版本；请确认 PostgreSQL 已启动并已执行 admin API 迁移。"
        ) from exc
    if current != expected:
        raise LaunchError(
            f"业务库迁移未到代码 head：当前 {current or '无'}，期望 {expected}；"
            "请先在 admin/api 执行 alembic upgrade head。"
        )


def _write_native_environment(
    source_path: Path, i4_dir: Path, postgres_ip: str, redis_ip: str
) -> Path:
    target = i4_dir / "native.env"
    try:
        source = source_path.read_bytes()
        payload = _native_env_bytes(source, postgres_ip, redis_ip)
    except OSError as exc:
        raise LaunchError("LangGraph dotenv 无法读取。") from exc
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise LaunchError("既有原生环境文件必须是普通文件。")
        try:
            if target.read_bytes() == payload:
                os.chmod(target, 0o600)
                return target
        except OSError as exc:
            raise LaunchError("既有原生环境文件无法读取。") from exc
        # 用户明确要求本次维护发布不留配置备份；只有显式发布开关才跳过，
        # 日常启动仍保留原有可追溯备份行为。
        if os.environ.get("AGENT_RELEASE_NO_BACKUP", "").strip().lower() not in {
            "1",
            "true",
            "yes",
        }:
            _backup_native_env(target)
    _atomic_private_bytes(target, payload)
    return target


def _absolute_mount_source(source: str, agent_root: Path) -> str:
    candidate = Path(source).expanduser()
    if candidate.is_absolute():
        return str(candidate.resolve())
    if source not in {".", ".."} and not source.startswith(("./", "../")):
        raise LaunchError(
            "LangGraph 服务包含非 bind mount；原生描述符拒绝引用顶层 volume。"
        )
    return str((agent_root / candidate).resolve())


def _native_service_descriptor(
    compose_path: Path,
    agent_root: Path,
    native_env_path: Path,
) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise LaunchError("Agent Python 环境缺少 PyYAML。") from exc
    try:
        source = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise LaunchError("原始 Compose 描述符无法安全解析。") from exc
    if not isinstance(source, dict) or not isinstance(source.get("name"), str):
        raise LaunchError("原始 Compose 描述符缺少项目名。")
    services = source.get("services")
    if not isinstance(services, dict) or not isinstance(
        services.get("langgraph-api"), dict
    ):
        raise LaunchError("原始 Compose 描述符缺少唯一 langgraph-api 服务。")
    service = copy.deepcopy(services["langgraph-api"])
    service.pop("depends_on", None)

    env_file = service.get("env_file")
    if isinstance(env_file, str):
        env_files = [env_file]
    elif isinstance(env_file, list):
        env_files = env_file
    else:
        raise LaunchError("langgraph-api 必须有单一 env_file。")
    if len(env_files) != 1 or not isinstance(env_files[0], str):
        raise LaunchError("langgraph-api env_file 必须只有一个路径。")
    if Path(env_files[0]).name != ".env.langgraph":
        raise LaunchError("langgraph-api 必须使用仓库现有 .env.langgraph。")
    service["env_file"] = [str(native_env_path.resolve())]

    volumes = service.get("volumes")
    if not isinstance(volumes, list):
        raise LaunchError("langgraph-api volumes 格式无效。")
    absolute_volumes: list[Any] = []
    for volume in volumes:
        if isinstance(volume, str):
            parts = volume.split(":")
            if len(parts) < 2:
                raise LaunchError("langgraph-api mount 必须指定绝对源路径和目标路径。")
            parts[0] = _absolute_mount_source(parts[0], agent_root)
            absolute_volumes.append(":".join(parts))
        elif isinstance(volume, dict):
            mount = copy.deepcopy(volume)
            source_path = mount.get("source")
            if not isinstance(source_path, str):
                raise LaunchError("langgraph-api mount 缺少 source。")
            mount["source"] = _absolute_mount_source(source_path, agent_root)
            absolute_volumes.append(mount)
        else:
            raise LaunchError("langgraph-api mount 格式无效。")
    service["volumes"] = absolute_volumes

    environment = service.get("environment")
    if not isinstance(environment, dict):
        raise LaunchError("langgraph-api environment 格式无效。")
    if any(
        key in environment
        for key in (
            "DATABASE_URI",
            "POSTGRES_URI",
            "RUNTIME_DATABASE_URI",
            "LANGGRAPH_STORE",
            "REDIS_URI",
        )
    ):
        raise LaunchError(
            "langgraph-api environment 不得覆盖私有文件的数据库或 Redis 配置。"
        )
    descriptor = {"name": source["name"], "services": {"langgraph-api": service}}
    return descriptor


def _write_native_compose(descriptor: dict[str, Any], i4_dir: Path) -> Path:
    try:
        import yaml
    except ImportError as exc:
        raise LaunchError("Agent Python 环境缺少 PyYAML。") from exc
    payload = yaml.safe_dump(descriptor, allow_unicode=True, sort_keys=False).encode(
        "utf-8"
    )
    target = i4_dir / "native-compose.yaml"
    _atomic_private_bytes(target, payload)
    return target


def _atomic_private_json(path: Path, config: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".bridge.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _bridge_argv(agent_root: Path, config_path: Path) -> tuple[str, ...]:
    return (
        str(agent_root / ".venv" / "bin" / "python"),
        str(agent_root / "deployment" / "local_host_bridge.py"),
        "--config",
        str(config_path),
    )


def _tracked_bridge_matches(log_dir: Path, expected: tuple[str, ...]) -> int | None:
    pid = _read_pid(log_dir, "local-host-bridge")
    if pid is None:
        return None
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        actual = (
            tuple(shlex.split(result.stdout.strip())) if result.returncode == 0 else ()
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return pid if actual == expected else None


def _stop_tracked_bridge(pid: int, expected: tuple[str, ...], log_dir: Path) -> None:
    if _tracked_bridge_matches(log_dir, expected) != pid:
        raise LaunchError("私有桥进程身份复查失败；未终止任何进程。")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        raise LaunchError("受管私有桥无法安全重启。") from exc
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            _append_lifecycle(
                log_dir, "local-host-bridge", "stopped_for_ip_change", pid=pid
            )
            return
        except PermissionError as exc:
            raise LaunchError("受管私有桥状态无法确认；未强制终止。") from exc
        time.sleep(0.1)
    raise LaunchError("受管私有桥未及时退出；未强制终止。")


def _ensure_bridge(
    agent_root: Path,
    config_path: Path,
    actual_ip: str,
    log_dir: Path,
) -> None:
    config, previous_ips = _check_bridge_config(config_path)
    changed = previous_ips != (actual_ip,)
    bind_host = str(config["bind_host"])
    bridge_hosts = tuple(dict.fromkeys((bind_host, "127.0.0.1", BRIDGE_HOST)))
    listening = _is_listening(BRIDGE_PORT, bridge_hosts)
    expected = _bridge_argv(agent_root, config_path)
    managed_pid: int | None = None
    if listening and changed:
        managed_pid = _tracked_bridge_matches(log_dir, expected)
        if managed_pid is None:
            raise LaunchError(
                "私有桥 IP 已变化但 19110 由非受管进程监听；未终止任何进程。"
            )
    if changed:
        config["allowed_source_ips"] = [actual_ip]
        _atomic_private_json(config_path, config)
        _out("已将私有桥来源限制更新为当前 LangGraph 容器 IP。")
    if listening and not changed:
        _append_lifecycle(
            log_dir, "local-host-bridge", "skipped_existing", port=BRIDGE_PORT
        )
        _out("跳过 local-host-bridge：来源 IP 未变且 19110 已监听。")
        return
    if listening and changed:
        assert managed_pid is not None
        _stop_tracked_bridge(managed_pid, expected, log_dir)
        if _is_listening(BRIDGE_PORT, bridge_hosts):
            raise LaunchError("私有桥端口仍被占用；未终止其他进程。")
    service = Service(
        key="local-host-bridge",
        port=BRIDGE_PORT,
        health_path="",
        command=expected,
        cwd=agent_root,
        env=_merged_env(agent_root / ".env"),
    )
    _spawn_service(service, log_dir)
    _out("已启动 local-host-bridge，等待 TCP 监听。")
    if not _wait_for_port(BRIDGE_PORT, bridge_hosts):
        raise LaunchError("local-host-bridge 未在 20 秒内监听 19110。")
    _append_lifecycle(log_dir, "local-host-bridge", "tcp_listening", port=BRIDGE_PORT)
    _out("就绪 local-host-bridge：19110 TCP 已监听。")


def _service_specs(
    iam_root: Path,
    agent_root: Path,
    rag_root: Path,
    iam_env: dict[str, str],
    agent_env: dict[str, str],
    rag_env: dict[str, str],
) -> tuple[Service, ...]:
    agent_python = agent_root / ".venv" / "bin" / "python"
    rag_python = rag_root / ".venv" / "bin" / "python"
    npm_iam = shutil.which("npm", path=iam_env.get("PATH")) or "npm"
    npm_agent = shutil.which("npm", path=agent_env.get("PATH")) or "npm"
    java_home = iam_env.get("JAVA_HOME")
    java = (
        str(Path(java_home) / "bin" / "java")
        if java_home
        else (shutil.which("java", path=iam_env.get("PATH")) or "java")
    )
    record_root = rag_root / ".local" / "remote-iam-live" / "records"
    return (
        Service(
            "iam-java",
            19100,
            "/healthz",
            (java, "-jar", str(iam_root / "server" / "target" / "iam-server.jar")),
            iam_root / "server",
            iam_env,
        ),
        Service(
            "agent-admin-api",
            8100,
            "/health",
            (
                str(agent_python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8100",
                "--no-access-log",
            ),
            agent_root / "admin" / "api",
            agent_env,
        ),
        Service(
            "rag-api",
            8099,
            "/health",
            (
                str(rag_python),
                "-m",
                "env_rag_final.access",
                "--host",
                "127.0.0.1",
                "--port",
                "8099",
                "--auth-mode",
                "remote",
                "--env-file",
                str(rag_root / ".env"),
                "--record-root",
                str(record_root),
            ),
            rag_root,
            rag_env,
        ),
        Service(
            "iam-web",
            19101,
            "/",
            (npm_iam, "run", "preview"),
            iam_root / "web",
            _merged_env(iam_root / ".env.local", iam_root / "web" / ".env.local"),
        ),
        Service(
            "agent-chat-web",
            3000,
            "/",
            (
                npm_agent,
                "run",
                "start",
                "--",
                "--hostname",
                "127.0.0.1",
                "--port",
                "3000",
            ),
            agent_root / "frontend",
            _merged_env(agent_root / ".env", agent_root / "frontend" / ".env.local"),
        ),
        Service(
            "agent-admin-web",
            3100,
            "/",
            (npm_agent, "run", "start", "--", "--hostname", "127.0.0.1"),
            agent_root / "admin" / "web",
            _merged_env(
                agent_root / ".env", agent_root / "admin" / "web" / ".env.local"
            ),
        ),
    )


def _preflight_builds(
    services: tuple[Service, ...], agent_root: Path, iam_root: Path, rag_root: Path
) -> None:
    artifacts: dict[str, tuple[Path, ...]] = {
        "iam-java": (iam_root / "server" / "target" / "iam-server.jar",),
        "iam-web": (iam_root / "web" / "dist" / "index.html",),
        "agent-admin-api": (
            agent_root / ".venv" / "bin" / "python",
            agent_root / "admin" / "api" / "app" / "main.py",
        ),
        "rag-api": (
            rag_root / ".venv" / "bin" / "python",
            rag_root / "src" / "env_rag_final" / "access" / "__main__.py",
        ),
        "agent-chat-web": (
            agent_root / "frontend" / "node_modules" / ".bin" / "next",
            agent_root / "frontend" / ".next" / "BUILD_ID",
        ),
        "agent-admin-web": (
            agent_root / "admin" / "web" / "node_modules" / ".bin" / "next",
            agent_root / "admin" / "web" / ".next" / "BUILD_ID",
        ),
    }
    for service in services:
        if _is_listening(service.port):
            continue
        for artifact in artifacts[service.key]:
            if not artifact.exists():
                raise LaunchError(f"{service.key} 缺少已构建文件：{artifact.name}。")
        if service.key in {"iam-web", "agent-chat-web", "agent-admin-web"}:
            if shutil.which(service.command[0], path=service.env.get("PATH")) is None:
                raise LaunchError(f"{service.key} 缺少 npm。")
        if service.key == "iam-java" and not Path(service.command[0]).is_file():
            raise LaunchError("IAM 缺少可执行 Java。")
    bridge_python = agent_root / ".venv" / "bin" / "python"
    bridge_script = agent_root / "deployment" / "local_host_bridge.py"
    if not _is_listening(BRIDGE_PORT, ("127.0.0.1", BRIDGE_HOST)):
        for artifact in (bridge_python, bridge_script):
            if not artifact.is_file():
                raise LaunchError(f"私有桥缺少运行文件：{artifact.name}。")


def _start_graph(
    agent_root: Path,
    container_cli: str,
    compose_cli: str | None,
    native_env_path: Path,
    native_compose_path: Path,
    log_dir: Path,
) -> None:
    graph_was_listening = _is_listening(GRAPH_PORT)
    if graph_was_listening:
        _out("跳过 LangGraph：2024 已监听，不执行 Compose。")
        _append_lifecycle(log_dir, "langgraph", "skipped_existing", port=GRAPH_PORT)
    else:
        if compose_cli is None:
            raise LaunchError("缺少 container-compose。")
        native_values = _parse_literal_env(native_env_path)
        compose_environment = os.environ.copy()
        for key in native_values:
            compose_environment.pop(key, None)
        command = [
            compose_cli,
            "up",
            "-d",
            "--env-file",
            str(native_env_path.resolve()),
            "-f",
            str(native_compose_path.resolve()),
            "--profile",
            "prod",
            "langgraph-api",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=agent_root,
                env=compose_environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LaunchError("LangGraph Compose 启动失败。") from exc
        if result.returncode != 0:
            raise LaunchError("LangGraph Compose 启动失败。")
        _append_lifecycle(log_dir, "langgraph", "compose_started", port=GRAPH_PORT)
        _out("已请求启动 LangGraph。")
    actual_ip = _container_ip(container_cli, GRAPH_CONTAINER)
    config_path = agent_root / ".local" / "i4-live" / "bridge.json"
    _ensure_bridge(agent_root, config_path, actual_ip, log_dir)
    _wait_for_http("langgraph", GRAPH_PORT, "/ok", GRAPH_HEALTH_TIMEOUT_SECONDS)
    _append_lifecycle(log_dir, "langgraph", "healthy", port=GRAPH_PORT, http_status=200)
    _out("就绪 LangGraph：HTTP 200。")


def _status() -> int:
    checks = (
        ("iam-java", "127.0.0.1", 19100, "/healthz"),
        ("iam-web", "127.0.0.1", 19101, "/"),
        ("agent-admin-api", "127.0.0.1", 8100, "/health"),
        ("agent-chat-web", "127.0.0.1", 3000, "/"),
        ("agent-admin-web", "127.0.0.1", 3100, "/"),
        ("rag-api", "127.0.0.1", 8099, "/health"),
        ("langgraph", "127.0.0.1", GRAPH_PORT, "/ok"),
    )
    healthy = True
    for name, host, port, path in checks:
        if not _is_listening(port, (host,)):
            _out(f"未监听 {name}：{port}。")
            healthy = False
            continue
        status = _http_status(port, path, 2.0)
        if status == 200:
            _out(f"就绪 {name}：HTTP 200。")
        else:
            _out(f"未就绪 {name}：HTTP {status if status is not None else '无响应'}。")
            healthy = False
    container_cli = shutil.which("container")
    bridge_host = _detect_bridge_host(container_cli) if container_cli else BRIDGE_HOST
    if _is_listening(BRIDGE_PORT, (bridge_host, "127.0.0.1")):
        _out(f"就绪 local-host-bridge：{BRIDGE_PORT} TCP 已监听。")
    else:
        _out(f"未监听 local-host-bridge：{BRIDGE_PORT}。")
        healthy = False
    return 0 if healthy else 1


def _start(args: argparse.Namespace) -> int:
    agent_root, iam_root, rag_root = _project_paths(args)
    for root, label in ((agent_root, "Agent"), (iam_root, "IAM"), (rag_root, "RAG")):
        if not root.is_dir():
            raise LaunchError(f"{label}仓库路径不存在。")

    container_cli = shutil.which("container")
    if container_cli is None:
        raise LaunchError("缺少 Apple container CLI。")
    global BRIDGE_HOST
    BRIDGE_HOST = _detect_bridge_host(container_cli)
    iam_env, agent_env, rag_env = _validate_host_env(iam_root, agent_root, rag_root)
    graph_path = agent_root / ".env.langgraph"
    _validate_graph_env(graph_path)
    _verify_admin_schema(agent_root, agent_env)
    native_env_path = agent_root / ".local" / "i4-live" / "native.env"
    native_descriptor = _native_service_descriptor(
        agent_root / "docker-compose.yaml",
        agent_root,
        native_env_path,
    )
    bridge_path = agent_root / ".local" / "i4-live" / "bridge.json"
    bridge_config, _ = _check_bridge_config(bridge_path)
    if bridge_config["bind_host"] != BRIDGE_HOST:
        raise LaunchError("生产启动要求私有桥绑定 192.168.64.1。")

    services = _service_specs(
        iam_root, agent_root, rag_root, iam_env, agent_env, rag_env
    )
    _preflight_builds(services, agent_root, iam_root, rag_root)
    compose_cli = None
    if not _is_listening(GRAPH_PORT):
        compose_cli = shutil.which("container-compose")
        if compose_cli is None:
            raise LaunchError("缺少 Apple container-compose CLI。")

    postgres_ip, redis_ip = _inspect_existing_dependencies(container_cli)
    i4_dir = _prepare_private_i4_dir(agent_root)
    native_env_path = _write_native_environment(
        graph_path, i4_dir, postgres_ip, redis_ip
    )
    native_compose_path = _write_native_compose(native_descriptor, i4_dir)
    log_dir = _private_services_dir(agent_root)
    by_key = {service.key: service for service in services}
    _start_http_service(by_key["iam-java"], log_dir)
    _start_graph(
        agent_root,
        container_cli,
        compose_cli,
        native_env_path,
        native_compose_path,
        log_dir,
    )
    record_root = rag_root / ".local" / "remote-iam-live" / "records"
    record_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if record_root.is_symlink() or not record_root.is_dir():
        raise LaunchError("RAG remote records 路径必须是普通目录。")
    os.chmod(record_root, 0o700)
    for key in (
        "agent-admin-api",
        "rag-api",
        "iam-web",
        "agent-chat-web",
        "agent-admin-web",
    ):
        _start_http_service(by_key[key], log_dir)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动或检查已切库的本机分离服务。")
    parser.add_argument("--iam-project", type=Path, default=None)
    parser.add_argument("--rag-project", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "status"):
        child = subparsers.add_parser(action)
        child.add_argument("--iam-project", type=Path, default=argparse.SUPPRESS)
        child.add_argument("--rag-project", type=Path, default=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the requested read-only status check or bounded local startup."""
    args = _parser().parse_args(argv)
    if args.action == "status":
        return _status()
    try:
        return _start(args)
    except LaunchError as exc:
        _err(f"启动失败：{exc}")
        return 1
    except Exception:
        _err("启动失败：发生未公开的内部错误；未输出配置或异常内容。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
