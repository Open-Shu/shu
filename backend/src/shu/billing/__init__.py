"""Stripe billing integration module.

This module handles:
- Customer portal access
- Subscription lifecycle via webhooks
- Usage reporting to Stripe Meters API
- Seat quantity sync

Billing state is persisted in the ``billing_state`` table (singleton row) and
audited in ``billing_state_audit``; all mutations go through
``BillingStateService``.
"""

from shu.billing.service import BillingService

__all__ = ["BillingService"]
