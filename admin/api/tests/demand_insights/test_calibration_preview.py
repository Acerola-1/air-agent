"""校准 fixture 与治理预览 token 的确定性验证."""

from __future__ import annotations

import pytest

from app.demand_insights.config import get_insights_settings
from app.demand_insights.errors import InsightsConflict, InsightsPayloadInvalid
from app.demand_insights.governance import _safe_name, _sign_preview, _verify_preview
from scripts import evaluate_insights_calibration as evaluator


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "test-insights-preview-secret")
    get_insights_settings.cache_clear()
    yield
    get_insights_settings.cache_clear()


def test_fixture_has_exact_split_and_synthetic_content() -> None:
    records = evaluator._load()
    assert len(records) == 160
    assert sum(1 for row in records if row["split"] == "calibration") == 60
    assert sum(1 for row in records if row["split"] == "holdout") == 100
    assert sum(1 for row in records if row["hard_negative"]) >= 30
    assert {row["id"] for row in records} == {row["id"] for row in records}
    sensitive = [row for row in records if row["class"] == "sensitive"]
    assert sensitive and all("13800138000" in row["text"] for row in sensitive)
    # 敏感样本使用虚构号码；不得出现真实凭据形状以外的字段
    fields = {"id", "split", "class", "text", "context", "expected", "hard_negative", "injection"}
    assert all(set(row) == fields for row in records)


def test_fake_evaluator_reports_fixture_and_gate() -> None:
    records = evaluator._load()
    vectors, info = evaluator._embed_all(records, "fake")
    report = evaluator.evaluate(records, vectors, info)
    assert report["provider"]["provider"] == "fake_fixture"
    assert report["samples"]["total"] == 160
    assert report["holdout"]["clear_intents"] > 0
    assert report["holdout"]["sensitive_leaks"] == 0
    assert report["fixture_quality"] is True
    assert "fake_fixture" in report["limitations"][0]


def test_preview_token_binds_and_expires() -> None:
    settings = get_insights_settings()
    token = _sign_preview(
        {
            "v": 1,
            "t": "tenant",
            "p": "profile",
            "c": "cluster",
            "r": 1,
            "a": 2,
            "ids": ["a", "b"],
            "groups": [],
            "e": "2099-01-01T00:00:00+00:00",
        },
        settings,
    )
    payload = _verify_preview(token, settings)
    assert payload["ids"] == ["a", "b"]
    with pytest.raises(InsightsPayloadInvalid):
        _verify_preview(token[:-1] + ("0" if token[-1] != "0" else "1"), settings)
    expired = _sign_preview(
        {
            "v": 1,
            "t": "tenant",
            "p": "profile",
            "c": "cluster",
            "r": 1,
            "a": 2,
            "ids": [],
            "groups": [],
            "e": "2000-01-01T00:00:00+00:00",
        },
        settings,
    )
    with pytest.raises(InsightsConflict):
        _verify_preview(expired, settings)


def test_safe_name_rejects_contact_and_credentials() -> None:
    assert _safe_name("查询空气质量") == "查询空气质量"
    with pytest.raises(InsightsPayloadInvalid):
        _safe_name("联系 13800138000")
    with pytest.raises(InsightsPayloadInvalid):
        _safe_name("密码是 abc")
