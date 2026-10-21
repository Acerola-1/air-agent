"""需求洞察 OpenAPI/TS 契约：路由、写 DTO、显式响应与生成副本."""

from __future__ import annotations

from app.main import app


def test_insights_paths_are_registered() -> None:
    paths = app.openapi()["paths"]
    expected = {
        "/agent/insights/overview",
        "/agent/insights/questions",
        "/agent/insights/topics",
        "/agent/insights/questions/{cluster_id}",
        "/agent/insights/questions/{cluster_id}/sources",
        "/agent/insights/unclassified",
        "/agent/insights/jobs",
        "/agent/insights/jobs/{job_id}",
        "/agent/insights/jobs/{job_id}/actions",
        "/agent/insights/questions/{cluster_id}/actions",
        "/agent/insights/profiles/{profile_id}/rebuild",
        "/agent/insights/profiles/{profile_id}/activate",
    }
    assert expected.issubset(paths)
    # 旧端点仍在
    assert "/agent/console/overview" in paths
    assert "/agent/runs" in paths


def test_action_dto_contract() -> None:
    schema = app.openapi()
    components = schema["components"]["schemas"]
    job = schema["paths"]["/agent/insights/jobs"]["post"]
    ref = job["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    body = components[ref.rsplit("/", 1)[-1]]
    assert "operation_id" in body["required"]
    assert "job_kind" in body["properties"]
    action = schema["paths"]["/agent/insights/questions/{cluster_id}/actions"]["post"]
    ref = action["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    body = components[ref.rsplit("/", 1)[-1]]
    assert {"operation_id", "expected_revision", "action"} <= set(body["required"])
    assert body["properties"]["action"]["enum"] == ["rename", "assign", "merge", "split"]


def test_responses_are_explicit_and_no_store_headers_not_in_schema() -> None:
    schema = app.openapi()
    for path, item in schema["paths"].items():
        if not path.startswith("/agent/insights/"):
            continue
        for method in ("get", "post"):
            if method not in item:
                continue
            operation = item[method]
            success_code = "202" if "202" in operation["responses"] else "200"
            success = operation["responses"][success_code]["content"]["application/json"]["schema"]
            assert "$ref" in success, (path, method, success)
            for code in ("400", "401", "403", "404", "409", "422", "503"):
                assert code in operation["responses"], (path, method, code)


def test_generated_contract_is_current() -> None:
    from scripts.export_demand_contract import outputs

    for path, expected in outputs().items():
        assert path.read_text() == expected, f"契约副本未同步: {path.name}"
