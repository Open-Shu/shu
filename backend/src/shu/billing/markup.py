"""Resolve the customer-billed markup multiplier on a `BillingState`.

The markup converts raw provider cost (`LLMUsage.total_cost`, in raw USD) to
customer-billed cost — the same unit as `BillingState.total_grant_amount`.
Trial-cap enforcement and the trial-banner remaining-grant display both need
to compare local raw cost against the customer-billed grant; without markup
they'd silently under-count usage by a constant factor.

CP ships `usage_markup_multiplier` on the BillingState wire (SHU-774), so
in steady-state the value is already on `state` and this helper is a direct
read. The fallback path still matters for `HEALTHY_DEFAULT` (cold-start CP
outage) and self-hosted deployments where CP isn't configured.
"""

from __future__ import annotations

from decimal import Decimal

from shu.billing.config import get_billing_settings
from shu.billing.cp_client import BillingState


def resolve_markup(state: BillingState) -> Decimal:
    """Return the markup attached to `state`, or the configured default."""
    if state.usage_markup_multiplier is not None and state.usage_markup_multiplier > 0:
        return state.usage_markup_multiplier
    return get_billing_settings().usage_markup_multiplier_default
