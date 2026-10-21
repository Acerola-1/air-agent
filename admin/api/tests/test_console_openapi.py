"""运营后台 OpenAPI 契约：路由、错误语义相关字段与写操作 DTO 形状."""

from __future__ import annotations

from app.main import app


def test_console_paths_are_registered() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    expected = {
        "/agent/console/overview",
        "/agent/console/trends",
        "/agent/console/users",
        "/agent/console/runs",
        "/agent/console/runs/{attempt_id}",
        "/agent/console/requests/{request_id}/content",
        "/agent/console/answers/{answer_id}/content",
        "/agent/console/feedback",
        "/agent/console/feedback/{answer_id}/history",
        "/agent/console/feedback/{answer_id}/content",
        "/agent/console/feedback/{answer_id}/actions",
        "/agent/console/capabilities",
        "/agent/console/capabilities/details",
        "/agent/console/performance",
        "/agent/console/costs",
    }
    assert expected.issubset(paths)
    # 旧端点注册没有被本 change 移除。
    assert "/agent/runs" in paths
    assert "/agent/feedback/answers/{answer_id}" in paths


def test_console_action_dto_contract() -> None:
    schema = app.openapi()
    action = schema["paths"]["/agent/console/feedback/{answer_id}/actions"]["post"]
    body_ref = action["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    components = schema["components"]["schemas"]
    body = components[body_ref.rsplit("/", 1)[-1]]
    assert set(body["required"]) >= {
        "operation_id",
        "expected_revision",
        "expected_feedback_revision",
        "action",
    }
    assert body["properties"]["action"]["enum"] == ["start", "resolve", "reopen"]


def test_console_content_parameters_are_bounded() -> None:
    schema = app.openapi()
    answer = schema["paths"]["/agent/console/answers/{answer_id}/content"]["get"]
    params = {item["name"]: item for item in answer["parameters"]}
    assert params["content_version"]["required"] is True
    limit_schema = params["limit"]["schema"]
    assert any(item.get("maximum") == 8000 for item in limit_schema.get("anyOf", [limit_schema]))


def test_console_responses_have_explicit_dtos() -> None:
    schema = app.openapi()
    for path, item in schema["paths"].items():
        if not path.startswith("/agent/console/"):
            continue
        for method in ("get", "post"):
            if method not in item:
                continue
            operation = item[method]
            success = operation["responses"]["200"]["content"]["application/json"]["schema"]
            assert "$ref" in success, (path, success)
            for code in ("400", "401", "403", "404", "409", "422", "503"):
                assert code in operation["responses"], (path, code)


def test_console_generated_contracts_are_current() -> None:
    from scripts.export_console_contract import outputs

    for path, expected in outputs().items():
        assert path.read_text() == expected, f"契约副本未同步: {path.name}"
