"""Regression tests for BaseProviderAdapter usage-tracking helpers.

Guards the SHU-700 JSON-safety invariant: `self.usage` must round-trip
cleanly through JSON (for persistence into `Message.message_metadata` and
SSE event serialization), while preserving Decimal precision for cost.

The fix stores `cost` as a stringified Decimal and special-cases the
`cost` key in `_aggregate_usage` so cross-cycle accumulation (tool-use
loops) sums with Decimal precision rather than string concatenation.

Also covers SHU-802 `get_partial_usage_snapshot()`: the partial-usage
capture path read by `_call_provider` on terminate / shutdown so the
interrupted-stream `LLMUsage` row carries real token counts when the
provider had emitted usage events before the break.
"""

import json
from decimal import Decimal

import pytest

from shu.core.safe_decimal import safe_decimal
from shu.services.providers.adapter_base import BaseProviderAdapter
from shu.services.providers.adapters.anthropic_adapter import AnthropicAdapter
from shu.services.providers.adapters.completions_adapter import CompletionsAdapter
from shu.services.providers.adapters.gemini_adapter import GeminiAdapter


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


# SHU-802: ``get_partial_usage_snapshot()`` is what `_call_provider` reads
# on the terminate path to capture provider-emitted usage that landed
# before the break. The base adapter just returns a copy of ``self.usage``
# (correct for adapters that update usage eagerly inside
# ``handle_provider_event`` like responses_adapter). Deferred-extract
# adapters (completions, gemini, anthropic) MUST override to flush their
# stashed ``latest_usage_event`` / ``_latest_usage_event`` into
# ``self.usage`` first, otherwise the most recent provider-emitted usage
# is silently dropped on terminate — that was the AC10 violation Codex
# flagged before this fix.


def _make_completions_adapter() -> CompletionsAdapter:
    """Build a bare CompletionsAdapter without running its full __init__.

    The snapshot tests only need ``self.usage`` and ``self.latest_usage_event``
    to exist; skipping the full constructor keeps the test free of
    encryption-key / settings / provider dependencies.
    """
    adapter = CompletionsAdapter.__new__(CompletionsAdapter)
    adapter.usage = {}
    adapter.latest_usage_event = None
    return adapter


def _make_gemini_adapter() -> GeminiAdapter:
    adapter = GeminiAdapter.__new__(GeminiAdapter)
    adapter.usage = {}
    adapter._latest_usage_event = None
    return adapter


def _make_anthropic_adapter() -> AnthropicAdapter:
    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    adapter.usage = {}
    adapter._latest_usage_event = None
    return adapter


class TestBasePartialUsageSnapshot:
    """SHU-802: base adapter snapshot — eager-extract path (responses_adapter)."""

    def test_empty_adapter_returns_empty_dict(self):
        """A fresh adapter that's never seen a usage event returns ``{}`` —
        NOT ``None``. ``_call_provider`` then sets ``partial_usage_unavailable=True``
        because ``not {}`` evaluates True."""
        adapter = _make_adapter()
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot == {}
        assert isinstance(snapshot, dict)

    def test_returns_current_usage_when_populated(self):
        """For an eager-extract adapter, ``self.usage`` already reflects
        the provider-emitted usage at any point. Snapshot returns it
        directly. This is the responses_adapter happy path — terminate
        mid-stream, snapshot carries the real numbers."""
        adapter = _make_adapter()
        adapter._update_usage(10, 20, 0, 0, 30)
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot == {
            "input_tokens": 10,
            "output_tokens": 20,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 30,
        }

    def test_returns_a_copy_not_a_reference(self):
        """Callers must not be able to corrupt the adapter's internal
        state by mutating the snapshot. ``_call_provider`` passes the
        snapshot through to the VariantStreamResult, which flows into
        finalize and persists as ``Message.message_metadata`` — any
        downstream mutation must not bleed back into the adapter."""
        adapter = _make_adapter()
        adapter._update_usage(10, 20, 0, 0, 30)
        snapshot = adapter.get_partial_usage_snapshot()
        snapshot["input_tokens"] = 9999
        assert adapter.usage["input_tokens"] == 10, (
            "snapshot mutation leaked back into adapter.usage — must return a copy"
        )


class TestCompletionsAdapterPartialUsageSnapshot:
    """SHU-802: completions_adapter override flushes the deferred usage."""

    def test_snapshot_flushes_pending_usage_event(self):
        """The OpenAI-style adapter stashes the latest usage chunk in
        ``latest_usage_event`` and only flushes during
        ``finalize_provider_events``. On terminate the consumer loop
        breaks before finalize runs, so the override must call
        ``_extract_usage`` to pull the pending chunk into ``self.usage``
        before the snapshot returns. Without the override, the LLMUsage
        row would record zeros even though the provider had emitted
        real numbers."""
        adapter = _make_completions_adapter()
        adapter.latest_usage_event = {
            "usage": {
                "prompt_tokens": 42,
                "completion_tokens": 17,
                "total_tokens": 59,
            }
        }
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot["input_tokens"] == 42
        assert snapshot["output_tokens"] == 17
        assert snapshot["total_tokens"] == 59
        # And self.usage now reflects the flush — a second snapshot
        # would still see these numbers (idempotent against re-call).
        assert adapter.usage["input_tokens"] == 42

    def test_latest_usage_event_cleared_after_snapshot(self):
        """Idempotency: a second snapshot call must not double-count
        the same usage chunk. The override clears ``latest_usage_event``
        to None after the flush so a subsequent call to either snapshot
        OR finalize_provider_events doesn't roll the same chunk in twice."""
        adapter = _make_completions_adapter()
        adapter.latest_usage_event = {
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}
        }
        adapter.get_partial_usage_snapshot()
        assert adapter.latest_usage_event is None
        # Second snapshot returns the same values (no double-count).
        second = adapter.get_partial_usage_snapshot()
        assert second["input_tokens"] == 5

    def test_no_pending_event_returns_existing_usage(self):
        """When ``latest_usage_event`` is None (e.g. provider never emitted
        a usage chunk before the break), the snapshot just returns
        whatever ``self.usage`` already holds — possibly empty, possibly
        populated from a prior tool-call cycle."""
        adapter = _make_completions_adapter()
        # Pre-populate as if a prior tool-loop cycle had already flushed.
        adapter.usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 150,
        }
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot["input_tokens"] == 100
        assert snapshot["output_tokens"] == 50

    def test_flush_aggregates_with_prior_cycle_usage(self):
        """Multi-tool-call cycles: ``self.usage`` carries the prior
        cycle's totals, and the latest_usage_event holds the current
        cycle's pending chunk. Snapshot aggregates both — that's the
        whole point of routing through ``_update_usage`` rather than
        replacing ``self.usage`` outright."""
        adapter = _make_completions_adapter()
        adapter.usage = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 150,
        }
        adapter.latest_usage_event = {
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}
        }
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot["input_tokens"] == 108  # 100 + 8
        assert snapshot["output_tokens"] == 54  # 50 + 4
        assert snapshot["total_tokens"] == 162  # 150 + 12


class TestGeminiAdapterPartialUsageSnapshot:
    """SHU-802: gemini_adapter override (uses ``_latest_usage_event`` and
    ``usageMetadata`` shape — distinct enough from completions_adapter that
    it needs its own pin)."""

    def test_snapshot_flushes_pending_usage_metadata(self):
        adapter = _make_gemini_adapter()
        adapter._latest_usage_event = {
            "usageMetadata": {
                "promptTokenCount": 30,
                "candidatesTokenCount": 12,
                "thoughtsTokenCount": 7,
                "totalTokenCount": 49,
            }
        }
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot["input_tokens"] == 30
        assert snapshot["output_tokens"] == 12
        assert snapshot["reasoning_tokens"] == 7
        assert snapshot["total_tokens"] == 49

    def test_latest_usage_event_cleared_after_snapshot(self):
        adapter = _make_gemini_adapter()
        adapter._latest_usage_event = {
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            }
        }
        adapter.get_partial_usage_snapshot()
        assert adapter._latest_usage_event is None

    def test_no_pending_event_returns_existing_usage(self):
        adapter = _make_gemini_adapter()
        adapter.usage = {
            "input_tokens": 50,
            "output_tokens": 25,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 75,
        }
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot == adapter.usage
        assert snapshot is not adapter.usage  # still a copy


class TestAnthropicAdapterPartialUsageSnapshot:
    """SHU-802: anthropic_adapter override (uses ``_latest_usage_event`` and
    the cache_read/cache_creation_input_tokens shape)."""

    def test_snapshot_flushes_pending_usage_event(self):
        adapter = _make_anthropic_adapter()
        adapter._latest_usage_event = {
            "usage": {
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
            }
        }
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot["input_tokens"] == 200
        assert snapshot["output_tokens"] == 80
        # cache_read + cache_creation are summed into cached_tokens.
        assert snapshot["cached_tokens"] == 15

    def test_latest_usage_event_cleared_after_snapshot(self):
        adapter = _make_anthropic_adapter()
        adapter._latest_usage_event = {
            "usage": {"input_tokens": 1, "output_tokens": 1}
        }
        adapter.get_partial_usage_snapshot()
        assert adapter._latest_usage_event is None

    def test_no_pending_event_returns_existing_usage(self):
        adapter = _make_anthropic_adapter()
        adapter.usage = {
            "input_tokens": 7,
            "output_tokens": 3,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 10,
        }
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot == adapter.usage


# SHU-803 AC9b: Anthropic's `message_start` event carries initial input_tokens
# nested under `message.usage` rather than top-level. The pre-fix capture
# clause (`if "usage" in chunk: self._latest_usage_event = chunk`) misses
# that shape entirely, so a terminate fired before any `message_delta`
# lands with `input_tokens=0` even though the provider had emitted them.
# These tests pin both shapes through the actual `handle_provider_event`
# code path (not just the snapshot) so a regression in the additive
# branch can't be masked by the existing top-level test.


def _make_anthropic_adapter_for_event_handling() -> AnthropicAdapter:
    """Like ``_make_anthropic_adapter`` but also seeds the streaming-state
    attributes (`_stream_content`, `_stream_tool_calls`) that
    ``handle_provider_event`` reaches into.
    """
    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    adapter.usage = {}
    adapter._latest_usage_event = None
    adapter._stream_content = []
    adapter._stream_tool_calls = {}
    return adapter


class TestAnthropicHandleProviderEventUsageCapture:
    """SHU-803 AC9b: `handle_provider_event` must stash both Anthropic
    usage shapes (`message_start` nested + `message_delta` top-level) onto
    `_latest_usage_event` so `get_partial_usage_snapshot()` can flush them
    on terminate."""

    @pytest.mark.asyncio
    async def test_message_start_chunk_captures_nested_usage(self):
        """The fix path: `message_start` carries `message.usage` (input_tokens
        on the initial event). Pre-fix this was lost; post-fix it lands on
        `_latest_usage_event` and the snapshot flushes it into `self.usage`.
        """
        adapter = _make_anthropic_adapter_for_event_handling()
        await adapter.handle_provider_event(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5",
                    "usage": {"input_tokens": 42, "output_tokens": 1},
                },
            }
        )
        # The normalized stash exposes the usage at the same top-level
        # `usage` key that `_extract_usage` jmespath-searches for.
        assert adapter._latest_usage_event == {
            "usage": {"input_tokens": 42, "output_tokens": 1}
        }
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot["input_tokens"] == 42
        assert snapshot["output_tokens"] == 1

    @pytest.mark.asyncio
    async def test_message_delta_chunk_still_captures_top_level_usage(self):
        """Regression guard for the existing path: `message_delta` carries
        `usage` at top-level. The additive `elif` branch must not break this.
        """
        adapter = _make_anthropic_adapter_for_event_handling()
        await adapter.handle_provider_event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": None, "stop_sequence": None},
                "usage": {"output_tokens": 17},
            }
        )
        # Existing behavior: the whole chunk is stashed.
        assert adapter._latest_usage_event is not None
        assert adapter._latest_usage_event["usage"] == {"output_tokens": 17}
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot["output_tokens"] == 17

    @pytest.mark.asyncio
    async def test_message_start_then_message_delta_accumulates(self):
        """End-to-end stream shape: ``message_start`` lands input_tokens,
        then ``message_delta`` carries output_tokens. The capture branch
        MERGES the message_delta's top-level usage with the prior
        ``_latest_usage_event`` from ``message_start`` so BOTH axes
        survive to the snapshot. Without the merge, message_delta would
        overwrite ``_latest_usage_event`` with output-only and
        input_tokens would land as 0 on the LLMUsage row — the bug that
        SHU-803 AC9b closes.
        """
        adapter = _make_anthropic_adapter_for_event_handling()
        await adapter.handle_provider_event(
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 100, "output_tokens": 1}},
            }
        )
        await adapter.handle_provider_event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": None, "stop_sequence": None},
                "usage": {"output_tokens": 45},
            }
        )
        snapshot = adapter.get_partial_usage_snapshot()
        # BOTH axes — input_tokens from message_start, output_tokens
        # from message_delta — survive the merge.
        assert snapshot["input_tokens"] == 100, (
            "SHU-803 AC9b: message_start input_tokens must survive a later "
            "message_delta. Pre-fix the delta overwrote _latest_usage_event."
        )
        assert snapshot["output_tokens"] == 45

    @pytest.mark.asyncio
    async def test_terminate_after_message_start_only_captures_input_tokens(self):
        """The load-bearing scenario for the fix: terminate fires AFTER
        `message_start` but BEFORE any `message_delta`. Pre-fix, input_tokens
        were lost (`_latest_usage_event` stayed None). Post-fix, input_tokens
        land on the snapshot.
        """
        adapter = _make_anthropic_adapter_for_event_handling()
        await adapter.handle_provider_event(
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 250, "output_tokens": 0}},
            }
        )
        # No message_delta arrives — terminate fires here.
        snapshot = adapter.get_partial_usage_snapshot()
        assert snapshot["input_tokens"] == 250, (
            "SHU-803 AC9b: input_tokens from message_start must survive "
            "a terminate before any message_delta lands. Pre-fix this was 0."
        )

    @pytest.mark.asyncio
    async def test_content_block_delta_chunk_does_not_overwrite_usage(self):
        """Content deltas are the bulk of the stream; they have no `usage`
        field. The new branch must not fire and stomp on a previously
        captured usage event.
        """
        adapter = _make_anthropic_adapter_for_event_handling()
        # Seed via a message_start.
        await adapter.handle_provider_event(
            {"type": "message_start", "message": {"usage": {"input_tokens": 99}}}
        )
        # Now a content_block_delta — has no usage anywhere.
        await adapter.handle_provider_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            }
        )
        # The stash from message_start must survive.
        assert adapter._latest_usage_event == {"usage": {"input_tokens": 99}}


# SHU-803 AC9e: the base ``cancel()`` is a no-op default — adapters whose
# providers don't expose a real cancel API inherit it and return False
# immediately so the consumer loop falls through to drain.


class TestBaseProviderAdapterCancel:
    """The base ``cancel()`` returns False without doing any work.

    Adapters that DO support cancel (currently only ResponsesAdapter)
    override; the rest inherit this no-op so the consumer loop's
    ``asyncio.gather(adapter.cancel(), _drain(...))`` call site stays
    uniform across providers.
    """

    @pytest.mark.asyncio
    async def test_default_returns_false(self):
        adapter = _make_adapter()
        result = await adapter.cancel()
        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_raise(self):
        """The cancel contract says implementations MUST NOT raise. The
        default trivially upholds this, but the test pins the invariant
        so a future regression that adds a side-effect can't slip in."""
        adapter = _make_adapter()
        # If cancel raised, this would surface as a test failure.
        await adapter.cancel()
