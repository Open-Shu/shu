"""Tests for shu.billing.markup.resolve_markup."""

from __future__ import annotations

from decimal import Decimal

import pytest

from shu.billing.config import BillingSettings, get_billing_settings
from shu.billing.cp_client import BillingState
from shu.billing.entitlements import EntitlementSet
from shu.billing.markup import resolve_markup


def _state(*, markup: Decimal | None) -> BillingState:
    return BillingState(
        openrouter_key_disabled=False,
        payment_failed_at=None,
        payment_grace_days=0,
        entitlements=EntitlementSet(),
        is_trial=False,
        trial_deadline=None,
        total_grant_amount=Decimal(0),
        remaining_grant_amount=Decimal(0),
        seat_price_usd=Decimal(0),
        usage_markup_multiplier=markup,
    )


@pytest.fixture(autouse=True)
def _settings_with_known_default(monkeypatch: pytest.MonkeyPatch) -> BillingSettings:
    """Pin the configured default to a known value so the fallback assertions
    don't drift if a future settings change moves the constant.
    """
    settings = get_billing_settings()
    monkeypatch.setattr(settings, "usage_markup_multiplier_default", Decimal("1.25"))
    return settings


class TestResolveMarkup:
    def test_attached_value_is_returned(self) -> None:
        assert resolve_markup(_state(markup=Decimal("1.5"))) == Decimal("1.5")

    def test_none_falls_back_to_configured_default(self) -> None:
        """Cold-start HEALTHY_DEFAULT and the no-metered-item branch both
        leave the field as None — consumers see the configured default
        rather than a crash or a zero multiplier.
        """
        assert resolve_markup(_state(markup=None)) == Decimal("1.25")
