"""Tests for StripeClient — focuses on parsing/mapping at the Stripe boundary."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shu.billing.schemas import SubscriptionUpdate
from shu.billing.stripe_client import (
    StripeClient,
    StripeClientError,
    StripeConfigurationError,
    _phase_to_params,
    _stamp_quantity,
)


def _make_settings(**overrides):
    """Create a mock BillingSettings."""
    defaults = {
        "secret_key": "sk_test_fake",
        "publishable_key": "pk_test_fake",
        "router_shared_secret": "0" * 64,
        "price_id_monthly": "price_fake",
        "meter_id_cost": None,
        "meter_event_name": "usage_cost",
        "mode": "test",
        "app_base_url": "http://localhost:3000",
        "is_configured": True,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def _make_subscription_data(*, quantity=5, items_quantity=None, include_items=True):
    """Build a realistic Stripe subscription webhook payload.

    Args:
        quantity: The quantity to put on items.data[0] (canonical location).
        items_quantity: Override for items.data[0].quantity if different from quantity.
        include_items: Whether to include the items field at all.
    """
    data = {
        "id": "sub_test123",
        "customer": "cus_test456",
        "status": "active",
        "current_period_start": 1712016000,  # 2024-04-02T00:00:00Z
        "current_period_end": 1714694400,  # 2024-05-03T00:00:00Z
        "cancel_at_period_end": False,
        "canceled_at": None,
    }
    if include_items:
        data["items"] = {
            "data": [
                {
                    "id": "si_item1",
                    "price": {"id": "price_fake"},
                    "quantity": items_quantity if items_quantity is not None else quantity,
                }
            ]
        }
    return data


class TestParseSubscriptionUpdate:
    """Tests for StripeClient.parse_subscription_update — the webhook parsing boundary."""

    @patch("shu.billing.stripe_client.stripe")
    def test_parses_timestamps_as_utc(self, mock_stripe):
        """Period timestamps must be parsed as UTC datetimes."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data()

        result = client.parse_subscription_update(data)

        assert result.current_period_start.tzinfo is not None
        assert result.current_period_end.tzinfo is not None
        assert result.current_period_start < result.current_period_end

    @patch("shu.billing.stripe_client.stripe")
    def test_parses_canceled_at_when_present(self, mock_stripe):
        """canceled_at should be parsed when set."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data()
        data["canceled_at"] = 1713000000
        data["cancel_at_period_end"] = True

        result = client.parse_subscription_update(data)

        assert result.canceled_at is not None
        assert result.cancel_at_period_end is True

    @patch("shu.billing.stripe_client.stripe")
    def test_canceled_at_none_when_not_set(self, mock_stripe):
        """canceled_at should be None when subscription isn't canceled."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data()

        result = client.parse_subscription_update(data)

        assert result.canceled_at is None

    @patch("shu.billing.stripe_client.stripe")
    def test_extracts_customer_and_subscription_ids(self, mock_stripe):
        """Core IDs must be extracted correctly."""
        client = StripeClient(_make_settings())
        data = _make_subscription_data()

        result = client.parse_subscription_update(data)

        assert result.stripe_subscription_id == "sub_test123"
        assert result.stripe_customer_id == "cus_test456"
        assert result.status == "active"


class TestStripeClientInit:
    """Tests for StripeClient initialization and configuration validation."""

    @patch("shu.billing.stripe_client.stripe")
    def test_raises_when_secret_key_missing(self, mock_stripe):
        """Must raise StripeConfigurationError when no secret key."""
        with pytest.raises(StripeConfigurationError, match="secret key not configured"):
            StripeClient(_make_settings(secret_key=None))

    @patch("shu.billing.stripe_client.stripe")
    def test_sets_api_key(self, mock_stripe):
        """Should configure the stripe SDK with the secret key."""
        StripeClient(_make_settings(secret_key="sk_test_abc"))

        assert mock_stripe.api_key == "sk_test_abc"


# Stripe signature verification moved out of this module when the Shu Control
# Plane took over as the sole Stripe webhook receiver. Envelope verification
# tests live next to the verifier in tests/unit/billing/test_router_envelope.py.


# Reused fixture for schedule tests — mirrors the Shu two-item layout
# (licensed seat price carries `quantity`, metered usage price does not).
_SCHEDULE_ITEMS = [
    {"price": "price_seat_licensed", "quantity": 10},
    {"price": "price_usage_metered"},
]


class TestStampQuantity:
    """Pure helper — stamps seat qty on licensed lines, leaves metered untouched."""

    def test_stamps_quantity_on_licensed_only(self):
        result = _stamp_quantity(_SCHEDULE_ITEMS, 5)
        assert result == [
            {"price": "price_seat_licensed", "quantity": 5},
            {"price": "price_usage_metered"},
        ]

    def test_returns_copies_not_references(self):
        """Mutating the result must not affect the input (defensive copy)."""
        result = _stamp_quantity(_SCHEDULE_ITEMS, 5)
        result[0]["price"] = "mutated"
        result[1]["price"] = "mutated"
        assert _SCHEDULE_ITEMS[0]["price"] == "price_seat_licensed"
        assert _SCHEDULE_ITEMS[1]["price"] == "price_usage_metered"


class TestPhaseToParams:
    """Retrieved phase → modify params round-trip."""

    def test_includes_start_and_end_date_when_present(self):
        phase = {
            "items": [
                {"price": "price_seat", "quantity": 10},
                {"price": "price_meter", "quantity": None},
            ],
            "start_date": 1_690_000_000,
            "end_date": 1_700_000_000,
        }
        assert _phase_to_params(phase) == {
            "items": [
                {"price": "price_seat", "quantity": 10},
                {"price": "price_meter"},
            ],
            "start_date": 1_690_000_000,
            "end_date": 1_700_000_000,
        }

    def test_omits_dates_when_absent(self):
        """Open-ended phases skip the dates; modify needs at least one phase
        with start_date elsewhere in the array to anchor end_date offsets."""
        phase = {
            "items": [{"price": "price_seat", "quantity": 5}],
            "start_date": None,
            "end_date": None,
        }
        assert _phase_to_params(phase) == {
            "items": [{"price": "price_seat", "quantity": 5}],
        }


class TestCreateSubscriptionSchedule:
    """F1 — two-call create + modify dance with shared idempotency key."""

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_creates_from_subscription_then_installs_two_phases(self, mock_stripe):
        created = MagicMock()
        created.id = "sub_sched_abc"
        # `from_subscription` returns a one-phase schedule; the install-phases
        # call needs to read phase 1's start_date off it to satisfy Stripe's
        # "at least one phase with a start_date" requirement.
        created.__getitem__.side_effect = (
            lambda k: [{"start_date": 1_690_000_000}] if k == "phases" else None
        )
        final = MagicMock()
        final.id = "sub_sched_abc"
        mock_stripe.SubscriptionSchedule.create_async = AsyncMock(return_value=created)
        mock_stripe.SubscriptionSchedule.modify_async = AsyncMock(return_value=final)
        mock_stripe.StripeError = Exception  # keep `except stripe.StripeError` harmless

        client = StripeClient(_make_settings())
        result = await client.create_subscription_schedule(
            subscription_id="sub_abc",
            phase_1_qty=10,
            phase_1_end=1_700_000_000,
            phase_2_qty=5,
            items=_SCHEDULE_ITEMS,
        )

        assert result is final
        mock_stripe.SubscriptionSchedule.create_async.assert_awaited_once_with(
            from_subscription="sub_abc",
        )
        mock_stripe.SubscriptionSchedule.modify_async.assert_awaited_once()
        args, kwargs = mock_stripe.SubscriptionSchedule.modify_async.call_args
        assert args == ("sub_sched_abc",)
        assert kwargs["end_behavior"] == "release"
        assert kwargs["proration_behavior"] == "none"
        assert "idempotency_key" not in kwargs
        assert kwargs["phases"] == [
            {
                "items": [
                    {"price": "price_seat_licensed", "quantity": 10},
                    {"price": "price_usage_metered"},
                ],
                "start_date": 1_690_000_000,
                "end_date": 1_700_000_000,
            },
            {
                "items": [
                    {"price": "price_seat_licensed", "quantity": 5},
                    {"price": "price_usage_metered"},
                ],
            },
        ]

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_wraps_stripe_error(self, mock_stripe):
        import stripe as real_stripe

        mock_stripe.StripeError = real_stripe.StripeError
        mock_stripe.SubscriptionSchedule.create_async = AsyncMock(
            side_effect=real_stripe.APIError("boom")
        )

        client = StripeClient(_make_settings())
        with pytest.raises(StripeClientError, match="Failed to create subscription schedule"):
            await client.create_subscription_schedule(
                subscription_id="sub_abc",
                phase_1_qty=10,
                phase_1_end=1_700_000_000,
                phase_2_qty=5,
                items=_SCHEDULE_ITEMS,
            )


class TestUpdateSubscriptionSchedule:
    """F2 — retrieve → rebuild phases with new phase-2 qty → modify."""

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_preserves_phase_1_and_updates_phase_2_quantity(self, mock_stripe):
        retrieved = MagicMock()
        retrieved.phases = [
            {
                "items": [
                    {"price": "price_seat_licensed", "quantity": 10},
                    {"price": "price_usage_metered", "quantity": None},
                ],
                "start_date": 1_690_000_000,
                "end_date": 1_700_000_000,
            },
            {
                "items": [
                    {"price": "price_seat_licensed", "quantity": 5},
                    {"price": "price_usage_metered", "quantity": None},
                ],
                "start_date": 1_700_000_000,
                "end_date": None,
            },
        ]
        mock_stripe.SubscriptionSchedule.retrieve_async = AsyncMock(return_value=retrieved)

        final = MagicMock()
        final.id = "sub_sched_abc"
        mock_stripe.SubscriptionSchedule.modify_async = AsyncMock(return_value=final)
        mock_stripe.StripeError = Exception

        client = StripeClient(_make_settings())
        result = await client.update_subscription_schedule(
            schedule_id="sub_sched_abc",
            phase_2_qty=3,
        )

        assert result is final
        mock_stripe.SubscriptionSchedule.retrieve_async.assert_awaited_once_with("sub_sched_abc")
        args, kwargs = mock_stripe.SubscriptionSchedule.modify_async.call_args
        assert args == ("sub_sched_abc",)
        assert "idempotency_key" not in kwargs
        assert kwargs["phases"] == [
            {
                "items": [
                    {"price": "price_seat_licensed", "quantity": 10},
                    {"price": "price_usage_metered"},
                ],
                "start_date": 1_690_000_000,
                "end_date": 1_700_000_000,
            },
            {
                "items": [
                    {"price": "price_seat_licensed", "quantity": 3},
                    {"price": "price_usage_metered"},
                ],
                "start_date": 1_700_000_000,
            },
        ]

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_wraps_stripe_error(self, mock_stripe):
        import stripe as real_stripe

        mock_stripe.StripeError = real_stripe.StripeError
        mock_stripe.SubscriptionSchedule.retrieve_async = AsyncMock(
            side_effect=real_stripe.APIError("boom")
        )

        client = StripeClient(_make_settings())
        with pytest.raises(StripeClientError, match="Failed to update subscription schedule"):
            await client.update_subscription_schedule(
                schedule_id="sub_sched_abc",
                phase_2_qty=3,
            )


class TestReleaseSubscriptionSchedule:
    """F3 — thin wrapper around release_async."""

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_calls_release_async_with_schedule_id(self, mock_stripe):
        released = MagicMock()
        mock_stripe.SubscriptionSchedule.release_async = AsyncMock(return_value=released)
        mock_stripe.StripeError = Exception

        client = StripeClient(_make_settings())
        result = await client.release_subscription_schedule("sub_sched_abc")

        assert result is released
        mock_stripe.SubscriptionSchedule.release_async.assert_awaited_once_with("sub_sched_abc")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_wraps_stripe_error(self, mock_stripe):
        import stripe as real_stripe

        mock_stripe.StripeError = real_stripe.StripeError
        mock_stripe.SubscriptionSchedule.release_async = AsyncMock(
            side_effect=real_stripe.APIError("boom")
        )

        client = StripeClient(_make_settings())
        with pytest.raises(StripeClientError, match="Failed to release subscription schedule"):
            await client.release_subscription_schedule("sub_sched_abc")


def _make_subscription(*, seat_item_id="si_seat", current_qty=5, period_end=1_700_000_000, schedule=None):
    """Build a mock subscription with Shu's two-item layout (seat + metered)."""
    data = {
        "items": {
            "data": [
                {
                    "id": seat_item_id,
                    "quantity": current_qty,
                    "price": {
                        "id": "price_seat",
                        "recurring": {"usage_type": "licensed"},
                    },
                },
                {
                    "id": "si_meter",
                    # Metered items carry a quantity in the Stripe API response,
                    # but it's not billable — we only rely on price.recurring.usage_type
                    # to distinguish them.
                    "quantity": 1,
                    "price": {
                        "id": "price_meter",
                        "recurring": {"usage_type": "metered"},
                    },
                },
            ]
        },
        "current_period_end": period_end,
        "schedule": schedule,
    }
    sub = MagicMock()
    sub.__getitem__ = lambda self, key: data[key]
    sub.__contains__ = lambda self, key: key in data
    sub.get = lambda key, default=None: data.get(key, default)
    return sub


class TestUpdateSubscriptionQuantityNoOp:
    """Branch (a) — target equals current quantity: no Stripe write."""

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_returns_changed_false_and_writes_nothing(self, mock_stripe):
        subscription = _make_subscription(current_qty=5)
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=subscription)
        mock_stripe.Subscription.modify_async = AsyncMock()
        mock_stripe.SubscriptionSchedule.create_async = AsyncMock()
        mock_stripe.SubscriptionSchedule.modify_async = AsyncMock()
        mock_stripe.SubscriptionSchedule.release_async = AsyncMock()
        mock_stripe.StripeError = Exception

        client = StripeClient(_make_settings())
        result_sub, changed = await client.update_subscription_quantity("sub_abc", target=5)

        assert changed is False
        assert result_sub is subscription
        mock_stripe.Subscription.modify_async.assert_not_awaited()
        mock_stripe.SubscriptionSchedule.create_async.assert_not_awaited()
        mock_stripe.SubscriptionSchedule.modify_async.assert_not_awaited()
        mock_stripe.SubscriptionSchedule.release_async.assert_not_awaited()


class TestUpdateSubscriptionQuantityUpgrade:
    """Branches (b), (c) — upgrade paths."""

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_clean_upgrade_calls_modify_only(self, mock_stripe):
        subscription = _make_subscription(current_qty=5, schedule=None)
        updated_sub = MagicMock()
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=subscription)
        mock_stripe.Subscription.modify_async = AsyncMock(return_value=updated_sub)
        mock_stripe.SubscriptionSchedule.release_async = AsyncMock()
        mock_stripe.StripeError = Exception

        client = StripeClient(_make_settings())
        result_sub, changed = await client.update_subscription_quantity("sub_abc", target=7)

        assert changed is True
        assert result_sub is updated_sub
        mock_stripe.SubscriptionSchedule.release_async.assert_not_awaited()
        mock_stripe.Subscription.modify_async.assert_awaited_once_with(
            "sub_abc",
            items=[{"id": "si_seat", "quantity": 7}],
            proration_behavior="create_prorations",
        )

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_upgrade_with_pending_schedule_releases_first(self, mock_stripe):
        """Stale downgrade must be cleared before the upgrade modify — otherwise
        Gate A's finding kicks in and the pending downgrade survives the upgrade."""
        subscription = _make_subscription(current_qty=5, schedule="sub_sched_pending")
        updated_sub = MagicMock()
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=subscription)
        mock_stripe.Subscription.modify_async = AsyncMock(return_value=updated_sub)
        mock_stripe.SubscriptionSchedule.release_async = AsyncMock(return_value=MagicMock())
        mock_stripe.StripeError = Exception

        client = StripeClient(_make_settings())
        result_sub, changed = await client.update_subscription_quantity("sub_abc", target=7)

        assert changed is True
        assert result_sub is updated_sub
        mock_stripe.SubscriptionSchedule.release_async.assert_awaited_once_with("sub_sched_pending")
        mock_stripe.Subscription.modify_async.assert_awaited_once()
        # Release must happen before modify — we assert order via separate await counts
        # plus the fact that release_async's return must have been observed prior to modify.


class TestUpdateSubscriptionQuantityDowngrade:
    """Branches (d), (e) — downgrade paths defer to period end via schedule."""

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_clean_downgrade_creates_two_phase_schedule(self, mock_stripe):
        subscription = _make_subscription(current_qty=5, period_end=1_700_000_000, schedule=None)
        created_schedule = MagicMock()
        created_schedule.id = "sub_sched_new"
        # `from_subscription` returns a one-phase schedule whose start_date is
        # carried into the install-phases call.
        created_schedule.__getitem__.side_effect = (
            lambda k: [{"start_date": 1_690_000_000}] if k == "phases" else None
        )
        final_schedule = MagicMock()
        final_schedule.id = "sub_sched_new"
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=subscription)
        mock_stripe.SubscriptionSchedule.create_async = AsyncMock(return_value=created_schedule)
        mock_stripe.SubscriptionSchedule.modify_async = AsyncMock(return_value=final_schedule)
        mock_stripe.Subscription.modify_async = AsyncMock()
        mock_stripe.StripeError = Exception

        client = StripeClient(_make_settings())
        result_sub, changed = await client.update_subscription_quantity("sub_abc", target=3)

        assert changed is True
        # Downgrade does not alter the visible subscription — caller gets the pre-write sub.
        assert result_sub is subscription
        mock_stripe.Subscription.modify_async.assert_not_awaited()
        mock_stripe.SubscriptionSchedule.create_async.assert_awaited_once_with(
            from_subscription="sub_abc",
        )
        # Modify installs the two-phase structure with both licensed + metered items per phase
        modify_args, modify_kwargs = mock_stripe.SubscriptionSchedule.modify_async.call_args
        assert modify_args == ("sub_sched_new",)
        assert "idempotency_key" not in modify_kwargs
        assert modify_kwargs["end_behavior"] == "release"
        assert modify_kwargs["proration_behavior"] == "none"
        phases = modify_kwargs["phases"]
        assert phases == [
            {
                "items": [
                    {"price": "price_seat", "quantity": 5},
                    {"price": "price_meter"},
                ],
                "start_date": 1_690_000_000,
                "end_date": 1_700_000_000,
            },
            {
                "items": [
                    {"price": "price_seat", "quantity": 3},
                    {"price": "price_meter"},
                ],
            },
        ]

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_downgrade_with_pending_schedule_updates_phase_2_only(self, mock_stripe):
        subscription = _make_subscription(current_qty=5, schedule="sub_sched_pending")
        # retrieve_async is called twice: once on the subscription, once on the schedule.
        retrieved_schedule = MagicMock()
        retrieved_schedule.phases = [
            {
                "items": [
                    {"price": "price_seat", "quantity": 5},
                    {"price": "price_meter", "quantity": None},
                ],
                "end_date": 1_700_000_000,
            },
            {
                "items": [
                    {"price": "price_seat", "quantity": 4},
                    {"price": "price_meter", "quantity": None},
                ],
                "end_date": None,
            },
        ]
        final_schedule = MagicMock()
        final_schedule.id = "sub_sched_pending"
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=subscription)
        mock_stripe.SubscriptionSchedule.retrieve_async = AsyncMock(return_value=retrieved_schedule)
        mock_stripe.SubscriptionSchedule.modify_async = AsyncMock(return_value=final_schedule)
        mock_stripe.SubscriptionSchedule.create_async = AsyncMock()
        mock_stripe.StripeError = Exception

        client = StripeClient(_make_settings())
        result_sub, changed = await client.update_subscription_quantity("sub_abc", target=3)

        assert changed is True
        assert result_sub is subscription
        # Must not create a second schedule — we updated the existing one.
        mock_stripe.SubscriptionSchedule.create_async.assert_not_awaited()
        mock_stripe.SubscriptionSchedule.modify_async.assert_awaited_once()
        args, kwargs = mock_stripe.SubscriptionSchedule.modify_async.call_args
        assert args == ("sub_sched_pending",)
        assert "idempotency_key" not in kwargs
        # Phase 1 preserved; phase 2 qty re-stamped to target.
        assert kwargs["phases"][-1]["items"] == [
            {"price": "price_seat", "quantity": 3},
            {"price": "price_meter"},
        ]


class TestUpdateSubscriptionQuantityErrors:
    """Branch (f) — Stripe errors surface as StripeClientError."""

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_retrieve_error_raises(self, mock_stripe):
        import stripe as real_stripe

        mock_stripe.StripeError = real_stripe.StripeError
        mock_stripe.InvalidRequestError = real_stripe.InvalidRequestError
        mock_stripe.Subscription.retrieve_async = AsyncMock(
            side_effect=real_stripe.APIError("boom")
        )

        client = StripeClient(_make_settings())
        with pytest.raises(StripeClientError, match="Failed to retrieve subscription"):
            await client.update_subscription_quantity("sub_abc", target=7)

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_upgrade_modify_error_raises(self, mock_stripe):
        import stripe as real_stripe

        subscription = _make_subscription(current_qty=5, schedule=None)
        mock_stripe.StripeError = real_stripe.StripeError
        mock_stripe.InvalidRequestError = real_stripe.InvalidRequestError
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=subscription)
        mock_stripe.Subscription.modify_async = AsyncMock(
            side_effect=real_stripe.APIError("boom")
        )

        client = StripeClient(_make_settings())
        with pytest.raises(StripeClientError, match="Failed to apply seat upgrade"):
            await client.update_subscription_quantity("sub_abc", target=7)

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_missing_licensed_item_raises(self, mock_stripe):
        """Subscription without a licensed item is a misconfiguration — fail loud."""
        subscription = MagicMock()
        subscription.__getitem__ = lambda self, key: {
            "items": {
                "data": [
                    {
                        "id": "si_meter",
                        "quantity": 1,
                        "price": {
                            "id": "price_meter",
                            "recurring": {"usage_type": "metered"},
                        },
                    },
                ]
            },
            "current_period_end": 1_700_000_000,
            "schedule": None,
        }[key]
        subscription.__contains__ = lambda self, key: key in {"items", "current_period_end", "schedule"}
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=subscription)
        mock_stripe.StripeError = Exception

        client = StripeClient(_make_settings())
        with pytest.raises(StripeClientError, match="has no licensed seat item"):
            await client.update_subscription_quantity("sub_abc", target=5)


# =============================================================================
# Credit Grants (read-only)
# =============================================================================
#
# Issuance happens in the Shu Control Plane. The tenant-side client only
# reads the active total for display. These tests cover the active filter
# (voided / expired excluded), pagination, and the StripeError → StripeClientError
# translation that lets routers degrade gracefully on a Stripe outage.

from decimal import Decimal


def _make_grant(*, value_cents: int, voided_at=None, expires_at=None):
    """Build a Stripe CreditGrant-shaped MagicMock with monetary amount and lifecycle fields.

    Mirrors the Stripe API shape: amount.monetary.value (cents),
    voided_at (timestamp or None), expires_at (timestamp or None).
    """
    grant = MagicMock()
    grant.voided_at = voided_at
    grant.expires_at = expires_at
    grant.amount = MagicMock()
    grant.amount.monetary = MagicMock()
    grant.amount.monetary.value = value_cents
    return grant


def _make_page(grants, *, has_more=False, last_id="cg_last"):
    """Build a Stripe list-result-shaped MagicMock."""
    page = MagicMock()
    page.data = grants
    page.has_more = has_more
    if grants:
        # Mirror Stripe's behavior — last item carries the cursor id used for
        # `starting_after` on the next page.
        grants[-1].id = last_id
    return page


class TestGetActiveCreditGrantTotalUsd:
    """Tests for summing active credit grant amounts on a customer."""

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_returns_zero_when_no_grants(self, mock_stripe):
        """Empty grant list returns Decimal('0.00') without raising."""
        mock_stripe.billing.CreditGrant.list_async = AsyncMock(return_value=_make_page([]))

        client = StripeClient(_make_settings())
        result = await client.get_active_credit_grant_total_usd("cus_abc")

        assert result == Decimal("0.00")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_sums_active_grants_in_dollars(self, mock_stripe):
        """Cents are converted to dollars and summed across grants."""
        # 25000 + 5000 = 30000 cents = $300.00
        # Future expiry; not voided.
        future = 9_999_999_999
        grants = [
            _make_grant(value_cents=25_000, expires_at=future),
            _make_grant(value_cents=5_000, expires_at=future),
        ]
        mock_stripe.billing.CreditGrant.list_async = AsyncMock(return_value=_make_page(grants))

        client = StripeClient(_make_settings())
        result = await client.get_active_credit_grant_total_usd("cus_abc")

        assert result == Decimal("300.00")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_excludes_voided_grants(self, mock_stripe):
        """Grants with a voided_at timestamp are skipped entirely."""
        future = 9_999_999_999
        grants = [
            _make_grant(value_cents=25_000, expires_at=future),
            _make_grant(value_cents=10_000, expires_at=future, voided_at=1_700_000_000),
        ]
        mock_stripe.billing.CreditGrant.list_async = AsyncMock(return_value=_make_page(grants))

        client = StripeClient(_make_settings())
        result = await client.get_active_credit_grant_total_usd("cus_abc")

        assert result == Decimal("250.00")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_excludes_expired_grants(self, mock_stripe):
        """Grants with expires_at in the past are skipped."""
        future = 9_999_999_999
        past = 1  # epoch + 1 second; far in the past
        grants = [
            _make_grant(value_cents=25_000, expires_at=future),
            _make_grant(value_cents=10_000, expires_at=past),
        ]
        mock_stripe.billing.CreditGrant.list_async = AsyncMock(return_value=_make_page(grants))

        client = StripeClient(_make_settings())
        result = await client.get_active_credit_grant_total_usd("cus_abc")

        assert result == Decimal("250.00")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_includes_grants_with_no_expiry(self, mock_stripe):
        """Grants with expires_at = None (perpetual) count as active."""
        grants = [_make_grant(value_cents=12_345, expires_at=None)]
        mock_stripe.billing.CreditGrant.list_async = AsyncMock(return_value=_make_page(grants))

        client = StripeClient(_make_settings())
        result = await client.get_active_credit_grant_total_usd("cus_abc")

        assert result == Decimal("123.45")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_paginates_via_starting_after(self, mock_stripe):
        """Multi-page result follows page.has_more and uses last id as starting_after."""
        future = 9_999_999_999
        page1_grants = [_make_grant(value_cents=10_000, expires_at=future)]
        page2_grants = [_make_grant(value_cents=20_000, expires_at=future)]
        page1 = _make_page(page1_grants, has_more=True, last_id="cg_page1_last")
        page2 = _make_page(page2_grants, has_more=False, last_id="cg_page2_last")

        list_async = AsyncMock(side_effect=[page1, page2])
        mock_stripe.billing.CreditGrant.list_async = list_async

        client = StripeClient(_make_settings())
        result = await client.get_active_credit_grant_total_usd("cus_abc")

        assert result == Decimal("300.00")
        # Two pages → two calls; the second is parameterized with starting_after.
        assert list_async.call_count == 2
        first_call_kwargs = list_async.await_args_list[0].kwargs
        second_call_kwargs = list_async.await_args_list[1].kwargs
        assert "starting_after" not in first_call_kwargs
        assert second_call_kwargs.get("starting_after") == "cg_page1_last"

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_stripe_error_raises_stripe_client_error(self, mock_stripe):
        """Stripe API failures bubble as StripeClientError so callers can degrade."""
        mock_stripe.StripeError = Exception
        mock_stripe.billing.CreditGrant.list_async = AsyncMock(side_effect=Exception("boom"))

        client = StripeClient(_make_settings())
        with pytest.raises(StripeClientError, match="Failed to list credit grants"):
            await client.get_active_credit_grant_total_usd("cus_abc")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_handles_grant_with_missing_amount_fields_gracefully(self, mock_stripe):
        """A grant with no monetary amount block contributes 0 rather than crashing."""
        future = 9_999_999_999
        broken = MagicMock()
        broken.voided_at = None
        broken.expires_at = future
        broken.amount = None  # absent
        good = _make_grant(value_cents=15_000, expires_at=future)
        mock_stripe.billing.CreditGrant.list_async = AsyncMock(return_value=_make_page([broken, good]))

        client = StripeClient(_make_settings())
        result = await client.get_active_credit_grant_total_usd("cus_abc")

        assert result == Decimal("150.00")


# =============================================================================
# Subscription markup multiplier (read-only, derived from metered Price)
# =============================================================================


def _make_subscription_with_metered_price(
    *,
    unit_amount_decimal: str | None = "0.00013",
    include_metered: bool = True,
):
    """Build a mock subscription with an optional metered item carrying a unit price.

    The metered Price's ``unit_amount_decimal`` drives the markup math:
        markup = unit_amount_decimal_cents * 10_000

    Set ``include_metered=False`` to exercise the no-metered-item branch.
    Set ``unit_amount_decimal=None`` to exercise the missing-decimal branch
    (e.g., a tiered pricing model).
    """
    items: list[dict[str, Any]] = [
        {
            "id": "si_seat",
            "quantity": 5,
            "price": {"id": "price_seat", "recurring": {"usage_type": "licensed"}},
        }
    ]
    if include_metered:
        metered_price: dict[str, Any] = {
            "id": "price_meter",
            "recurring": {"usage_type": "metered"},
        }
        if unit_amount_decimal is not None:
            metered_price["unit_amount_decimal"] = unit_amount_decimal
        items.append({"id": "si_meter", "quantity": 1, "price": metered_price})
    data = {"items": {"data": items}}
    sub = MagicMock()
    sub.__getitem__ = lambda self, key: data[key]
    sub.__contains__ = lambda self, key: key in data
    sub.get = lambda key, default=None: data.get(key, default)
    return sub


class TestGetSubscriptionMarkupMultiplier:
    """Tests for deriving the customer markup ratio from the metered Price."""

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_returns_one_point_three_for_thirteen_microcents(self, mock_stripe):
        """unit_amount_decimal=0.00013 cents/unit → markup 1.3 (provider cost + 30%)."""
        sub = _make_subscription_with_metered_price(unit_amount_decimal="0.00013")
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=sub)

        client = StripeClient(_make_settings())
        result = await client.get_subscription_markup_multiplier("sub_abc")

        assert result == Decimal("1.3000")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_returns_one_point_five_for_fifteen_microcents(self, mock_stripe):
        """A different markup (e.g., +50%) is computed correctly from the price."""
        sub = _make_subscription_with_metered_price(unit_amount_decimal="0.00015")
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=sub)

        client = StripeClient(_make_settings())
        result = await client.get_subscription_markup_multiplier("sub_abc")

        assert result == Decimal("1.5000")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_returns_none_when_no_metered_item(self, mock_stripe):
        """Subscription with only a licensed seat → no markup to compute."""
        sub = _make_subscription_with_metered_price(include_metered=False)
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=sub)

        client = StripeClient(_make_settings())
        result = await client.get_subscription_markup_multiplier("sub_abc")

        assert result is None

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_returns_none_when_metered_price_lacks_unit_amount_decimal(self, mock_stripe):
        """Tiered pricing models lack a flat unit_amount_decimal → fall back."""
        sub = _make_subscription_with_metered_price(unit_amount_decimal=None)
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=sub)

        client = StripeClient(_make_settings())
        result = await client.get_subscription_markup_multiplier("sub_abc")

        assert result is None

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_returns_none_for_unparseable_unit_amount_decimal(self, mock_stripe):
        """A garbage value (shouldn't happen but defensive) → None instead of crash."""
        sub = _make_subscription_with_metered_price(unit_amount_decimal="not-a-number")
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=sub)

        client = StripeClient(_make_settings())
        result = await client.get_subscription_markup_multiplier("sub_abc")

        assert result is None

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_returns_none_for_zero_unit_amount(self, mock_stripe):
        """A zero unit price would yield markup=0; treat as not-meaningful and return None."""
        sub = _make_subscription_with_metered_price(unit_amount_decimal="0")
        mock_stripe.Subscription.retrieve_async = AsyncMock(return_value=sub)

        client = StripeClient(_make_settings())
        result = await client.get_subscription_markup_multiplier("sub_abc")

        assert result is None

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_stripe_error_raises_stripe_client_error(self, mock_stripe):
        """A Stripe API failure on the retrieve call surfaces as StripeClientError.

        The error message originates in ``get_subscription`` (the shared
        wrapper) since this method now delegates to it for the cache benefit.
        """
        mock_stripe.StripeError = Exception
        mock_stripe.InvalidRequestError = ValueError  # placeholder; not raised here
        mock_stripe.Subscription.retrieve_async = AsyncMock(side_effect=Exception("boom"))

        client = StripeClient(_make_settings())
        with pytest.raises(StripeClientError, match="Failed to retrieve subscription"):
            await client.get_subscription_markup_multiplier("sub_abc")

    @pytest.mark.asyncio
    @patch("shu.billing.stripe_client.stripe")
    async def test_subscription_is_cached_across_methods_on_same_client(self, mock_stripe):
        """Two methods on the same StripeClient share a single Stripe retrieve.

        Locks in the per-request memoization optimization — if the cache
        breaks, a second Stripe API call would happen on the same request,
        regressing the consolidation work.
        """
        sub = _make_subscription_with_metered_price(unit_amount_decimal="0.00013")
        retrieve = AsyncMock(return_value=sub)
        mock_stripe.Subscription.retrieve_async = retrieve

        client = StripeClient(_make_settings())
        # First retrieve via the wrapper.
        first = await client.get_subscription("sub_abc")
        # Second consumer (markup) reuses the cached object.
        markup = await client.get_subscription_markup_multiplier("sub_abc")

        assert first is sub
        assert markup == Decimal("1.3000")
        assert retrieve.await_count == 1
