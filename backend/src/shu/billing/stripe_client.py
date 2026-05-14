"""Stripe SDK wrapper.

Encapsulates all direct Stripe API interactions. The rest of the billing
module uses this client rather than importing stripe directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import stripe
from stripe import Subscription

from shu.billing.config import BillingSettings, get_billing_settings
from shu.billing.schemas import (
    PortalSessionResponse,
    SubscriptionUpdate,
    UsageMeterEvent,
)
from shu.core.logging import get_logger

logger = get_logger(__name__)


class StripeClientError(Exception):
    """Base exception for Stripe client errors."""

    def __init__(self, message: str, stripe_error: stripe.StripeError | None = None) -> None:
        super().__init__(message)
        self.stripe_error = stripe_error


class StripeConfigurationError(StripeClientError):
    """Raised when Stripe is not properly configured."""

    pass


class StripeClient:
    """Wrapper around the Stripe SDK.

    All Stripe API calls go through this client to:
    - Centralize configuration
    - Provide consistent error handling
    - Enable easier testing/mocking
    - Abstract SDK version changes
    """

    def __init__(self, settings: BillingSettings | None = None) -> None:
        self._settings = settings or get_billing_settings()
        self._validate_config()
        self._configure_stripe()

    def _validate_config(self) -> None:
        """Validate that required configuration is present."""
        if not self._settings.secret_key:
            raise StripeConfigurationError("Stripe secret key not configured. Set SHU_STRIPE_SECRET_KEY.")

    def _configure_stripe(self) -> None:
        """Configure the Stripe SDK with our settings."""
        stripe.api_key = self._settings.secret_key
        # Pin the Stripe API version explicitly. Without this, the SDK sends
        # requests using the account's Dashboard-configured default version,
        # which Stripe can auto-roll without warning. A silent version bump
        # broke parse_subscription_update when 2026-03-25.dahlia moved
        # current_period_{start,end} off the Subscription object onto each
        # SubscriptionItem (SHU-707). Bumping this pin requires reviewing all
        # parse_* functions in this file for payload-shape compatibility.
        # See https://docs.stripe.com/upgrades for the changelog.
        stripe.api_version = "2026-03-25.dahlia"
        # Set app info for Stripe Dashboard identification
        stripe.set_app_info(
            "Shu",
            version="1.0.0",
            url="https://github.com/Open-Shu/shu",
        )

    # =========================================================================
    # Customer Portal
    # =========================================================================

    async def create_portal_session(
        self,
        customer_id: str,
        return_url: str,
    ) -> PortalSessionResponse:
        """Create a Stripe Customer Portal session.

        The portal allows customers to manage their subscription, payment
        methods, and view invoices.

        Args:
            customer_id: Stripe customer ID
            return_url: URL to return to after portal session

        Returns:
            PortalSessionResponse with portal URL

        """
        try:
            session = await stripe.billing_portal.Session.create_async(
                customer=customer_id,
                return_url=return_url,
            )

            logger.info(
                "Created portal session",
                extra={"customer_id": customer_id},
            )

            return PortalSessionResponse(url=session.url)

        except stripe.StripeError as e:
            logger.error(
                "Failed to create portal session",
                extra={"customer_id": customer_id, "error": str(e)},
            )
            raise StripeClientError(f"Failed to create portal session: {e}", e) from e

    # =========================================================================
    # Subscriptions
    # =========================================================================

    async def get_subscription(self, subscription_id: str) -> Subscription | None:
        """Retrieve a subscription by ID.

        Returns None when Stripe responds "no such subscription" so callers
        can decide whether the absence is fatal. Other Stripe errors raise
        StripeClientError. Always expanded with ``items.data.price`` so
        seat-item identification doesn't need a second fetch.
        """
        try:
            return await stripe.Subscription.retrieve_async(
                subscription_id,
                expand=["items.data.price"],
            )
        except stripe.InvalidRequestError as e:
            if "No such subscription" in str(e):
                return None
            raise StripeClientError(f"Failed to retrieve subscription: {e}", e) from e
        except stripe.StripeError as e:
            raise StripeClientError(f"Failed to retrieve subscription: {e}", e) from e

    async def get_upcoming_invoice(
        self,
        subscription_id: str,
        *,
        subscription_items: list[dict[str, Any]] | None = None,
        subscription_proration_behavior: str | None = None,
    ) -> stripe.Invoice | None:
        """Preview the upcoming invoice for a subscription, optionally with item overrides.

        No item overrides → baseline preview at current quantity. Overrides plus
        ``subscription_proration_behavior='create_prorations'`` → preview of
        the change applied now, including proration lines.
        """
        # API 2026-03-25.dahlia replaced ``Invoice.upcoming`` with
        # ``Invoice.create_preview`` and folded the per-subscription overrides
        # under a nested ``subscription_details`` object (was top-level
        # ``subscription_items`` / ``subscription_proration_behavior``).
        params: dict[str, Any] = {"subscription": subscription_id}
        sub_details: dict[str, Any] = {}
        if subscription_items is not None:
            sub_details["items"] = subscription_items
        if subscription_proration_behavior is not None:
            sub_details["proration_behavior"] = subscription_proration_behavior
        if sub_details:
            params["subscription_details"] = sub_details
        try:
            return await stripe.Invoice.create_preview_async(**params)
        except stripe.InvalidRequestError as e:
            # No upcoming invoice exists (e.g. cancelled sub). Treat as missing
            # preview rather than a hard failure so callers can fall back to
            # "no price preview" UX.
            if "No upcoming invoices" in str(e) or "No such subscription" in str(e):
                return None
            raise StripeClientError(f"Failed to preview upcoming invoice: {e}", e) from e
        except stripe.StripeError as e:
            raise StripeClientError(f"Failed to preview upcoming invoice: {e}", e) from e

    async def get_subscription_seat_state(
        self,
        subscription_id: str,
    ) -> tuple[int, int, str | None]:
        """Return (live_qty, target_qty, schedule_id) — Stripe is the source of truth.

        ``live_qty`` is the seat item's current ``quantity`` (what the customer
        is being billed for right now). ``target_qty`` is what the seat count
        will be after the next billing cycle: the schedule's last-phase
        quantity if a schedule is attached, otherwise equal to ``live_qty``.

        Two API calls when a schedule is attached, one otherwise. The DB no
        longer caches these — every action that needs them reads here.
        """
        subscription = await self.get_subscription(subscription_id)
        if subscription is None:
            raise StripeClientError(f"Subscription {subscription_id!r} not found")
        seat_item = find_seat_item(subscription)
        if seat_item is None:
            raise StripeClientError(f"Subscription {subscription_id!r} has no licensed seat item")
        live = int(seat_item["quantity"])

        schedule_id = subscription.get("schedule")
        target = live
        if schedule_id:
            try:
                schedule = await stripe.SubscriptionSchedule.retrieve_async(schedule_id)
            except stripe.StripeError as e:
                raise StripeClientError(f"Failed to retrieve subscription schedule: {e}", e) from e
            if schedule.phases:
                last_phase_items = schedule.phases[-1]["items"]
                for item in last_phase_items:
                    qty = item.get("quantity")
                    if qty is not None:
                        target = int(qty)
                        break
        return live, target, schedule_id

    async def update_subscription_quantity(
        self,
        subscription_id: str,
        target: int,
    ) -> tuple[Subscription, bool]:
        """Apply a seat-count target to a subscription, branching on direction.

        Single entry point for all seat changes. Fetches the subscription,
        identifies the licensed (seat) item, then branches:

        - **No-op** (``target == current_qty``): return the fetched sub
          untouched, ``changed=False``.
        - **Upgrade** (``target > current_qty``): release any pending
          downgrade schedule first (Gate A: modify-without-release succeeds
          but leaves stale intent — product UX, not API necessity — see
          :ref:`SHU-704 Gate A`), then ``Subscription.modify`` with
          ``create_prorations``.
        - **Downgrade** (``target < current_qty``): defer to period end via
          a two-phase schedule. Update the existing schedule's phase-2 qty
          if one is attached; otherwise create a fresh schedule.

        Returns ``(subscription, changed)``. On downgrade, ``subscription``
        is the pre-write fetched object (visible quantity is still the
        phase-1 value through period end); the schedule carries the future
        intent. ``changed`` is False only for the no-op branch — schedule
        writes still count as changed.
        """
        try:
            subscription = await stripe.Subscription.retrieve_async(subscription_id)
        except stripe.InvalidRequestError as e:
            raise StripeClientError(f"Subscription {subscription_id!r} not found: {e}", e) from e
        except stripe.StripeError as e:
            raise StripeClientError(f"Failed to retrieve subscription: {e}", e) from e

        seat_item = find_seat_item(subscription)
        if seat_item is None:
            raise StripeClientError(f"Subscription {subscription_id!r} has no licensed seat item")

        current_qty = int(seat_item["quantity"])
        period_end = resolve_period_end(subscription, seat_item)
        schedule_id = subscription.get("schedule")

        if target == current_qty:
            logger.debug(
                "Seat quantity already matches target",
                extra={"subscription_id": subscription_id, "quantity": target},
            )
            # If a downgrade schedule was previously installed, the caller's
            # intent of "be at target=current" implicitly cancels it — leaving
            # the schedule attached would silently drop seats at period end.
            # Release defensively so target/quantity/schedule stay coherent.
            if schedule_id:
                await self.release_subscription_schedule(schedule_id)
                return subscription, True
            return subscription, False

        if target > current_qty:
            return await self._apply_upgrade(
                subscription_id=subscription_id,
                seat_item_id=seat_item["id"],
                current_qty=current_qty,
                target=target,
                schedule_id=schedule_id,
            )

        return await self._schedule_downgrade(
            subscription=subscription,
            subscription_id=subscription_id,
            current_qty=current_qty,
            target=target,
            period_end=period_end,
            schedule_id=schedule_id,
        )

    async def _apply_upgrade(
        self,
        *,
        subscription_id: str,
        seat_item_id: str,
        current_qty: int,
        target: int,
        schedule_id: str | None,
    ) -> tuple[Subscription, bool]:
        """Release any pending downgrade, then modify with prorations."""
        if schedule_id:
            # Even though Gate A showed modify succeeds with an attached
            # schedule, leaving it would silently re-apply the pending
            # downgrade at period end — the upgrade user did not ask for that.
            await self.release_subscription_schedule(schedule_id)

        try:
            updated = await stripe.Subscription.modify_async(
                subscription_id,
                items=[{"id": seat_item_id, "quantity": target}],
                proration_behavior="create_prorations",
            )
            logger.info(
                "Applied seat upgrade",
                extra={
                    "subscription_id": subscription_id,
                    "from_quantity": current_qty,
                    "to_quantity": target,
                    "released_schedule_id": schedule_id,
                },
            )
            return updated, True
        except stripe.StripeError as e:
            logger.error(
                "Failed to apply seat upgrade",
                extra={"subscription_id": subscription_id, "error": str(e)},
            )
            raise StripeClientError(f"Failed to apply seat upgrade: {e}", e) from e

    async def _schedule_downgrade(
        self,
        *,
        subscription: Subscription,
        subscription_id: str,
        current_qty: int,
        target: int,
        period_end: int,
        schedule_id: str | None,
    ) -> tuple[Subscription, bool]:
        """Install or update a two-phase schedule so the downgrade lands at period end."""
        if schedule_id:
            updated_schedule = await self.update_subscription_schedule(
                schedule_id=schedule_id,
                phase_2_qty=target,
            )
        else:
            updated_schedule = await self.create_subscription_schedule(
                subscription_id=subscription_id,
                phase_1_qty=current_qty,
                phase_1_end=period_end,
                phase_2_qty=target,
                items=_build_schedule_items(subscription),
            )
        logger.info(
            "Scheduled seat downgrade",
            extra={
                "subscription_id": subscription_id,
                "schedule_id": updated_schedule.id,
                "from_quantity": current_qty,
                "to_quantity": target,
                "effective_at": period_end,
            },
        )
        # Visible quantity on the subscription doesn't change until the schedule
        # transitions at period end — we return the pre-write sub so callers
        # reflect the current (still billed) state, not the target.
        return subscription, True

    async def create_subscription_schedule(
        self,
        subscription_id: str,
        phase_1_qty: int,
        phase_1_end: int,
        phase_2_qty: int,
        items: list[dict[str, Any]],
    ) -> stripe.SubscriptionSchedule:
        """Create a two-phase subscription schedule for a deferred seat downgrade.

        Phase 1 preserves the current seat count through ``phase_1_end``
        (period boundary — caller has already paid for this phase). Phase 2
        is open-ended at the lower ``phase_2_qty``. ``end_behavior="release"``
        ensures the subscription keeps running on phase-2 terms once the
        schedule's last phase completes rather than cancelling.

        Stripe requires a two-call dance: ``create`` with ``from_subscription``
        must be the only field (API rule), then ``modify`` installs phases.
        """
        try:
            created = await stripe.SubscriptionSchedule.create_async(
                from_subscription=subscription_id,
            )
            # Stripe's modify endpoint requires at least one phase with a
            # start_date to anchor end_date offsets. The schedule returned by
            # `from_subscription` has phase 1's start_date already populated
            # (matches the subscription's current period start) — preserve it.
            phase_1_start = created["phases"][0]["start_date"]
            phase_1_items = _stamp_quantity(items, phase_1_qty)
            phase_2_items = _stamp_quantity(items, phase_2_qty)
            updated = await stripe.SubscriptionSchedule.modify_async(
                created.id,
                phases=[
                    {
                        "items": phase_1_items,
                        "start_date": phase_1_start,
                        "end_date": phase_1_end,
                    },
                    {"items": phase_2_items},
                ],
                end_behavior="release",
                # Suppress proration entries on the upcoming invoice. Phase 1
                # always mirrors the current subscription quantity (so the
                # mid-cycle transition would emit ±lines that cancel to $0),
                # and phase 2 lands on a period boundary (no partial-period
                # to prorate). The customer-facing invoice would otherwise
                # show two equal-and-opposite proration entries that net out
                # but read like a refund-then-charge.
                proration_behavior="none",
            )

            logger.info(
                "Created subscription schedule",
                extra={
                    "subscription_id": subscription_id,
                    "schedule_id": updated.id,
                    "phase_1_qty": phase_1_qty,
                    "phase_2_qty": phase_2_qty,
                },
            )
            return updated

        except stripe.StripeError as e:
            logger.error(
                "Failed to create subscription schedule",
                extra={"subscription_id": subscription_id, "error": str(e)},
            )
            raise StripeClientError(f"Failed to create subscription schedule: {e}", e) from e

    async def update_subscription_schedule(
        self,
        schedule_id: str,
        phase_2_qty: int,
    ) -> stripe.SubscriptionSchedule:
        """Adjust phase-2 quantity on an existing schedule.

        Used when a second seat change arrives before the scheduled phase-2
        start: we edit the pending phase rather than releasing + recreating,
        preserving phase-1's committed end date. Phase-1 is echoed back
        unchanged; phase-2 items are rebuilt with the new quantity stamped
        onto lines that carry a quantity.
        """
        try:
            schedule = await stripe.SubscriptionSchedule.retrieve_async(schedule_id)
            phases = [_phase_to_params(phase) for phase in schedule.phases]
            phases[-1]["items"] = _stamp_quantity(phases[-1]["items"], phase_2_qty)
            updated = await stripe.SubscriptionSchedule.modify_async(
                schedule_id,
                phases=phases,
            )

            logger.info(
                "Updated subscription schedule",
                extra={"schedule_id": schedule_id, "phase_2_qty": phase_2_qty},
            )
            return updated

        except stripe.StripeError as e:
            logger.error(
                "Failed to update subscription schedule",
                extra={"schedule_id": schedule_id, "error": str(e)},
            )
            raise StripeClientError(f"Failed to update subscription schedule: {e}", e) from e

    async def release_subscription_schedule(
        self,
        schedule_id: str,
    ) -> stripe.SubscriptionSchedule:
        """Release a schedule — cancels the pending phase, leaves the subscription live.

        Called on an upgrade when a downgrade schedule is pending: we abandon
        the scheduled downgrade so the upgrade can apply immediately.
        """
        try:
            released = await stripe.SubscriptionSchedule.release_async(schedule_id)
            logger.info("Released subscription schedule", extra={"schedule_id": schedule_id})
            return released
        except stripe.StripeError as e:
            logger.error(
                "Failed to release subscription schedule",
                extra={"schedule_id": schedule_id, "error": str(e)},
            )
            raise StripeClientError(f"Failed to release subscription schedule: {e}", e) from e

    async def cancel_subscription(
        self,
        subscription_id: str,
        at_period_end: bool = True,
    ) -> Subscription:
        """Cancel a subscription.

        Args:
            subscription_id: Stripe subscription ID
            at_period_end: If True, cancel at end of current period (default).
                          If False, cancel immediately.

        """
        try:
            if at_period_end:
                subscription = await stripe.Subscription.modify_async(
                    subscription_id,
                    cancel_at_period_end=True,
                )
            else:
                subscription = await stripe.Subscription.cancel_async(subscription_id)

            logger.info(
                "Canceled subscription",
                extra={
                    "subscription_id": subscription_id,
                    "at_period_end": at_period_end,
                },
            )

            return subscription

        except stripe.StripeError as e:
            logger.error(
                "Failed to cancel subscription",
                extra={"subscription_id": subscription_id, "error": str(e)},
            )
            raise StripeClientError(f"Failed to cancel subscription: {e}", e) from e

    # =========================================================================
    # Usage Metering (Stripe Billing Meters)
    # =========================================================================

    async def report_usage(self, event: UsageMeterEvent) -> Any:
        """Report usage to Stripe Meters API.

        This is used for usage-based billing (token overage).

        Args:
            event: Usage event data including customer, timestamp, value

        Returns:
            Created MeterEvent or None if meters not configured

        """
        if not self._settings.meter_id_cost:
            logger.debug("Meter ID not configured, skipping usage report")
            return None

        try:
            meter_event = await stripe.billing.MeterEvent.create_async(
                event_name=event.event_name,
                identifier=event.identifier,
                payload={
                    **event.payload,
                    # Canonical fields must win over any same-key entries in payload.
                    "stripe_customer_id": event.stripe_customer_id,
                    "value": str(event.value),
                },
                timestamp=event.timestamp,
            )

            logger.debug(
                "Reported usage to Stripe",
                extra={
                    "customer_id": event.stripe_customer_id,
                    "value": event.value,
                    "meter_event_id": meter_event.identifier,
                },
            )

            return meter_event

        except stripe.StripeError as e:
            # Usage reporting failures should not break the app
            logger.error(
                "Failed to report usage to Stripe",
                extra={
                    "customer_id": event.stripe_customer_id,
                    "value": event.value,
                    "error": str(e),
                },
            )
            return None

    async def get_meter_event_summary(
        self,
        customer_id: str,
        start_time: int,
        end_time: int,
    ) -> int:
        """Get aggregated meter event total for a customer in a time range.

        Queries Stripe's Meter Event Summaries API to find out how much
        usage Stripe has recorded. Used for compare-and-correct reconciliation.

        Args:
            customer_id: Stripe customer ID
            start_time: Unix timestamp for range start
            end_time: Unix timestamp for range end

        Returns:
            Aggregated cost total (in microdollars) from Stripe, or 0 if no data / meter not configured.

        """
        if not self._settings.meter_id_cost:
            return 0

        try:
            # Explicit cursor pagination over the public API avoids the private
            # _auto_paging_iter_async() method. For a single customer/period the
            # result is almost always one page, but we paginate correctly in case
            # Stripe adds granularity that increases the result set in future.
            total = 0
            last_id: str | None = None
            while True:
                kwargs: dict[str, Any] = {
                    "customer": customer_id,
                    "start_time": start_time,
                    "end_time": end_time,
                }
                if last_id is not None:
                    kwargs["starting_after"] = last_id

                page = await stripe.billing.Meter.list_event_summaries_async(
                    self._settings.meter_id_cost,
                    **kwargs,
                )

                for summary in page.data:
                    total += int(summary.aggregated_value)

                if not page.has_more or not page.data:
                    break

                last_id = page.data[-1].id

            return total

        except stripe.StripeError as e:
            logger.error(
                "Failed to get meter event summary",
                extra={
                    "customer_id": customer_id,
                    "error": str(e),
                },
            )
            raise StripeClientError(f"Failed to get meter summary: {e}", e) from e

    # =========================================================================
    # Webhooks
    # =========================================================================
    #
    # Stripe signature verification used to live here as construct_webhook_event.
    # It was retired when the Shu Control Plane took over as the sole Stripe
    # webhook receiver — the control plane verifies Stripe signatures at its
    # edge, then forwards events to this tenant under an HMAC envelope.
    # Envelope verification is in shu.billing.router_envelope.

    def parse_subscription_update(self, subscription_data: dict[str, Any]) -> SubscriptionUpdate:
        """Parse subscription data from a webhook event into our DTO.

        Args:
            subscription_data: The 'data.object' from a subscription webhook event

        Returns:
            SubscriptionUpdate DTO

        """
        # Stripe API 2026-03-25.dahlia moved current_period_* from the subscription object
        # onto each subscription item. Prefer the item-level value when present; fall back
        # to subscription-level fields for older API versions. Any item works — periods are
        # uniform across the items in a subscription. If neither side carries valid timestamps,
        # the payload shape has drifted beyond what this parser understands — fail loudly with
        # context rather than letting datetime.fromtimestamp raise a bare TypeError.
        items_data = subscription_data.get("items", {}).get("data", [])
        any_item = items_data[0] if items_data else None
        period_source = (
            any_item
            if any_item and "current_period_start" in any_item and "current_period_end" in any_item
            else subscription_data
        )
        period_start_ts = period_source.get("current_period_start")
        period_end_ts = period_source.get("current_period_end")
        if not isinstance(period_start_ts, (int, float)) or not isinstance(period_end_ts, (int, float)):
            raise StripeClientError(
                f"Subscription {subscription_data.get('id')!r} webhook payload has no valid "
                "current_period_start/current_period_end at item level or subscription level; "
                f"Stripe API version may have drifted past {stripe.api_version!r}"
            )

        return SubscriptionUpdate(
            stripe_subscription_id=subscription_data["id"],
            stripe_customer_id=subscription_data["customer"],
            status=subscription_data["status"],
            current_period_start=datetime.fromtimestamp(period_start_ts, tz=UTC),
            current_period_end=datetime.fromtimestamp(period_end_ts, tz=UTC),
            cancel_at_period_end=subscription_data.get("cancel_at_period_end", False),
            canceled_at=(
                datetime.fromtimestamp(subscription_data["canceled_at"], tz=UTC)
                if subscription_data.get("canceled_at")
                else None
            ),
        )


def resolve_period_end(subscription: Any, seat_item: Any | None) -> int:
    """Resolve ``current_period_end`` as a unix timestamp.

    API 2026-03-25.dahlia moved ``current_period_*`` off the Subscription
    object onto each SubscriptionItem. Prefer item-level when present, fall
    back to subscription root for older API versions. Raises KeyError if
    neither carries the field — that's a payload-shape drift the caller
    should surface, not silently paper over.
    """
    if seat_item is not None and "current_period_end" in seat_item:
        return int(seat_item["current_period_end"])
    return int(subscription["current_period_end"])


def find_seat_item(subscription: Any) -> Any | None:
    """Locate the licensed (seat) item in a subscription's items.

    Shu's subscription shape pairs a licensed seat price with a metered
    usage price. Identifying the seat by ``recurring.usage_type == "licensed"``
    is more durable than matching a configured price ID — price rotations
    (annual ↔ monthly) leave the usage_type fingerprint intact.
    """
    for item in subscription["items"]["data"]:
        if _is_licensed(item["price"]):
            return item
    return None


def _build_schedule_items(subscription: Any) -> list[dict[str, Any]]:
    """Compose per-phase item params from the live subscription's items.

    Licensed entries include a ``quantity`` key (its value doesn't matter —
    ``_stamp_quantity`` replaces it at phase-construction time). Metered
    entries omit ``quantity`` so Stripe's API doesn't reject the phase.
    Avoids hardcoding price IDs: the composition inherits whatever prices
    the subscription already has.
    """
    composed: list[dict[str, Any]] = []
    for item in subscription["items"]["data"]:
        price = item["price"]
        entry: dict[str, Any] = {"price": price["id"]}
        if _is_licensed(price):
            entry["quantity"] = item["quantity"]
        composed.append(entry)
    return composed


def _is_licensed(price: Any) -> bool:
    """Return True when the price is a fixed-quantity recurring (licensed) price.

    One-off prices lack ``recurring`` entirely; metered prices have
    ``usage_type == "metered"``. Both return False.
    """
    if "recurring" not in price or price["recurring"] is None:
        return False
    recurring = price["recurring"]
    if "usage_type" not in recurring:
        return False
    return recurring["usage_type"] == "licensed"


def _stamp_quantity(items: list[dict[str, Any]], qty: int) -> list[dict[str, Any]]:
    """Return a copy of ``items`` with ``qty`` stamped onto lines that carry a quantity.

    The Shu subscription shape pairs a licensed seat price (has ``quantity``)
    with a metered usage price (no ``quantity`` — consumption is reported via
    meter events). Stripe rejects ``quantity`` on metered phase items, so we
    only stamp licensed lines. Detection is by the presence of a ``quantity``
    key on the incoming item — caller's responsibility to compose items
    accordingly.
    """
    return [({**item, "quantity": qty} if "quantity" in item else {**item}) for item in items]


def _phase_to_params(phase: Any) -> dict[str, Any]:
    """Convert a retrieved schedule phase into modify-call params.

    Stripe's retrieve shape on phases carries more fields than modify accepts
    (ids, plans, proration metadata). We round-trip only the minimum modify
    needs: the item list (price + quantity for licensed; price-only for
    metered) plus the phase's start_date / end_date when present. Stripe's
    modify endpoint requires at least one phase to carry a start_date so it
    can anchor end_date offsets — preserving the retrieved start_date on
    phase 1 keeps that anchor.
    """
    items: list[dict[str, Any]] = []
    for item in phase["items"]:
        params: dict[str, Any] = {"price": item["price"]}
        # Presence of `quantity` distinguishes licensed (stamp-through) from
        # metered (omit); see _stamp_quantity for why metered stays bare.
        if "quantity" in item and item["quantity"] is not None:
            params["quantity"] = item["quantity"]
        items.append(params)

    result: dict[str, Any] = {"items": items}
    if "start_date" in phase and phase["start_date"] is not None:
        result["start_date"] = phase["start_date"]
    if "end_date" in phase and phase["end_date"] is not None:
        result["end_date"] = phase["end_date"]
    return result
