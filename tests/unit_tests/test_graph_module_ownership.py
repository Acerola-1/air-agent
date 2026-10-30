"""业务归属和可信装配的架构回归."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from application.business_policies import configure, resolve_policy
from common.capabilities import list_graph_skills

ROOT = Path(__file__).resolve().parents[2]


def _import_violations(source: str, package: str, forbidden: set[str]) -> list[int]:
    """检查所有作用域，包括 TYPE_CHECKING 和相对、字面量动态导入."""
    tree = ast.parse(source)
    aliases = {"__import__"}
    importlib_aliases = {"importlib"}
    builtins_aliases = {"builtins"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importlib_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "importlib"
            )
            builtins_aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "builtins"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            aliases.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "__import__"
            )

    def forbidden_module(name: str) -> bool:
        parts = name.split(".")
        return parts[0] in forbidden or (
            parts[0] == "src" and len(parts) > 1 and parts[1] in forbidden
        )

    violations = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                try:
                    base = importlib.util.resolve_name("." * node.level + base, package)
                except ImportError:
                    violations.append(node.lineno)
                    continue
            names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.Call):
            dynamic = isinstance(node.func, ast.Name) and node.func.id in aliases
            dynamic |= (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in importlib_aliases
            )
            dynamic |= (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "__import__"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in builtins_aliases
            )
            if dynamic:
                # 变量拼接无法静态证明安全；必须改成可审计的字面量。
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    violations.append(node.lineno)
                    continue
                name = node.args[0].value
                if not isinstance(name, str) or name.startswith("."):
                    violations.append(node.lineno)
                    continue
                names = [name]
        if any(forbidden_module(name) for name in names):
            violations.append(node.lineno)
    return violations


@pytest.mark.parametrize(
    ("source", "package"),
    [
        ("import data_analysis.graph", "basic_qa"),
        ("from src import data_analysis", "basic_qa"),
        ("def f():\n from data_analysis import graph", "basic_qa"),
        ("if TYPE_CHECKING:\n import data_analysis", "basic_qa"),
        ("from . import graph", "data_analysis"),
        ("import importlib as il\nil.import_module('data_analysis.graph')", "basic_qa"),
        (
            "from importlib import import_module as load\nload('data_analysis')",
            "basic_qa",
        ),
        ("__import__('data_analysis')", "basic_qa"),
        ("import builtins as b\nb.__import__('data_analysis')", "basic_qa"),
        ("from builtins import __import__ as load\nload('data_analysis')", "basic_qa"),
        ("__import__(name)", "basic_qa"),
    ],
)
def test_import_boundary_scanner_rejects_hidden_references(source, package):
    assert _import_violations(source, package, {"data_analysis"})


@pytest.mark.parametrize(
    ("directory", "forbidden"),
    [
        ("common", {"basic_qa", "data_analysis", "intelligent_tracing", "application"}),
        ("basic_qa", {"data_analysis"}),
        ("data_analysis", {"basic_qa"}),
    ],
)
def test_module_dependency_boundaries(directory, forbidden):
    for path in (ROOT / "src" / directory).rglob("*.py"):
        relative = path.relative_to(ROOT / "src")
        package = ".".join(relative.parts[:-1])
        violations = _import_violations(
            path.read_text(encoding="utf-8"), package, forbidden
        )
        assert not violations, (path, violations)


def test_common_has_no_graph_builder_calls():
    for path in (ROOT / "src/common").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"StateGraph", "build_business_graph"}, (
                    path,
                    node.lineno,
                )


def test_retired_common_graph_and_controllers_are_absent():
    for name in (
        "business_graph",
        "context_preparation.py",
        "context_guard.py",
        "conversation_summarizer.py",
        "turn_tool_governance.py",
        "skill_discovery.py",
        "context.py",
    ):
        assert not (ROOT / "src/common" / name).exists(), name


@pytest.mark.parametrize(
    ("graph", "blocked"), [("basic_qa", "data_analysis"), ("data_analysis", "basic_qa")]
)
def test_graph_bootstrap_is_independent_and_has_no_network(graph, blocked):
    code = """
import importlib.abc, importlib, json, socket, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, *args):
  if fullname.split('.')[0] == sys.argv[2]: raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
def denied(*args, **kwargs): raise AssertionError('network during graph import')
socket.socket.connect = denied
module = importlib.import_module(sys.argv[1]+'.graph')
graph = module.graph({})
assert graph.checkpointer is None
print(json.dumps(sorted(graph.get_graph().nodes)))
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "LANGFUSE_PROMPTS_ENABLED": "false",
        "LANGFUSE_ENABLED": "false",
    }
    result = subprocess.run(
        [sys.executable, "-c", code, graph, blocked],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "call_model" in json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("fault", [AttributeError, TypeError, RuntimeError])
def test_catalogue_policy_failure_does_not_relax_menu(monkeypatch, fault):
    from data_analysis import capability_policy

    configure()

    def broken(_menu):
        raise fault("synthetic policy failure")

    monkeypatch.setattr(capability_policy, "menu_skill", broken)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as failure:
        list_graph_skills("data-analysis", menu_name="CityHourBroadcast")
    assert failure.value.status_code == 503


def test_three_graphs_are_explicitly_assembled_without_other_graph_rules():
    configure()
    assert resolve_policy("basic-qa").menu_skill("CityHourBroadcast") is None
    assert (
        resolve_policy("data-analysis").menu_skill("CityHourBroadcast")
        == "broadcast-hour"
    )
    assert resolve_policy("intelligent-tracing").titles() == {}
    with pytest.raises(ValueError):
        resolve_policy("unknown")


@pytest.mark.parametrize(
    ("graph_id", "blocked"),
    [("basic-qa", "data_analysis"), ("data-analysis", "basic_qa")],
)
def test_registered_policy_loads_only_the_requested_graph(graph_id, blocked):
    code = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, *args):
  if fullname.split('.')[0] == sys.argv[2]: raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from application.business_policies import configure, resolve_policy
configure()
resolve_policy(sys.argv[1])
assert not any(name.split('.')[0] == sys.argv[2] for name in sys.modules)
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-c", code, graph_id, blocked],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_admission_menu_faults_fail_closed_with_zero_network():
    code = """
import os,sys,socket
sys.path.insert(0,'admin/api')
os.environ['DATABASE_URI']='postgresql://synthetic:synthetic@127.0.0.1:1/graph_ownership_test'
os.environ['ADMIN_RUNTIME_DATABASE_URI']=os.environ['DATABASE_URI']
from app.services.ledger import validate_skill_selection, CapabilityValidationError
from data_analysis import capability_policy
calls=[]
def no_network(*args,**kwargs): calls.append(1); raise AssertionError('network')
socket.socket.connect=no_network
# A valid same-menu selection is the positive control.
item=validate_skill_selection('data-analysis','skill:data-analysis:broadcast-hour',menu_name='CityHourBroadcast')
assert item['id']=='skill:data-analysis:broadcast-hour'
for failure in (AttributeError,TypeError,RuntimeError):
 def broken(*args): raise failure('synthetic policy failure')
 capability_policy.validate_selection=broken
 try: validate_skill_selection('data-analysis','skill:data-analysis:broadcast-hour',menu_name='CityHourBroadcast')
 except CapabilityValidationError: pass
 else: raise AssertionError('menu exception was swallowed')
assert calls==[]
"""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
