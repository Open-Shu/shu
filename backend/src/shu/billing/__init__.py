"""Stripe billing integration module.

This module handles:
- Checkout session creation
- Customer portal access
- Subscription management via webhooks
- Usage reporting to Stripe Meters API

Billing configuration is stored in system_settings under the 'billing' key.
"""

from shu.billing.service import BillingService

__all__ = ["BillingService"]
