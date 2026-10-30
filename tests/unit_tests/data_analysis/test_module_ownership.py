"""数据分析结构归位及跨图隔离契约."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from data_analysis import capability_policy
from data_analysis.menu_skill_mapping import DATA_ANALYSIS_SKILL_MAPPINGS


def test_catalog_mapping_and_selection_keep_distinct_matching_rules(monkeypatch):
    mapping = DATA_ANALYSIS_SKILL_MAPPINGS[0]
    menu = mapping.menu_names[0]
    assert capability_policy.titles()[mapping.skill_name] == mapping.title
    assert capability_policy.menu_skill(menu) == mapping.skill_name
    assert capability_policy.menu_skill(menu.lower()) is None
    assert capability_policy.menu_skill("unknown") is None
    assert capability_policy.required_tool_ids(["first", "first", "second"]) == [
        "first",
        "first",
        "second",
    ]
    capability_policy.validate_selection(mapping.skill_name, menu.lower())
    capability_policy.validate_selection("another", "unknown")
    with pytest.raises(ValueError):
        capability_policy.validate_selection("another", menu.lower())

    def fail(_menu):
        raise RuntimeError("mapping failure")

    monkeypatch.setattr(capability_policy, "match_menu_skill", fail)
    with pytest.raises(RuntimeError, match="mapping failure"):
        capability_policy.validate_selection(mapping.skill_name, menu)


@pytest.mark.parametrize(
    ("module", "blocked"),
    [("data_analysis.graph", "basic_qa"), ("basic_qa.graph", "data_analysis")],
)
def test_graph_import_and_compile_without_other_graph(module, blocked):
    code = """
import importlib.abc
import sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == sys.argv[2] or fullname.startswith(sys.argv[2] + "."):
            raise ImportError("unexpected cross-graph import: " + fullname)
sys.meta_path.insert(0, Deny())
module = __import__(sys.argv[1], fromlist=["graph"])
assert module.graph({}) is not None
assert not any(name == sys.argv[2] or name.startswith(sys.argv[2] + ".") for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, module, blocked],
        cwd=Path(__file__).resolve().parents[3],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src"),
        },
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_context_controllers_and_recovery_are_independent(monkeypatch):
    import basic_qa.context.preparation as basic_preparation
    import basic_qa.context.recovery as basic_recovery
    import data_analysis.context.preparation as data_preparation
    import data_analysis.context.recovery as data_recovery

    original_basic_summary = basic_preparation.summarize_conversation_history
    original_basic_recovery = basic_recovery.prepare_model_request
    marker = object()
    monkeypatch.setattr(data_preparation, "summarize_conversation_history", marker)
    monkeypatch.setattr(data_recovery, "prepare_model_request", marker)
    assert basic_preparation.summarize_conversation_history is original_basic_summary
    assert basic_recovery.prepare_model_request is original_basic_recovery
    assert basic_preparation.PreparedRequest is not data_preparation.PreparedRequest
    other_marker = object()
    monkeypatch.setattr(
        basic_preparation, "summarize_conversation_history", other_marker
    )
    monkeypatch.setattr(basic_recovery, "prepare_model_request", other_marker)
    assert data_preparation.summarize_conversation_history is marker
    assert data_recovery.prepare_model_request is marker
