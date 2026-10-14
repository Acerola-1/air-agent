"""版本化模型价格与 Decimal 成本计算（spec agent-cost-accounting）.

语义约束：

- 价格版本不可变；按 (tenant, provider, model, **调用开始时间**) 匹配半开区间
  [effective_from, effective_to)；重叠由迁移排他约束拒绝。
- 分项：输入非缓存 = input - cache_read - cache_write（缓存/推理是 usage 子集，
  绝不重复计入总量）；缓存读/写按独立费率；推理默认 included_in_output（随输
  出价，不重复收费），independent 模式按独立费率。
- 非法 usage（子集越界、负数）→ invalid_usage，费用未知且不静默裁零。
- 缺 usage → usage_missing；缺价格 → price_unknown；一律 NULL，不用 0 冒充。
- 聚合按币种分组；已知小计与覆盖（已计价/缺usage/缺价）分列；真实零费用需要
  明确价格与计费证据（amount=0 且 status=measured）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

# 每百万 token 计费单位（unit='1m_tokens'）
_UNIT = Decimal(1_000_000)


class PriceRecord(Protocol):
    """价格版本记录形状（存储层实现；测试用 fixture 替身）."""

    price_ref: str
    provider: str
    model: str
    currency: str
    unit: str
    input_per_unit: Decimal
    output_per_unit: Decimal
    cache_read_per_unit: Decimal | None
    cache_write_per_unit: Decimal | None
    reasoning_mode: str
    reasoning_per_unit: Decimal | None
    effective_from: dt.datetime
    effective_to: dt.datetime | None


@dataclass(frozen=True)
class CostResult:
    """单次观测的计费结果：显式区分已测 / 缺失 / 非法."""

    observation_id: str
    measurement_status: str  # measured | usage_missing | invalid_usage | price_unknown
    amount: Decimal | None
    currency: str | None
    price_ref: str | None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_event_payload(self) -> dict[str, Any]:
        """runtime.cost 事件载荷（未知不伪造 amount=0）."""
        payload: dict[str, Any] = {
            "measurement_status": self.measurement_status,
        }
        if self.amount is not None:
            payload["amount"] = format(self.amount, "f")
        if self.currency:
            payload["currency"] = self.currency
        if self.price_ref:
            payload["price_ref"] = self.price_ref
        return payload


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def select_price(
    prices: list[PriceRecord],
    *,
    provider: str,
    model: str,
    called_at: dt.datetime,
) -> PriceRecord | None:
    """按实际 provider/model 与调用开始时间选价格版本（半开区间；歧义拒绝）."""
    called = called_at if called_at.tzinfo else called_at.replace(tzinfo=dt.UTC)
    matches = []
    for price in prices:
        if price.provider != provider or price.model != model:
            continue
        start = price.effective_from
        if start.tzinfo is None:
            start = start.replace(tzinfo=dt.UTC)
        if called < start:
            continue
        if price.effective_to is not None:
            end = price.effective_to
            if end.tzinfo is None:
                end = end.replace(tzinfo=dt.UTC)
            if called >= end:
                continue
        matches.append(price)
    if len(matches) != 1:
        # 0 个 = price_unknown；>1 个 = 配置歧义（同样拒绝计价，宁可未知）
        return None
    return matches[0]


def compute_cost(
    price: PriceRecord | None,
    *,
    observation_id: str,
    usage: dict[str, Any],
    measurement_status: str,
) -> CostResult:
    """按价格版本与受限 usage 计算单次费用（Decimal；缺失/非法不伪造）."""
    if measurement_status != "measured":
        return CostResult(
            observation_id=observation_id,
            measurement_status=measurement_status,
            amount=None,
            currency=None,
            price_ref=None,
        )
    for value in usage.values():
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            return CostResult(observation_id, "invalid_usage", None, None, None)
    values = {
        key: _as_int(usage.get(key))
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        )
    }
    input_tokens = values["input_tokens"]
    output_tokens = values["output_tokens"]
    if input_tokens is None and output_tokens is None:
        # 完全没有任何计数 → usage_missing；部分计数（如失败尝试只有输入）
        # 仍按已测部分计费（spec 重试场景：首失败 10 输入计入总消耗）
        return CostResult(
            observation_id=observation_id,
            measurement_status="usage_missing",
            amount=None,
            currency=None,
            price_ref=None,
        )
    cache_read = values["cache_read_tokens"] or 0
    cache_write = values["cache_write_tokens"] or 0
    reasoning = values["reasoning_tokens"] or 0
    # 非法 usage：子集越界 / 推理大于输出（子集关系由价格语义声明）
    invalid_cache = cache_read + cache_write > (input_tokens or 0)
    invalid_reasoning = reasoning > (output_tokens or 0)
    if invalid_cache or invalid_reasoning:
        return CostResult(
            observation_id=observation_id,
            measurement_status="invalid_usage",
            amount=None,
            currency=None,
            price_ref=None,
        )
    if price is None:
        return CostResult(
            observation_id=observation_id,
            measurement_status="price_unknown",
            amount=None,
            currency=None,
            price_ref=None,
        )
    unit = _UNIT if price.unit == "1m_tokens" else Decimal(1)
    input_plain = (input_tokens or 0) - cache_read - cache_write
    independent_reasoning = reasoning and price.reasoning_mode == "independent"
    if (
        (cache_read and price.cache_read_per_unit is None)
        or (cache_write and price.cache_write_per_unit is None)
        or (independent_reasoning and price.reasoning_per_unit is None)
    ):
        return CostResult(observation_id, "price_unknown", None, None, price.price_ref)
    amount = Decimal(input_plain) * (price.input_per_unit or Decimal(0)) / unit
    amount += Decimal(cache_read) * (price.cache_read_per_unit or Decimal(0)) / unit
    amount += Decimal(cache_write) * (price.cache_write_per_unit or Decimal(0)) / unit
    billed_output = (output_tokens or 0) - (
        reasoning if price.reasoning_mode == "independent" else 0
    )
    amount += Decimal(billed_output) * (price.output_per_unit or Decimal(0)) / unit
    if price.reasoning_mode == "independent":
        amount += Decimal(reasoning) * (price.reasoning_per_unit or Decimal(0)) / unit
    return CostResult(
        observation_id=observation_id,
        measurement_status="measured",
        amount=amount.quantize(Decimal("0.0000000001")),
        currency=price.currency,
        price_ref=price.price_ref,
        detail={
            "complete_usage": input_tokens is not None and output_tokens is not None,
            "missing_components": [
                k for k in ("input_tokens", "output_tokens") if values[k] is None
            ],
            "input_plain": input_plain,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "output": output_tokens,
            "reasoning": reasoning if price.reasoning_mode == "independent" else 0,
        },
    )


@dataclass(frozen=True)
class CostSummary:
    """聚合小计：已知小计与缺失覆盖分列（不同币种不相加）."""

    by_currency: dict[str, Decimal]
    total_observations: int
    priced: int
    usage_missing: int
    price_unknown: int
    invalid_usage: int
    zero_cost_measured: int


def summarize_costs(results: list[CostResult]) -> CostSummary:
    """按币种分组聚合；已知小计不含未知项（不用 0 顶替未知）."""
    by_currency: dict[str, Decimal] = {}
    priced = usage_missing = price_unknown = invalid_usage = zero_measured = 0
    for result in results:
        if result.measurement_status == "measured":
            if result.amount is None:
                continue
            priced += 1
            if result.amount == 0:
                zero_measured += 1
            currency = result.currency or "UNKNOWN"
            previous = by_currency.get(currency, Decimal(0))
            by_currency[currency] = previous + result.amount
        elif result.measurement_status == "usage_missing":
            usage_missing += 1
        elif result.measurement_status == "price_unknown":
            price_unknown += 1
        elif result.measurement_status == "invalid_usage":
            invalid_usage += 1
    return CostSummary(
        by_currency=by_currency,
        total_observations=len(results),
        priced=priced,
        usage_missing=usage_missing,
        price_unknown=price_unknown,
        invalid_usage=invalid_usage,
        zero_cost_measured=zero_measured,
    )
