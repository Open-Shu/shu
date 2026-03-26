"""Reference pricing for LLM models.

This module is the **source of truth** for model pricing. On startup the
application syncs these values into ``llm_models.cost_per_input_token`` /
``cost_per_output_token`` so they are queryable for budgeting and analytics.

Per-token costs in USD per million tokens ($/MTok).
Sources (verified 2026-03-19):
- Anthropic: https://platform.claude.com/docs/en/docs/about-claude/models
- OpenAI: https://developers.openai.com/api/docs/pricing
- Google: https://ai.google.dev/gemini-api/docs/pricing
Local/self-hosted models have zero cost.

Usage:
    from shu.core.model_pricing import get_pricing

    pricing = get_pricing("claude-haiku-4-5-20251001")
    # -> {"input": Decimal("0.000001"), "output": Decimal("0.000005")}
"""

import logging
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_MTOK = Decimal("1000000")

# $/MTok pricing: (input, output)
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
# fmt: on

MODEL_PRICING: dict[str, dict[str, Decimal]] = {
    name: {
        "input": Decimal(inp) / _MTOK,
        "output": Decimal(out) / _MTOK,
    }
    for name, (inp, out) in _PRICING_PER_MTOK.items()
}


def get_pricing(model_name: str) -> dict[str, Decimal] | None:
    """Look up per-token pricing for a model name. Returns None if unknown."""
    return MODEL_PRICING.get(model_name)


async def sync_pricing_to_db(db: AsyncSession) -> dict[str, int]:
    """Push reference pricing into llm_models rows.

    For every model_name in MODEL_PRICING that exists in llm_models, sets
    cost_per_input_token and cost_per_output_token. Models not in the
    reference dict are left untouched.

    Returns {"updated": N, "skipped": N} where skipped means the model_name
    was in MODEL_PRICING but not found in llm_models.
    """
    from shu.models.llm_provider import LLMModel

    # Get all model names currently in the DB
    result = await db.execute(select(LLMModel.model_name))
    db_model_names = {row[0] for row in result.all()}

    updated = 0
    skipped = 0

    for model_name, pricing in MODEL_PRICING.items():
        if model_name not in db_model_names:
            skipped += 1
            continue

        # Skip zero-cost models (local/self-hosted) — leave DB columns as NULL
        # so "has pricing" checks (IS NOT NULL) work correctly.
        if not pricing["input"] and not pricing["output"]:
            continue

        await db.execute(
            update(LLMModel)
            .where(LLMModel.model_name == model_name)
            .values(
                cost_per_input_token=pricing["input"],
                cost_per_output_token=pricing["output"],
            )
        )
        updated += 1

    await db.commit()
    logger.info("Model pricing sync complete: %d updated, %d skipped (not in DB)", updated, skipped)
    return {"updated": updated, "skipped": skipped}
