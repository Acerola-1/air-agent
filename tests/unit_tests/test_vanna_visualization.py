"""Text2SQL 图表类型确定性规则测试."""

from __future__ import annotations

import pandas as pd

from common.config.vanna_sql_adapter import VannaOllamaSqlAdapter


class _VannaAdapterUnderTest(VannaOllamaSqlAdapter):
    def add_ddl(self, ddl: str, **kwargs) -> str | None:
        return None

    def add_documentation(self, documentation: str, **kwargs) -> str | None:
        return None

    def add_question_sql(self, question: str, sql: str, **kwargs) -> str | None:
        return None

    def generate_embedding(self, data: str, **kwargs):
        return []

    def get_related_ddl(self, question: str, **kwargs):
        return []

    def get_related_documentation(self, question: str, **kwargs):
        return []

    def get_similar_question_sql(self, question: str, **kwargs):
        return []

    def get_training_data(self, **kwargs):
        return pd.DataFrame()

    def remove_training_data(self, id: str, **kwargs) -> bool:
        return True


def _adapter() -> VannaOllamaSqlAdapter:
    adapter = _VannaAdapterUnderTest.__new__(_VannaAdapterUnderTest)

    def fail_submit_prompt(*_args, **_kwargs):
        raise AssertionError("deterministic visualization should not call LLM")

    adapter.submit_prompt = fail_submit_prompt  # type: ignore[method-assign]
    return adapter


def test_visualization_type_none_for_single_value_result() -> None:
    df = pd.DataFrame({"aqi": [42]})

    result = _adapter().determine_visualization_type(df=df)

    assert result == "none"


def test_visualization_type_table_for_large_result() -> None:
    df = pd.DataFrame(
        {"region_name": [f"城市{i}" for i in range(26)], "aqi": range(26)}
    )

    result = _adapter().determine_visualization_type(df=df)

    assert result == "table"


def test_visualization_type_line_for_time_numeric_result() -> None:
    df = pd.DataFrame(
        {
            "时间": pd.date_range("2026-05-01", periods=3),
            "pm25": [10, 12, 14],
        }
    )

    result = _adapter().determine_visualization_type(df=df)

    assert result == "line"


def test_visualization_type_bar_for_category_numeric_result() -> None:
    df = pd.DataFrame({"城市": ["郑州", "洛阳", "开封"], "aqi": [80, 65, 70]})

    result = _adapter().determine_visualization_type(df=df)

    assert result == "bar"


def test_visualization_type_table_for_ambiguous_result_without_llm() -> None:
    df = pd.DataFrame({"城市": ["郑州", "洛阳", "开封"], "级别": ["良", "优", "良"]})

    result = _adapter().determine_visualization_type(df=df)

    assert result == "table"
