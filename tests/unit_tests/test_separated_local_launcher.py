"""Guard real deployment failures without starting processes or touching services."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SPEC = importlib.util.spec_from_file_location(
    "separated_local_launcher",
    Path(__file__).resolve().parents[2] / "deployment/run_separated_local.py",
)
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


def native_values():
    owner = "postgresql://owner:synthetic-secret@old-host/air_agent_business"
    return {
        "DATABASE_URI": owner,
        "POSTGRES_URI": owner,
        "RUNTIME_DATABASE_URI": owner.replace("owner:", "runtime:"),
        "LANGGRAPH_STORE": json.dumps(
            {"kind": "postgres", "uri": owner, "extra": True}
        ),
        "AGENT_IDENTITY_BACKEND": "remote",
        "IAM_BASE_URL": "http://192.168.64.1:19110",
        "MAIN_LLM_BASE_URL": "http://192.168.64.1:19110/v1",
        "OPTIONAL_EMPTY": "",
        "API_KEY": "synthetic-secret-preserved",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("LANGSMITH_TRACING", "true"),
        ("LANGSMITH_TRACING_V2", "yes"),
        ("LANGCHAIN_TRACING_V2", "1"),
        ("LANGCHAIN_TRACING", "on"),
    ],
)
def test_langsmith_cannot_be_enabled_by_deployment_env(tmp_path, key, value):
    env = native_values() | {key: value}
    path = tmp_path / "native.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    with pytest.raises(launcher.LaunchError, match="仅使用 Langfuse"):
        launcher._validate_graph_env(path)


def test_disabled_langsmith_preserves_langfuse_configuration():
    env = {
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGCHAIN_TRACING": "false",
        "LANGFUSE_ENABLED": "true",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-fixture",
        # The licensed native server still requires an authentication key.
        "LANGSMITH_API_KEY": "synthetic-license-key",
    }
    before = env.copy()
    launcher._validate_langfuse_only(env, "fixture")
    assert env == before


def test_native_network_rewrite_preserves_store_shape_and_secrets(tmp_path):
    values = native_values()
    raw = "".join(f"{key}={value}\n" for key, value in values.items()).encode()
    rendered = launcher._native_env_bytes(raw, "192.168.64.10", "192.168.64.11")
    path = tmp_path / "native.env"
    path.write_bytes(rendered)
    result = launcher._validate_graph_env(path)
    store = json.loads(result["LANGGRAPH_STORE"])
    assert store == {"kind": "postgres", "uri": result["DATABASE_URI"], "extra": True}
    assert "@192.168.64.10:5432/air_agent_business" in result["DATABASE_URI"]
    assert result["REDIS_URI"] == "redis://192.168.64.11:6379"
    assert result["OPTIONAL_EMPTY"] == ""
    assert result["API_KEY"] == values["API_KEY"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("DATABASE_URI", "postgresql://owner:synthetic@old-host/air_agent"),
        ("AGENT_IDENTITY_BACKEND", "local"),
        ("LANGGRAPH_STORE", "postgresql://owner:synthetic@old-host/air_agent_business"),
    ],
)
def test_native_preflight_rejects_source_or_local_config(tmp_path, key, value):
    values = native_values() | {key: value}
    path = tmp_path / "native.env"
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    with pytest.raises(launcher.LaunchError):
        launcher._validate_graph_env(path)


def test_native_env_follows_the_current_gateway_only(tmp_path):
    """Apple container moves the gateway; render it instead of hand-editing."""
    values = native_values() | {
        "LANGFUSE_BASE_URL": "http://192.168.64.1:3001",
        "KNOWLEDGE_API_URL": "http://192.168.64.1:19110/rag",
        "DEPENDENCY_HOST": "http://192.168.64.12:8000",
        "PUBLIC_URL": "https://api.example.com/v1",
        # 合法内网服务也常用 .1 结尾：不是本机私有桥，必须原样保留。
        "INTERNAL_MCP_URL": "http://10.20.30.1:8000/mcp",
    }
    path = tmp_path / "native.env"
    path.write_bytes(
        launcher._native_env_bytes(
            "".join(f"{key}={value}\n" for key, value in values.items()).encode(),
            "192.168.65.4",
            "192.168.65.5",
            "192.168.65.1",
        )
    )
    result = launcher._validate_graph_env(path)
    assert result["IAM_BASE_URL"] == "http://192.168.65.1:19110"
    assert result["MAIN_LLM_BASE_URL"] == "http://192.168.65.1:19110/v1"
    assert result["LANGFUSE_BASE_URL"] == "http://192.168.65.1:3001"
    assert result["KNOWLEDGE_API_URL"] == "http://192.168.65.1:19110/rag"
    assert result["DEPENDENCY_HOST"] == "http://192.168.64.12:8000"
    assert result["PUBLIC_URL"] == "https://api.example.com/v1"
    assert result["INTERNAL_MCP_URL"] == "http://10.20.30.1:8000/mcp"


@pytest.mark.parametrize(
    "value",
    [
        "http://192.168.64.1:19111",
        "http://192.168.64.1:19110/api",
        "https://example.com:19110",
        "http://127.0.0.1:19110",
    ],
)
def test_graph_env_still_requires_the_private_bridge(tmp_path, value):
    values = native_values() | {"IAM_BASE_URL": value}
    path = tmp_path / "native.env"
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    with pytest.raises(launcher.LaunchError):
        launcher._validate_graph_env(path)


def test_bridge_config_accepts_a_stale_gateway_for_automatic_rewrite(tmp_path):
    path = tmp_path / "bridge.json"
    path.write_text(
        json.dumps(
            {
                "bind_host": "192.168.64.1",
                "bind_port": 19110,
                "allowed_source_ips": ["192.168.65.23"],
            }
        )
    )
    path.chmod(0o600)
    config, allowed = launcher._check_bridge_config(path)
    assert config["bind_host"] == "192.168.64.1"
    assert allowed == ("192.168.65.23",)


def test_bridge_config_still_refuses_public_or_missing_bind_host(tmp_path):
    for bind_host in ("8.8.8.8", "", None):
        path = tmp_path / "bridge.json"
        path.write_text(
            json.dumps(
                {
                    "bind_host": bind_host,
                    "bind_port": 19110,
                    "allowed_source_ips": ["192.168.65.23"],
                }
            )
        )
        path.chmod(0o600)
        with pytest.raises(launcher.LaunchError):
            launcher._check_bridge_config(path)


def test_container_networks_reads_configuration_for_stopped_containers(monkeypatch):
    """A stopped container keeps its network in configuration, not status."""
    payload = [
        {
            "configuration": {
                "networks": [{"network": "air-agent-langfuse", "options": {}}]
            },
            "status": {"state": "stopped", "networks": []},
        }
    ]
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload)),
    )
    assert launcher._container_networks("container", "fixture") == (
        "air-agent-langfuse",
    )
    assert launcher._container_state("container", "fixture") == "stopped"


def test_langfuse_pinned_network_constants_match_compose():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yaml").read_text())
    services = compose["services"]
    for name in launcher.LANGFUSE_SERVICES:
        assert launcher.LANGFUSE_NETWORK in services[name]["networks"], name
    assert compose["networks"][launcher.LANGFUSE_NETWORK]["name"] == (
        launcher.LANGFUSE_NETWORK
    )
    # The gateway must be a constant: it is the first address of a pinned subnet.
    assert launcher.LANGFUSE_GATEWAY.split(".")[-1] == "1"
    pin = launcher.LANGFUSE_NETWORK_SUBNET.split("/")[0].rsplit(".", 1)[0]
    assert launcher.LANGFUSE_GATEWAY.rsplit(".", 1)[0] == pin


def test_langfuse_running_containers_must_still_pass_health_check(monkeypatch):
    """在跑不等于就绪：重复启动分支也必须走 HTTP 门禁，不能直接返回成功。"""
    monkeypatch.setattr(launcher, "_langfuse_enabled", lambda _root: True)
    monkeypatch.setattr(launcher, "_ensure_langfuse_network", lambda _cli: None)
    monkeypatch.setattr(launcher, "_container_state", lambda _cli, _name: "running")
    monkeypatch.setattr(
        launcher,
        "_container_networks",
        lambda _cli, _name: (launcher.LANGFUSE_NETWORK,),
    )
    checked: list[tuple] = []

    def unhealthy(name, port, path, timeout_seconds=None):
        checked.append((name, port))
        raise launcher.LaunchError("langfuse 健康检查未返回 200")

    monkeypatch.setattr(launcher, "_wait_for_http", unhealthy)

    with pytest.raises(launcher.LaunchError):
        launcher._ensure_langfuse_stack(Path("/tmp"), "container", "container-compose")

    assert checked == [("langfuse", launcher.LANGFUSE_HTTP_PORT)]


def test_running_dependency_with_closed_port_is_not_ready(monkeypatch):
    monkeypatch.setattr(launcher, "_container_state", lambda _cli, _name: "running")
    monkeypatch.setattr(
        launcher, "_published_tcp_ports", lambda _cli, _name: (("127.0.0.1", 15432),)
    )
    monkeypatch.setattr(launcher, "_is_listening", lambda _port, _hosts=(): False)

    with pytest.raises(launcher.LaunchError):
        launcher._ensure_running_containers(
            "container", ("air-agent-postgres",), "依赖", timeout_seconds=0.0
        )


def test_running_dependency_with_open_port_is_accepted(monkeypatch):
    monkeypatch.setattr(launcher, "_container_state", lambda _cli, _name: "running")
    monkeypatch.setattr(
        launcher, "_published_tcp_ports", lambda _cli, _name: (("127.0.0.1", 15432),)
    )
    monkeypatch.setattr(launcher, "_is_listening", lambda _port, _hosts=(): True)

    launcher._ensure_running_containers(
        "container", ("air-agent-postgres",), "依赖", timeout_seconds=0.0
    )


def test_command_tail_redacts_url_credentials():
    result = subprocess.CompletedProcess(
        [],
        1,
        "",
        "connection failed: postgresql://fixture:fixture-credential-123@127.0.0.1/db",
    )
    tail = launcher._command_tail(result)
    assert "fixture-credential-123" not in tail
    assert "postgresql://***@127.0.0.1/db" in tail


def test_reads_actual_apple_container_state_field(monkeypatch):
    payload = [
        {
            "status": {
                "state": "running",
                "networks": [
                    {"ipv4Address": "192.168.64.10/24"},
                ],
            }
        }
    ]
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload)),
    )
    assert launcher._container_ip("container", "fixture") == "192.168.64.10"


def test_native_descriptor_keeps_only_application_and_refuses_uri_override(tmp_path):
    root = tmp_path
    compose = root / "compose.yaml"
    service = {
        "env_file": [".env.langgraph"],
        "environment": {"LANGGRAPH_AUTH_TYPE": "custom"},
        "depends_on": {"postgres": {}},
        "volumes": ["./src:/deps/air_agent/src"],
    }
    data = {
        "name": "air-agent",
        "services": {"langgraph-api": service, "postgres": {}},
        "volumes": {"pgdata": {}},
    }
    compose.write_text(yaml.safe_dump(data))
    result = launcher._native_service_descriptor(compose, root, root / "native.env")
    assert set(result["services"]) == {"langgraph-api"}
    assert "volumes" not in result
    assert "depends_on" not in result["services"]["langgraph-api"]
    service["environment"]["DATABASE_URI"] = "postgresql://legacy/air_agent"
    compose.write_text(yaml.safe_dump(data))
    with pytest.raises(launcher.LaunchError):
        launcher._native_service_descriptor(compose, root, root / "native.env")
