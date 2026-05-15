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

from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shu.core.logging import get_logger

logger = get_logger(__name__)

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
    # --- Local / self-hosted ---
    "gemma3:12b":                        ("0",     "0"),
    "openai/gpt-oss-20b":               ("0",     "0"),
    "openai/gpt-oss-120b":              ("0",     "0"),
    "qwen/qwen3-vl-8b":                 ("0",     "0"),
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


def get_pricing(model_name: str) -> dict[str, Decimal] | None:
    """Look up per-unit pricing for a model name. Returns None if unknown.

    Unit is per-token for chat/embedding models and per-page for ocr models,
    matching ``llm_models.model_type``.
    """
    return MODEL_PRICING.get(model_name)


async def sync_pricing_to_db(db: AsyncSession) -> dict[str, int]:
    """Push reference pricing into llm_models rows.

    For every model_name in MODEL_PRICING that exists in llm_models, sets
    cost_per_input_unit and cost_per_output_unit. Models not in the
    reference dict are left untouched.

    Returns {"updated": N, "cleared": N, "skipped": N}:
      - updated: row written with non-zero rates.
      - cleared: row explicitly NULLed because the reference pricing is zero
        (demote-to-free case — prevents stale rates from a prior sync leaking
        into DB-rate-fallback cost math).
      - skipped: model_name was in MODEL_PRICING but not found in llm_models.
    """
    from shu.models.llm_provider import LLMModel

    # Get all model names currently in the DB
    result = await db.execute(select(LLMModel.model_name))
    db_model_names = {row[0] for row in result.all()}

    updated = 0
    cleared = 0
    skipped = 0

    for model_name, pricing in MODEL_PRICING.items():
        if model_name not in db_model_names:
            skipped += 1
            continue

        # Zero-cost models (local/self-hosted) should have NULL rate columns so
        # "has pricing" checks (IS NOT NULL) are truthful. If a model used to be
        # paid and got demoted to free in model_pricing.py, a bare `continue`
        # here would leave stale non-null rates in the DB and the fallback cost
        # math would keep billing the old rates. Explicitly NULL the columns so
        # the source-of-truth change actually lands. Explicit equality (not
        # Python truthiness) avoids the Decimal-falsiness foot-gun that bit us
        # in the cost-contract fallback (now in services/usage_recording.py).
        if pricing["input"] == 0 and pricing["output"] == 0:
            await db.execute(
                update(LLMModel)
                .where(LLMModel.model_name == model_name)
                .values(
                    cost_per_input_unit=None,
                    cost_per_output_unit=None,
                )
            )
            cleared += 1
            continue

        await db.execute(
            update(LLMModel)
            .where(LLMModel.model_name == model_name)
            .values(
                cost_per_input_unit=pricing["input"],
                cost_per_output_unit=pricing["output"],
            )
        )
        updated += 1

    await db.commit()
    logger.info(
        "Model pricing sync complete: %d updated, %d cleared, %d skipped (not in DB)",
        updated,
        cleared,
        skipped,
    )
    return {"updated": updated, "cleared": cleared, "skipped": skipped}
