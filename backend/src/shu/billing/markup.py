"""Per-tenant resolution of the customer-billed markup multiplier.

The markup converts raw provider cost (`LLMUsage.total_cost`, in raw USD) to
customer-billed cost — the same unit as `BillingState.total_grant_amount`.
Trial-cap enforcement and the trial-banner remaining-grant display both need
to compare local raw cost against the customer-billed grant; without markup
they'd silently under-count usage by a constant factor.

`BillingStateCache` already attaches the value to BillingState on each
refresh (see `_attach_markup`), so consumers read it straight off `state` —
this helper just folds in the fallback when the field is None.

TODO: Migrate to CP. When the control plane starts shipping
`usage_markup_multiplier` on the BillingState wire (it already owns the
Stripe relationship and the metered Price), drop `BillingStateCache._attach_markup`
and this helper becomes trivial — every populated state will already carry
the right value. The fallback path still matters for `HEALTHY_DEFAULT`
(cold-start CP outage) and self-hosted deployments without Stripe.
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
