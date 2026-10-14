"""价格与成本单元测试（fixture 驱动，不依赖数据库）.

对齐 spec agent-cost-accounting 的确定性场景：
- 缓存/推理子集：1000 含缓存 400 / 输出 200 含推理 50 → 输入普通计 600
- 价格切换边界：T 前后调用分别匹配旧/新版本
- 非法 usage：子集越界 → invalid_usage，费用未知不裁零
- 缺 usage / 缺价格 → 分列，不用 0 冒充
- 多 Skill 共用模型调用 → 成本只计一次（守恒由聚合层保证）
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from common.observability.pricing import (
    CostResult,
    compute_cost,
    select_price,
    summarize_costs,
)


@dataclass
class FakePrice:
    price_ref: str
    provider: str = "p"
    model: str = "m"
    currency: str = "CNY"
    unit: str = "1m_tokens"
    input_per_unit: Decimal = Decimal("2.0")
    output_per_unit: Decimal = Decimal("8.0")
    cache_read_per_unit: Decimal | None = Decimal("0.2")
    cache_write_per_unit: Decimal | None = None
    reasoning_mode: str = "included_in_output"
    reasoning_per_unit: Decimal | None = None
    effective_from: dt.datetime = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    effective_to: dt.datetime | None = None


def _ts(hour: int) -> dt.datetime:
    return dt.datetime(2026, 10, 13, hour, tzinfo=dt.UTC)


class TestSubsetBilling:
    def test_cache_and_reasoning_subsets(self) -> None:
        price = FakePrice(price_ref="pv-1")
        result = compute_cost(
            price,
            observation_id="o1",
            usage={
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_read_tokens": 400,
                "reasoning_tokens": 50,
            },
            measurement_status="measured",
        )
        assert result.measurement_status == "measured"
        # 输入普通 600 * 2/1M + 缓存 400 * 0.2/1M + 输出 200 * 8/1M
        expected = (
            Decimal(600) * Decimal("2.0") / Decimal(1_000_000)
            + Decimal(400) * Decimal("0.2") / Decimal(1_000_000)
            + Decimal(200) * Decimal("8.0") / Decimal(1_000_000)
        )
        assert result.amount == expected.quantize(Decimal("0.000001"))
        # 推理不重复收费（included_in_output）
        assert result.detail["reasoning"] == 0

    def test_retry_usage_billed_per_attempt_not_duplicated(self) -> None:
        """重试两次各 10 输入：两次独立计费 25 tokens 的费用（1逻辑/2尝试）."""
        price = FakePrice(price_ref="pv-1")
        first = compute_cost(
            price,
            observation_id="a",
            usage={"input_tokens": 10},
            measurement_status="measured",
        )
        second = compute_cost(
            price,
            observation_id="b",
            usage={"input_tokens": 10, "output_tokens": 5},
            measurement_status="measured",
        )
        summary = summarize_costs([first, second])
        assert summary.priced == 2
        assert summary.by_currency["CNY"] == first.amount + second.amount

    def test_invalid_subset_is_not_clamped(self) -> None:
        price = FakePrice(price_ref="pv-1")
        result = compute_cost(
            price,
            observation_id="o2",
            usage={"input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 400},
            measurement_status="measured",
        )
        assert result.measurement_status == "invalid_usage"
        assert result.amount is None

    def test_negative_usage_invalid(self) -> None:
        price = FakePrice(price_ref="pv-1")
        result = compute_cost(
            price,
            observation_id="o3",
            usage={"input_tokens": -5},
            measurement_status="measured",
        )
        assert result.measurement_status == "invalid_usage"

    def test_missing_usage_not_zero(self) -> None:
        price = FakePrice(price_ref="pv-1")
        result = compute_cost(
            price, observation_id="o4", usage={}, measurement_status="usage_missing"
        )
        assert result.amount is None
        assert result.measurement_status == "usage_missing"

    def test_unknown_price_not_zero(self) -> None:
        result = compute_cost(
            None,
            observation_id="o5",
            usage={"input_tokens": 10, "output_tokens": 5},
            measurement_status="measured",
        )
        assert result.measurement_status == "price_unknown"
        assert result.amount is None

    def test_real_zero_requires_evidence(self) -> None:
        price = FakePrice(
            price_ref="pv-free", input_per_unit=Decimal(0), output_per_unit=Decimal(0)
        )
        result = compute_cost(
            price,
            observation_id="o6",
            usage={"input_tokens": 10, "output_tokens": 5},
            measurement_status="measured",
        )
        assert result.amount == Decimal("0.000000")
        assert result.measurement_status == "measured"


class TestPriceSelection:
    def test_boundary_switch_at_t(self) -> None:
        old = FakePrice(price_ref="pv-old", effective_from=_ts(0), effective_to=_ts(12))
        new = FakePrice(price_ref="pv-new", effective_from=_ts(12), effective_to=None)
        prices = [old, new]
        assert (
            select_price(prices, provider="p", model="m", called_at=_ts(11)).price_ref
            == "pv-old"
        )
        # 半开区间：T 时刻起用新版本
        assert (
            select_price(prices, provider="p", model="m", called_at=_ts(12)).price_ref
            == "pv-new"
        )

    def test_no_price_unknown(self) -> None:
        assert select_price([], provider="p", model="m", called_at=_ts(1)) is None

    def test_overlapping_is_ambiguous_rejected(self) -> None:
        a = FakePrice(price_ref="pv-a", effective_from=_ts(0), effective_to=_ts(12))
        b = FakePrice(price_ref="pv-b", effective_from=_ts(6), effective_to=_ts(18))
        assert select_price([a, b], provider="p", model="m", called_at=_ts(8)) is None

    def test_model_mismatch_no_match(self) -> None:
        price = FakePrice(price_ref="pv-1", model="other")
        assert select_price([price], provider="p", model="m", called_at=_ts(1)) is None


class TestAggregation:
    def test_summary_splits_known_and_missing(self) -> None:
        results = [
            CostResult("o1", "measured", Decimal("0.01"), "CNY", "p1"),
            CostResult("o2", "measured", Decimal("0.02"), "CNY", "p1"),
            CostResult("o3", "usage_missing", None, None, None),
        ]
        summary = summarize_costs(results)
        assert summary.by_currency["CNY"] == Decimal("0.03")
        assert summary.priced == 2
        assert summary.usage_missing == 1
        assert summary.total_observations == 3

    def test_currencies_not_merged(self) -> None:
        results = [
            CostResult("o1", "measured", Decimal("0.01"), "CNY", "p1"),
            CostResult("o2", "measured", Decimal("0.02"), "USD", "p2"),
        ]
        summary = summarize_costs(results)
        assert set(summary.by_currency) == {"CNY", "USD"}

    def test_shared_model_call_counted_once(self) -> None:
        """多 Skill 共用同一次模型调用：聚合输入里只有一条观测记录."""
        single = CostResult("obs-shared", "measured", Decimal("0.05"), "CNY", "p1")
        summary = summarize_costs([single])
        assert summary.by_currency["CNY"] == Decimal("0.05")
        assert summary.total_observations == 1
