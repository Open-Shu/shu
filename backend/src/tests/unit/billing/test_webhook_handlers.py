"""Tests for webhook handlers — InvoicePaidHandler rollover branch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from shu.billing.webhook_handlers import InvoicePaidHandler


def _make_event(billing_reason: str | None) -> MagicMock:
    event = MagicMock()
    event.id = "evt_test_1"
    event.data.object = {
        "customer": "cus_1",
        "subscription": "sub_1",
        "id": "in_1",
        "amount_paid": 1000,
        "billing_reason": billing_reason,
    }
    return event


class TestInvoicePaidCycleRollover:
    @pytest.mark.asyncio
    async def test_on_cycle_rollover_invoked_for_subscription_cycle(self):
        """billing_reason == 'subscription_cycle' triggers rollover callback."""
        handler = InvoicePaidHandler()
        on_cycle_rollover = AsyncMock()

        await handler.handle(
            _make_event("subscription_cycle"),
            on_cycle_rollover=on_cycle_rollover,
        )

        on_cycle_rollover.assert_awaited_once_with(
            "cus_1", "sub_1", "in_1", "evt_test_1", "subscription_cycle"
        )

    @pytest.mark.asyncio
    async def test_on_cycle_rollover_receives_other_reasons_for_filtering(self):
        """The handler forwards all reasons; the callback filters internally.

        Keeping the filter in the callback (not the handler) means the
        handler stays generic and the test can verify the billing_reason is
        passed through intact.
        """
        handler = InvoicePaidHandler()
        on_cycle_rollover = AsyncMock()

        await handler.handle(
            _make_event("subscription_create"),
            on_cycle_rollover=on_cycle_rollover,
        )

        on_cycle_rollover.assert_awaited_once_with(
            "cus_1", "sub_1", "in_1", "evt_test_1", "subscription_create"
        )

    @pytest.mark.asyncio
    async def test_on_payment_recovered_still_called_unchanged(self):
        """Regression: existing payment-recovered path survives the extension."""
        handler = InvoicePaidHandler()
        on_payment_recovered = AsyncMock()

        await handler.handle(
            _make_event("subscription_cycle"),
            on_payment_recovered=on_payment_recovered,
        )

        on_payment_recovered.assert_awaited_once_with(
            "cus_1", "sub_1", "in_1", "evt_test_1"
        )

    @pytest.mark.asyncio
    async def test_no_callbacks_noops_cleanly(self):
        """Handler tolerates missing callbacks — it just logs and returns."""
        handler = InvoicePaidHandler()
        await handler.handle(_make_event("subscription_cycle"))

    @pytest.mark.asyncio
    async def test_both_callbacks_invoked_when_provided(self):
        handler = InvoicePaidHandler()
        on_payment_recovered = AsyncMock()
        on_cycle_rollover = AsyncMock()

        await handler.handle(
            _make_event("subscription_cycle"),
            on_payment_recovered=on_payment_recovered,
            on_cycle_rollover=on_cycle_rollover,
        )

        on_payment_recovered.assert_awaited_once()
        on_cycle_rollover.assert_awaited_once()
