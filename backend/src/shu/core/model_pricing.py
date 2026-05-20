"""Reference pricing for LLM models.

This module is the **source of truth** for model pricing. On startup the
application syncs these values into ``llm_models.cost_per_input_unit`` /
``cost_per_output_unit`` so they are queryable for budgeting and analytics.

Unit semantics are determined by ``llm_models.model_type``:
- ``chat`` / ``embedding`` — rates are per-token.
- ``ocr`` — rates are per-page.

Two input tables reflect this:
- ``_PRICING_PER_MTOK`` holds per-token rates expressed in USD per million
  tokens ($/MTok), which matches how vendors publish token pricing.
- ``_PRICING_PER_UNIT`` holds per-unit rates expressed as the absolute
  USD cost of one unit (e.g. one page for OCR), avoiding an artificial
  million-unit scaling for non-token pricing.

Both merge into the public ``MODEL_PRICING`` map with identical shape
(``{"input": Decimal, "output": Decimal}``) so downstream code doesn't
need to care which input table a model came from.

Sources (verified 2026-03-19):
- Anthropic: https://platform.claude.com/docs/en/docs/about-claude/models
- OpenAI: https://developers.openai.com/api/docs/pricing
- Google: https://ai.google.dev/gemini-api/docs/pricing
- Mistral OCR: https://docs.mistral.ai/capabilities/OCR/basic_ocr/
Local/self-hosted models have zero cost.

Usage:
    from shu.core.model_pricing import get_pricing

    pricing = get_pricing("claude-haiku-4-5-20251001")
    # -> {"input": Decimal("0.000001"), "output": Decimal("0.000005")}
"""

import re
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.logging import get_logger

logger = get_logger(__name__)

# SHU-803 AC9d: regex used by ``_normalize_model_key`` to strip provider
# prefixes (``vendor/``) and tier suffixes (``:nitro``, ``:free``, etc.)
# before comparing against MODEL_PRICING. Compiled once at import time.
_PROVIDER_PREFIX_RE = re.compile(r"^[^/]+/")
_TIER_SUFFIX_RE = re.compile(r":[^/:]+$")

_MTOK = Decimal("1000000")

# $/MTok pricing (per-token, for chat and embedding models): (input, output)
# fmt: off
_PRICING_PER_MTOK: dict[str, tuple[str, str]] = {
    # --- Anthropic ---
    "claude-haiku-4-5-20251001":         ("1",     "5"),
    "claude-opus-4-5-20251101":          ("5",     "25"),
    # --- OpenAI ---
    "gpt-4.1-nano":                      ("0.10",  "0.40"),
    "gpt-5-nano":                        ("0.05",  "0.40"),
    "gpt-5.2":                           ("1.75",  "14"),
    "gpt-5.2-pro":                       ("21",    "168"),
    "gpt-5.4":                           ("2.50",  "15"),
    "gpt-5.4-mini":                      ("0.75",  "4.50"),
    "gpt-5.4-nano":                      ("0.20",  "1.25"),
    # --- Google ---
    "models/gemini-3-flash-preview":     ("0.50",  "3"),
    "models/gemini-3-pro-preview":       ("2",     "12"),
    "models/gemini-3-pro-image-preview": ("2",     "12"),
    "gemma-4-31B-it":                    ("0.18",   "0.50"),
    # --- Open Source ---
    "qwen3.5-397b-a17b":                 ("0.55",   "3.50"),
}

# Absolute per-unit pricing (e.g. per-page for OCR): (input, output)
_PRICING_PER_UNIT: dict[str, tuple[str, str]] = {
    # --- Mistral OCR (per page) ---
    "mistral-ocr-latest": ("0.002", "0"),
}
# fmt: on

MODEL_PRICING: dict[str, dict[str, Decimal]] = {
    **{
        name: {"input": Decimal(inp) / _MTOK, "output": Decimal(out) / _MTOK}
        for name, (inp, out) in _PRICING_PER_MTOK.items()
    },
    **{name: {"input": Decimal(inp), "output": Decimal(out)} for name, (inp, out) in _PRICING_PER_UNIT.items()},
}


def _normalize_model_key(name: str) -> str:
    """SHU-803 AC9d: collapse OpenRouter-style slugs to a MODEL_PRICING-comparable key.

    OpenRouter exposes the same underlying model under a vendor-prefixed,
    tier-suffixed slug (``google/gemma-4-31b-it:nitro``) while
    ``MODEL_PRICING`` uses bare upstream names (``gemma-4-31B-it``).
    Exact-string lookup misses those slugs and the DB-rate-fallback path
    in ``usage_recording.py`` lands cost=0 even when tokens are counted
    correctly — the billing-evasion abuse vector this AC closes.

    Normalization rules (applied in order):
    1. Strip a leading ``vendor/`` prefix (one slash; anything before the
       slash is dropped). Matches OpenRouter / OpenAI-compatible gateway
       conventions.
    2. Strip a trailing ``:tier`` suffix (``:nitro``, ``:free``, ``:beta``,
       etc.). Tier variants share rates with the base model.
    3. Lowercase. ``gemma-4-31B-it`` and ``gemma-4-31b-it`` are the same
       model to operators; case differences are a footgun.

    The normalized form is used as a FALLBACK only — exact matches in
    ``MODEL_PRICING`` still win, so an operator who wants a vendor-specific
    rate can keep the slug verbatim in MODEL_PRICING and it will resolve
    via the exact-match path.
    """
    if not name:
        return name
    normalized = _PROVIDER_PREFIX_RE.sub("", name, count=1)
    normalized = _TIER_SUFFIX_RE.sub("", normalized, count=1)
    return normalized.lower()


# SHU-803 AC9d: precomputed normalized-key map. Built at module load so the
# hot lookup path stays O(1). Keyed by the normalized form of every
# MODEL_PRICING key; values are references into MODEL_PRICING itself so
# updates to ``MODEL_PRICING`` propagate automatically (Python dict values
# are shared references).
_NORMALIZED_PRICING: dict[str, dict[str, Decimal]] = {
    _normalize_model_key(name): pricing for name, pricing in MODEL_PRICING.items()
}


def get_pricing(model_name: str) -> dict[str, Decimal] | None:
    """Look up per-unit pricing for a model name. Returns None if unknown.

    Unit is per-token for chat/embedding models and per-page for ocr models,
    matching ``llm_models.model_type``.

    Lookup strategy (SHU-803 AC9d):
    1. Exact match against ``MODEL_PRICING``. Preserves the pre-existing
       behavior for any operator who keeps slug-verbatim keys.
    2. Normalized fallback via ``_normalize_model_key``. Covers the
       OpenRouter slug case (``google/gemma-4-31b-it:nitro`` resolves to
       the ``gemma-4-31B-it`` rates).
    """
    pricing = MODEL_PRICING.get(model_name)
    if pricing is not None:
        return pricing
    return _NORMALIZED_PRICING.get(_normalize_model_key(model_name))


async def sync_pricing_to_db(db: AsyncSession) -> dict[str, int]:
    """Push reference pricing into llm_models rows.

    For every ``llm_models.model_name`` that resolves to a MODEL_PRICING
    entry (exact or normalized — see :func:`get_pricing`), sets
    ``cost_per_input_unit`` and ``cost_per_output_unit``. Models that
    don't resolve are left untouched.

    SHU-803 AC9d: iteration is over DB model names (not MODEL_PRICING
    keys) so OpenRouter slugs like ``google/gemma-4-31b-it:nitro``
    pick up rates via the normalized fallback. The pre-fix exact-string
    iteration left those rows NULL and the DB-rate fallback computed
    cost=0.

    SHU-803 AC9i: any case where two distinct DB model_name values
    resolve to the same MODEL_PRICING entry *via the normalized fallback*
    (i.e. neither matched exactly) logs a WARN naming both. Tier variants
    that share rates by design (``...:nitro`` and ``...:free``) are the
    common case and the WARN is informational — operators add explicit
    MODEL_PRICING entries if they want the variants priced differently.

    Returns {"updated": N, "cleared": N, "skipped": N}:
      - updated: row written with non-zero rates.
      - cleared: row explicitly NULLed because reference pricing is zero
        (demote-to-free case — prevents stale rates from a prior sync
        leaking into DB-rate-fallback cost math).
      - skipped: db model_name didn't resolve to any MODEL_PRICING entry.
    """
    from shu.models.llm_provider import LLMModel

    result = await db.execute(select(LLMModel.model_name))
    db_model_names = [row[0] for row in result.all()]

    updated = 0
    cleared = 0
    skipped: list[str] = []
    # SHU-803 AC9i: track DB names that resolved via the normalized fallback,
    # keyed by the normalized key they hit. Collisions (>1 db_name per key)
    # are logged after the sync loop.
    normalized_collisions: dict[str, list[str]] = {}

    for db_model_name in db_model_names:
        # Exact match first (preserves pre-fix behavior for any operator
        # who keeps slug-verbatim MODEL_PRICING keys).
        pricing = MODEL_PRICING.get(db_model_name)
        if pricing is None:
            normalized_key = _normalize_model_key(db_model_name)
            pricing = _NORMALIZED_PRICING.get(normalized_key)
            if pricing is not None:
                normalized_collisions.setdefault(normalized_key, []).append(db_model_name)

        if pricing is None:
            skipped.append(db_model_name)
            continue

        # Zero-cost models (local/self-hosted) should have NULL rate columns so
        # "has pricing" checks (IS NOT NULL) are truthful. If a model used to be
        # paid and got demoted to free in model_pricing.py, a bare `continue`
        # here would leave stale non-null rates in the DB and the fallback cost
        # math would keep billing the old rates. Explicit equality (not Python
        # truthiness) avoids the Decimal-falsiness foot-gun.
        if pricing["input"] == 0 and pricing["output"] == 0:
            await db.execute(
                update(LLMModel)
                .where(LLMModel.model_name == db_model_name)
                .values(
                    cost_per_input_unit=None,
                    cost_per_output_unit=None,
                )
            )
            cleared += 1
            continue

        await db.execute(
            update(LLMModel)
            .where(LLMModel.model_name == db_model_name)
            .values(
                cost_per_input_unit=pricing["input"],
                cost_per_output_unit=pricing["output"],
            )
        )
        updated += 1

    await db.commit()

    # SHU-803 AC9i: surface multi-db-name-to-one-pricing-key cases. Tier
    # variants are the expected common case (``...:nitro`` and ``...:free``
    # both normalize to the base); the WARN is informational so operators
    # can confirm the resolution matches intent.
    for normalized_key, db_names in normalized_collisions.items():
        if len(db_names) > 1:
            logger.warning(
                "Pricing-lookup normalization collision: %d DB model_name values "
                "resolved to MODEL_PRICING key %r via normalization fallback. "
                "If these should be priced differently, add explicit MODEL_PRICING "
                "entries. Names: %s",
                len(db_names),
                normalized_key,
                ", ".join(db_names),
            )

    logger.info(
        "Model pricing sync complete: %d updated, %d cleared, %d skipped (no MODEL_PRICING match): %s",
        updated,
        cleared,
        len(skipped),
        skipped,
    )
    return {"updated": updated, "cleared": cleared, "skipped": len(skipped)}
