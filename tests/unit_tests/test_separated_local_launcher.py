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
