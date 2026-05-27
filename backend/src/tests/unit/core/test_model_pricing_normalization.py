"""Unit tests for SHU-803 AC9d/9i pricing-lookup normalization.

The lookup must resolve OpenRouter-style slugs (``vendor/model:tier``) to
the corresponding bare-name ``MODEL_PRICING`` entries so the DB-rate
fallback in ``usage_recording.py`` produces non-zero costs for chat rows
that don't carry provider-authoritative wire cost (the billing-evasion
abuse vector AC9d closes).

These tests pin both the pure lookup (``get_pricing``) and the
DB-side sync (``sync_pricing_to_db``) — the latter including the
collision-warning behavior from AC9i.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.core.model_pricing import (
    MODEL_PRICING,
    _normalize_model_key,
    get_pricing,
    sync_pricing_to_db,
)


class TestNormalizeModelKey:
    """Pure-string normalization. No DB, no MODEL_PRICING lookups."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # OpenRouter happy path — vendor prefix + tier suffix.
            ("google/gemma-4-31b-it:nitro", "gemma-4-31b-it"),
            ("google/gemma-4-31b-it:free", "gemma-4-31b-it"),
            # Vendor prefix only.
            ("google/gemma-4-31b-it", "gemma-4-31b-it"),
            # Tier suffix only.
            ("gemma-4-31b-it:nitro", "gemma-4-31b-it"),
            # Bare name (no prefix, no suffix). Lowercased.
            ("gemma-4-31B-it", "gemma-4-31b-it"),
            # Qwen slug variant.
            ("qwen/qwen3.5-397b-a17b:free", "qwen3.5-397b-a17b"),
            # Already-normalized OCR model (no prefix or suffix).
            ("mistral-ocr-latest", "mistral-ocr-latest"),
            # Edge: empty string falls through untouched.
            ("", ""),
        ],
    )
    def test_normalization_strips_prefix_suffix_and_lowercases(self, raw, expected):
        assert _normalize_model_key(raw) == expected

    def test_only_first_slash_is_treated_as_provider_prefix(self):
        """Path-like names should keep nested segments after the first slash.

        Some Gemini model identifiers carry ``models/gemini-3-pro-preview``
        as the canonical name. The first slash is a provider-like prefix
        and gets stripped; everything after is preserved.
        """
        assert _normalize_model_key("models/gemini-3-pro-preview") == "gemini-3-pro-preview"

    def test_tier_suffix_must_be_at_the_end(self):
        """Colons inside the model name (e.g. an embedded version) are not
        treated as tier suffixes. Only a trailing ``:tier`` is stripped.
        """
        # A colon in the middle: nothing to strip.
        assert _normalize_model_key("custom:v1/model-x") == "model-x"


class TestGetPricingResolution:
    """``get_pricing`` resolution: exact match first, normalized fallback second."""

    def test_exact_bare_name_match_wins(self):
        """Pre-existing keys in MODEL_PRICING must continue to resolve
        without going through the normalized fallback path. Regression
        guard for the existing call sites in ``usage_recording.py``.
        """
        # Bare-name keys present in MODEL_PRICING (verified at module load).
        rates = get_pricing("gemma-4-31B-it")
        assert rates is not None
        assert rates["input"] > Decimal("0")
        assert rates["output"] > Decimal("0")

    def test_openrouter_slug_resolves_via_normalized_fallback(self):
        """The AC9d load-bearing assertion: an OpenRouter slug for Gemma-4
        resolves to the bare-name rates. Pre-fix this returned None and the
        DB-rate fallback in ``usage_recording.py`` produced cost=0.
        """
        slug_rates = get_pricing("google/gemma-4-31b-it:nitro")
        bare_rates = get_pricing("gemma-4-31B-it")
        assert slug_rates is not None
        assert bare_rates is not None
        # Same underlying dict (we store references, not copies, in
        # _NORMALIZED_PRICING — see model_pricing.py).
        assert slug_rates is bare_rates

    def test_qwen_openrouter_slug_resolves(self):
        """Same pattern for the Qwen open-weight model — the other half of
        the SHU-803 abuse-vector close-out."""
        rates = get_pricing("qwen/qwen3.5-397b-a17b:free")
        assert rates is not None
        assert rates["input"] > Decimal("0")

    def test_unknown_model_returns_none(self):
        """Both lookup paths exhausted → None, signaling 'no DB-rate
        fallback available' to the cost contract in usage_recording.py."""
        assert get_pricing("anthropic/nonexistent-model:foo") is None
        assert get_pricing("totally-made-up-model") is None

    def test_case_insensitive_match_via_normalization(self):
        """Mixed-case slugs (operator typo or vendor inconsistency) still
        resolve via the lowercase normalization step."""
        rates = get_pricing("GOOGLE/Gemma-4-31B-IT:NITRO")
        assert rates is not None
        assert rates is get_pricing("gemma-4-31B-it")


class TestSyncPricingToDb:
    """``sync_pricing_to_db`` over a mocked AsyncSession.

    We mock the session to avoid Postgres dependency in unit tests. The
    assertion targets the resolved rates + the WARN-on-collision behavior
    rather than SQL emission shape.
    """

    @pytest.mark.asyncio
    async def test_openrouter_slug_picks_up_rates_via_normalized_fallback(self):
        """The load-bearing AC9d scenario at the DB layer: an LLMModel row
        whose ``model_name`` is the OpenRouter slug gets its rates populated
        via the normalized fallback. Pre-fix it would be skipped (no
        exact match in MODEL_PRICING) and its rate columns would stay NULL.
        """
        session = MagicMock()
        # Return one row that needs the normalized fallback.
        result_mock = MagicMock()
        result_mock.all.return_value = [("google/gemma-4-31b-it:nitro",)]
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()

        counts = await sync_pricing_to_db(session)

        assert counts["updated"] == 1
        assert counts["skipped"] == 0
        # The update call shape isn't asserted in detail — what matters is
        # that we issued at least one update for the OpenRouter-slug name.
        # The first execute() is the SELECT; subsequent execute() calls are
        # the UPDATEs.
        assert session.execute.await_count >= 2

    @pytest.mark.asyncio
    async def test_collision_logs_warning_with_both_names(self, caplog):
        """AC9i: two distinct DB model_name values resolving to the same
        MODEL_PRICING entry via the normalized fallback emit a WARN naming
        both. This is informational for tier-variant resolution
        (``...:nitro`` and ``...:free`` sharing rates by design) but
        operators want the breadcrumb so they can confirm the resolution
        matches intent.
        """
        session = MagicMock()
        # Two OpenRouter slugs both normalize to "gemma-4-31b-it".
        result_mock = MagicMock()
        result_mock.all.return_value = [
            ("google/gemma-4-31b-it:nitro",),
            ("google/gemma-4-31b-it:free",),
        ]
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()

        with caplog.at_level(logging.WARNING, logger="shu.core.model_pricing"):
            await sync_pricing_to_db(session)

        collision_records = [
            r for r in caplog.records if "normalization collision" in r.getMessage()
        ]
        assert len(collision_records) == 1, (
            f"Expected one collision WARN; got {[r.getMessage() for r in caplog.records]}"
        )
        msg = collision_records[0].getMessage()
        assert "google/gemma-4-31b-it:nitro" in msg
        assert "google/gemma-4-31b-it:free" in msg

    @pytest.mark.asyncio
    async def test_exact_match_does_not_count_as_collision(self, caplog):
        """A DB row whose ``model_name`` matches MODEL_PRICING exactly
        resolves via the exact-match path and never touches the normalized
        fallback. It must NOT participate in collision detection — only
        normalized-fallback resolutions count.
        """
        session = MagicMock()
        result_mock = MagicMock()
        # One exact-match name + one normalized-fallback name. Even if
        # both normalize to the same key, the exact match doesn't go
        # through the fallback path so no collision is recorded.
        result_mock.all.return_value = [
            ("gemma-4-31B-it",),  # exact match in MODEL_PRICING
            ("google/gemma-4-31b-it:nitro",),  # normalized fallback only
        ]
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()

        with caplog.at_level(logging.WARNING, logger="shu.core.model_pricing"):
            await sync_pricing_to_db(session)

        collision_records = [
            r for r in caplog.records if "normalization collision" in r.getMessage()
        ]
        assert collision_records == [], (
            "Exact matches must not participate in collision detection — only "
            "multiple normalized-fallback resolutions to the same key warn."
        )

    @pytest.mark.asyncio
    async def test_skipped_count_reflects_unknown_models(self):
        """DB models with no MODEL_PRICING resolution (exact or normalized)
        are left untouched and counted under ``skipped``."""
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.all.return_value = [("totally-fake-model-name",)]
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()

        counts = await sync_pricing_to_db(session)
        assert counts["updated"] == 0
        assert counts["skipped"] == 1

    @pytest.mark.asyncio
    async def test_no_collision_warning_for_single_normalized_resolution(self, caplog):
        """One DB name resolving via normalization is not a collision; no WARN."""
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.all.return_value = [("google/gemma-4-31b-it:nitro",)]
        session.execute = AsyncMock(return_value=result_mock)
        session.commit = AsyncMock()

        with caplog.at_level(logging.WARNING, logger="shu.core.model_pricing"):
            await sync_pricing_to_db(session)

        collision_records = [
            r for r in caplog.records if "normalization collision" in r.getMessage()
        ]
        assert collision_records == []


class TestModelPricingInvariants:
    """Module-load invariants worth pinning."""

    def test_normalized_table_has_one_entry_per_model_pricing_key(self):
        """A regression guard: if two MODEL_PRICING keys normalize to the
        same value (e.g., somebody adds both ``gemma-4-31B-it`` and
        ``gemma-4-31b-it`` with different rates), the second one silently
        overwrites the first in _NORMALIZED_PRICING. This test would catch
        that — currently all keys normalize uniquely.
        """
        normalized_seen: dict[str, str] = {}
        for key in MODEL_PRICING:
            normalized = _normalize_model_key(key)
            assert normalized not in normalized_seen, (
                f"MODEL_PRICING keys {normalized_seen[normalized]!r} and {key!r} "
                f"both normalize to {normalized!r}. Pick one canonical key "
                f"or change the normalization rules."
            )
            normalized_seen[normalized] = key
