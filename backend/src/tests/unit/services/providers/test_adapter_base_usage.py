"""Regression tests for BaseProviderAdapter usage-tracking helpers.

Guards the SHU-700 JSON-safety invariant: `self.usage` must round-trip
cleanly through JSON (for persistence into `Message.message_metadata` and
SSE event serialization), while preserving Decimal precision for cost.

The fix stores `cost` as a stringified Decimal and special-cases the
`cost` key in `_aggregate_usage` so cross-cycle accumulation (tool-use
loops) sums with Decimal precision rather than string concatenation.
"""

import json
from decimal import Decimal

import pytest

from shu.core.safe_decimal import safe_decimal
from shu.services.providers.adapter_base import BaseProviderAdapter


def _make_adapter() -> BaseProviderAdapter:
    """Build a bare BaseProviderAdapter instance without running __init__.

    The usage helpers (_get_usage, _update_usage, _aggregate_usage) don't
    need provider/db/settings state — they just operate on `self.usage`.
    Skipping __init__ keeps the test free of encryption-key / settings
    dependencies.
    """
    adapter = BaseProviderAdapter.__new__(BaseProviderAdapter)
    adapter.usage = {}
    return adapter


class TestGetUsageShape:
    def test_without_cost_omits_cost_key(self):
        adapter = _make_adapter()
        usage = adapter._get_usage(10, 20, 0, 0, 30)
        assert "cost" not in usage
        assert usage == {
            "input_tokens": 10,
            "output_tokens": 20,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 30,
        }

    def test_with_cost_stores_string(self):
        adapter = _make_adapter()
        usage = adapter._get_usage(10, 20, 0, 0, 30, cost=Decimal("0.042"))
        assert usage["cost"] == "0.042"
        assert isinstance(usage["cost"], str), (
            "cost must be stored as str to stay JSON-serializable; storing "
            "it as Decimal broke message_metadata persistence in SHU-700"
        )

    def test_cost_precision_is_preserved_through_round_trip(self):
        """Small-fraction costs (typical for single-token requests) survive intact.

        Note: ``str(Decimal("0.000000015"))`` is ``"1.5E-8"`` (Python's canonical
        representation switches to scientific notation for small numbers). Values
        round-trip losslessly via Decimal() parsing, so only value-equality matters;
        the exact string form does not.
        """
        adapter = _make_adapter()
        usage = adapter._get_usage(1, 1, 0, 0, 2, cost=Decimal("0.000000015"))
        assert safe_decimal(usage["cost"]) == Decimal("0.000000015")


class TestUsageDictJsonRoundTrip:
    """The load-bearing invariant: usage dicts must survive json.dumps/loads."""

    def test_usage_with_cost_survives_json_dumps(self):
        adapter = _make_adapter()
        usage = adapter._get_usage(100, 50, 10, 0, 150, cost=Decimal("0.00123"))

        # If Decimal ever leaks back in, json.dumps raises TypeError.
        serialized = json.dumps(usage)
        deserialized = json.loads(serialized)

        assert deserialized["input_tokens"] == 100
        assert deserialized["output_tokens"] == 50
        # Precise round-trip via safe_decimal (the actual path used by callers).
        assert safe_decimal(deserialized["cost"]) == Decimal("0.00123")

    def test_usage_without_cost_survives_json_dumps(self):
        adapter = _make_adapter()
        usage = adapter._get_usage(5, 10, 0, 0, 15)
        # No cost key → plain int dict, trivially JSON-safe.
        assert json.loads(json.dumps(usage)) == usage

    def test_aggregated_usage_stays_json_safe(self):
        """After _aggregate_usage, cost must still be a string — not a Decimal."""
        adapter = _make_adapter()
        first = adapter._get_usage(10, 20, 0, 0, 30, cost=Decimal("0.01"))
        second = adapter._get_usage(5, 15, 0, 0, 20, cost=Decimal("0.02"))

        aggregated = adapter._aggregate_usage(first, second)
        assert isinstance(aggregated["cost"], str)
        # json.dumps is the real regression test — it raises on Decimal.
        serialized = json.dumps(aggregated)
        deserialized = json.loads(serialized)
        assert safe_decimal(deserialized["cost"]) == Decimal("0.03")


class TestAggregateUsage:
    def test_tokens_sum_as_ints(self):
        adapter = _make_adapter()
        first = adapter._get_usage(10, 20, 5, 0, 30)
        second = adapter._get_usage(3, 7, 2, 1, 10)
        result = adapter._aggregate_usage(first, second)
        assert result["input_tokens"] == 13
        assert result["output_tokens"] == 27
        assert result["cached_tokens"] == 7
        assert result["reasoning_tokens"] == 1
        assert result["total_tokens"] == 40

    def test_cost_sums_with_decimal_precision(self):
        """Critical: two cost-bearing cycles (tool-use loop) must sum losslessly."""
        adapter = _make_adapter()
        first = adapter._get_usage(10, 20, 0, 0, 30, cost=Decimal("0.000000015"))
        second = adapter._get_usage(10, 20, 0, 0, 30, cost=Decimal("0.000000020"))

        result = adapter._aggregate_usage(first, second)
        # If the aggregator concatenated strings instead of summing, this'd be
        # "0.0000000150.000000020". If it used float, we'd drift.
        assert safe_decimal(result["cost"]) == Decimal("0.000000035")

    def test_cost_in_only_one_dict_still_sums(self):
        """Missing cost on one side defaults to 0 — doesn't lose the other side's value."""
        adapter = _make_adapter()
        first = adapter._get_usage(10, 20, 0, 0, 30, cost=Decimal("0.042"))
        second = adapter._get_usage(5, 5, 0, 0, 10)  # no cost

        result = adapter._aggregate_usage(first, second)
        assert safe_decimal(result["cost"]) == Decimal("0.042")


class TestUpdateUsageStatefulAccumulation:
    """_update_usage mutates self.usage — verify the aggregation lands correctly."""

    def test_first_update_seeds_dict(self):
        adapter = _make_adapter()
        adapter._update_usage(100, 50, 0, 0, 150, cost=Decimal("0.01"))
        assert adapter.usage["input_tokens"] == 100
        assert safe_decimal(adapter.usage["cost"]) == Decimal("0.01")

    def test_second_update_aggregates(self):
        adapter = _make_adapter()
        adapter._update_usage(100, 50, 0, 0, 150, cost=Decimal("0.01"))
        adapter._update_usage(200, 75, 0, 0, 275, cost=Decimal("0.02"))

        assert adapter.usage["input_tokens"] == 300
        assert adapter.usage["output_tokens"] == 125
        assert adapter.usage["total_tokens"] == 425
        assert safe_decimal(adapter.usage["cost"]) == Decimal("0.03")

        # Regression: the accumulated dict must still be JSON-safe.
        json.dumps(adapter.usage)

    @pytest.mark.parametrize(
        ("first_cost", "second_cost", "expected"),
        [
            # Common accumulation patterns from real OpenRouter responses.
            (Decimal("0.000001"), Decimal("0.000002"), Decimal("0.000003")),
            (Decimal("1.5"), Decimal("0.5"), Decimal("2.0")),
            # Zero cost on either side.
            (Decimal("0"), Decimal("0.042"), Decimal("0.042")),
            (Decimal("0.042"), Decimal("0"), Decimal("0.042")),
        ],
    )
    def test_cost_accumulation_parametrized(self, first_cost, second_cost, expected):
        adapter = _make_adapter()
        adapter._update_usage(1, 1, 0, 0, 2, cost=first_cost)
        adapter._update_usage(1, 1, 0, 0, 2, cost=second_cost)
        assert safe_decimal(adapter.usage["cost"]) == expected
